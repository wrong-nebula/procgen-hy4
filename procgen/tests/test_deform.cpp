// Deformers: topology is untouched, geometry checked via volume and intersections.
#include "testing.hpp"

#include "pg/ops/deform.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>

using namespace pg;

TEST(taper_volume_is_exact_frustum) {
    Mesh m = make_box({1, 1, 1}, 4);
    const double k = 0.6;
    taper(m.positions, k);
    const QualityReport q = analyze(m);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 1.0 + k * k / 12.0, 1e-12);  // integral of (1 + k z)^2 over [-1/2, 1/2]
}

TEST(twist_preserves_volume_and_topology) {
    Mesh m = make_box({1, 1, 2}, 24);
    twist(m.positions, 0.8);
    const QualityReport q = analyze(m);
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    CHECK_NEAR(q.volume, 2.0, 2e-3);  // discretisation only: side quads become non-planar
}

TEST(bend_symmetric_solid_keeps_volume) {
    Mesh m = make_box({4, 1, 0.5}, 32);
    bend(m.positions, 0.5);
    const QualityReport q = analyze(m);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 2.0, 2e-3);
    // Ends rotated by +-1 rad: the bent bar rises toward +Z at both ends.
    CHECK(bounds(m).hi.z > 0.4);
}

TEST(bend_zero_curvature_is_identity) {
    Mesh m = make_box({1, 1, 1}, 2);
    const auto before = m.positions;
    bend(m.positions, 0.0);
    CHECK(m.positions == before);
}

TEST(gradient_noise_is_bounded_and_deterministic) {
    double lo = 1e9, hi = -1e9;
    for (int i = 0; i < 20000; ++i) {
        const Vec3 p{i * 0.137, i * 0.071 - 3.0, i * 0.019 + 1.0};
        const double n = gradient_noise(p, 7);
        lo = std::min(lo, n);
        hi = std::max(hi, n);
        CHECK(n == gradient_noise(p, 7));
    }
    CHECK(lo >= -1.0 && hi <= 1.0);
    CHECK(hi - lo > 0.8);  // actually varies
    CHECK(gradient_noise({0.3, 0.4, 0.5}, 1) != gradient_noise({0.3, 0.4, 0.5}, 2));
}

TEST(noise_displacement_stays_watertight_and_bounded) {
    Mesh m = make_box({1, 1, 1}, 16);
    const auto before = m.positions;
    NoiseParams p{.amplitude = 0.03, .frequency = 3.0, .seed = 11, .octaves = 3};
    displace_noise(m, p);
    const QualityReport q = analyze(m);
    CHECK(q.clean());
    double max_disp = 0.0;
    for (std::size_t i = 0; i < before.size(); ++i) max_disp = std::max(max_disp, length(m.positions[i] - before[i]));
    CHECK(max_disp > 0.0);
    CHECK(max_disp <= 0.03 * (1 + 0.5 + 0.25) + 1e-12);
}
