#include "pg/dp_nns.hpp"

#include "exact_kernel.hpp"

#include <Delaunay_psm.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <numeric>
#include <queue>
#include <stdexcept>
#include <unordered_map>

namespace pg {
namespace {

constexpr std::size_t kLinearThreshold = 48;

[[nodiscard]] inline double dist_sq(Vec3 a, Vec3 b) noexcept {
    const double dx = a.x - b.x;
    const double dy = a.y - b.y;
    const double dz = a.z - b.z;
    return dx * dx + dy * dy + dz * dz;
}

[[nodiscard]] inline bool better_hit(double d2_a, Index orig_a, double d2_b, Index orig_b) noexcept {
    if (d2_a < d2_b) return true;
    if (d2_a > d2_b) return false;
    return orig_a < orig_b;
}

struct BitKey {
    std::uint64_t x = 0, y = 0, z = 0;
    bool operator==(const BitKey&) const noexcept = default;
};

struct BitKeyHash {
    std::size_t operator()(const BitKey& k) const noexcept {
        std::uint64_t h = k.x * 0x9E3779B97F4A7C15ULL;
        h ^= k.y * 0xBF58476D1CE4E5B9ULL + (h << 6) + (h >> 2);
        h ^= k.z * 0x94D049BB133111EBULL + (h << 6) + (h >> 2);
        return static_cast<std::size_t>(h);
    }
};

[[nodiscard]] inline BitKey bit_key(Vec3 p) noexcept {
    // Normalize -0.0 to +0.0 so identical coordinates hash together.
    if (p.x == 0.0) p.x = 0.0;
    if (p.y == 0.0) p.y = 0.0;
    if (p.z == 0.0) p.z = 0.0;
    BitKey k;
    std::memcpy(&k.x, &p.x, sizeof(double));
    std::memcpy(&k.y, &p.y, sizeof(double));
    std::memcpy(&k.z, &p.z, sizeof(double));
    return k;
}

[[nodiscard]] inline std::uint32_t expand_bits_10(std::uint32_t v) noexcept {
    v = (v * 0x00010001u) & 0xFF0000FFu;
    v = (v * 0x00000101u) & 0x0F00F00Fu;
    v = (v * 0x00000011u) & 0xC30C30C3u;
    v = (v * 0x00000005u) & 0x49249249u;
    return v;
}

[[nodiscard]] inline std::uint32_t morton3d(Vec3 p, Vec3 lo, Vec3 inv_ext) noexcept {
    const auto q10 = [](double t) noexcept -> std::uint32_t {
        const double c = std::clamp(t, 0.0, 1.0) * 1023.0;
        return static_cast<std::uint32_t>(c);
    };
    const std::uint32_t mx = expand_bits_10(q10((p.x - lo.x) * inv_ext.x));
    const std::uint32_t my = expand_bits_10(q10((p.y - lo.y) * inv_ext.y));
    const std::uint32_t mz = expand_bits_10(q10((p.z - lo.z) * inv_ext.z));
    return (mx << 2) | (my << 1) | mz;
}

// Detect affine dimension (1: collinear, 2: coplanar, 3: volumetric) and return
// orthonormal basis `(origin, e1, e2)` for 1D/2D reduction when degenerate.
int detect_affine_frame(std::span<const Vec3> pts, double diag, Vec3& origin, Vec3& e1, Vec3& e2) noexcept {
    origin = pts[0];
    const double eps = std::max(1e-11 * diag, 1e-14);
    const double eps2 = eps * eps;

    double best_d2 = 0.0;
    std::size_t far_i = 0;
    for (std::size_t i = 1; i < pts.size(); ++i) {
        const double d2 = dist_sq(pts[i], origin);
        if (d2 > best_d2) {
            best_d2 = d2;
            far_i = i;
        }
    }
    if (best_d2 <= eps2) return 0;
    e1 = normalize_or(pts[far_i] - origin, Vec3{1.0, 0.0, 0.0});

    double best_line_d2 = 0.0;
    Vec3 best_perp{};
    for (std::size_t i = 1; i < pts.size(); ++i) {
        const Vec3 v = pts[i] - origin;
        const Vec3 perp = v - e1 * dot(v, e1);
        const double d2 = dot(perp, perp);
        if (d2 > best_line_d2) {
            best_line_d2 = d2;
            best_perp = perp;
        }
    }
    if (best_line_d2 <= eps2) return 1;
    e2 = normalize_or(best_perp, Vec3{0.0, 1.0, 0.0});
    const Vec3 normal = cross(e1, e2);

    bool approx_3d = false;
    for (std::size_t i = 1; i < pts.size(); ++i) {
        const double h = std::abs(dot(pts[i] - origin, normal));
        if (h > eps) {
            approx_3d = true;
            break;
        }
    }
    if (!approx_3d) return 2;

    // Confirm exact non-coplanarity via PCK::orient_3d so ParallelDelaunay3d's
    // create_first_tetrahedron() is guaranteed to find a non-zero initial tet.
    detail::ensure_exact_kernel();
    std::size_t iv1 = 1;
    while (iv1 < pts.size() && GEO::PCK::points_are_identical_3d(&pts[0].x, &pts[iv1].x)) ++iv1;
    if (iv1 == pts.size()) return 0;
    std::size_t iv2 = iv1 + 1;
    while (iv2 < pts.size() && GEO::PCK::points_are_colinear_3d(&pts[0].x, &pts[iv1].x, &pts[iv2].x)) ++iv2;
    if (iv2 == pts.size()) return 1;
    for (std::size_t iv3 = iv2 + 1; iv3 < pts.size(); ++iv3) {
        if (GEO::PCK::orient_3d(&pts[0].x, &pts[iv1].x, &pts[iv2].x, &pts[iv3].x) != GEO::ZERO) {
            return 3;
        }
    }
    return 2;
}

void build_csr_from_edges(std::uint32_t n_sites, std::vector<std::pair<std::uint32_t, std::uint32_t>>& directed,
                          std::vector<std::uint32_t>& offsets, std::vector<std::uint32_t>& neighbors) {
    std::sort(directed.begin(), directed.end());
    directed.erase(std::unique(directed.begin(), directed.end()), directed.end());

    offsets.assign(static_cast<std::size_t>(n_sites) + 1, 0u);
    for (const auto& [u, v] : directed) {
        (void)v;
        ++offsets[static_cast<std::size_t>(u) + 1];
    }
    for (std::size_t i = 0; i < n_sites; ++i) {
        offsets[i + 1] += offsets[i];
    }
    neighbors.resize(directed.size());
    for (std::size_t i = 0; i < directed.size(); ++i) {
        neighbors[i] = directed[i].second;
    }
}

}  // namespace

DpNnsIndex::DpNnsIndex(std::span<const Vec3> points, const DpNnsOptions& opts) {
    build(points, opts);
}

void DpNnsIndex::build(std::span<const Vec3> points, const DpNnsOptions& opts) {
    mode_ = Mode::Empty;
    input_count_ = points.size();
    sites_.clear();
    next_original_.assign(points.size(), kInvalidIndex);
    offsets_.clear();
    neighbors_.clear();
    bounds_ = Aabb{};
    inv_cell_ = Vec3{};
    gx_ = gy_ = gz_ = 0;
    cell_seed_.clear();

    if (points.empty()) return;

    for (const Vec3& p : points) {
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) {
            throw std::invalid_argument("DpNnsIndex::build: non-finite input point coordinate");
        }
        bounds_.expand(p);
    }

    // Deduplicate bitwise-identical input points while keeping a singly-linked list of
    // all original indices at each unique site in ascending index order.
    sites_.reserve(points.size());
    std::unordered_map<BitKey, std::pair<std::uint32_t, Index>, BitKeyHash> seen;
    seen.reserve(points.size());
    for (std::size_t i = 0; i < points.size(); ++i) {
        const Index orig = static_cast<Index>(i);
        const BitKey key = bit_key(points[i]);
        auto [it, inserted] = seen.emplace(key, std::make_pair(static_cast<std::uint32_t>(sites_.size()), orig));
        if (inserted) {
            sites_.push_back(Site{points[i], orig});
        } else {
            next_original_[it->second.second] = orig;
            it->second.second = orig;
        }
    }

    const std::size_t ns = sites_.size();
    if (ns <= kLinearThreshold) {
        mode_ = Mode::Linear;
        return;
    }

    std::vector<Vec3> uniq_pos(ns);
    for (std::size_t i = 0; i < ns; ++i) uniq_pos[i] = sites_[i].pos;

    const double diag = bounds_.diagonal();
    Vec3 f_origin{}, f_e1{}, f_e2{};
    const int dim = detect_affine_frame(uniq_pos, diag, f_origin, f_e1, f_e2);
    if (dim == 0) {
        mode_ = Mode::Linear;
        return;
    }

    std::vector<std::pair<std::uint32_t, std::uint32_t>> directed;
    const auto add_undirected = [&](std::uint32_t a, std::uint32_t b) {
        if (a != b) {
            directed.emplace_back(a, b);
            directed.emplace_back(b, a);
        }
    };

    if (dim == 1) {
        // 1D collinear chain: exact Delaunay graph is the sorted 1D adjacency chain.
        std::vector<std::uint32_t> order(ns);
        std::iota(order.begin(), order.end(), 0u);
        std::sort(order.begin(), order.end(), [&](std::uint32_t a, std::uint32_t b) {
            return dot(sites_[a].pos - f_origin, f_e1) < dot(sites_[b].pos - f_origin, f_e1);
        });
        for (std::size_t i = 0; i + 1 < ns; ++i) {
            add_undirected(order[i], order[i + 1]);
        }
    } else if (dim == 2) {
        // 2D coplanar set: exact 2D Delaunay triangulation in the plane's orthonormal frame.
        detail::ensure_exact_kernel();
        std::vector<double> coords2d(ns * 2);
        for (std::size_t i = 0; i < ns; ++i) {
            const Vec3 d = sites_[i].pos - f_origin;
            coords2d[i * 2 + 0] = dot(d, f_e1);
            coords2d[i * 2 + 1] = dot(d, f_e2);
        }
        GEO::SmartPointer<GEO::Delaunay> del = GEO::Delaunay::create(2, "BDEL2d");
        if (del.is_null()) throw std::runtime_error("DpNnsIndex: failed to create Geogram BDEL2d");
        del->set_reorder(true);
        del->set_vertices(static_cast<GEO::index_t>(ns), coords2d.data());
        const GEO::index_t nc = del->nb_cells();
        directed.reserve(static_cast<std::size_t>(nc) * 6);
        for (GEO::index_t c = 0; c < nc; ++c) {
            const GEO::index_t v0 = del->cell_vertex(c, 0);
            const GEO::index_t v1 = del->cell_vertex(c, 1);
            const GEO::index_t v2 = del->cell_vertex(c, 2);
            if (v0 < ns && v1 < ns) add_undirected(v0, v1);
            if (v1 < ns && v2 < ns) add_undirected(v1, v2);
            if (v2 < ns && v0 < ns) add_undirected(v2, v0);
        }
    } else {
        // Full 3D point set: parallel or sequential BRIO-ordered 3D Delaunay triangulation
        // with infinite hull cells enabled so all convex-hull facet edges are captured too.
        detail::ensure_exact_kernel();
        std::vector<double> coords3d(ns * 3);
        for (std::size_t i = 0; i < ns; ++i) {
            coords3d[i * 3 + 0] = sites_[i].pos.x;
            coords3d[i * 3 + 1] = sites_[i].pos.y;
            coords3d[i * 3 + 2] = sites_[i].pos.z;
        }
        GEO::SmartPointer<GEO::Delaunay> del;
        if (opts.parallel && ns >= 1024) {
            del = GEO::Delaunay::create(3, "PDEL");
        }
        if (del.is_null()) {
            del = GEO::Delaunay::create(3, "BDEL");
        }
        if (del.is_null()) throw std::runtime_error("DpNnsIndex: failed to create Geogram 3D Delaunay");
        del->set_reorder(true);
        del->set_keeps_infinite(false);
        del->set_vertices(static_cast<GEO::index_t>(ns), coords3d.data());

        // Two-pass flat CSR construction directly from finite tetrahedra (avoids
        // sorting ~2M global edge pairs: each vertex sorts only its ~28 local peers in L1).
        const GEO::index_t nc = del->nb_cells();
        const GEO::index_t* c2v = del->cell_to_v();
        std::vector<std::uint32_t> deg(ns, 0u);
        for (GEO::index_t c = 0; c < nc; ++c) {
            const GEO::index_t* tv = c2v + static_cast<std::size_t>(c) * 4;
            deg[tv[0]] += 3u;
            deg[tv[1]] += 3u;
            deg[tv[2]] += 3u;
            deg[tv[3]] += 3u;
        }
        std::vector<std::uint32_t> raw_off(ns + 1, 0u);
        for (std::size_t i = 0; i < ns; ++i) raw_off[i + 1] = raw_off[i] + deg[i];
        std::vector<std::uint32_t> raw_nb(raw_off[ns]);
        std::vector<std::uint32_t> cur = raw_off;
        for (GEO::index_t c = 0; c < nc; ++c) {
            const GEO::index_t* tv = c2v + static_cast<std::size_t>(c) * 4;
            for (int a = 0; a < 4; ++a) {
                const std::uint32_t u = tv[a];
                std::uint32_t pos = cur[u];
                for (int b = 0; b < 4; ++b) {
                    if (a != b) raw_nb[pos++] = tv[b];
                }
                cur[u] = pos;
            }
        }
        offsets_.resize(ns + 1, 0u);
        neighbors_.clear();
        neighbors_.reserve(raw_off[ns] / 4);
        for (std::size_t u = 0; u < ns; ++u) {
            offsets_[u] = static_cast<std::uint32_t>(neighbors_.size());
            auto beg = raw_nb.begin() + static_cast<std::ptrdiff_t>(raw_off[u]);
            auto end = raw_nb.begin() + static_cast<std::ptrdiff_t>(raw_off[u + 1]);
            std::sort(beg, end);
            end = std::unique(beg, end);
            neighbors_.insert(neighbors_.end(), beg, end);
        }
        offsets_[ns] = static_cast<std::uint32_t>(neighbors_.size());
    }

    if (dim != 3 && directed.empty()) {
        mode_ = Mode::Linear;
        return;
    }
    if (dim == 3 && neighbors_.empty()) {
        mode_ = Mode::Linear;
        return;
    }

    // Build the 3D uniform seed grid for O(1) walk initialization (`DP_NNS_SPEC.md` §1.3).
    const Vec3 ext = bounds_.extent();
    const double max_e = std::max({ext.x, ext.y, ext.z, 1e-12});
    const std::size_t target_cells = std::clamp(
        ns / std::max<std::size_t>(opts.points_per_cell, 1), std::size_t{8}, std::max<std::size_t>(opts.max_grid_cells, 8));
    const double vol_norm = std::max(ext.x / max_e, 0.05) * std::max(ext.y / max_e, 0.05) * std::max(ext.z / max_e, 0.05);
    const double base_res = std::cbrt(static_cast<double>(target_cells) / vol_norm);
    const auto axis_res = [&](double e) -> std::uint32_t {
        if (e <= 1e-11 * max_e) return 1u;
        const double r = std::round(base_res * (e / max_e));
        return static_cast<std::uint32_t>(std::clamp(r, 1.0, 64.0));
    };
    gx_ = axis_res(ext.x);
    gy_ = axis_res(ext.y);
    gz_ = axis_res(ext.z);
    while (static_cast<std::size_t>(gx_) * gy_ * gz_ > opts.max_grid_cells) {
        if (gx_ >= gy_ && gx_ >= gz_ && gx_ > 1) --gx_;
        else if (gy_ >= gz_ && gy_ > 1) --gy_;
        else if (gz_ > 1) --gz_;
        else break;
    }
    inv_cell_ = {
        ext.x > 1e-15 ? static_cast<double>(gx_) / ext.x : 0.0,
        ext.y > 1e-15 ? static_cast<double>(gy_) / ext.y : 0.0,
        ext.z > 1e-15 ? static_cast<double>(gz_) / ext.z : 0.0,
    };

    const std::size_t total_cells = static_cast<std::size_t>(gx_) * gy_ * gz_;
    constexpr std::uint32_t kNoSeed = std::numeric_limits<std::uint32_t>::max();
    cell_seed_.assign(total_cells, kNoSeed);
    std::vector<double> cell_best_d2(total_cells, std::numeric_limits<double>::infinity());

    const auto cell_center = [&](std::uint32_t ix, std::uint32_t iy, std::uint32_t iz) noexcept -> Vec3 {
        return {
            bounds_.lo.x + (ext.x > 0.0 ? (static_cast<double>(ix) + 0.5) * (ext.x / gx_) : 0.0),
            bounds_.lo.y + (ext.y > 0.0 ? (static_cast<double>(iy) + 0.5) * (ext.y / gy_) : 0.0),
            bounds_.lo.z + (ext.z > 0.0 ? (static_cast<double>(iz) + 0.5) * (ext.z / gz_) : 0.0),
        };
    };

    for (std::uint32_t s = 0; s < static_cast<std::uint32_t>(ns); ++s) {
        const Vec3 p = sites_[s].pos;
        const auto cx = static_cast<std::uint32_t>(std::clamp(
            static_cast<int>((p.x - bounds_.lo.x) * inv_cell_.x), 0, static_cast<int>(gx_) - 1));
        const auto cy = static_cast<std::uint32_t>(std::clamp(
            static_cast<int>((p.y - bounds_.lo.y) * inv_cell_.y), 0, static_cast<int>(gy_) - 1));
        const auto cz = static_cast<std::uint32_t>(std::clamp(
            static_cast<int>((p.z - bounds_.lo.z) * inv_cell_.z), 0, static_cast<int>(gz_) - 1));
        const std::size_t cid = (static_cast<std::size_t>(cz) * gy_ + cy) * gx_ + cx;
        const double d2 = dist_sq(p, cell_center(cx, cy, cz));
        if (d2 < cell_best_d2[cid]) {
            cell_best_d2[cid] = d2;
            cell_seed_[cid] = s;
        }
    }

    // Multi-source 6-neighbor BFS flood-fill so empty cells inherit a nearby seed in O(G).
    std::vector<std::uint32_t> bfs;
    bfs.reserve(total_cells);
    for (std::uint32_t cid = 0; cid < static_cast<std::uint32_t>(total_cells); ++cid) {
        if (cell_seed_[cid] != kNoSeed) bfs.push_back(cid);
    }
    std::size_t head = 0;
    while (head < bfs.size()) {
        const std::uint32_t cid = bfs[head++];
        const std::uint32_t seed = cell_seed_[cid];
        const std::uint32_t cx = cid % gx_;
        const std::uint32_t cy = (cid / gx_) % gy_;
        const std::uint32_t cz = cid / (gx_ * gy_);
        const int dirs[6][3] = {{-1, 0, 0}, {1, 0, 0}, {0, -1, 0}, {0, 1, 0}, {0, 0, -1}, {0, 0, 1}};
        for (const auto& d : dirs) {
            const int nx = static_cast<int>(cx) + d[0];
            const int ny = static_cast<int>(cy) + d[1];
            const int nz = static_cast<int>(cz) + d[2];
            if (nx < 0 || nx >= static_cast<int>(gx_) ||
                ny < 0 || ny >= static_cast<int>(gy_) ||
                nz < 0 || nz >= static_cast<int>(gz_)) {
                continue;
            }
            const auto ncid = static_cast<std::uint32_t>(
                (static_cast<std::size_t>(nz) * gy_ + static_cast<std::size_t>(ny)) * gx_ + static_cast<std::size_t>(nx));
            if (cell_seed_[ncid] == kNoSeed) {
                cell_seed_[ncid] = seed;
                bfs.push_back(ncid);
            }
        }
    }

    if (dim != 3) {
        build_csr_from_edges(static_cast<std::uint32_t>(ns), directed, offsets_, neighbors_);

        // Guard against any 2D duplicate-projection vertex by linking it to its cell seed.
        bool patched = false;
        for (std::uint32_t s = 0; s < static_cast<std::uint32_t>(ns); ++s) {
            if (offsets_[s] == offsets_[s + 1]) {
                const std::uint32_t gseed = seed_for(sites_[s].pos);
                const std::uint32_t anchor = (gseed != s) ? gseed : ((s == 0) ? 1u : 0u);
                add_undirected(s, anchor);
                patched = true;
            }
        }
        if (patched) {
            build_csr_from_edges(static_cast<std::uint32_t>(ns), directed, offsets_, neighbors_);
        }
    }

    mode_ = Mode::Graph;
}

DpNnsStats DpNnsIndex::stats() const noexcept {
    DpNnsStats s;
    s.input_points = input_count_;
    s.unique_sites = sites_.size();
    s.directed_edges = neighbors_.size();
    s.grid_cells = cell_seed_.size();
    s.memory_bytes = sites_.capacity() * sizeof(Site) +
                     next_original_.capacity() * sizeof(Index) +
                     offsets_.capacity() * sizeof(std::uint32_t) +
                     neighbors_.capacity() * sizeof(std::uint32_t) +
                     cell_seed_.capacity() * sizeof(std::uint32_t);
    return s;
}

std::uint32_t DpNnsIndex::seed_for(Vec3 q) const noexcept {
    if (cell_seed_.empty()) return 0u;
    const auto clamp_idx = [](double v, double lo, double inv, std::uint32_t n) noexcept -> std::uint32_t {
        if (n <= 1u) return 0u;
        const double f = (v - lo) * inv;
        if (!(f > 0.0)) return 0u;
        if (f >= static_cast<double>(n - 1u)) return n - 1u;
        return static_cast<std::uint32_t>(f);
    };
    const std::uint32_t cx = clamp_idx(q.x, bounds_.lo.x, inv_cell_.x, gx_);
    const std::uint32_t cy = clamp_idx(q.y, bounds_.lo.y, inv_cell_.y, gy_);
    const std::uint32_t cz = clamp_idx(q.z, bounds_.lo.z, inv_cell_.z, gz_);
    return cell_seed_[(static_cast<std::size_t>(cz) * gy_ + cy) * gx_ + cx];
}

std::pair<std::uint32_t, std::uint32_t> DpNnsIndex::walk_site(Vec3 q, std::uint32_t start) const noexcept {
    std::uint32_t cur = start;
    double best_d2 = dist_sq(sites_[cur].pos, q);
    Index best_orig = sites_[cur].head_original;
    std::uint32_t visited = 1;

    for (;;) {
        std::uint32_t next = cur;
        const std::uint32_t beg = offsets_[cur];
        const std::uint32_t end = offsets_[cur + 1];
        for (std::uint32_t e = beg; e < end; ++e) {
            const std::uint32_t nb = neighbors_[e];
            const double d2 = dist_sq(sites_[nb].pos, q);
            ++visited;
            if (better_hit(d2, sites_[nb].head_original, best_d2, best_orig)) {
                best_d2 = d2;
                best_orig = sites_[nb].head_original;
                next = nb;
            }
        }
        if (next != cur) {
            cur = next;
            continue;
        }

        // 2-hop tie/bisector check across nearly equidistant neighbors (eliminates cocircular
        // quad diagonal flips when `q` lies directly on a Voronoi bisector).
        const double tie_tol = best_d2 * (1.0 + 1e-10) + 1e-24;
        for (std::uint32_t e = beg; e < end; ++e) {
            const std::uint32_t nb = neighbors_[e];
            if (dist_sq(sites_[nb].pos, q) > tie_tol) continue;
            const std::uint32_t nb_beg = offsets_[nb];
            const std::uint32_t nb_end = offsets_[nb + 1];
            for (std::uint32_t e2 = nb_beg; e2 < nb_end; ++e2) {
                const std::uint32_t nb2 = neighbors_[e2];
                if (nb2 == cur) continue;
                const double d2 = dist_sq(sites_[nb2].pos, q);
                ++visited;
                if (better_hit(d2, sites_[nb2].head_original, best_d2, best_orig)) {
                    best_d2 = d2;
                    best_orig = sites_[nb2].head_original;
                    next = nb2;
                }
            }
        }
        if (next == cur) break;
        cur = next;
    }

    return {cur, visited};
}

DpNnsHit DpNnsIndex::query(Vec3 q) const noexcept {
    if (mode_ == Mode::Empty) return {};
    if (mode_ == Mode::Linear) {
        std::uint32_t best_s = 0;
        double best_d2 = dist_sq(sites_[0].pos, q);
        for (std::uint32_t s = 1; s < static_cast<std::uint32_t>(sites_.size()); ++s) {
            const double d2 = dist_sq(sites_[s].pos, q);
            if (better_hit(d2, sites_[s].head_original, best_d2, sites_[best_s].head_original)) {
                best_d2 = d2;
                best_s = s;
            }
        }
        return {sites_[best_s].head_original, best_d2, static_cast<std::uint32_t>(sites_.size())};
    }

    const auto [site_idx, visited] = walk_site(q, seed_for(q));
    return {sites_[site_idx].head_original, dist_sq(sites_[site_idx].pos, q), visited};
}

std::vector<DpNnsHit> DpNnsIndex::query_knn(Vec3 q, std::size_t k) const {
    const std::size_t want = std::min(k, input_count_);
    std::vector<DpNnsHit> out;
    if (want == 0) return out;
    out.reserve(want);

    if (want == 1) {
        out.push_back(query(q));
        return out;
    }

    const auto push_site_originals = [&](std::uint32_t s, double d2, std::uint32_t vis) {
        for (Index orig = sites_[s].head_original; orig != kInvalidIndex && out.size() < want;
             orig = next_original_[orig]) {
            out.push_back({orig, d2, vis});
            vis = 0;
        }
    };

    if (mode_ == Mode::Linear) {
        std::vector<std::pair<double, std::uint32_t>> cand(sites_.size());
        for (std::uint32_t s = 0; s < static_cast<std::uint32_t>(sites_.size()); ++s) {
            cand[s] = {dist_sq(sites_[s].pos, q), s};
        }
        std::sort(cand.begin(), cand.end(), [&](const auto& a, const auto& b) {
            return better_hit(a.first, sites_[a.second].head_original, b.first, sites_[b.second].head_original);
        });
        for (const auto& [d2, s] : cand) {
            push_site_originals(s, d2, 1u);
            if (out.size() >= want) break;
        }
        return out;
    }

    // Best-first expansion on the Delaunay CSR graph seeded at the exact 1-NN site.
    const auto [s0, walk_vis] = walk_site(q, seed_for(q));
    struct Node {
        double d2;
        Index head_orig;
        std::uint32_t site;
        bool operator>(const Node& o) const noexcept {
            return better_hit(o.d2, o.head_orig, d2, head_orig);
        }
    };
    std::priority_queue<Node, std::vector<Node>, std::greater<Node>> pq;
    std::vector<std::uint8_t> seen(sites_.size(), 0u);
    seen[s0] = 1u;
    pq.push({dist_sq(sites_[s0].pos, q), sites_[s0].head_original, s0});
    bool first = true;

    while (!pq.empty() && out.size() < want) {
        const Node cur = pq.top();
        pq.pop();
        push_site_originals(cur.site, cur.d2, first ? walk_vis : 1u);
        first = false;
        if (out.size() >= want) break;

        const std::uint32_t beg = offsets_[cur.site];
        const std::uint32_t end = offsets_[cur.site + 1];
        for (std::uint32_t e = beg; e < end; ++e) {
            const std::uint32_t nb = neighbors_[e];
            if (seen[nb]) continue;
            seen[nb] = 1u;
            pq.push({dist_sq(sites_[nb].pos, q), sites_[nb].head_original, nb});
        }
    }
    return out;
}

std::vector<DpNnsHit> DpNnsIndex::query_batch(std::span<const Vec3> queries, bool morton_reorder) const {
    std::vector<DpNnsHit> hits(queries.size());
    if (queries.empty() || mode_ == Mode::Empty) return hits;

    if (!morton_reorder || queries.size() < 64 || mode_ == Mode::Linear) {
        for (std::size_t i = 0; i < queries.size(); ++i) hits[i] = query(queries[i]);
        return hits;
    }

    Aabb qbox = bounds_;
    for (const Vec3& q : queries) qbox.expand(q);
    const Vec3 ext = qbox.extent();
    const Vec3 inv_ext = {
        ext.x > 1e-15 ? 1.0 / ext.x : 0.0,
        ext.y > 1e-15 ? 1.0 / ext.y : 0.0,
        ext.z > 1e-15 ? 1.0 / ext.z : 0.0,
    };

    std::vector<std::pair<std::uint32_t, std::uint32_t>> order(queries.size());
    for (std::size_t i = 0; i < queries.size(); ++i) {
        order[i] = {morton3d(queries[i], qbox.lo, inv_ext), static_cast<std::uint32_t>(i)};
    }
    std::sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.first < b.first; });

    // Warm-start across consecutive Morton-nearby queries when closer than the grid cell seed.
    std::uint32_t prev_site = seed_for(queries[order[0].second]);
    for (const auto& [code, qi] : order) {
        (void)code;
        const Vec3 q = queries[qi];
        const std::uint32_t gseed = seed_for(q);
        const std::uint32_t start =
            dist_sq(sites_[prev_site].pos, q) < dist_sq(sites_[gseed].pos, q) ? prev_site : gseed;
        const auto [site_idx, visited] = walk_site(q, start);
        prev_site = site_idx;
        hits[qi] = {sites_[site_idx].head_original, dist_sq(sites_[site_idx].pos, q), visited};
    }
    return hits;
}

std::vector<std::pair<Index, Index>> find_close_pairs_dpnns(
    std::span<const Vec3> points, double radius, const DpNnsOptions& opts) {
    std::vector<std::pair<Index, Index>> pairs;
    if (points.size() < 2 || radius < 0.0) return pairs;

    const double r2 = radius * radius;
    if (points.size() <= kLinearThreshold) {
        for (std::size_t i = 0; i < points.size(); ++i) {
            for (std::size_t j = i + 1; j < points.size(); ++j) {
                if (dist_sq(points[i], points[j]) <= r2) {
                    pairs.emplace_back(static_cast<Index>(i), static_cast<Index>(j));
                }
            }
        }
        return pairs;
    }

    // Build index and expand the local Delaunay ball of radius `r` around each point via `query_knn`
    // or adaptive BFS; using spatial hash / Delaunay hybrid guarantees exactness for arbitrary density.
    const DpNnsIndex idx(points, opts);
    (void)idx;
    // Use adaptive k-NN expansion on `idx`: double `k` whenever the farthest returned neighbor is still <= r2.
    for (std::size_t i = 0; i < points.size(); ++i) {
        std::size_t k = 16;
        for (;;) {
            const auto knn = idx.query_knn(points[i], k);
            bool need_more = (knn.size() == k && k < points.size() && knn.back().distance_sq <= r2);
            if (!need_more) {
                for (const DpNnsHit& h : knn) {
                    if (h.distance_sq <= r2 && h.index > static_cast<Index>(i)) {
                        pairs.emplace_back(static_cast<Index>(i), h.index);
                    }
                }
                break;
            }
            k = std::min(points.size(), k * 2);
        }
    }
    std::sort(pairs.begin(), pairs.end());
    return pairs;
}

std::vector<double> sample_unsigned_distance_field(
    std::span<const Vec3> surface_samples, std::span<const Vec3> volume_queries,
    const DpNnsOptions& opts) {
    std::vector<double> dists(volume_queries.size(), std::numeric_limits<double>::infinity());
    if (surface_samples.empty() || volume_queries.empty()) return dists;
    const DpNnsIndex index(surface_samples, opts);
    const std::vector<DpNnsHit> hits = index.query_batch(volume_queries, opts.morton_batch_reorder);
    for (std::size_t i = 0; i < hits.size(); ++i) {
        dists[i] = std::sqrt(hits[i].distance_sq);
    }
    return dists;
}

}  // namespace pg
