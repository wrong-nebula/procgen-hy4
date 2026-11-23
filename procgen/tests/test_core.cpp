// Primitives, quality metrics, cleanup and the self-intersection detector.
#include "testing.hpp"

#include "pg/cleanup.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>
#include <numbers>

using namespace pg;

TEST(box_is_closed_genus0) {
    for (std::uint32_t seg : {1u, 2u, 7u}) {
        const Mesh m = make_box({2.0, 3.0, 0.5}, seg);
        const QualityReport q = analyze(m);
        CHECK(q.watertight());
        CHECK(q.clean());
        CHECK_EQ(q.euler, 2);
        CHECK_EQ(q.faces, std::size_t(6) * seg * seg);
        CHECK_EQ(q.vertices, std::size_t(6) * seg * seg + 2);  // V = F + 2 for an all-quad sphere
        CHECK_NEAR(q.volume, 3.0, 1e-12);
        CHECK_NEAR(q.area, 2.0 * (6.0 + 1.0 + 1.5), 1e-12);
    }
}

TEST(cylinder_volume_matches_prism) {
    const std::uint32_t n = 24;
    const Mesh m = make_cylinder(n, 0.75, 2.0);
    const QualityReport q = analyze(m);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    const double polygon_area = 0.5 * n * 0.75 * 0.75 * std::sin(2.0 * std::numbers::pi / n);
    CHECK_NEAR(q.volume, polygon_area * 2.0, 1e-12);
}

TEST(grid_is_open_disk) {
    const Mesh m = make_grid(4, 3, 4.0, 3.0);
    const QualityReport q = analyze(m);
    CHECK(!q.watertight());
    CHECK_EQ(q.boundary_edges, std::size_t(2 * (4 + 3)));
    CHECK_EQ(q.nonmanifold_edges, std::size_t(0));
    CHECK_EQ(q.inconsistent_edges, std::size_t(0));
    CHECK_EQ(q.euler, 1);
    CHECK_NEAR(q.edge_lengths.min, 1.0, 1e-12);
    CHECK_NEAR(q.edge_lengths.max, 1.0, 1e-12);
    CHECK_NEAR(q.edge_lengths.stddev, 0.0, 1e-12);
}

TEST(volume_is_translation_stable) {
    Mesh m = make_box({1, 1, 1}, 3);
    translate(m, {1e6, -2e6, 3e6});
    CHECK_NEAR(analyze(m).volume, 1.0, 1e-6);
}

TEST(flipped_face_is_inconsistent) {
    Mesh m = make_box({1, 1, 1}, 1);
    // Reverse face 0 in place.
    std::reverse(m.face_indices.begin() + m.face_offsets[0], m.face_indices.begin() + m.face_offsets[1]);
    const QualityReport q = analyze(m);
    CHECK_EQ(q.inconsistent_edges, std::size_t(4));
    CHECK(!q.watertight());
}

TEST(invalid_indices_detected) {
    Mesh m = make_grid(1, 1, 1, 1);
    m.face_indices[2] = 99;
    const QualityReport q = analyze(m);
    CHECK(!q.indices_valid);
    CHECK(!q.watertight());
}

TEST(overlapping_boxes_self_intersect) {
    Mesh a = make_box({1, 1, 1}, 2);
    Mesh b = make_box({1, 1, 1}, 2);
    translate(b, {0.5, 0.31, 0.17});
    const Mesh m = merge(a, b);
    const QualityReport q = analyze(m);
    CHECK(q.watertight());  // each shell is closed...
    CHECK(q.self_intersections.intersecting_pairs > 0);  // ...but they cross
    CHECK(!q.clean());
}

TEST(disjoint_boxes_do_not_self_intersect) {
    Mesh a = make_box({1, 1, 1}, 4);
    Mesh b = make_box({1, 1, 1}, 4);
    translate(b, {1.5, 0, 0});
    CHECK_EQ(analyze(merge(a, b)).self_intersections.intersecting_pairs, std::size_t(0));
}

TEST(touching_boxes_are_not_crossing) {
    // Faces in contact but not crossing: coplanar contact is not a proper crossing.
    Mesh a = make_box({1, 1, 1}, 1);
    Mesh b = make_box({1, 1, 1}, 1);
    translate(b, {1.0, 0, 0});
    CHECK_EQ(analyze(merge(a, b)).self_intersections.intersecting_pairs, std::size_t(0));
}

TEST(weld_merges_duplicates_and_drops_collapsed) {
    Mesh m;
    m.add_vertex({0, 0, 0});
    m.add_vertex({1, 0, 0});
    m.add_vertex({1, 1, 0});
    m.add_vertex({1e-12, 0, 0});  // duplicate of 0
    m.add_vertex({5, 5, 5});      // unreferenced after weld
    m.add_face({0, 1, 2});
    m.add_face({3, 1, 2});        // becomes identical to face 0 (still valid)
    m.add_face({0, 3, 1});        // collapses to a 2-gon -> removed
    const CleanupStats s = weld_vertices(m, 1e-9);
    CHECK_EQ(s.merged_vertices, std::size_t(1));
    CHECK_EQ(s.removed_faces, std::size_t(1));
    CHECK_EQ(s.removed_vertices, std::size_t(2));  // merged duplicate + the stray vertex
    CHECK_EQ(m.vertex_count(), std::size_t(3));
    CHECK_EQ(m.face_count(), std::size_t(2));
    CHECK(indices_valid(m));
}

TEST(primitives_reject_bad_params) {
    CHECK_THROWS(make_box({1, 1, 1}, 0));
    CHECK_THROWS(make_box({1, -1, 1}, 1));
    CHECK_THROWS(make_grid(0, 1, 1, 1));
    CHECK_THROWS(make_cylinder(2, 1, 1));
}

namespace {
// Reference weld: O(n^2), same contract (lowest-index representative in range).
Mesh weld_reference(Mesh m, double tol) {
    const std::size_t n = m.vertex_count();
    std::vector<Index> rep(n);
    for (std::size_t v = 0; v < n; ++v) {
        rep[v] = static_cast<Index>(v);
        for (std::size_t u = 0; u < v; ++u) {
            if (rep[u] != u) continue;
            const Vec3 d = m.positions[u] - m.positions[v];
            if (dot(d, d) <= tol * tol) { rep[v] = static_cast<Index>(u); break; }
        }
    }
    for (Index& v : m.face_indices) v = rep[v];
    remove_collapsed_faces(m);
    compact_vertices(m);
    return m;
}
}  // namespace

TEST(weld_matches_bruteforce_reference) {
    // Clusters straddling cell boundaries, with offsets near the tolerance.
    std::uint64_t state = 0x1234567;
    auto rnd = [&] { state = state * 6364136223846793005ull + 1442695040888963407ull; return double(state >> 11) * 0x1.0p-53; };
    for (double tol : {1e-3, 0.37}) {
        Mesh m;
        for (int c = 0; c < 300; ++c) {
            // Centres snapped to cell boundaries half the time.
            Vec3 centre{std::floor(rnd() * 40) * 2 * tol, (rnd() - 0.5) * 30 * tol, rnd() * 20 * tol};
            if (c % 2) centre.x += 1e-15;
            for (int k = 0; k < 4; ++k) {
                const double r = tol * (k == 3 ? 0.999999 : 0.6 * rnd());
                const Vec3 dir = normalize_or({rnd() - 0.5, rnd() - 0.5, rnd() - 0.5}, {1, 0, 0});
                m.add_vertex(centre + dir * r);
            }
        }
        const auto nv = static_cast<Index>(m.vertex_count());
        for (Index i = 0; i + 2 < nv; i += 3) m.add_face({i, (i + 7) % nv, (i + 13) % nv});
        Mesh fast = m;
        weld_vertices(fast, tol);
        const Mesh ref = weld_reference(m, tol);
        CHECK_EQ(fast.vertex_count(), ref.vertex_count());
        CHECK(fast.positions == ref.positions);
        CHECK(fast.face_indices == ref.face_indices);
        CHECK(fast.face_offsets == ref.face_offsets);
    }
}
