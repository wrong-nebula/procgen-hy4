#include "pg/primitives.hpp"

#include "pg/cleanup.hpp"

#include <cmath>
#include <numbers>
#include <stdexcept>

namespace pg {

Mesh make_grid(std::uint32_t nx, std::uint32_t ny, double size_x, double size_y) {
    if (nx == 0 || ny == 0) throw std::invalid_argument("make_grid: nx and ny must be >= 1");
    Mesh m;
    m.reserve(std::size_t(nx + 1) * (ny + 1), std::size_t(nx) * ny, std::size_t(nx) * ny * 4);
    for (std::uint32_t j = 0; j <= ny; ++j) {
        const double v = (static_cast<double>(j) / ny - 0.5) * size_y;
        for (std::uint32_t i = 0; i <= nx; ++i) {
            const double u = (static_cast<double>(i) / nx - 0.5) * size_x;
            m.add_vertex({u, v, 0.0});
        }
    }
    const Index row = nx + 1;
    for (Index j = 0; j < ny; ++j) {
        for (Index i = 0; i < nx; ++i) {
            const Index a = j * row + i;
            m.add_face({a, a + 1, a + 1 + row, a + row});
        }
    }
    return m;
}

Mesh make_box(Vec3 size, std::uint32_t segments) {
    if (segments == 0) throw std::invalid_argument("make_box: segments must be >= 1");
    if (!(size.x > 0 && size.y > 0 && size.z > 0)) throw std::invalid_argument("make_box: size must be positive");
    const Vec3 h = size * 0.5;
    // (normal, u, v) with cross(u, v) == normal so quads wind outward.
    struct Side { Vec3 n, u, v; };
    constexpr Side sides[6] = {
        {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}},  {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},
        {{0, 1, 0}, {0, 0, 1}, {1, 0, 0}},  {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}},
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},  {{0, 0, -1}, {0, 1, 0}, {1, 0, 0}},
    };
    const std::uint32_t n = segments;
    const std::size_t per_side_v = std::size_t(n + 1) * (n + 1);
    Mesh m;
    m.reserve(per_side_v * 6, std::size_t(n) * n * 6, std::size_t(n) * n * 24);
    auto comp = [](Vec3 axis, Vec3 e) { return std::abs(axis.x) * e.x + std::abs(axis.y) * e.y + std::abs(axis.z) * e.z; };
    for (const Side& s : sides) {
        const Vec3 c{s.n.x * h.x, s.n.y * h.y, s.n.z * h.z};
        const double hu = comp(s.u, h), hv = comp(s.v, h);
        const auto base = static_cast<Index>(m.vertex_count());
        for (std::uint32_t j = 0; j <= n; ++j) {
            // t in [0, 1] exactly at the ends, so shared edges agree bit-for-bit.
            const double tv = 2.0 * (static_cast<double>(j) / n) - 1.0;
            for (std::uint32_t i = 0; i <= n; ++i) {
                const double tu = 2.0 * (static_cast<double>(i) / n) - 1.0;
                m.add_vertex(c + s.u * (tu * hu) + s.v * (tv * hv));
            }
        }
        const Index row = n + 1;
        for (Index j = 0; j < n; ++j)
            for (Index i = 0; i < n; ++i) {
                const Index a = base + j * row + i;
                m.add_face({a, a + 1, a + 1 + row, a + row});
            }
    }
    const double min_side = std::min({size.x, size.y, size.z});
    weld_vertices(m, 1e-9 * min_side / n);
    return m;
}

Mesh make_cylinder(std::uint32_t segments, double radius, double height) {
    if (segments < 3) throw std::invalid_argument("make_cylinder: segments must be >= 3");
    if (!(radius > 0 && height > 0)) throw std::invalid_argument("make_cylinder: radius/height must be positive");
    Mesh m;
    const Index n = segments;
    m.reserve(std::size_t(n) * 2, std::size_t(n) + 2, std::size_t(n) * 6);
    for (int ring = 0; ring < 2; ++ring) {
        const double z = ring == 0 ? -0.5 * height : 0.5 * height;
        for (Index i = 0; i < n; ++i) {
            const double t = 2.0 * std::numbers::pi * static_cast<double>(i) / n;
            m.add_vertex({radius * std::cos(t), radius * std::sin(t), z});
        }
    }
    for (Index i = 0; i < n; ++i) {
        const Index j = (i + 1) % n;
        m.add_face({i, j, j + n, i + n});
    }
    std::vector<Index> cap(n);
    for (Index i = 0; i < n; ++i) cap[i] = n + i;  // top, CCW from +Z
    m.add_face(cap);
    for (Index i = 0; i < n; ++i) cap[i] = n - 1 - i;  // bottom, reversed
    m.add_face(cap);
    return m;
}

}  // namespace pg
