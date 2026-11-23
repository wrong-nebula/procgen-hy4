// Micro-benchmarks with attached quality checks. Each case times an operation
// on a representative input, then validates the output once (outside the timed
// region) so a speedup that breaks geometry is visible in the same record.
//
//   pg_bench [--filter substr] [--reps N] [--json]
#include "pg/cleanup.hpp"
#include "pg/closest.hpp"
#include "pg/dp_nns.hpp"
#include "pg/ops/bevel.hpp"
#include "pg/ops/boolean.hpp"
#include "pg/ops/deform.hpp"
#include "pg/ops/scatter.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/ops/extrude.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

using namespace pg;
using Clock = std::chrono::steady_clock;

namespace {

struct Checks {
    bool watertight = false;
    bool expect_watertight = true;
    std::size_t self_intersections = 0;
    double volume = 0.0, expected_volume = NAN, volume_tol = 1e-9;
    std::size_t faces = 0;
    double edge_ratio = 0.0;
    std::size_t wrong_answers = 0;  // queries whose result differs from the reference
    [[nodiscard]] double volume_rel_error() const {
        return std::isnan(expected_volume) ? 0.0 : std::abs(volume - expected_volume) / std::abs(expected_volume);
    }
    [[nodiscard]] bool pass() const {
        return (!expect_watertight || watertight) && self_intersections == 0 && volume_rel_error() < volume_tol && wrong_answers == 0;
    }
};

struct Case {
    const char* name;
    std::function<Mesh()> run;  // timed
    std::function<void(const Mesh&, Checks&)> check;
    bool check_self_intersections = true;
};

Checks quality_of(const Mesh& m, bool si) {
    QualityOptions o;
    o.check_self_intersections = si;
    const QualityReport q = analyze(m, o);
    Checks c;
    c.watertight = q.watertight();
    c.self_intersections = q.self_intersections.intersecting_pairs;
    c.volume = q.volume;
    c.faces = q.faces;
    c.edge_ratio = q.edge_lengths.ratio();
    return c;
}

volatile std::size_t g_sink = 0;  // keeps results observable

}  // namespace

int main(int argc, char** argv) {
    const char* filter = nullptr;
    int reps = 15;
    bool json = false;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--filter") && i + 1 < argc) filter = argv[++i];
        else if (!std::strcmp(argv[i], "--reps") && i + 1 < argc) reps = std::max(1, std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "--json")) json = true;
    }

    // Inputs are built once, outside timing.
    const Mesh box128 = make_box({1, 1, 1}, 128);                 // 98k quads
    const auto top128 = select_by_normal(box128, {0, 0, 1}, 0.99); // 16k-face region
    const Mesh grid256 = make_grid(256, 256, 1, 1);                // 65k quads
    const auto all_grid = select_all(grid256);
    const auto all_box = select_all(box128);
    const Mesh box48 = make_box({1, 1, 1}, 48);
    // Polygon soup: every face owns its corners (393k vertices -> 98k after weld).
    Mesh soup128;
    soup128.reserve(box128.corner_count(), box128.face_count(), box128.corner_count());
    for (std::size_t f = 0; f < box128.face_count(); ++f) {
        std::vector<Index> poly;
        for (Index v : box128.face(f)) poly.push_back(soup128.add_vertex(box128.positions[v]));
        soup128.add_face(poly);
    }

    const Mesh cube = make_box({1, 1, 1}, 1);
    const Mesh box8 = make_box({1, 1, 1}, 8);
    const Mesh drill48 = make_cylinder(48, 0.2, 2.0);
    const double hole48 = 0.5 * 48 * 0.04 * std::sin(2 * 3.141592653589793 / 48);
    const Mesh box13 = make_box({1, 1, 1}, 13);
    const Mesh drill37 = make_cylinder(37, 0.27, 2.0);
    const double hole37 = 0.5 * 37 * (0.27 * 0.27) * std::sin(2 * 3.141592653589793 / 37);
    const Mesh box32 = make_box({1, 1, 1}, 32);
    const Mesh sphere = catmull_clark(cube, 4);
    // Closest-point search (pg_bench_closest compares it with the scan and other structures):
    // 49k triangles, 10,000 points about 0.15 m off them.
    const Mesh sphere6 = catmull_clark(cube, 6);
    const ClosestIndex sphere6_index(sphere6);
    const DpNnsIndex sphere6_dpnns(sphere6.positions);
    std::vector<Vec3> around6;
    for (std::size_t i = 0; around6.size() < 10000; i += 7) around6.push_back(sphere6.positions[i % sphere6.positions.size()] * 1.35);
    const auto dpnns_matches_oracle = [&](const Mesh&, Checks& c) {
        c.expect_watertight = false;
        for (std::size_t i = 0; i < around6.size(); i += 250) {
            const DpNnsHit got = sphere6_dpnns.query(around6[i]);
            double best = 1e300;
            for (const Vec3& v : sphere6.positions) {
                const Vec3 dv = v - around6[i];
                const double d2 = dot(dv, dv);
                if (d2 < best) best = d2;
            }
            c.wrong_answers += !(std::abs(got.distance_sq - best) <= 1e-11);
        }
    };
    const auto index_matches_scan = [&](const Mesh&, Checks& c) {
        c.expect_watertight = false;
        for (std::size_t i = 0; i < around6.size(); i += 250) {
            ClosestHit a, b;
            const bool ha = closest_point_scan(sphere6, around6[i], 0.25, a), hb = sphere6_index.closest(around6[i], 0.25, b);
            c.wrong_answers += ha != hb || (ha && !(a.point == b.point && a.normal == b.normal && a.distance == b.distance && a.triangle == b.triangle));
        }
    };

    std::vector<Case> cases = {
        {"subdivide_cube_L6", [&] { return catmull_clark(cube, 6); },
         [](const Mesh&, Checks& c) { c.expected_volume = 0.327638095; c.volume_tol = 1e-8; }, false},
        {"boolean_box8_minus_cyl48", [&] { return mesh_boolean(box8, drill48, BooleanOp::Difference); },
         [&](const Mesh&, Checks& c) { c.expected_volume = 1.0 - hole48; }, true},
        {"boolean_box13_minus_cyl37", [&] { return mesh_boolean(box13, drill37, BooleanOp::Difference); },
         [&](const Mesh&, Checks& c) { c.expected_volume = 1.0 - hole37; }, true},
        {"bevel_box32", [&] { return bevel(box32, {.width = 0.02}); }, [](const Mesh&, Checks&) {}, true},
        {"scatter_poisson_10k", [&] {
             const auto pts = scatter_points(sphere, {.count = 10000, .min_distance = 0.004, .seed = 1});
             g_sink = g_sink + pts.size();
             return Mesh{};
         }, [](const Mesh&, Checks& c) { c.expect_watertight = false; }, false},
        {"noise_box128", [&] { Mesh m = box128; displace_noise(m, {.amplitude = 0.003}); return m; },
         [](const Mesh&, Checks&) {}, false},
        {"extrude_region_box128_top",
         [&] { return extrude(box128, top128, {.distance = 0.25}); },
         [](const Mesh&, Checks& c) { c.expected_volume = 1.25; }, true},
        {"extrude_region_box128_all",
         [&] { return extrude(box128, all_box, {.distance = 0.05}); },
         [](const Mesh&, Checks& c) { c.expected_volume = 1.1 * 1.1 * 1.1; }, false},
        {"extrude_individual_grid256",
         [&] { return extrude(grid256, all_grid, {.distance = 0.01, .mode = ExtrudeMode::Individual}); },
         [](const Mesh&, Checks& c) { c.expect_watertight = false; }, false},
        {"make_box_seg128", [] { return make_box({1, 1, 1}, 128); },
         [](const Mesh&, Checks& c) { c.expected_volume = 1.0; }, false},
        {"weld_soup_box128", [&] { Mesh m = soup128; weld_vertices(m, 1e-9); return m; },
         [](const Mesh&, Checks& c) { c.expected_volume = 1.0; }, false},
        {"analyze_box_seg128_noSI",
         [&] { const auto q = analyze(box128, {.check_self_intersections = false}); g_sink = g_sink + q.edges; return Mesh{}; },
         [](const Mesh&, Checks& c) { c.expect_watertight = false; }, false},
        {"closest_index_build_sphere_L6",
         [&] {
             const ClosestIndex built(sphere6);
             g_sink = g_sink + built.triangle_count();
             return Mesh{};
         },
         index_matches_scan, false},
        {"closest_index_10k_queries_L6",
         [&] {
             std::size_t hits = 0;
             for (const Vec3& p : around6) {
                 ClosestHit h;
                 hits += sphere6_index.closest(p, 0.25, h);
             }
             g_sink = g_sink + hits;
             return Mesh{};
         },
         index_matches_scan, false},
        {"self_intersect_box48",
         [&] {
             const auto r = find_self_intersections(box48);
             g_sink = g_sink + r.intersecting_pairs;
             return Mesh{};
         },
         [](const Mesh&, Checks& c) { c.expect_watertight = false; }, false},
        {"dp_nns_build_sphere_L6",
         [&] {
             const DpNnsIndex built(sphere6.positions);
             g_sink = g_sink + built.stats().directed_edges;
             return Mesh{};
         },
         dpnns_matches_oracle, false},
        {"dp_nns_10k_queries_L6",
         [&] {
             const auto hits = sphere6_dpnns.query_batch(around6, true);
             g_sink = g_sink + hits.size();
             return Mesh{};
         },
         dpnns_matches_oracle, false},
    };

    int failed = 0;
    if (json) std::printf("[\n");
    bool first = true;
    for (const Case& c : cases) {
        if (filter && !std::strstr(c.name, filter)) continue;
        Mesh out = c.run();  // warm-up, also the checked output
        std::vector<double> ns;
        ns.reserve(static_cast<std::size_t>(reps));
        for (int r = 0; r < reps; ++r) {
            const auto t0 = Clock::now();
            Mesh m = c.run();
            const auto t1 = Clock::now();
            g_sink = g_sink + m.face_count();
            ns.push_back(static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count()));
        }
        std::sort(ns.begin(), ns.end());
        const double median = ns[ns.size() / 2];
        const double best = ns.front();

        Checks q = out.face_count() ? quality_of(out, c.check_self_intersections) : Checks{};
        c.check(out, q);
        if (out.face_count() == 0) q.watertight = true;
        if (!q.pass()) ++failed;

        if (json) {
            std::printf("%s  {\"name\":\"%s\",\"median_ns\":%.0f,\"min_ns\":%.0f,\"reps\":%d,\"faces\":%zu,"
                        "\"watertight\":%s,\"self_intersections\":%zu,\"volume_rel_error\":%.3e,"
                        "\"edge_ratio\":%.4f,\"pass\":%s}",
                        first ? "" : ",\n", c.name, median, best, reps, q.faces, q.watertight ? "true" : "false",
                        q.self_intersections, q.volume_rel_error(), q.edge_ratio, q.pass() ? "true" : "false");
        } else {
            std::printf("%-32s median %9.3f ms  min %9.3f ms  faces %8zu  %s\n", c.name, median * 1e-6, best * 1e-6,
                        q.faces, q.pass() ? "PASS" : "FAIL");
        }
        first = false;
    }
    if (json) std::printf("\n]\n");
    return failed == 0 ? 0 : 1;
}
