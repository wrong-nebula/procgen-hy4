// Scattering: samples on the surface, area-uniform, Poisson spacing, instancing.
#include "testing.hpp"

#include "pg/ops/scatter.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>

using namespace pg;

TEST(scatter_points_lie_on_surface) {
    const Mesh box = make_box({1, 1, 1}, 3);
    const ScatterPoints s = scatter_points(box, {.count = 2000, .seed = 3});
    CHECK_EQ(s.size(), std::size_t(2000));
    for (std::size_t i = 0; i < s.size(); ++i) {
        const Vec3 p = s.positions[i];
        CHECK_NEAR(std::max({std::abs(p.x), std::abs(p.y), std::abs(p.z)}), 0.5, 1e-12);
        CHECK_NEAR(length(s.normals[i]), 1.0, 1e-12);
    }
}

TEST(scatter_is_area_uniform) {
    // Box 2 x 1 x 1: faces normal to X have 1/10 of the area each, Y and Z 2/10.
    const Mesh box = make_box({2, 1, 1}, 2);
    const std::uint32_t n = 100000;
    const ScatterPoints s = scatter_points(box, {.count = n, .seed = 9});
    double on_x = 0;
    for (const Vec3& nm : s.normals) on_x += std::abs(nm.x) > 0.5 ? 1 : 0;
    const double expect = 0.2 * n, sigma = std::sqrt(n * 0.2 * 0.8);
    CHECK(std::abs(on_x - expect) < 5 * sigma);
}

TEST(scatter_respects_min_distance) {
    const Mesh sphere = catmull_clark(make_box({1, 1, 1}, 1), 3);
    const double r = 0.05;
    const ScatterPoints s = scatter_points(sphere, {.count = 5000, .min_distance = r, .seed = 5});
    CHECK(s.size() > 100);
    CHECK(s.size() < 5000);  // the surface can't hold 5000 disks of radius r/2
    double min_d2 = 1e9;
    for (std::size_t i = 0; i < s.size(); ++i)
        for (std::size_t j = i + 1; j < s.size(); ++j) {
            const Vec3 d = s.positions[i] - s.positions[j];
            min_d2 = std::min(min_d2, dot(d, d));
        }
    CHECK(min_d2 >= r * r);
}

TEST(scatter_is_deterministic_per_seed) {
    const Mesh box = make_box({1, 1, 1}, 2);
    const ScatterParams p{.count = 300, .min_distance = 0.02, .seed = 42};
    CHECK(scatter_points(box, p).positions == scatter_points(box, p).positions);
    ScatterParams q = p;
    q.seed = 43;
    CHECK(scatter_points(box, p).positions != scatter_points(box, q).positions);
}

TEST(instances_are_closed_disjoint_and_aligned) {
    const Mesh surface = catmull_clark(make_box({1, 1, 1}, 1), 3);
    const Mesh rivet = make_cylinder(8, 0.02, 0.04);  // diameter 0.04 < spacing
    const ScatterPoints s = scatter_points(surface, {.count = 150, .min_distance = 0.08, .seed = 2});
    const Mesh out = instance_on_points(rivet, s, {.scale = 1.0, .random_spin = true, .seed = 4});
    const QualityReport q = analyze(out);
    const QualityReport one = analyze(rivet);
    CHECK(q.clean());
    CHECK_EQ(q.euler, static_cast<std::int64_t>(2 * s.size()));
    CHECK_NEAR(q.volume, one.volume * static_cast<double>(s.size()), 1e-12);

    // The instance's +Z axis follows the surface normal.
    Mesh marker;
    marker.add_vertex({0, 0, 1});
    marker.add_vertex({0, 0, 0});
    marker.add_vertex({1, 0, 0});
    marker.add_face({0, 1, 2});
    const Mesh m2 = instance_on_points(marker, s, {.scale = 0.5});
    for (std::size_t i = 0; i < s.size(); ++i) {
        const Vec3 tip = m2.positions[3 * i] - s.positions[i];
        CHECK_NEAR(dot(tip, s.normals[i]), 0.5, 1e-12);
        CHECK_NEAR(length(tip), 0.5, 1e-12);
    }
}
