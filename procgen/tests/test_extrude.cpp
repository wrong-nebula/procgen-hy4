// Extrude: topology preservation, exact volume change, even thickness.
#include "testing.hpp"

#include "pg/ops/extrude.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

using namespace pg;

TEST(extrude_top_face_of_box) {
    const Mesh box = make_box({1, 1, 1}, 1);
    const Mesh out = extrude(box, select_by_normal(box, {0, 0, 1}, 0.99), {.distance = 0.5});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t(10));
    CHECK_EQ(q.vertices, std::size_t(12));
    CHECK_NEAR(q.volume, 1.5, 1e-12);
}

TEST(extrude_region_on_segmented_box) {
    // A 4x4 patch of top faces moves as one region: walls only on its rim.
    const std::uint32_t n = 4;
    const Mesh box = make_box({1, 1, 1}, n);
    const Mesh out = extrude(box, select_by_normal(box, {0, 0, 1}, 0.99), {.distance = 0.25});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t(6 * n * n + 4 * n));
    CHECK_NEAR(q.volume, 1.25, 1e-12);
}

TEST(extrude_all_faces_even_thickness_is_exact_offset) {
    // Shell offset of a cube by d gives a cube of side 1 + 2d (corners scaled by sqrt 3).
    const Mesh box = make_box({1, 1, 1}, 3);
    const double d = 0.1;
    const Mesh out = extrude(box, select_all(box), {.distance = d});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.faces, box.face_count());  // closed region: no walls, pure inflation
    CHECK_NEAR(q.volume, (1 + 2 * d) * (1 + 2 * d) * (1 + 2 * d), 1e-12);
}

TEST(extrude_without_even_thickness_is_not_exact) {
    const Mesh box = make_box({1, 1, 1}, 1);
    const Mesh out = extrude(box, select_all(box), {.distance = 0.1, .even_thickness = false});
    const double v = analyze(out).volume;
    CHECK(v < 1.2 * 1.2 * 1.2 - 1e-3);  // corners move only d along the diagonal
}

TEST(extrude_inward_keeps_volume_identity) {
    const Mesh box = make_box({1, 1, 1}, 2);
    const Mesh out = extrude(box, select_by_normal(box, {0, 0, 1}, 0.99), {.distance = -0.3});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 0.7, 1e-12);
}

TEST(extrude_through_far_side_self_intersects) {
    // Push the centre top face down through the bottom. The bottom is sheared so
    // the walls cross its triangles properly instead of along shared grid lines
    // (edge-on contact is deliberately not reported, see quality.hpp).
    Mesh box = make_box({1, 1, 1}, 3);
    for (Vec3& p : box.positions)
        if (p.z < -0.4) p += Vec3{0.03, 0.05, 0.0};
    std::vector<std::uint8_t> mask(box.face_count(), 0);
    for (std::size_t f = 0; f < box.face_count(); ++f) {
        const Vec3 c = face_centroid(box, f);
        mask[f] = (c.z > 0.49 && std::abs(c.x) < 0.1 && std::abs(c.y) < 0.1) ? 1 : 0;
    }
    const Mesh out = extrude(box, mask, {.distance = -1.6});
    const QualityReport q = analyze(out);
    CHECK(q.watertight());  // topology is still closed...
    CHECK(q.self_intersections.intersecting_pairs > 0);  // ...but geometry folds through
}

TEST(extrude_individual_on_grid) {
    const Mesh grid = make_grid(3, 3, 3, 3);
    const Mesh out = extrude(grid, select_all(grid), {.distance = 1.0, .mode = ExtrudeMode::Individual});
    const QualityReport q = analyze(out);
    CHECK_EQ(q.faces, std::size_t(9 * 5));
    CHECK_EQ(q.nonmanifold_edges, std::size_t(0));
    CHECK_EQ(q.inconsistent_edges, std::size_t(0));
    // Interior edges pair up the back-to-back walls of neighbouring cells; only
    // the grid's outer rim stays open.
    CHECK_EQ(q.boundary_edges, std::size_t(12));
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
}

TEST(extrude_individual_on_closed_box_is_watertight) {
    const Mesh box = make_box({1, 1, 1}, 1);
    const auto mask = select_by_normal(box, {1, 0, 0}, 0.99);
    const Mesh out = extrude(box, mask, {.distance = 0.2, .mode = ExtrudeMode::Individual});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 1.2, 1e-12);
}

TEST(extrude_region_on_open_grid_keeps_manifold) {
    const Mesh grid = make_grid(4, 4, 1, 1);
    std::vector<std::uint8_t> mask(grid.face_count(), 0);
    mask[5] = mask[6] = mask[9] = mask[10] = 1;  // central 2x2 block
    const Mesh out = extrude(grid, mask, {.distance = 0.3});
    const QualityReport q = analyze(out);
    CHECK_EQ(q.nonmanifold_edges, std::size_t(0));
    CHECK_EQ(q.inconsistent_edges, std::size_t(0));
    CHECK_EQ(q.boundary_edges, std::size_t(16));  // the grid's outer rim only
    CHECK_EQ(q.faces, std::size_t(16 + 8));
    CHECK_EQ(q.euler, 1);
}

TEST(extrude_zero_distance_flags_degenerate_walls) {
    const Mesh box = make_box({1, 1, 1}, 1);
    const Mesh out = extrude(box, select_by_normal(box, {0, 0, 1}, 0.99), {.distance = 0.0});
    const QualityReport q = analyze(out);
    CHECK(q.watertight());
    CHECK_EQ(q.degenerate_faces, std::size_t(4));
}

TEST(extrude_empty_selection_is_identity) {
    const Mesh box = make_box({1, 1, 1}, 2);
    const Mesh out = extrude(box, std::vector<std::uint8_t>(box.face_count(), 0), {});
    CHECK_EQ(out.face_count(), box.face_count());
    CHECK_EQ(out.vertex_count(), box.vertex_count());
}

TEST(extrude_rejects_mask_size_mismatch) {
    const Mesh box = make_box({1, 1, 1}, 1);
    CHECK_THROWS(extrude(box, std::vector<std::uint8_t>(3, 1), {}));
}

TEST(extrude_cylinder_cap_is_watertight) {
    const Mesh cyl = make_cylinder(32, 0.5, 1.0);
    const Mesh out = extrude(cyl, select_by_normal(cyl, {0, 0, 1}, 0.99), {.distance = 0.4});
    const QualityReport base = analyze(cyl);
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, base.volume * 1.4, 1e-12);
}
