// Closest-point search: the full scan against pg::ClosestIndex and the structures that were tried
// beside it (bench/closest_candidates.hpp). Every timing comes with an exactness count: the number
// of queries whose answer differs from the scan's, which must be 0.
//
//   pg_bench_closest [--reps N] [--only documents,sphere,sizes] [--scan-sample N] [--stat median|least]
//
// Build and query times are medians over `reps` runs (default 7), or the fastest run with
// `--stat least`. The scan is timed on a sample of each query class (default 150 queries) because
// 20,000 queries over 200,000 triangles take a minute.
#include "closest_candidates.hpp"

#include "pg/closest.hpp"
#include "pg/doc.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <utility>
#include <vector>

using namespace pg;
using Clock = std::chrono::steady_clock;

#ifndef PG_RECIPES
#define PG_RECIPES "recipes"
#endif

namespace {

volatile double g_sink = 0;

double seconds_of(const std::function<void()>& fn) {
    const auto t0 = Clock::now();
    fn();
    return std::chrono::duration<double>(Clock::now() - t0).count();
}
bool g_least = false;  // --stat least: report the fastest run instead of the median (steadier on a busy machine)
double median_seconds(int reps, const std::function<void()>& fn) {
    std::vector<double> s;
    for (int r = 0; r < reps; ++r) s.push_back(seconds_of(fn));
    std::sort(s.begin(), s.end());
    return g_least ? s.front() : s[s.size() / 2];
}

struct Rng {
    std::uint64_t s;
    std::uint64_t next() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        return s;
    }
    double unit() { return static_cast<double>(next() >> 11) * 0x1p-53; }
    double range(double lo, double hi) { return lo + (hi - lo) * unit(); }
    Vec3 direction() {
        for (;;) {
            const Vec3 v{range(-1, 1), range(-1, 1), range(-1, 1)};
            const double l = length(v);
            if (l > 0.1 && l <= 1) return v / l;
        }
    }
};

struct Answer {
    bool hit = false;
    double d = 0;
    std::uint32_t order = 0;
    Vec3 q{};
    [[nodiscard]] bool same(const Answer& o) const { return hit == o.hit && (!hit || (d == o.d && order == o.order && q == o.q)); }
};
Answer answer_of(bool hit, const ClosestHit& h) { return {hit, h.distance, h.triangle, h.point}; }
Answer answer_of(const cand::Found& f) { return {f.hit(), f.d, f.order, f.q}; }

using Query = std::function<Answer(Vec3)>;

// ---------------------------------------------------------------------------------------------------
// Documents: what a real evaluation gains.
struct DetailDigest {
    std::size_t details = 0;
    std::uint64_t hash = 1469598103934665603ull;
};
DetailDigest digest(const doc::Geometry& g) {
    DetailDigest d;
    const auto mix = [&](const void* bytes, std::size_t n) {
        const auto* b = static_cast<const unsigned char*>(bytes);
        for (std::size_t i = 0; i < n; ++i) d.hash = (d.hash ^ b[i]) * 1099511628211ull;
    };
    for (const doc::Part& part : g.parts)
        for (const doc::Detail& x : part.proto->details) {
            ++d.details;
            const double v[] = {x.origin.x, x.origin.y, x.origin.z, x.normal.x, x.normal.y, x.normal.z, x.tangent.x, x.tangent.y, x.tangent.z,
                                x.end.x,    x.end.y,    x.end.z,    x.size_a,   x.size_b,   x.height,   x.fillet};
            mix(v, sizeof v);
            const std::uint16_t tags[] = {static_cast<std::uint16_t>(x.shape), static_cast<std::uint16_t>(x.profile), x.material, x.fastener};
            mix(tags, sizeof tags);
        }
    return d;
}

void bench_document(const char* label, const std::string& path, const doc::Controls& controls, int reps) {
    std::printf("\n%s\n", label);
    const char* names[] = {"auto", "scan", "index"};
    const doc::ProjectionSearch modes[] = {doc::ProjectionSearch::Auto, doc::ProjectionSearch::Scan, doc::ProjectionSearch::Index};
    DetailDigest reference;
    double scan_ms = 0;
    std::printf("  %-6s %10s %9s %9s %8s %7s %10s %9s %9s %9s  %s\n", "search", "eval ms", "queries", "scanned", "builds", "tris", "build ms", "scan ms", "index ms", "other ms", "details");
    for (int m : {1, 2, 0}) {
        doc::set_projection_search(modes[m]);
        doc::Result result;
        const double total = median_seconds(reps, [&] { result = doc::evaluate_file(path, controls); });
        // Where the time goes, from the least disturbed of a few profiled runs (the profile adds two
        // clock readings to every query, so its parts need not sum to the unprofiled total).
        doc::ProjectionProfile profile;
        double profiled = 1e300;
        for (int r = 0; r < std::min(reps, 5); ++r) {
            doc::ProjectionProfile one;
            doc::profile_projection(&one);
            const double s = seconds_of([&] { result = doc::evaluate_file(path, controls); });
            doc::profile_projection(nullptr);
            if (s < profiled) {
                profiled = s;
                profile = one;
            }
        }
        const DetailDigest d = digest(result.geometry);
        if (m == 1) {
            reference = d;
            scan_ms = total * 1e3;
        }
        const double projection = profile.build_seconds + profile.scan_seconds + profile.index_seconds;
        std::printf("  %-6s %10.2f %9llu %9llu %8llu %7llu %10.2f %9.2f %9.2f %9.2f  %zu %s", names[m], total * 1e3,
                    static_cast<unsigned long long>(profile.queries), static_cast<unsigned long long>(profile.scanned),
                    static_cast<unsigned long long>(profile.builds), static_cast<unsigned long long>(profile.built_triangles), profile.build_seconds * 1e3,
                    profile.scan_seconds * 1e3, profile.index_seconds * 1e3, (profiled - projection) * 1e3, d.details,
                    d.details == reference.details && d.hash == reference.hash ? "identical" : "DIFFERENT");
        if (m != 1) std::printf("  (%.2fx)", scan_ms / (total * 1e3));
        std::printf("\n");
    }
    doc::set_projection_search(doc::ProjectionSearch::Auto);
}

// ---------------------------------------------------------------------------------------------------
// The synthetic stress case.
struct Classes {
    std::vector<const char*> names;
    std::vector<std::vector<Vec3>> points;
};

// A subdivided cube is a slightly flattened sphere: rows of guide points 0.15 m off it (consecutive
// points 4 cm apart, like a rivet row), points just on it, points inside (down to the centre) and
// points beyond the reach.
Classes sphere_queries(const Mesh& m, std::size_t total) {
    Rng rng{20261115};
    double radius = 0;
    for (const Vec3& v : m.positions) radius += length(v);
    radius /= static_cast<double>(m.positions.size());
    Classes c;
    c.names = {"guide rows", "on surface", "inside", "beyond reach"};
    c.points.resize(4);
    const std::size_t guides = total * 2 / 5, others = total / 5;
    while (c.points[0].size() < guides) {
        const double theta = rng.range(0.15, 0.85) * 3.141592653589793, ring = (radius + 0.15) * std::sin(theta);
        const double step = 0.04 / ring, start = rng.range(0, 6.283185307179586);
        for (double phi = 0; phi < 6.283185307179586 && c.points[0].size() < guides; phi += step)
            c.points[0].push_back({ring * std::cos(start + phi), ring * std::sin(start + phi), (radius + 0.15) * std::cos(theta)});
    }
    const auto on_surface = [&] { return m.positions[rng.next() % m.positions.size()]; };
    for (std::size_t i = 0; i < others; ++i) {
        c.points[1].push_back(on_surface() * (1.0 + rng.range(-0.003, 0.003)));
        c.points[2].push_back(i == 0 ? Vec3{} : on_surface() * rng.range(0.0, 0.98));
        c.points[3].push_back(rng.direction() * (radius + rng.range(0.4, 5.0)));
    }
    return c;
}

struct Row {
    std::string name;
    double build_ms = 0, memory_mb = 0;
    std::vector<double> us;        // per class: microseconds per query
    std::size_t mismatches = 0;    // against the scan (sampled) and against the library index (all)
};

void print_rows(const Classes& classes, const std::vector<Row>& rows) {
    std::printf("  %-34s %9s %8s", "structure", "build ms", "MB");
    for (const char* n : classes.names) std::printf(" %13s", n);
    std::printf(" %9s  %s\n", "all", "differs");
    std::size_t total = 0;
    for (const auto& p : classes.points) total += p.size();
    for (const Row& r : rows) {
        std::printf("  %-34s %9.2f %8.2f", r.name.c_str(), r.build_ms, r.memory_mb);
        double all = 0;
        for (std::size_t k = 0; k < r.us.size(); ++k) {
            std::printf(" %10.3f us", r.us[k]);
            all += r.us[k] * static_cast<double>(classes.points[k].size());
        }
        std::printf(" %6.1f ms  %zu\n", all * 1e-3, r.mismatches);
    }
    std::printf("  (query columns: microseconds per query; `all`: the whole set of %zu queries)\n", total);
}

// Times `query` on every class and counts answers that differ from `truth` (one per query, by class).
Row measure(const std::string& name, const Classes& classes, const std::vector<std::vector<Answer>>& truth, int reps, const Query& query,
            const std::function<void()>& reset = [] {}) {
    Row row;
    row.name = name;
    for (std::size_t k = 0; k < classes.points.size(); ++k) {
        const std::vector<Vec3>& pts = classes.points[k];
        std::vector<Answer> got(pts.size());
        const double s = median_seconds(reps, [&] {
            reset();
            for (std::size_t i = 0; i < pts.size(); ++i) got[i] = query(pts[i]);
        });
        row.us.push_back(s / static_cast<double>(pts.size()) * 1e6);
        for (std::size_t i = 0; i < pts.size(); ++i) row.mismatches += !got[i].same(truth[k][i]);
        g_sink = g_sink + got[0].d;
    }
    return row;
}

void bench_sphere(int reps, std::size_t scan_sample) {
    const Mesh sphere = catmull_clark(make_box({1, 1, 1}, 1), 7);
    const Classes classes = sphere_queries(sphere, 20000);
    const double reach = 0.25;
    std::size_t triangles = 0;
    for (std::size_t f = 0; f < sphere.face_count(); ++f) triangles += sphere.face(f).size() - 2;
    std::printf("\nSubdivided sphere: %zu triangles, 20,000 queries, reach %.2f m\n", triangles, reach);

    using Split = ClosestIndex::Options::Split;
    const ClosestIndex index(sphere);

    // The scan, on a sample of each class; the library index on all of it, checked against that sample.
    std::vector<std::vector<Answer>> truth(classes.points.size());
    Row scan_row;
    scan_row.name = "full scan (sampled)";
    std::size_t index_vs_scan = 0, sampled = 0;
    for (std::size_t k = 0; k < classes.points.size(); ++k) {
        const std::vector<Vec3>& pts = classes.points[k];
        const std::size_t stride = std::max<std::size_t>(1, pts.size() / scan_sample);
        std::vector<Answer> scanned;
        const double s = median_seconds(std::min(reps, 3), [&] {
            scanned.clear();
            for (std::size_t i = 0; i < pts.size(); i += stride) {
                ClosestHit h;
                const bool hit = closest_point_scan(sphere, pts[i], reach, h);
                scanned.push_back(answer_of(hit, h));
            }
        });
        scan_row.us.push_back(s / static_cast<double>(scanned.size()) * 1e6);
        for (const Vec3& p : pts) {
            ClosestHit h;
            const bool hit = index.closest(p, reach, h);
            truth[k].push_back(answer_of(hit, h));
        }
        for (std::size_t i = 0, j = 0; i < pts.size(); i += stride, ++j) index_vs_scan += !scanned[j].same(truth[k][i]);
        sampled += scanned.size();
    }
    std::printf("  library index against the scan on %zu sampled queries: %zu differ\n", sampled, index_vs_scan);
    std::size_t hits = 0, queries = 0;
    for (const auto& t : truth)
        for (const Answer& a : t) {
            hits += a.hit;
            ++queries;
        }
    std::printf("  %zu of %zu queries land within reach\n", hits, queries);

    std::vector<Row> rows;
    rows.push_back(scan_row);
    const auto index_row = [&](const std::string& name, ClosestIndex::Options o) {
        ClosestIndex built;
        const double build = median_seconds(reps, [&] { built = ClosestIndex(sphere, o); });
        Row r = measure(name, classes, truth, reps, [&](Vec3 p) {
            ClosestHit h;
            const bool hit = built.closest(p, reach, h);
            return answer_of(hit, h);
        });
        r.build_ms = build * 1e3;
        r.memory_mb = static_cast<double>(built.memory_bytes()) / 1048576.0;
        rows.push_back(r);
    };
    index_row("index: SAH longest axis, leaf 4", {Split::SurfaceArea, 4, false});
    index_row("index: SAH three axes, leaf 4", {Split::SurfaceArea, 4, true});
    index_row("index: median split, leaf 4", {Split::Median, 4, false});
    index_row("index: SAH longest axis, leaf 2", {Split::SurfaceArea, 2, false});
    index_row("index: SAH longest axis, leaf 8", {Split::SurfaceArea, 8, false});
    index_row("index: SAH longest axis, leaf 1", {Split::SurfaceArea, 1, false});

    const double index_mb = static_cast<double>(index.memory_bytes()) / 1048576.0;

    // The same queries in another order: along a Morton curve (the paper's reordering for cache
    // locality) and shuffled (what a row of guide points gains from being a row).
    const auto reordered = [&](const std::string& name, bool morton) {
        Classes other = classes;
        std::vector<std::vector<Answer>> other_truth = truth;
        const Aabb box = index.bounds();
        const Vec3 lo = box.lo - Vec3{6, 6, 6}, span = box.extent() + Vec3{12, 12, 12};
        Rng shuffle{77};
        for (std::size_t k = 0; k < classes.points.size(); ++k) {
            const std::vector<Vec3>& pts = classes.points[k];
            std::vector<std::pair<std::uint64_t, std::uint32_t>> keys(pts.size());
            for (std::uint32_t i = 0; i < pts.size(); ++i) {
                std::uint64_t key = shuffle.next();
                if (morton) {
                    key = 0;
                    const std::uint64_t q[3] = {static_cast<std::uint64_t>(std::clamp((pts[i].x - lo.x) / span.x, 0.0, 1.0) * 1048575.0),
                                                static_cast<std::uint64_t>(std::clamp((pts[i].y - lo.y) / span.y, 0.0, 1.0) * 1048575.0),
                                                static_cast<std::uint64_t>(std::clamp((pts[i].z - lo.z) / span.z, 0.0, 1.0) * 1048575.0)};
                    for (int bit = 19; bit >= 0; --bit)
                        for (int a = 0; a < 3; ++a) key = key << 1 | ((q[a] >> bit) & 1u);
                }
                keys[i] = {key, i};
            }
            std::sort(keys.begin(), keys.end());
            for (std::size_t i = 0; i < pts.size(); ++i) {
                other.points[k][i] = pts[keys[i].second];
                other_truth[k][i] = truth[k][keys[i].second];
            }
        }
        Row r = measure(name, other, other_truth, reps, [&](Vec3 p) {
            ClosestHit h;
            const bool hit = index.closest(p, reach, h);
            return answer_of(hit, h);
        });
        r.memory_mb = index_mb;
        rows.push_back(r);
    };
    reordered("  queries along a Morton curve", true);
    reordered("  queries shuffled", false);

    const cand::WideBvh wide(index);
    {
        Row r = measure("same tree, bench traversal", classes, truth, reps, [&](Vec3 p) {
            cand::Found f;
            wide.closest(p, reach, f);
            return answer_of(f);
        });
        r.memory_mb = index_mb;
        rows.push_back(r);
    }
    {
        std::uint32_t last = cand::kNone;
        Row r = measure("  + seed: the previous hit", classes, truth, reps, [&](Vec3 p) {
            cand::Found f;
            if (wide.closest(p, reach, f, 1.0, last)) last = f.slot;
            return answer_of(f);
        }, [&] { last = cand::kNone; });
        r.memory_mb = index_mb;
        rows.push_back(r);
    }
    for (const int resolution : {16, 48}) {
        std::vector<cand::SeedGrid> grid;
        const double build = median_seconds(std::min(reps, 3), [&] {
            grid.clear();
            grid.emplace_back(wide, resolution, reach);
        });
        Row r = measure("  + seed: grid " + std::to_string(resolution) + "^3", classes, truth, reps, [&](Vec3 p) {
            cand::Found f;
            wide.closest(p, reach, f, 1.0, grid[0].seed(p));
            return answer_of(f);
        });
        r.build_ms = build * 1e3;
        r.memory_mb = index_mb + static_cast<double>(grid[0].memory_bytes()) / 1048576.0;
        rows.push_back(r);
    }
    {
        std::vector<cand::ClassicBvh> classic;
        const double build = median_seconds(std::min(reps, 3), [&] {
            classic.clear();
            classic.emplace_back(index);
        });
        Row r = measure("same tree, one double box per node", classes, truth, reps, [&](Vec3 p) {
            cand::Found f;
            classic[0].closest(p, reach, f);
            return answer_of(f);
        });
        r.build_ms = build * 1e3;
        r.memory_mb = static_cast<double>(index.triangles().size() * sizeof(ClosestIndex::Triangle) + classic[0].memory_bytes()) / 1048576.0;
        rows.push_back(r);
    }
    for (const double density : {0.03, 0.25, 2.0}) {
        std::vector<cand::Grid> grid;
        const double build = median_seconds(std::min(reps, 3), [&] {
            grid.clear();
            grid.emplace_back(index, density);
        });
        char name[80];
        std::snprintf(name, sizeof name, "uniform grid %dx%dx%d", grid[0].n[0], grid[0].n[1], grid[0].n[2]);
        Row r = measure(name, classes, truth, std::min(reps, 3), [&](Vec3 p) {
            cand::Found f;
            grid[0].closest(p, reach, f);
            return answer_of(f);
        });
        r.build_ms = build * 1e3;
        r.memory_mb = static_cast<double>(index.triangles().size() * sizeof(ClosestIndex::Triangle) + grid[0].memory_bytes()) / 1048576.0;
        rows.push_back(r);
    }
    print_rows(classes, rows);
    std::printf("  (build ms of the seed grids, the one-box tree and the uniform grids is on top of the index's own: they are built from it)\n");

    // The same queries with no reach: the nearest point however far.
    std::printf("\nSame sphere, no reach (the nearest point however far)\n");
    std::vector<std::vector<Answer>> unbounded(classes.points.size());
    std::size_t differ = 0;
    sampled = 0;
    Row far_scan;
    far_scan.name = "full scan (sampled)";
    for (std::size_t k = 0; k < classes.points.size(); ++k) {
        const std::vector<Vec3>& pts = classes.points[k];
        for (const Vec3& p : pts) {
            ClosestHit h;
            const bool hit = index.closest(p, cand::kInfinity, h);
            unbounded[k].push_back(answer_of(hit, h));
        }
        const std::size_t stride = std::max<std::size_t>(1, pts.size() / (scan_sample / 3 + 1));
        std::size_t n = 0;
        const double s = seconds_of([&] {
            for (std::size_t i = 0; i < pts.size(); i += stride, ++n) {
                ClosestHit h;
                const bool hit = closest_point_scan(sphere, pts[i], cand::kInfinity, h);
                differ += !answer_of(hit, h).same(unbounded[k][i]);
            }
        });
        far_scan.us.push_back(s / static_cast<double>(n) * 1e6);
        sampled += n;
    }
    std::printf("  library index against the scan on %zu sampled queries: %zu differ\n", sampled, differ);
    std::vector<Row> far_rows = {far_scan};
    far_rows.push_back(measure("index: SAH longest axis, leaf 4", classes, unbounded, reps, [&](Vec3 p) {
        ClosestHit h;
        const bool hit = index.closest(p, cand::kInfinity, h);
        return answer_of(hit, h);
    }));
    print_rows(classes, far_rows);
}

// ---------------------------------------------------------------------------------------------------
// Break-even: how many queries on one part before building the index pays for itself.
void bench_sizes(int reps) {
    std::printf("\nBreak-even by mesh size (queries 0.1 diagonals off the surface, reach 0.25 diagonals)\n");
    struct Input {
        std::string name;
        Mesh mesh;
    };
    std::vector<Input> inputs;
    inputs.push_back({"box, 1 segment", make_box({1, 1, 1}, 1)});
    inputs.push_back({"box, 2 segments", make_box({1, 1, 1}, 2)});
    inputs.push_back({"box, 3 segments", make_box({1, 1, 1}, 3)});
    inputs.push_back({"box, 4 segments", make_box({1, 1, 1}, 4)});
    inputs.push_back({"box, 8 segments", make_box({1, 1, 1}, 8)});
    const Mesh cube = make_box({1, 1, 1}, 1);
    for (const std::uint32_t level : {4u, 5u, 6u, 7u}) inputs.push_back({"sphere, level " + std::to_string(level), catmull_clark(cube, level)});
    {
        const doc::Result shell = doc::evaluate_file(std::string(PG_RECIPES) + "/site/rolling-stock/atlantic-shell.json");
        const doc::Proto* casing = nullptr;
        for (const doc::Part& part : shell.geometry.parts)
            if (!casing || part.proto->details.size() > casing->details.size()) casing = part.proto.get();
        if (casing) inputs.insert(inputs.begin() + 5, {"locomotive casing", casing->mesh});
    }
    std::printf("  %-20s %9s %10s %11s %11s %9s %11s %11s %11s %11s  %s\n", "mesh", "triangles", "build ms", "scan us/q", "index us/q", "speed-up", "break-even",
                "grid ms", "grid us/q", "break-even", "differs");
    for (const Input& in : inputs) {
        const Mesh& m = in.mesh;
        std::size_t triangles = 0;
        for (std::size_t f = 0; f < m.face_count(); ++f) triangles += m.face(f).size() - 2;
        const Aabb box = bounds(m);
        const double size = box.diagonal();
        Rng rng{triangles};
        std::vector<Vec3> pts;
        for (int i = 0; i < 2000; ++i) {
            const auto fv = m.face(rng.next() % m.face_count());
            const Vec3 a = m.positions[fv[0]], b = m.positions[fv[1]], c = m.positions[fv[2]];
            const Vec3 n = normalize_or(cross(b - a, c - a), Vec3{0, 0, 1});
            double u = rng.unit(), v = rng.unit();
            if (u + v > 1) {
                u = 1 - u;
                v = 1 - v;
            }
            pts.push_back(a + (b - a) * u + (c - a) * v + n * (size * rng.range(0.05, 0.15)));
        }
        ClosestIndex index;
        const int build_reps = triangles > 50000 ? reps : reps * 20;
        const double build = median_seconds(build_reps, [&] { index = ClosestIndex(m); });
        const std::size_t scan_count = std::max<std::size_t>(20, std::min<std::size_t>(pts.size(), 4000000 / std::max<std::size_t>(triangles, 1)));
        std::vector<Answer> by_scan(scan_count), by_index(pts.size());
        const double scan = median_seconds(reps, [&] {
            for (std::size_t i = 0; i < scan_count; ++i) {
                ClosestHit h;
                const bool hit = closest_point_scan(m, pts[i], 0.25 * size, h);
                by_scan[i] = answer_of(hit, h);
            }
        }) / static_cast<double>(scan_count);
        const double query = median_seconds(reps, [&] {
            for (std::size_t i = 0; i < pts.size(); ++i) {
                ClosestHit h;
                const bool hit = index.closest(pts[i], 0.25 * size, h);
                by_index[i] = answer_of(hit, h);
            }
        }) / static_cast<double>(pts.size());
        std::size_t differ = 0;
        for (std::size_t i = 0; i < scan_count; ++i) differ += !by_scan[i].same(by_index[i]);
        // The uniform grid on the same queries (a quarter of a cell per triangle, the best of those tried).
        std::vector<cand::Grid> grid;
        const double grid_build = median_seconds(build_reps, [&] {
            grid.clear();
            grid.emplace_back(index, 0.25);
        });
        std::vector<Answer> by_grid(pts.size());
        const double grid_query = median_seconds(reps, [&] {
            for (std::size_t i = 0; i < pts.size(); ++i) {
                cand::Found f;
                grid[0].closest(pts[i], 0.25 * size, f);
                by_grid[i] = answer_of(f);
            }
        }) / static_cast<double>(pts.size());
        for (std::size_t i = 0; i < pts.size(); ++i) differ += !by_grid[i].same(by_index[i]);
        const auto even = [&](double b, double q) {
            return scan > q ? std::to_string(static_cast<long long>(std::ceil(b / (scan - q)))) : std::string("never");
        };
        std::printf("  %-20s %9zu %10.4f %11.3f %11.3f %8.1fx %11s %11.4f %11.3f %11s  %zu\n", in.name.c_str(), triangles, build * 1e3, scan * 1e6, query * 1e6,
                    scan / query, even(build, query).c_str(), grid_build * 1e3, grid_query * 1e6, even(grid_build, grid_query).c_str(), differ);
    }
    std::printf("  (grid build ms leaves out gathering the triangles, which it takes from the index: about a tenth of the index's build)\n");
}

}  // namespace

int main(int argc, char** argv) {
    int reps = 7;
    std::size_t scan_sample = 150;
    std::string only = "documents,sphere,sizes";
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--reps") && i + 1 < argc) reps = std::max(1, std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "--only") && i + 1 < argc) only = argv[++i];
        else if (!std::strcmp(argv[i], "--scan-sample") && i + 1 < argc) scan_sample = static_cast<std::size_t>(std::max(1, std::atoi(argv[++i])));
        else if (!std::strcmp(argv[i], "--stat") && i + 1 < argc) g_least = !std::strcmp(argv[++i], "least");
    }
    std::printf("Times are the %s of %d runs.\n", g_least ? "fastest" : "median", reps);
    const std::string recipes = PG_RECIPES;
    if (only.find("documents") != std::string::npos) {
        bench_document("Locomotive casing: site/rolling-stock/atlantic-shell.json", recipes + "/site/rolling-stock/atlantic-shell.json", {}, reps * 3);
        bench_document("Whole locomotive: site/rolling-stock/rail-locomotive.json", recipes + "/site/rolling-stock/rail-locomotive.json", {}, reps);
        bench_document("Riveted panel 12 x 6 m, pitch 0.04: site/plating/riveted-panel.json", recipes + "/site/plating/riveted-panel.json",
                       {{"width", 12.0f}, {"height", 6.0f}, {"pitch", 0.04f}}, reps);
    }
    if (only.find("sphere") != std::string::npos) bench_sphere(reps, scan_sample);
    if (only.find("sizes") != std::string::npos) bench_sizes(reps);
    return 0;
}
