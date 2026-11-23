// Catmull–Clark: topology preservation, convergence, boundary rules.
#include "testing.hpp"

#include "pg/ops/extrude.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>
#include <numbers>
#include <vector>

using namespace pg;

TEST(subdivide_cube_once) {
    const Mesh out = catmull_clark(make_box({1, 1, 1}, 1), 1);
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.faces, std::size_t(24));
    CHECK_EQ(q.vertices, std::size_t(26));
    CHECK_EQ(q.euler, 2);
    for (std::size_t f = 0; f < out.face_count(); ++f) CHECK_EQ(out.face(f).size(), std::size_t(4));
    // Original corner moves to (F + 2R + (n-3)P)/n with n = 3: |coord| = 5/18.
    CHECK_NEAR(std::abs(out.positions[0].x), 5.0 / 18.0, 1e-15);
}

TEST(subdivide_cube_volume_converges_quadratically) {
    // Volumes: 1, 5/12, 0.3501, 0.3331, 0.3289, ... -> ~0.3276. Successive
    // differences shrink ~4x per level (O(h^2) convergence to the limit surface).
    const Mesh cube = make_box({1, 1, 1}, 1);
    std::vector<double> vol;
    for (std::uint32_t l = 0; l <= 5; ++l) {
        const QualityReport q = analyze(catmull_clark(cube, l), {.check_self_intersections = l <= 3});
        CHECK(q.watertight());
        CHECK_EQ(q.euler, 2);
        if (l <= 3) CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
        vol.push_back(q.volume);
    }
    CHECK_NEAR(vol[1], 5.0 / 12.0, 1e-12);
    for (std::size_t l = 1; l < vol.size(); ++l) CHECK(vol[l] < vol[l - 1]);
    for (std::size_t l = 3; l < vol.size(); ++l) {
        const double ratio = (vol[l - 2] - vol[l - 1]) / (vol[l - 1] - vol[l]);
        CHECK(ratio > 3.5 && ratio < 4.5);
    }
    CHECK(vol.back() > 0.327 && vol.back() < 0.3285);
}

TEST(subdivide_mixed_polygons) {
    // Cylinder has two n-gon caps: each k-gon becomes k quads.
    const Mesh cyl = make_cylinder(9, 1.0, 1.0);
    const Mesh out = catmull_clark(cyl, 2);
    const QualityReport q = analyze(out);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_EQ(q.faces, std::size_t((9 * 4 + 9 * 2) * 4));
}

TEST(subdivide_open_grid_keeps_boundary_and_plane) {
    const Mesh grid = make_grid(3, 2, 3.0, 2.0);
    const Mesh out = catmull_clark(grid, 2);
    const QualityReport q = analyze(out);
    CHECK_EQ(q.boundary_edges, std::size_t(2 * (3 + 2) * 4));
    CHECK_EQ(q.nonmanifold_edges, std::size_t(0));
    CHECK_EQ(q.inconsistent_edges, std::size_t(0));
    CHECK_EQ(q.euler, 1);
    double max_z = 0.0;
    for (const Vec3& p : out.positions) max_z = std::max(max_z, std::abs(p.z));
    CHECK_NEAR(max_z, 0.0, 1e-15);
    // Corners of a lone face are pinned (edge-and-corner boundary rule).
    CHECK(out.positions[0] == grid.positions[0]);
    // Uniform grids stay uniform (B-splines reproduce linear functions).
    CHECK_NEAR(q.edge_lengths.stddev, 0.0, 1e-12);
}

TEST(subdivide_hard_surface_after_extrude) {
    const Mesh box = make_box({1, 1, 1}, 2);
    const Mesh ext = extrude(box, select_by_normal(box, {0, 0, 1}, 0.99), {.distance = 0.5});
    const QualityReport q = analyze(catmull_clark(ext, 2));
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
}

TEST(subdivide_zero_levels_is_identity) {
    const Mesh box = make_box({1, 1, 1}, 2);
    const Mesh out = catmull_clark(box, 0);
    CHECK(out.positions == box.positions);
    CHECK(out.face_indices == box.face_indices);
}

TEST(subdivide_semi_sharp_creases_control_hardness) {
    // Tag all 12 edges of a unit cube with varying sharpness and verify monotone volume
    // convergence from smooth (~0.329) through semi-sharp fillets to exact cube (1.0).
    auto make_creased_cube = [](float s) {
        Mesh c = make_box({1, 1, 1}, 1);
        for (std::size_t f = 0; f < c.face_count(); ++f) {
            const auto fv = c.face(f);
            for (std::size_t i = 0; i < fv.size(); ++i) c.set_edge_crease(fv[i], fv[(i + 1) % fv.size()], s);
        }
        return c;
    };
    const double v0 = analyze(catmull_clark(make_creased_cube(0.0f), 4)).volume;
    const double v1 = analyze(catmull_clark(make_creased_cube(1.0f), 4)).volume;
    const double v2 = analyze(catmull_clark(make_creased_cube(2.0f), 4)).volume;
    const double vinf = analyze(catmull_clark(make_creased_cube(10.0f), 4)).volume;
    CHECK(v0 < 0.35);
    CHECK(v1 > v0 + 0.35);
    CHECK(v2 > v1 + 0.05 && v2 < 0.995);
    CHECK_NEAR(vinf, 1.0, 1e-12);
}

TEST(subdivide_chaikin_blends_varying_creases) {
    // Box with sharp top rim (4.0) and softer vertical edges (1.0): Chaikin transitions smoothly
    // and stays clean/watertight with distinct geometry from Uniform.
    Mesh c = make_box({1, 1, 1}, 1);
    for (std::size_t f = 0; f < c.face_count(); ++f) {
        const auto fv = c.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const Index a = fv[i], b = fv[(i + 1) % fv.size()];
            const bool top = c.positions[a].z > 0.0 && c.positions[b].z > 0.0;
            const bool vert = std::abs(c.positions[a].z - c.positions[b].z) > 0.5;
            if (top) c.set_edge_crease(a, b, 4.0f);
            else if (vert) c.set_edge_crease(a, b, 1.0f);
        }
    }
    const Mesh uni = catmull_clark(c, {.levels = 3, .crease_method = CreaseMethod::Uniform});
    const Mesh chk = catmull_clark(c, {.levels = 3, .crease_method = CreaseMethod::Chaikin});
    CHECK(analyze(uni).clean());
    CHECK(analyze(chk).clean());
    CHECK(std::abs(analyze(uni).volume - analyze(chk).volume) > 1e-4);
}

TEST(subdivide_linear_pre_subdiv_and_boundary_rules) {
    const Mesh cube = make_box({1, 1, 1}, 1);
    const double v_plain = analyze(catmull_clark(cube, {.levels = 2})).volume;
    const double v_pre = analyze(catmull_clark(cube, {.levels = 2, .linear_pre_subdiv = 1})).volume;
    CHECK(v_pre > v_plain + 0.25);

    const Mesh grid = make_grid(2, 2, 2.0, 2.0);
    const Mesh pinned = catmull_clark(grid, {.levels = 2, .boundary = BoundaryRule::EdgeAndCorner});
    const Mesh rounded = catmull_clark(grid, {.levels = 2, .boundary = BoundaryRule::EdgeOnly});
    CHECK(pinned.positions[0] == grid.positions[0]);
    CHECK(length(rounded.positions[0] - grid.positions[0]) > 0.05);
}

TEST(subdivide_limit_snap_normals_and_uvs) {
    // 1. Limit snapping moves level-2 cube onto the true limit surface (volume closer to ~0.3276).
    const Mesh cube = make_box({1, 1, 1}, 1);
    const double v_cage = analyze(catmull_clark(cube, {.levels = 2, .limit_snap = false})).volume;
    const SubdivideResult sr = catmull_clark_full(cube, {}, {.levels = 2, .limit_snap = true, .compute_limit_normals = true});
    const QualityReport q = analyze(sr.mesh);
    CHECK(q.clean());
    CHECK(q.volume < v_cage && std::abs(q.volume - 0.3276) < std::abs(v_cage - 0.3276));
    CHECK_EQ(sr.corner_normals.size(), sr.mesh.corner_count());
    for (std::size_t f = 0; f < sr.mesh.face_count(); ++f) {
        const auto fv = sr.mesh.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const Vec3 n = sr.corner_normals[sr.mesh.face_offsets[f] + i];
            CHECK_NEAR(length(n), 1.0, 1e-6);
            CHECK(dot(n, normalize_or(sr.mesh.positions[fv[i]], {0, 0, 1})) > 0.95);
        }
    }

    // 2. Infinitely sharp cube keeps split perpendicular limit normals on each face.
    Mesh sharp = cube;
    for (std::size_t f = 0; f < sharp.face_count(); ++f) {
        const auto fv = sharp.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) sharp.set_edge_crease(fv[i], fv[(i + 1) % fv.size()], 10.0f);
    }
    const SubdivideResult ssr = catmull_clark_full(sharp, {}, {.levels = 2, .limit_snap = true, .compute_limit_normals = true});
    CHECK_NEAR(analyze(ssr.mesh).volume, 1.0, 1e-12);
    for (std::size_t f = 0; f < ssr.mesh.face_count(); ++f) {
        const Vec3 fn = face_normal(ssr.mesh, f);
        for (Index c = ssr.mesh.face_offsets[f]; c < ssr.mesh.face_offsets[f + 1]; ++c) {
            CHECK(dot(ssr.corner_normals[c], fn) > 0.999);
        }
    }

    // 3. Face-varying UV subdivision preserves UV bounds and continuity on an open grid.
    const Mesh grid = make_grid(2, 2, 2.0, 2.0);
    std::vector<std::array<float, 2>> uvs(grid.corner_count());
    for (std::size_t c = 0; c < grid.corner_count(); ++c) {
        const Vec3 p = grid.positions[grid.face_indices[c]];
        uvs[c] = {static_cast<float>(p.x + 1.0), static_cast<float>(p.y + 1.0)};
    }
    const SubdivideResult uvr = catmull_clark_full(grid, uvs, {.levels = 2, .uv_rule = UvSubdivRule::CornersPlus1});
    CHECK_EQ(uvr.uv.size(), uvr.mesh.corner_count());
    for (std::size_t c = 0; c < uvr.mesh.corner_count(); ++c) {
        const Vec3 p = uvr.mesh.positions[uvr.mesh.face_indices[c]];
        CHECK_NEAR(uvr.uv[c][0], p.x + 1.0, 1e-4);
        CHECK_NEAR(uvr.uv[c][1], p.y + 1.0, 1e-4);
    }
}
