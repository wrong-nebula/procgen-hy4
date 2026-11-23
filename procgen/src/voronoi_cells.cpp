#include "voronoi_cells.hpp"

#include "exact_kernel.hpp"
#include "pg/cleanup.hpp"

#include <Delaunay_psm.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace pg {
namespace {

struct SplitMix64 {
    std::uint64_t state;
    explicit SplitMix64(std::uint64_t s) noexcept : state(s) {}
    std::uint64_t next() noexcept {
        std::uint64_t z = (state += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    }
    double unit() noexcept { return static_cast<double>(next() >> 11) * 0x1p-53; }
};

}  // namespace

VoronoiBoxResult make_voronoi_box(std::span<const Vec3> user_sites, const VoronoiBoxOptions& opts) {
    if (!(opts.size.x > 0.0 && opts.size.y > 0.0 && opts.size.z > 0.0)) {
        throw std::invalid_argument("voronoi: size components must be > 0");
    }
    if (opts.gap < 0.0) throw std::invalid_argument("voronoi: gap must be >= 0");
    if (opts.shrink < 0.0 || opts.shrink >= 0.95) throw std::invalid_argument("voronoi: shrink must be in [0, 0.95)");

    detail::ensure_exact_kernel();

    const Vec3 half = opts.size * 0.5;
    std::vector<Vec3> sites;
    if (!user_sites.empty()) {
        sites.reserve(user_sites.size());
        for (const Vec3& p : user_sites) {
            if (std::isfinite(p.x + p.y + p.z)) sites.push_back(p);
        }
    } else {
        const int n = std::clamp(opts.count, 1, 4096);
        sites.reserve(static_cast<std::size_t>(n));
        SplitMix64 rng(static_cast<std::uint64_t>(static_cast<std::int64_t>(opts.seed)) ^ 0xD1B54A32D192ED03ULL);
        // Stratified jittered grid + leftover samples so stone sizes are balanced rather than clumped.
        const double vol = opts.size.x * opts.size.y * opts.size.z;
        const double pitch = std::cbrt(vol / static_cast<double>(n));
        const int nx = std::max(1, static_cast<int>(std::round(opts.size.x / pitch)));
        const int ny = std::max(1, static_cast<int>(std::round(opts.size.y / pitch)));
        const int nz = std::max(1, static_cast<int>(std::round(opts.size.z / pitch)));
        std::vector<Vec3> pool;
        pool.reserve(static_cast<std::size_t>(nx * ny * nz));
        for (int iz = 0; iz < nz; ++iz) {
            for (int iy = 0; iy < ny; ++iy) {
                for (int ix = 0; ix < nx; ++ix) {
                    const double ux = (static_cast<double>(ix) + 0.18 + 0.64 * rng.unit()) / nx - 0.5;
                    const double uy = (static_cast<double>(iy) + 0.18 + 0.64 * rng.unit()) / ny - 0.5;
                    const double uz = (static_cast<double>(iz) + 0.18 + 0.64 * rng.unit()) / nz - 0.5;
                    pool.push_back({ux * opts.size.x, uy * opts.size.y, uz * opts.size.z});
                }
            }
        }
        // Deterministic Fisher-Yates shuffle
        for (std::size_t i = pool.size(); i > 1; --i) {
            const std::size_t j = static_cast<std::size_t>(rng.next() % i);
            std::swap(pool[i - 1], pool[j]);
        }
        for (std::size_t i = 0; i < std::min<std::size_t>(pool.size(), static_cast<std::size_t>(n)); ++i) {
            sites.push_back(pool[i]);
        }
        while (static_cast<int>(sites.size()) < n) {
            sites.push_back({
                (rng.unit() - 0.5) * opts.size.x * 0.92,
                (rng.unit() - 0.5) * opts.size.y * 0.92,
                (rng.unit() - 0.5) * opts.size.z * 0.92,
            });
        }
    }

    VoronoiBoxResult res;
    if (sites.empty()) return res;

    const std::size_t ns = sites.size();
    const double inset = opts.gap * 0.5;
    std::vector<std::pair<double, std::size_t>> order;
    order.reserve(ns);

    VBW::ConvexCell cell;
    for (std::size_t i = 0; i < ns; ++i) {
        const Vec3 pi = sites[i];
        cell.init_with_box(-half.x, -half.y, -half.z, half.x, half.y, half.z);

        order.clear();
        for (std::size_t j = 0; j < ns; ++j) {
            if (j == i) continue;
            const Vec3 d = sites[j] - pi;
            const double d2 = dot(d, d);
            if (d2 > 1e-20) order.emplace_back(d2, j);
        }
        std::sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.first < b.first; });

        const VBW::vec3 g_pi = VBW::make_vec3(pi.x, pi.y, pi.z);
        for (const auto& [d2, j] : order) {
            if (cell.empty()) break;
            const double dist = std::sqrt(d2);
            const double plane_dist = 0.5 * dist - inset;
            // Security-radius stop: if the shifted bisector lies strictly beyond the farthest
            // vertex of `cell` from `pi`, neither `j` nor any farther site can clip `cell`.
            if (plane_dist > 0.0) {
                cell.compute_geometry();
                if (cell.empty()) break;
                if (plane_dist * plane_dist > cell.squared_radius(g_pi) * (1.0 + 1e-9)) break;
            }
            const Vec3 inward = (pi - sites[j]) / dist;  // points toward `pi` (kept half-space >= 0)
            const Vec3 mid = (pi + sites[j]) * 0.5 + inward * inset;
            cell.clip_by_plane(VBW::make_vec4(inward.x, inward.y, inward.z, -dot(inward, mid)));
        }

        cell.compute_geometry();
        if (cell.empty() || cell.volume() <= 1e-12) continue;

        const VBW::vec3 bc_g = cell.barycenter();
        const Vec3 bc{bc_g.x, bc_g.y, bc_g.z};

        // Extract dual vertices (each active `ConvexCell` triangle is a 3D corner of the polyhedron)
        // and polygon faces (each contributing plane `v >= 1` forms a convex polygon loop).
        std::vector<Index> t_to_vid(cell.nb_t(), static_cast<Index>(-1));
        Mesh m;
        for (VBW::ushort t = cell.first_triangle();
             t != VBW::ushort(VBW::END_OF_LIST);
             t = cell.next_triangle(t)) {
            const VBW::vec3 tp = cell.triangle_point(t);
            Vec3 p{tp.x, tp.y, tp.z};
            if (opts.shrink > 0.0) {
                p = bc * opts.shrink + p * (1.0 - opts.shrink);
            }
            t_to_vid[t] = m.add_vertex(p);
        }

        std::vector<Index> poly;
        for (VBW::index_t v = 1; v < cell.nb_v(); ++v) {
            if (!cell.vertex_is_contributing(v)) continue;
            poly.clear();
            cell.for_each_Voronoi_vertex(v, [&](VBW::index_t t) {
                const Index vid = t_to_vid[t];
                if (vid != static_cast<Index>(-1) && (poly.empty() || poly.back() != vid)) {
                    poly.push_back(vid);
                }
            });
            if (poly.size() >= 2 && poly.front() == poly.back()) poly.pop_back();
            if (poly.size() < 3) continue;

            // Ensure outward CCW orientation relative to the cell's interior barycenter.
            Vec3 fc{}, area_n{};
            for (Index idx : poly) fc += m.positions[idx];
            fc = fc / static_cast<double>(poly.size());
            for (std::size_t k = 1; k + 1 < poly.size(); ++k) {
                area_n += cross(m.positions[poly[k]] - m.positions[poly[0]],
                                m.positions[poly[k + 1]] - m.positions[poly[0]]);
            }
            if (length(area_n) <= 1e-16) continue;
            if (dot(area_n, fc - bc) < 0.0) std::reverse(poly.begin(), poly.end());
            m.add_face(poly);
        }

        if (m.face_count() < 4) continue;
        weld_vertices(m, 1e-9);
        if (m.face_count() < 4) continue;

        res.cells.push_back(std::move(m));
        res.centroids.push_back(bc);
    }
    return res;
}

}  // namespace pg
