#include "pg/ops/scatter.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>
#include <stdexcept>
#include <unordered_map>

namespace pg {
namespace {

struct Rng {
    std::uint64_t s;
    std::uint64_t next() noexcept {
        std::uint64_t z = (s += 0x9E3779B97F4A7C15ull);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
        return z ^ (z >> 31);
    }
    double uniform() noexcept { return static_cast<double>(next() >> 11) * 0x1.0p-53; }  // [0, 1)
};

// Duff et al. 2017 branchless orthonormal basis around unit n.
void basis(Vec3 n, Vec3& t, Vec3& b) noexcept {
    const double sign = std::copysign(1.0, n.z);
    const double a = -1.0 / (sign + n.z);
    const double c = n.x * n.y * a;
    t = {1.0 + sign * n.x * n.x * a, sign * c, -sign * n.x};
    b = {c, sign + n.y * n.y * a, -n.y};
}

std::uint64_t cell_key(std::int64_t x, std::int64_t y, std::int64_t z) noexcept {
    constexpr std::uint64_t mask = (1ull << 21) - 1;
    return ((static_cast<std::uint64_t>(x) & mask) << 42) | ((static_cast<std::uint64_t>(y) & mask) << 21) |
           (static_cast<std::uint64_t>(z) & mask);
}

}  // namespace

ScatterPoints scatter_points(const Mesh& surface, const ScatterParams& params) {
    if (params.min_distance < 0.0) throw std::invalid_argument("scatter_points: min_distance must be >= 0");
    struct Tri { Vec3 a, b, c, n; Index face; };
    std::vector<Tri> tris;
    std::vector<double> cdf;
    double total = 0.0;
    for (std::size_t f = 0; f < surface.face_count(); ++f) {
        const auto fv = surface.face(f);
        const Vec3 n = face_normal(surface, f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
            const Vec3 a = surface.positions[fv[0]], b = surface.positions[fv[i]], c = surface.positions[fv[i + 1]];
            const double area = 0.5 * length(cross(b - a, c - a));
            if (!(area > 0.0)) continue;
            total += area;
            tris.push_back({a, b, c, n, static_cast<Index>(f)});
            cdf.push_back(total);
        }
    }
    ScatterPoints out;
    if (tris.empty() || params.count == 0) return out;

    Rng rng{params.seed};
    const double r = params.min_distance;
    const double r2 = r * r;
    const double inv_cell = r > 0.0 ? 1.0 / r : 0.0;
    std::unordered_map<std::uint64_t, std::vector<Index>> grid;
    const std::uint64_t budget = static_cast<std::uint64_t>(params.count) * std::max<std::uint32_t>(1, params.max_attempts);
    out.positions.reserve(params.count);

    for (std::uint64_t attempt = 0; attempt < budget && out.size() < params.count; ++attempt) {
        const double pick = rng.uniform() * total;
        const auto it = std::upper_bound(cdf.begin(), cdf.end(), pick);
        const Tri& t = tris[static_cast<std::size_t>(std::min<std::ptrdiff_t>(it - cdf.begin(), static_cast<std::ptrdiff_t>(tris.size()) - 1))];
        const double su = std::sqrt(rng.uniform());
        const double v = rng.uniform();
        const Vec3 p = t.a * (1.0 - su) + t.b * (su * (1.0 - v)) + t.c * (su * v);
        if (r > 0.0) {
            const auto cx = static_cast<std::int64_t>(std::floor(p.x * inv_cell));
            const auto cy = static_cast<std::int64_t>(std::floor(p.y * inv_cell));
            const auto cz = static_cast<std::int64_t>(std::floor(p.z * inv_cell));
            bool ok = true;
            for (std::int64_t dx = -1; dx <= 1 && ok; ++dx)
                for (std::int64_t dy = -1; dy <= 1 && ok; ++dy)
                    for (std::int64_t dz = -1; dz <= 1 && ok; ++dz) {
                        const auto g = grid.find(cell_key(cx + dx, cy + dy, cz + dz));
                        if (g == grid.end()) continue;
                        for (Index k : g->second) {
                            const Vec3 d = out.positions[k] - p;
                            if (dot(d, d) < r2) { ok = false; break; }
                        }
                    }
            if (!ok) continue;
            grid[cell_key(cx, cy, cz)].push_back(static_cast<Index>(out.size()));
        }
        out.positions.push_back(p);
        out.normals.push_back(t.n);
        out.faces.push_back(t.face);
    }
    return out;
}

Mesh instance_on_points(const Mesh& instance, const ScatterPoints& points, const InstanceParams& params) {
    if (!(params.scale > 0.0)) throw std::invalid_argument("instance_on_points: scale must be > 0");
    Mesh out;
    const std::size_t n = points.size();
    out.reserve(instance.vertex_count() * n, instance.face_count() * n, instance.corner_count() * n);
    Rng rng{params.seed};
    for (std::size_t i = 0; i < n; ++i) {
        Vec3 x{1, 0, 0}, y{0, 1, 0}, z{0, 0, 1};
        if (params.align_to_normal) {
            z = points.normals[i];
            basis(z, x, y);
        }
        if (params.random_spin) {
            const double a = 2.0 * std::numbers::pi * rng.uniform();
            const double c = std::cos(a), s = std::sin(a);
            const Vec3 x2 = x * c + y * s;
            y = y * c - x * s;
            x = x2;
        }
        const auto base = static_cast<Index>(out.vertex_count());
        const Vec3 o = points.positions[i];
        const double k = params.scale;
        for (const Vec3& p : instance.positions) out.positions.push_back(o + (x * p.x + y * p.y + z * p.z) * k);
        const auto cbase = static_cast<Index>(out.face_indices.size());
        for (std::size_t f = 1; f < instance.face_offsets.size(); ++f) out.face_offsets.push_back(instance.face_offsets[f] + cbase);
        for (Index v : instance.face_indices) out.face_indices.push_back(v + base);
    }
    return out;
}

}  // namespace pg
