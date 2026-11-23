// Closest point on a mesh: the index must give the scan's answer bit for bit.
#include "testing.hpp"

#include "pg/closest.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

using namespace pg;

namespace {

struct Rng {
    std::uint64_t s;
    std::uint64_t next() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        return s;
    }
    double unit() { return static_cast<double>(next() >> 11) * 0x1p-53; }      // [0, 1)
    double range(double lo, double hi) { return lo + (hi - lo) * unit(); }
    double log_range(double lo, double hi) { return lo * std::pow(hi / lo, unit()); }
    Vec3 in_box(const Aabb& b) { return {range(b.lo.x, b.hi.x), range(b.lo.y, b.hi.y), range(b.lo.z, b.hi.z)}; }
    Vec3 direction() {
        for (;;) {
            const Vec3 v{range(-1, 1), range(-1, 1), range(-1, 1)};
            const double l = length(v);
            if (l > 0.1 && l <= 1) return v / l;
        }
    }
};

// Bit for bit: a normal taken from a triangle with an infinite corner is NaN in both answers.
bool same(const ClosestHit& a, const ClosestHit& b) {
    const auto bits = [](Vec3 u, Vec3 v) { return std::memcmp(&u, &v, sizeof(Vec3)) == 0; };
    return bits(a.point, b.point) && bits(a.normal, b.normal) && std::memcmp(&a.distance, &b.distance, sizeof(double)) == 0 &&
           a.face == b.face && a.triangle == b.triangle;
}

struct Tally {
    std::size_t queries = 0, hits = 0, misses = 0, mismatches = 0;
};

void compare(const Mesh& m, const ClosestIndex& index, Vec3 p, double max_distance, double metric, Tally& tally) {
    ClosestHit a, b;
    const bool ha = closest_point_scan(m, p, max_distance, a, metric), hb = index.closest(p, max_distance, b, metric);
    ++tally.queries;
    tally.hits += ha;
    tally.misses += !ha;
    if (ha != hb || (ha && !same(a, b))) ++tally.mismatches;
}

// How many triangles attain the smallest distance (several: the tie-break decides the answer).
std::size_t tied(const Mesh& m, Vec3 p) {
    double best = std::numeric_limits<double>::infinity();
    std::size_t count = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
            const Vec3 a = m.positions[fv[0]], b = m.positions[fv[i]], c = m.positions[fv[i + 1]];
            if (!(length(cross(b - a, c - a)) > 1e-20)) continue;
            const double d = length(closest_on_triangle(p, a, b, c) - p);
            if (d < best) {
                best = d;
                count = 1;
            } else if (d == best) ++count;
        }
    }
    return count;
}

Aabb grown(const Aabb& b, double factor) {
    const Vec3 c = b.center(), h = b.extent() * (0.5 * factor);
    Aabb out;
    out.expand(c - h);
    out.expand(c + h);
    return out;
}

// Points that stress the search: in and around the bounds, on and just off vertices, edges and face
// centres, and far away.
std::vector<Vec3> probes(const Mesh& m, Rng& rng, std::size_t random_count) {
    std::vector<Vec3> out;
    const Aabb box = bounds(m);
    if (!box.valid()) return {Vec3{0, 0, 0}, Vec3{1, 2, 3}};
    const double size = std::max(box.diagonal(), 1e-12);
    const Aabb wide = grown(box, 2.5);
    for (std::size_t i = 0; i < random_count; ++i) out.push_back(rng.in_box(i % 3 ? wide : box));
    out.push_back(box.center());
    for (int corner = 0; corner < 8; ++corner) {  // the bounds' corners, and out along the diagonal
        const Vec3 c{corner & 1 ? box.hi.x : box.lo.x, corner & 2 ? box.hi.y : box.lo.y, corner & 4 ? box.hi.z : box.lo.z};
        out.push_back(c);
        out.push_back(c + (c - box.center()) * 0.5);
        out.push_back(c + (c - box.center()) * 100.0);
    }
    const std::size_t stride = std::max<std::size_t>(1, m.face_count() / 60);
    for (std::size_t f = 0; f < m.face_count(); f += stride) {
        const auto fv = m.face(f);
        Vec3 centre{};
        for (const Index v : fv) centre += m.positions[v];
        centre = centre / static_cast<double>(fv.size());
        const Vec3 a = m.positions[fv[0]], b = m.positions[fv[1]];
        for (const Vec3 on : {a, (a + b) * 0.5, centre}) {
            out.push_back(on);
            out.push_back(on + rng.direction() * (size * 1e-9));
            out.push_back(on + rng.direction() * (size * 0.02));
            out.push_back(on + (on - box.center()) * 0.3);
        }
    }
    return out;
}

void check_mesh(const Mesh& m, std::uint64_t seed, std::size_t random_count, Tally& tally) {
    Rng rng{seed};
    const std::vector<Vec3> points = probes(m, rng, random_count);
    const double size = bounds(m).valid() ? std::max(bounds(m).diagonal(), 1e-12) : 1.0;
    using Split = ClosestIndex::Options::Split;
    const ClosestIndex::Options variants[] = {{Split::SurfaceArea, 4, false}, {Split::SurfaceArea, 1, true}, {Split::Median, 8, false}, {Split::SurfaceArea, 2, false}};
    for (const ClosestIndex::Options& o : variants) {
        const ClosestIndex index(m, o);
        std::size_t k = 0;
        for (const Vec3& p : points) {
            const double reach[] = {std::numeric_limits<double>::infinity(), size * 0.3, size * 0.01, 0.0};
            const double metric[] = {1.0, 0.37, 3.0};
            compare(m, index, p, reach[k % 4], metric[k % 3], tally);
            compare(m, index, p, std::numeric_limits<double>::infinity(), 1.0, tally);
            ++k;
        }
    }
}

Mesh moved(Mesh m, Vec3 by) {
    translate(m, by);
    return m;
}
Mesh scaled(Mesh m, double s) {
    for (Vec3& p : m.positions) p = p * s;
    return m;
}

// Triangles of every temper: ordinary, obtuse, needles, slivers down to nothing, repeated and
// collinear corners, a few far from the rest.
Mesh soup(std::uint64_t seed, std::size_t count, double offset) {
    Rng rng{seed};
    Mesh m;
    for (std::size_t i = 0; i < count; ++i) {
        const Vec3 at = Vec3{rng.range(-1, 1), rng.range(-1, 1), rng.range(-1, 1)} + Vec3{offset, -offset, offset * 0.5};
        const double edge = rng.log_range(1e-4, 0.8);
        const Vec3 u = rng.direction();
        Vec3 w = cross(u, rng.direction());
        w = w / length(w);
        const Vec3 a = at, b = at + u * edge;
        Vec3 c;
        switch (i % 9) {
            case 0: c = a; break;                                                                // repeated corner
            case 1: c = a + u * (edge * rng.range(-1, 2)); break;                                // collinear
            case 2: c = a + u * (edge * rng.range(-1, 2)) + w * (edge * rng.log_range(1e-15, 1e-9)); break;  // sliver beyond the bound
            case 3: c = a + u * (edge * rng.range(-1, 2)) + w * (edge * rng.log_range(1e-6, 1e-3)); break;   // needle
            case 4: c = a + u * (edge * rng.range(-3, 4)) + w * (edge * rng.range(0.05, 0.3)); break;        // obtuse
            default: c = a + u * (edge * rng.range(0, 1)) + w * (edge * rng.range(0.3, 1.2)); break;
        }
        const Index first = m.add_vertex(a);
        m.add_vertex(b);
        m.add_vertex(c);
        m.add_face({first, first + 1, first + 2});
    }
    return m;
}

}  // namespace

TEST(closest_index_matches_the_full_scan) {
    const Mesh cube = make_box({1, 1, 1}, 1);
    const Mesh sphere = catmull_clark(cube, 4);
    Tally tally;
    check_mesh(cube, 1, 400, tally);
    check_mesh(make_box({2, 1, 0.5}, 8), 2, 400, tally);
    check_mesh(sphere, 3, 400, tally);
    check_mesh(make_cylinder(24, 0.5, 2.0), 4, 400, tally);               // 24-gon caps: long fans
    check_mesh(make_grid(20, 30, 3.0, 2.0), 5, 400, tally);               // open, flat
    check_mesh(merge(make_box({1, 1, 1}, 6), moved(make_cylinder(16, 0.3, 3.0), {1.5, 0.2, 0.1})), 6, 400, tally);
    check_mesh(moved(sphere, {1e7, -3e6, 2e7}), 7, 300, tally);            // far from the origin
    check_mesh(scaled(sphere, 1e-6), 8, 300, tally);                       // tiny
    check_mesh(scaled(make_box({3, 2, 1}, 5), 1e5), 9, 300, tally);        // huge
    check_mesh(merge(make_box({12, 6, 0.012}, 1), make_box({0.3, 0.3, 0.3}, 12)), 10, 400, tally);  // long thin sides beside fine ones
    for (std::uint64_t seed = 11; seed < 17; ++seed) check_mesh(soup(seed, 400, seed % 2 ? 0.0 : 5e5), seed, 500, tally);
    check_mesh(catmull_clark(cube, 5), 17, 100, tally);                    // 12,288 triangles: a deeper tree
    CHECK_EQ(tally.mismatches, std::size_t(0));
    CHECK(tally.queries > 100000 && tally.hits > 40000 && tally.misses > 10000);

    // Equidistant triangles: the answer is whichever the scan meets first.
    Tally ties;
    std::size_t several = 0;
    const ClosestIndex cube_index(cube), sphere_index(sphere);
    Rng rng{99};
    std::vector<Vec3> at = {{0, 0, 0}, {1, 1, 1}, {-1, 1, -1}, {0.75, 0.75, 0}, {0, -0.75, 0.75}, {0.5, 0.5, 0.5}, {0.5, 0.5, 0.1}, {0.9, 0.2, 0.9}};
    for (int i = 0; i < 200; ++i) {
        const double t = rng.range(-0.4, 0.4), out = rng.range(0, 2);
        at.push_back({0.5 + out, 0.5 + out, t});          // off an edge
        at.push_back({t, 0.5 + out, -0.5 - out});
        at.push_back({0.5 + out, 0.5 + out, 0.5 + out});  // off a corner
        at.push_back({t, t, 0});                          // on a diagonal plane, inside
        at.push_back({t, 0, 0});
    }
    for (const Vec3& p : at) {
        several += tied(cube, p) > 1;
        compare(cube, cube_index, p, std::numeric_limits<double>::infinity(), 1.0, ties);
        compare(cube, cube_index, p, 0.7, 2.0, ties);
    }
    CHECK(several > 600);
    for (const Vec3 p : {Vec3{0, 0, 0}, Vec3{1e-9, 0, 0}, Vec3{0, 0.1, 0}, Vec3{0.1, 0.1, 0.1}}) {
        several += tied(sphere, p) > 1;
        compare(sphere, sphere_index, p, std::numeric_limits<double>::infinity(), 1.0, ties);
    }
    CHECK_EQ(ties.mismatches, std::size_t(0));

    // The distance limit is inclusive, as `reach` is.
    ClosestHit hit;
    CHECK((cube_index.closest({0, 0, 1}, 0.5, hit) && hit.distance == 0.5 && hit.normal == Vec3{0, 0, 1} && hit.point == Vec3{0, 0, 0.5}));
    CHECK((!cube_index.closest({0, 0, 1}, std::nextafter(0.5, 0.0), hit)));
    CHECK((cube_index.closest({0, 0, 1}, 1.0, hit, 2.0) && hit.distance == 1.0));    // metric scales the distance
    CHECK((!cube_index.closest({0, 0, 1}, 0.99, hit, 2.0)));
    CHECK((!cube_index.closest({0, 0, 1}, std::numeric_limits<double>::quiet_NaN(), hit)));

    // Nothing to land on, and coordinates no bound can be given for.
    const Mesh nothing;
    CHECK(ClosestIndex(nothing).empty());
    CHECK((!ClosestIndex(nothing).closest({0, 0, 0}, 1.0, hit) && !closest_point_scan(nothing, {0, 0, 0}, 1.0, hit)));
    Mesh flat;  // only collinear corners
    flat.add_vertex({0, 0, 0});
    flat.add_vertex({1, 0, 0});
    flat.add_vertex({2, 0, 0});
    flat.add_face({0, 1, 2});
    CHECK((ClosestIndex(flat).empty() && !closest_point_scan(flat, {0, 0, 0}, 1.0, hit)));
    Mesh broken = make_box({1, 1, 1}, 4);
    broken.positions[7].x = std::numeric_limits<double>::infinity();
    broken.positions[11].y = std::numeric_limits<double>::quiet_NaN();
    Tally odd;
    check_mesh(broken, 21, 0, odd);
    Rng r2{5};
    const ClosestIndex broken_index(broken);
    for (int i = 0; i < 300; ++i) compare(broken, broken_index, {r2.range(-1, 1), r2.range(-1, 1), r2.range(-1, 1)}, 2.0, 1.0, odd);
    CHECK_EQ(odd.mismatches, std::size_t(0));
}

TEST(closest_point_strays_from_its_triangle_by_less_than_the_bound) {
    // The index skips a box only when the computed nearest point of every triangle in it is known to
    // lie within `stray` of that triangle's own box (src/closest.cpp). Measure the stray on triangles
    // of every shape, with the query placed where the arithmetic is worst: far out along the normal
    // above the corners, the edges and the boundaries between them.
    Rng rng{2026};
    double worst = 0.0;      // stray / (2^-52 * kappa * D^2 * longest edge): the constant the bound needs
    double tightest = 1e300; // bound / stray where there was any stray at all
    std::size_t tested = 0, strayed = 0;
    for (int i = 0; i < 400000; ++i) {
        const double edge = rng.log_range(1e-3, 1e3), height = edge * rng.log_range(0x1p-15, 1.0);
        const Vec3 origin = rng.direction() * (i % 4 ? rng.log_range(1e-3, 1e6) : 0.0);
        Vec3 u = rng.direction();
        Vec3 w = cross(u, rng.direction());
        w = w / length(w);
        if (i % 2) {  // along the axes: the triangle's edges are its box's, so any stray leaves the box
            const int axis = static_cast<int>(rng.next() % 3), turn = static_cast<int>(rng.next() % 2);
            const Vec3 axes[3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
            u = axes[axis] * (rng.next() & 1 ? 1.0 : -1.0);
            w = axes[(axis + 1 + turn) % 3] * (rng.next() & 1 ? 1.0 : -1.0);
        }
        const Vec3 n = cross(u, w);
        const double apex[] = {0.0, 1.0, 0.5, rng.range(-2, 3)};
        const Vec3 a = origin, b = origin + u * edge, c = origin + u * (edge * apex[rng.next() % 4]) + w * height;
        const double edge2 = std::max({dot(b - a, b - a), dot(c - a, c - a), dot(c - b, c - b)});
        const Vec3 normal = cross(b - a, c - a);
        const double kappa = edge2 / dot(normal, normal);
        if (!(dot(normal, normal) >= 0x1p-30 * edge2 * edge2)) continue;  // the index promises nothing for these
        // Barycentric position of the foot of the query: on a corner, an edge, inside, or just across.
        const double grid[] = {0.0, 1.0, 0.5, 1e-5, -1e-5, 1e-7, -1e-7, 1e-10, -1e-10, 1.0 + 1e-6, 1.0 - 1e-6, 1.0 - 1e-9, rng.unit(), rng.unit(), rng.unit(), rng.range(-1, 2)};
        const double s = grid[rng.next() % 16], t = grid[rng.next() % 16];
        // Up to the height times 5e5: just short of where the bound stops being offered.
        const double lift = (rng.next() & 1 ? 1.0 : -1.0) * (i % 7 ? height * rng.log_range(1e-2, 5e5) : 0.0);
        const Vec3 p = a + (b - a) * s + (c - a) * t + n * lift;
        const Vec3 q = closest_on_triangle(p, a, b, c);
        Aabb box;
        box.expand(a);
        box.expand(b);
        box.expand(c);
        const Vec3 out = vmax(vmax(box.lo - q, q - box.hi), Vec3{});
        const double stray = length(out);
        const double far2 = std::max({dot(p - a, p - a), dot(p - b, p - b), dot(p - c, p - c)});
        const double x = 0x1p-40 * kappa * far2;
        if (!(x < 0.25)) continue;                                        // nor for these
        const double largest = std::max({std::abs(box.lo.x), std::abs(box.lo.y), std::abs(box.lo.z), std::abs(box.hi.x), std::abs(box.hi.y), std::abs(box.hi.z)});
        const double bound = x * std::sqrt(edge2) + 0x1p-48 * largest;
        ++tested;
        CHECK(stray <= bound);
        if (stray > 0) {
            ++strayed;
            tightest = std::min(tightest, bound / stray);
            worst = std::max(worst, stray / (0x1p-52 * kappa * far2 * std::sqrt(edge2) + 0x1p-52 * largest));
        }
    }
    CHECK(tested > 200000 && strayed > 0);
    // In practice the nearest point all but never leaves its triangle's box, and then by a fraction of
    // one unit of eps * kappa * D^2 * edge. The bound allows 4096 of them.
    CHECK(worst < 4.0);
    CHECK(tightest > 1000.0);
}

TEST(raycast_index_matches_the_full_scan) {
    auto same_ray = [](const RaycastHit& a, const RaycastHit& b) {
        const auto bits = [](Vec3 u, Vec3 v) { return std::memcmp(&u, &v, sizeof(Vec3)) == 0; };
        return bits(a.point, b.point) && bits(a.normal, b.normal) &&
               std::memcmp(&a.t, &b.t, sizeof(double)) == 0 &&
               std::memcmp(&a.u, &b.u, sizeof(double)) == 0 &&
               std::memcmp(&a.v, &b.v, sizeof(double)) == 0 &&
               a.face == b.face && a.triangle == b.triangle && a.front_face == b.front_face;
    };

    const std::vector<Mesh> meshes = {
        make_box({1.0, 1.0, 1.0}, 1),
        make_box({2.4, 0.8, 1.7}, 12),
        catmull_clark(make_box({1.0, 1.0, 1.0}, 1), 4),
    };
    Rng rng{9091};
    std::size_t queries = 0, hits = 0, mismatches = 0;

    for (const Mesh& m : meshes) {
        const ClosestIndex idx(m);
        const Aabb big = grown(bounds(m), 2.5);
        for (int i = 0; i < 1200; ++i) {
            const Vec3 o = rng.in_box(big);
            Vec3 d = rng.direction();
            if (i % 6 == 0) {
                // Axis-aligned rays (`dir[a] == 0`) including grazing face-plane rays
                const int ax = i % 3;
                d = {ax == 0 ? 1.0 : 0.0, ax == 1 ? -1.0 : 0.0, ax == 2 ? 1.0 : 0.0};
            }
            const double t_min = (i % 5 == 0) ? rng.range(0.05, 0.4) : 0.0;
            const double t_max = (i % 7 == 0) ? rng.range(0.5, 1.8) : std::numeric_limits<double>::infinity();
            const bool cull = (i % 3 == 0);

            RaycastHit a{}, b{};
            const bool ha = raycast_scan(m, o, d, t_min, t_max, a, cull);
            const bool hb = idx.raycast(o, d, t_min, t_max, b, cull);
            const bool hany = idx.any_hit(o, d, t_min, t_max, cull);
            ++queries;
            hits += ha;
            if (ha != hb || ha != hany || (ha && !same_ray(a, b))) ++mismatches;
        }
    }
    CHECK(queries >= 3600 && hits > 400);
    CHECK_EQ(mismatches, std::size_t(0));
}
