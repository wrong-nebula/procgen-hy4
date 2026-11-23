// Boolean/CSG: exact volumes via inclusion-exclusion, watertightness, topology.
#include "testing.hpp"

#include "pg/ops/bevel.hpp"
#include "pg/ops/boolean.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>
#include <numbers>

using namespace pg;

namespace {
Mesh shifted_box(Vec3 size, Vec3 offset, std::uint32_t seg = 1) {
    Mesh m = make_box(size, seg);
    translate(m, offset);
    return m;
}
const Vec3 kShift{0.5, 0.31, 0.17};
const double kOverlap = 0.5 * 0.69 * 0.83;
}  // namespace

TEST(boolean_union_two_boxes) {
    const Mesh u = mesh_boolean(make_box({1, 1, 1}), shifted_box({1, 1, 1}, kShift), BooleanOp::Union);
    const QualityReport q = analyze(u);
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 2);
    CHECK_NEAR(q.volume, 2.0 - kOverlap, 1e-9);
}

TEST(boolean_intersection_two_boxes) {
    const QualityReport q =
        analyze(mesh_boolean(make_box({1, 1, 1}), shifted_box({1, 1, 1}, kShift), BooleanOp::Intersection));
    CHECK(q.watertight());
    CHECK_EQ(q.euler, 2);
    CHECK_NEAR(q.volume, kOverlap, 1e-9);
}

TEST(boolean_difference_two_boxes) {
    const QualityReport q =
        analyze(mesh_boolean(make_box({1, 1, 1}), shifted_box({1, 1, 1}, kShift), BooleanOp::Difference));
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 2);
    CHECK_NEAR(q.volume, 1.0 - kOverlap, 1e-9);
}

TEST(boolean_drilled_hole_has_genus_one) {
    const std::uint32_t n = 16;
    const double r = 0.2;
    const Mesh drilled = mesh_boolean(make_box({1, 1, 1}, 2), make_cylinder(n, r, 2.0), BooleanOp::Difference);
    const QualityReport q = analyze(drilled);
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 0);  // torus topology: V - E + F = 2 - 2g
    const double hole = 0.5 * n * r * r * std::sin(2 * std::numbers::pi / n);
    CHECK_NEAR(q.volume, 1.0 - hole, 1e-9);
}

TEST(boolean_coplanar_faces) {
    // Boxes sharing the z = +-0.5 planes and touching along x = 0.5.
    const Mesh a = make_box({1, 1, 1});
    const Mesh b = shifted_box({1, 0.6, 1}, {0.8, 0.1, 0.0});
    const QualityReport u = analyze(mesh_boolean(a, b, BooleanOp::Union));
    CHECK(u.watertight());
    CHECK_EQ(u.euler, 2);
    CHECK_NEAR(u.volume, 1.0 + 0.6 - 0.2 * 0.6, 1e-9);
    const QualityReport i = analyze(mesh_boolean(a, b, BooleanOp::Intersection));
    CHECK(i.watertight());
    CHECK_NEAR(i.volume, 0.2 * 0.6, 1e-9);
}

TEST(boolean_disjoint_and_identical) {
    const Mesh a = make_box({1, 1, 1});
    const QualityReport u = analyze(mesh_boolean(a, shifted_box({1, 1, 1}, {3, 0, 0}), BooleanOp::Union));
    CHECK(u.watertight());
    CHECK_EQ(u.euler, 4);
    CHECK_NEAR(u.volume, 2.0, 1e-9);
    const Mesh none = mesh_boolean(a, shifted_box({1, 1, 1}, {3, 0, 0}), BooleanOp::Intersection);
    CHECK_EQ(none.face_count(), std::size_t(0));
    const QualityReport same = analyze(mesh_boolean(a, a, BooleanOp::Union));
    CHECK(same.watertight());
    CHECK_NEAR(same.volume, 1.0, 1e-9);
}

TEST(boolean_notch) {
    // Cut a slot through one edge of the box.
    const Mesh notched = mesh_boolean(make_box({1, 1, 1}), shifted_box({0.4, 2, 0.4}, {0.5, 0, 0.5}), BooleanOp::Difference);
    const QualityReport q = analyze(notched);
    CHECK(q.watertight());
    CHECK_NEAR(q.volume, 1.0 - 0.2 * 0.2, 1e-9);
}

TEST(t_junction_repair_closes_cracks) {
    // A quad next to two half-quads: the long edge needs the midpoint inserted.
    Mesh m;
    for (Vec3 p : {Vec3{0, 0, 0}, Vec3{1, 0, 0}, Vec3{1, 1, 0}, Vec3{0, 1, 0}, Vec3{2, 0, 0}, Vec3{2, 1, 0}, Vec3{1, 0.5, 0},
                   Vec3{2, 0.5, 0}})
        m.add_vertex(p);
    m.add_face({0, 1, 2, 3});
    m.add_face({1, 4, 7, 6});
    m.add_face({6, 7, 5, 2});
    CHECK_EQ(analyze(m).boundary_edges, std::size_t(10));  // edge 1-2 doesn't match 1-6 + 6-2
    CHECK_EQ(repair_t_junctions(m, 1e-9), std::size_t(1));
    CHECK_EQ(analyze(m).boundary_edges, std::size_t(7));   // only the rectangle's rim
}

TEST(boolean_then_bevel_chain) {
    // Hard-surface chain: notch a box, then chamfer the result. The boolean
    // output has collinear stitch vertices that the bevel must handle.
    const Mesh notched = mesh_boolean(make_box({1, 1, 1}), shifted_box({0.4, 2, 0.4}, {0.5, 0, 0.5}), BooleanOp::Difference);
    const Mesh out = bevel(notched, {.width = 0.02});
    const QualityReport q = analyze(out);
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 2);
    CHECK(q.volume < 0.96 && q.volume > 0.94);
}

TEST(boolean_chains_on_boolean_output) {
    // Boolean output (collinear stitch vertices, restored uncut faces) fed back
    // in as input: regression for fan triangles with meaningless normals.
    const double hole = 0.5 * 16 * 0.04 * std::sin(2 * std::numbers::pi / 16);
    const Mesh drilled = mesh_boolean(make_box({1, 1, 1}, 2), make_cylinder(16, 0.2, 2.0), BooleanOp::Difference);
    const QualityReport far = analyze(mesh_boolean(drilled, shifted_box({0.1, 0.1, 0.1}, {5, 0, 0}), BooleanOp::Union));
    CHECK(far.watertight());
    CHECK_EQ(far.euler, 2);  // torus + separate sphere: 0 + 2
    CHECK_NEAR(far.volume, 1.0 - hole + 0.001, 1e-9);
    const QualityReport slot = analyze(mesh_boolean(drilled, shifted_box({0.9, 0.1, 2}, {0, -0.42, 0}), BooleanOp::Difference));
    CHECK(slot.watertight());
    CHECK_EQ(slot.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(slot.euler, -2);  // genus 2: drilled hole + through-slot
    CHECK_NEAR(slot.volume, 1.0 - hole - 0.9 * 0.1, 1e-9);
    for (bool merge : {false, true}) {  // both output modes stay exact
        const BooleanParams p{.merge_uncut_faces = merge};
        const Mesh h = mesh_boolean(make_box({1, 1, 1}, 2), make_cylinder(16, 0.2, 2.0), BooleanOp::Difference, p);
        const QualityReport q = analyze(mesh_boolean(h, shifted_box({0.2, 0.2, 2}, {0.4, 0.4, 0}), BooleanOp::Difference, p));
        CHECK(q.watertight());
        CHECK_NEAR(q.volume, 1.0 - hole - 0.04, 1e-9);
    }
}

TEST(boolean_merge_uncut_reduces_fragments) {
    const Mesh box = make_box({1, 1, 1}, 8), cyl = make_cylinder(48, 0.2, 2.0);
    const QualityReport frag = analyze(mesh_boolean(box, cyl, BooleanOp::Difference, {.merge_uncut_faces = false}));
    const QualityReport merged = analyze(mesh_boolean(box, cyl, BooleanOp::Difference));
    CHECK(merged.watertight());
    CHECK_NEAR(merged.volume, frag.volume, 1e-12);
    CHECK(merged.faces * 2 < frag.faces);
    CHECK(merged.edge_lengths.ratio() * 5 < frag.edge_lengths.ratio());
}

TEST(boolean_non_planar_inputs_terminate) {
    // Catmull-Clark faces are not planar: per-triangle planes keep corefinement exact.
    Mesh ball = catmull_clark(make_box({1, 1, 1}, 1), 3);
    for (Vec3& p : ball.positions) p = p * 2.4;
    const QualityReport q = analyze(mesh_boolean(ball, shifted_box({0.7, 0.7, 0.7}, {0.35, 0.3, 0.3}), BooleanOp::Union));
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 2);
}

TEST(boolean_local_corefinement_preserves_untouched_quads_and_rotated_inclusion_exclusion) {
    // 1. Local active-zone guarantee: a tilted cutter slicing only the +X/+Y/+Z corner
    // of an 8x8x8-segmented box must NEVER split quads on the -X/-Y/-Z faces, even with
    // merge_coplanar_fragments disabled (unlike global BSP planes that slice far faces).
    const Mesh stock = make_box({2, 2, 2}, 8);
    Mesh tilted = make_box({0.7, 0.8, 0.9}, 1);
    const double ca = std::cos(0.41), sa = std::sin(0.41);
    const double cb = std::cos(0.29), sb = std::sin(0.29);
    for (Vec3& p : tilted.positions) {
        const Vec3 r1{ca * p.x - sa * p.y, sa * p.x + ca * p.y, p.z};
        const Vec3 r2{cb * r1.x + sb * r1.z, r1.y, -sb * r1.x + cb * r1.z};
        p = r2 + Vec3{0.92, 0.88, 0.85};
    }

    const Mesh raw = mesh_boolean(stock, tilted, BooleanOp::Difference,
                                  {.merge_uncut_faces = true, .merge_coplanar_fragments = false});
    std::size_t untouched_neg_x_quads = 0;
    for (std::size_t f = 0; f < raw.face_count(); ++f) {
        const auto fv = raw.face(f);
        if (fv.size() == 4 && face_normal(raw, f).x < -0.999) ++untouched_neg_x_quads;
    }
    CHECK_EQ(untouched_neg_x_quads, std::size_t(64));  // all 8x8 quads on x = -1 untouched

    // 2. Rotated solids satisfy exact inclusion-exclusion across Union, Intersection, Difference.
    const QualityReport qa = analyze(stock);
    const QualityReport qb = analyze(tilted);
    const QualityReport qu = analyze(mesh_boolean(stock, tilted, BooleanOp::Union));
    const QualityReport qi = analyze(mesh_boolean(stock, tilted, BooleanOp::Intersection));
    const QualityReport qd = analyze(mesh_boolean(stock, tilted, BooleanOp::Difference));
    CHECK(qu.watertight());
    CHECK(qi.watertight());
    CHECK(qd.watertight());
    CHECK_EQ(qu.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(qi.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(qd.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_NEAR(qu.volume + qi.volume, qa.volume + qb.volume, 1e-9);
    CHECK_NEAR(qd.volume + qi.volume, qa.volume, 1e-9);

    // 3. Opposite-facing coplanar contact (glued bricks along x = 0.5).
    const Mesh left = make_box({1, 1, 1});
    const Mesh right = shifted_box({1, 1, 1}, {1.0, 0.0, 0.0});
    const QualityReport glued = analyze(mesh_boolean(left, right, BooleanOp::Union));
    CHECK(glued.watertight());
    CHECK_EQ(glued.euler, 2);
    CHECK_NEAR(glued.volume, 2.0, 1e-9);
    CHECK_EQ(mesh_boolean(left, right, BooleanOp::Intersection).face_count(), std::size_t(0));
    const QualityReport kept = analyze(mesh_boolean(left, right, BooleanOp::Difference));
    CHECK(kept.watertight());
    CHECK_NEAR(kept.volume, 1.0, 1e-9);
}
