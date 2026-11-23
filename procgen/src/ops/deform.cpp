#include "pg/ops/deform.hpp"

#include <cmath>

namespace pg {

void twist(std::span<Vec3> points, double radians_per_unit) noexcept {
    for (Vec3& p : points) {
        const double a = radians_per_unit * p.z;
        const double c = std::cos(a), s = std::sin(a);
        p = {c * p.x - s * p.y, s * p.x + c * p.y, p.z};
    }
}

void bend(std::span<Vec3> points, double curvature) noexcept {
    if (std::abs(curvature) < 1e-12) return;
    const double r0 = 1.0 / curvature;
    for (Vec3& p : points) {
        const double theta = curvature * p.x;
        const double r = r0 - p.z;
        p = {std::sin(theta) * r, p.y, r0 - std::cos(theta) * r};
    }
}

void taper(std::span<Vec3> points, double scale_per_unit) noexcept {
    for (Vec3& p : points) {
        const double s = 1.0 + scale_per_unit * p.z;
        p = {p.x * s, p.y * s, p.z};
    }
}

namespace {

inline std::uint64_t mix64(std::uint64_t x) noexcept {
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
    return x ^ (x >> 31);
}

inline double lattice_dot(std::int64_t ix, std::int64_t iy, std::int64_t iz, double fx, double fy, double fz,
                          std::uint64_t seed) noexcept {
    std::uint64_t h = mix64(seed ^ (static_cast<std::uint64_t>(ix) * 0x9E3779B97F4A7C15ull));
    h = mix64(h ^ (static_cast<std::uint64_t>(iy) * 0xC2B2AE3D27D4EB4Full));
    h = mix64(h ^ (static_cast<std::uint64_t>(iz) * 0x165667B19E3779F9ull));
    // 12 cube-edge gradients (Perlin 2002).
    switch (h % 12) {
        case 0: return fx + fy;
        case 1: return -fx + fy;
        case 2: return fx - fy;
        case 3: return -fx - fy;
        case 4: return fx + fz;
        case 5: return -fx + fz;
        case 6: return fx - fz;
        case 7: return -fx - fz;
        case 8: return fy + fz;
        case 9: return -fy + fz;
        case 10: return fy - fz;
        default: return -fy - fz;
    }
}

inline double fade(double t) noexcept { return t * t * t * (t * (t * 6.0 - 15.0) + 10.0); }
inline double lerp(double a, double b, double t) noexcept { return a + (b - a) * t; }

}  // namespace

double gradient_noise(Vec3 p, std::uint64_t seed) noexcept {
    const double flx = std::floor(p.x), fly = std::floor(p.y), flz = std::floor(p.z);
    const auto ix = static_cast<std::int64_t>(flx), iy = static_cast<std::int64_t>(fly), iz = static_cast<std::int64_t>(flz);
    const double fx = p.x - flx, fy = p.y - fly, fz = p.z - flz;
    const double u = fade(fx), v = fade(fy), w = fade(fz);
    auto g = [&](int dx, int dy, int dz) {
        return lattice_dot(ix + dx, iy + dy, iz + dz, fx - dx, fy - dy, fz - dz, seed);
    };
    const double x00 = lerp(g(0, 0, 0), g(1, 0, 0), u), x10 = lerp(g(0, 1, 0), g(1, 1, 0), u);
    const double x01 = lerp(g(0, 0, 1), g(1, 0, 1), u), x11 = lerp(g(0, 1, 1), g(1, 1, 1), u);
    // Gradients have length sqrt 2; the raw range is within [-1, 1] after this scale.
    return lerp(lerp(x00, x10, v), lerp(x01, x11, v), w) * 0.7071067811865476;
}

std::vector<Vec3> vertex_normals(const Mesh& m) {
    std::vector<Vec3> n(m.vertex_count());
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const Vec3 a = face_area_vector(m, f);
        for (Index v : m.face(f)) n[v] += a;
    }
    for (Vec3& v : n) v = normalize_or(v, Vec3{});
    return n;
}

void displace_noise(Mesh& m, const NoiseParams& params) {
    const std::vector<Vec3> n = vertex_normals(m);
    for (std::size_t v = 0; v < m.vertex_count(); ++v) {
        double amp = params.amplitude, freq = params.frequency, sum = 0.0;
        for (std::uint32_t o = 0; o < params.octaves; ++o) {
            sum += amp * gradient_noise(m.positions[v] * freq, params.seed + o);
            amp *= 0.5;
            freq *= 2.0;
        }
        m.positions[v] += n[v] * sum;
    }
}

}  // namespace pg
