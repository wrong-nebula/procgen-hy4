// Bevel: exact chamfer geometry on boxes, selective angles, mixed vertices.
#include "testing.hpp"

#include "pg/ops/bevel.hpp"
#include "pg/ops/extrude.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>

using namespace pg;

namespace {
// Unit cube chamfered by w: shrunk faces + 12 strips + 8 equilateral corners.
double chamfered_cube_area(double w) {
    return 6 * (1 - 2 * w) * (1 - 2 * w) + 12 * (1 - 2 * w) * w * std::sqrt(2.0) + 8 * (std::sqrt(3.0) / 2) * w * w;
}
}  // namespace

TEST(bevel_cube_exact) {
    const double w = 0.1;
    const Mesh out = bevel(make_box({1, 1, 1}, 1), {.width = w});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t(6 + 12 + 8));
    CHECK_EQ(q.vertices, std::size_t(24));
    CHECK_NEAR(q.area, chamfered_cube_area(w), 1e-12);
    CHECK(q.volume < 1.0 && q.volume > 1.0 - 12 * 0.5 * w * w);
}

TEST(bevel_segmented_box_matches_single_segment) {
    // Flat interior edges are below the angle threshold: only the 12 box edges bevel.
    const double w = 0.07;
    const Mesh one = bevel(make_box({1, 1, 1}, 1), {.width = w});
    const Mesh seg = bevel(make_box({1, 1, 1}, 4), {.width = w});
    const QualityReport q1 = analyze(one), q4 = analyze(seg);
    CHECK(q4.clean());
    CHECK_EQ(q4.euler, 2);
    CHECK_NEAR(q4.area, q1.area, 1e-12);
    CHECK_NEAR(q4.volume, q1.volume, 1e-12);
    CHECK_EQ(q4.faces, std::size_t(6 * 16 + 12 * 4 + 8));
}

TEST(bevel_threshold_skips_soft_edges) {
    // Catmull-Clark sphere: every dihedral angle is small, so nothing bevels.
    const Mesh smooth = catmull_clark(make_box({1, 1, 1}, 1), 3);
    const Mesh out = bevel(smooth, {.width = 0.01, .min_angle_deg = 30});
    CHECK_EQ(out.face_count(), smooth.face_count());
    CHECK(out.positions == smooth.positions);
}

TEST(bevel_extruded_hard_surface_is_watertight) {
    // Mixed vertices: the extrusion rim meets flat top faces.
    const Mesh box = make_box({1, 1, 1}, 3);
    std::vector<std::uint8_t> mask(box.face_count(), 0);
    for (std::size_t f = 0; f < box.face_count(); ++f) {
        const Vec3 c = face_centroid(box, f);
        mask[f] = (c.z > 0.49 && std::abs(c.x) < 0.2 && std::abs(c.y) < 0.2) ? 1 : 0;
    }
    const Mesh boss = extrude(box, mask, {.distance = 0.3});
    const Mesh out = bevel(boss, {.width = 0.03});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    const double base = analyze(boss).volume;
    CHECK(q.volume < base && q.volume > base * 0.95);
}

TEST(bevel_cylinder) {
    const Mesh cyl = make_cylinder(24, 0.5, 1.0);  // only the two rims are sharp
    const Mesh out = bevel(cyl, {.width = 0.05});
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t(24 + 2 + 48));
}

TEST(bevel_overwide_is_reported) {
    const QualityReport q = analyze(bevel(make_box({1, 1, 1}, 1), {.width = 0.7}));
    CHECK(q.watertight());
    CHECK(!q.clean());  // faces invert / cross: caught by the quality checks
}

TEST(bevel_rejects_open_mesh) {
    CHECK_THROWS(bevel(make_grid(2, 2, 1, 1), {}));
    CHECK_THROWS(bevel(make_box({1, 1, 1}, 1), {.width = 0.0}));
}

TEST(bevel_support_loops_all_quad_and_exact_volume) {
    // segments=2, profile=1.0 creates holding support loops on every face with zero shrinkage
    // and 100% quad topology (ideal cage for Catmull-Clark SDS).
    const Mesh box = make_box({1, 1, 1}, 1);
    const Mesh held = bevel(box, {.width = 0.08, .segments = 2, .profile = 1.0, .clamp_overlap = true, .crease_sharpness = 2.5f});
    const QualityReport q = analyze(held);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t(54));
    CHECK_EQ(q.quad_faces, std::size_t(54));
    CHECK_NEAR(q.quad_ratio(), 1.0, 1e-12);
    CHECK_NEAR(q.volume, 1.0, 1e-12);
    CHECK(q.creased_edges > 0);
    // Subdividing the held cage (with pure support loops, no extra crease) keeps a tight industrial radius.
    const Mesh held_pure = bevel(box, {.width = 0.08, .segments = 2, .profile = 1.0, .clamp_overlap = true});
    const QualityReport qs = analyze(catmull_clark(held_pure, {.levels = 2, .limit_snap = true}));
    CHECK(qs.clean());
    CHECK(qs.volume > 0.90 && qs.volume < 0.995);
    // And with ridge crease = 2.5 at levels = 3, it stays even sharper than pure support loops!
    const QualityReport qsc = analyze(catmull_clark(held, {.levels = 3, .limit_snap = true}));
    CHECK(qsc.clean());
    CHECK(qsc.volume > qs.volume && qsc.volume < 0.9999);
}

TEST(bevel_multisegment_fillet_and_clamp_overlap) {
    const Mesh box = make_box({1, 1, 1}, 1);
    for (std::uint32_t segs : {2u, 3u, 4u}) {
        const Mesh fil = bevel(box, {.width = 0.12, .segments = segs, .profile = 0.5, .clamp_overlap = true});
        const QualityReport q = analyze(fil);
        CHECK(q.clean());
        CHECK_EQ(q.euler, 2);
        CHECK(q.volume > 0.85 && q.volume < 0.99);
    }
    // Overwide bevel with clamp_overlap = true stays clean and non-self-intersecting.
    const QualityReport qc = analyze(bevel(box, {.width = 0.7, .segments = 2, .profile = 0.5, .clamp_overlap = true}));
    CHECK(qc.clean());
    CHECK_EQ(qc.euler, 2);

    // Multi-segment bevel on a cylinder rim stitches cleanly across 2-bevel rim vertices.
    const Mesh cyl = make_cylinder(16, 0.5, 1.0);
    const QualityReport qcyl = analyze(bevel(cyl, {.width = 0.06, .segments = 3, .profile = 0.5, .clamp_overlap = true}));
    CHECK(qcyl.clean());
    CHECK_EQ(qcyl.euler, 2);
}
