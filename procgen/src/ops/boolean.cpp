#include "pg/ops/boolean.hpp"

#include "../exact_kernel.hpp"
#include "pg/boxes.hpp"
#include "pg/cleanup.hpp"

#include <Delaunay_psm.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <numeric>
#include <unordered_map>
#include <utility>
#include <vector>

namespace pg {
namespace {

inline const double* ptr(const Vec3& p) noexcept { return &p.x; }
inline GEO::vec3 gvec(const Vec3& p) noexcept { return GEO::vec3(p.x, p.y, p.z); }
inline Vec3 from_gvec(const GEO::vec3& p) noexcept { return {p.x, p.y, p.z}; }

struct Plane {
    Vec3 n;
    double w;
    [[nodiscard]] double dist(Vec3 p) const noexcept { return dot(n, p) - w; }
};

struct Original {
    std::vector<Vec3> verts;
    std::vector<std::uint32_t> vids;
    Vec3 normal{};
    bool planar = false;
    std::uint8_t mesh_id = 0;  // 0 = A, 1 = B
    std::uint32_t first_tri = 0;
    std::uint32_t tri_count = 0;
};

struct InputTri {
    std::uint32_t v[3];       // indices in global deduplicated double vertex pool V
    std::uint32_t origin;     // index in originals
    std::uint32_t global_id;  // unique across A and B (for edge-plane intersection cache)
    Aabb box;
};

struct Vec3BitsHash {
    std::size_t operator()(const Vec3& p) const noexcept {
        auto norm_bits = [](double d) noexcept -> std::uint64_t {
            if (d == 0.0) return 0;  // canonicalize -0.0 and +0.0
            std::uint64_t b = 0;
            std::memcpy(&b, &d, sizeof(double));
            return b;
        };
        std::uint64_t h = norm_bits(p.x);
        h ^= norm_bits(p.y) + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        h ^= norm_bits(p.z) + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        return static_cast<std::size_t>(h);
    }
};

struct Vec3BitsEq {
    bool operator()(const Vec3& a, const Vec3& b) const noexcept {
        return a.x == b.x && a.y == b.y && a.z == b.z;
    }
};

// Fast conservative floating-point filter before exact GEO::PCK::aligned_3d.
inline bool non_collinear_3d(const Vec3& a, const Vec3& b, const Vec3& c) noexcept {
    const Vec3 u = b - a, v = c - a;
    const Vec3 cr = cross(u, v);
    const double u2 = dot(u, u), v2 = dot(v, v);
    if (dot(cr, cr) > 1e-20 * u2 * v2 && u2 > 0.0 && v2 > 0.0) return true;
    return !GEO::PCK::aligned_3d(ptr(a), ptr(b), ptr(c));
}

// Triangulate a polygon into strictly non-degenerate 3D triangles (!PCK::aligned_3d)
// while preserving all boundary half-edges (no T-junctions on collinear stitch vertices).
void triangulate_face(std::span<const std::uint32_t> fv, const std::vector<Vec3>& V, Vec3 face_n,
                      std::vector<std::array<std::uint32_t, 3>>& out_tris) {
    out_tris.clear();
    // Clean consecutive duplicate vertex IDs if any.
    std::vector<std::uint32_t> ring;
    ring.reserve(fv.size());
    for (std::uint32_t id : fv) {
        if (ring.empty() || ring.back() != id) ring.push_back(id);
    }
    while (ring.size() > 1 && ring.front() == ring.back()) ring.pop_back();
    if (ring.size() < 3) return;

    if (ring.size() == 3) {
        if (non_collinear_3d(V[ring[0]], V[ring[1]], V[ring[2]])) {
            out_tris.push_back({ring[0], ring[1], ring[2]});
        }
        return;
    }

    // Fast path for 4-vertex quads with no collinear triple: split along shorter diagonal.
    if (ring.size() == 4) {
        const std::uint32_t a = ring[0], b = ring[1], c = ring[2], d = ring[3];
        if (non_collinear_3d(V[a], V[b], V[c]) && non_collinear_3d(V[a], V[c], V[d])) {
            out_tris.push_back({a, b, c});
            out_tris.push_back({a, c, d});
            return;
        }
        if (non_collinear_3d(V[b], V[c], V[d]) && non_collinear_3d(V[b], V[d], V[a])) {
            out_tris.push_back({b, c, d});
            out_tris.push_back({b, d, a});
            return;
        }
    }

    // General polygon (including collinear stitch vertices along edges):
    // exact 2D ear-clipping on the orientation-preserving dominant plane of face_n.
    const double ax = std::abs(face_n.x), ay = std::abs(face_n.y), az = std::abs(face_n.z);
    int u_ax = 0, v_ax = 1;
    if (ax >= ay && ax >= az) {
        u_ax = face_n.x > 0.0 ? 1 : 2;
        v_ax = face_n.x > 0.0 ? 2 : 1;
    } else if (ay >= az) {
        u_ax = face_n.y > 0.0 ? 2 : 0;
        v_ax = face_n.y > 0.0 ? 0 : 2;
    } else {
        u_ax = face_n.z > 0.0 ? 0 : 1;
        v_ax = face_n.z > 0.0 ? 1 : 0;
    }

    auto p2 = [&](std::uint32_t vid) noexcept -> std::array<double, 2> {
        return {V[vid][u_ax], V[vid][v_ax]};
    };
    auto o2d = [&](std::uint32_t i0, std::uint32_t i1, std::uint32_t i2) noexcept -> int {
        const auto a = p2(i0), b = p2(i1), c = p2(i2);
        return static_cast<int>(GEO::PCK::orient_2d(a.data(), b.data(), c.data()));
    };

    // O(k) fast path for strictly convex polygons (e.g. cylinder / prism caps).
    {
        bool strictly_convex = true;
        const std::size_t rk = ring.size();
        for (std::size_t i = 0; i < rk; ++i) {
            if (o2d(ring[(i + rk - 1) % rk], ring[i], ring[(i + 1) % rk]) <= 0) {
                strictly_convex = false;
                break;
            }
        }
        if (strictly_convex) {
            for (std::size_t i = 1; i + 1 < rk; ++i) {
                out_tris.push_back({ring[0], ring[i], ring[i + 1]});
            }
            return;
        }
    }

    std::vector<std::size_t> idx(ring.size());
    std::iota(idx.begin(), idx.end(), std::size_t{0});

    while (idx.size() > 3) {
        const std::size_t k = idx.size();
        std::size_t chosen = k;
        double best_qual = -1.0;
        for (std::size_t i = 0; i < k; ++i) {
            const std::uint32_t va = ring[idx[(i + k - 1) % k]];
            const std::uint32_t vb = ring[idx[i]];
            const std::uint32_t vc = ring[idx[(i + 1) % k]];
            if (o2d(va, vb, vc) <= 0) continue;
            bool blocked = false;
            for (std::size_t j = 0; j < k; ++j) {
                const std::uint32_t vq = ring[idx[j]];
                if (vq == va || vq == vb || vq == vc) continue;
                if (o2d(va, vb, vq) >= 0 && o2d(vb, vc, vq) >= 0 && o2d(vc, va, vq) >= 0) {
                    blocked = true;
                    break;
                }
            }
            if (blocked) continue;
            const auto pa = p2(va), pb = p2(vb), pc = p2(vc);
            const double area2 = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0]);
            const double perim2 =
                ((pb[0] - pa[0]) * (pb[0] - pa[0]) + (pb[1] - pa[1]) * (pb[1] - pa[1])) +
                ((pc[0] - pb[0]) * (pc[0] - pb[0]) + (pc[1] - pb[1]) * (pc[1] - pb[1])) +
                ((pa[0] - pc[0]) * (pa[0] - pc[0]) + (pa[1] - pc[1]) * (pa[1] - pc[1]));
            const double qual = perim2 > 0.0 ? area2 / perim2 : 0.0;
            if (qual > best_qual) {
                best_qual = qual;
                chosen = i;
            }
        }
        if (chosen == k) {
            // Fallback for heavily twisted non-planar rings: fan remaining non-collinear triples.
            for (std::size_t i = 1; i + 1 < k; ++i) {
                const std::uint32_t va = ring[idx[0]], vb = ring[idx[i]], vc = ring[idx[i + 1]];
                if (!GEO::PCK::aligned_3d(ptr(V[va]), ptr(V[vb]), ptr(V[vc]))) {
                    out_tris.push_back({va, vb, vc});
                }
            }
            return;
        }
        const std::uint32_t va = ring[idx[(chosen + k - 1) % k]];
        const std::uint32_t vb = ring[idx[chosen]];
        const std::uint32_t vc = ring[idx[(chosen + 1) % k]];
        out_tris.push_back({va, vb, vc});
        idx.erase(idx.begin() + static_cast<std::ptrdiff_t>(chosen));
    }
    if (idx.size() == 3) {
        const std::uint32_t va = ring[idx[0]], vb = ring[idx[1]], vc = ring[idx[2]];
        if (!GEO::PCK::aligned_3d(ptr(V[va]), ptr(V[vb]), ptr(V[vc]))) {
            out_tris.push_back({va, vb, vc});
        }
    }
}

void load_mesh_tris(const Mesh& m, std::uint8_t mesh_id, double plane_eps, std::vector<Vec3>& V,
                    std::unordered_map<Vec3, std::uint32_t, Vec3BitsHash, Vec3BitsEq>& vmap,
                    std::vector<Original>& originals, std::vector<InputTri>& tris, std::uint32_t& next_global_tri_id) {
    std::vector<std::uint32_t> m_to_v(m.vertex_count());
    for (std::size_t i = 0; i < m.vertex_count(); ++i) {
        const Vec3 p = m.positions[i];
        const auto [it, inserted] = vmap.emplace(p, static_cast<std::uint32_t>(V.size()));
        if (inserted) V.push_back(p);
        m_to_v[i] = it->second;
    }

    struct AxisPlane { int ax; double c; std::uint32_t pid; };
    std::vector<AxisPlane> axis_planes;

    std::vector<std::uint32_t> fv_ids;
    std::vector<std::array<std::uint32_t, 3>> f_tris;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        const auto origin = static_cast<std::uint32_t>(originals.size());
        Original o;
        o.verts.reserve(fv.size());
        o.vids.reserve(fv.size());
        fv_ids.clear();
        for (Index v : fv) {
            o.verts.push_back(m.positions[v]);
            o.vids.push_back(m_to_v[v]);
            fv_ids.push_back(m_to_v[v]);
        }
        o.normal = face_normal(m, f);
        o.mesh_id = mesh_id;
        o.first_tri = static_cast<std::uint32_t>(tris.size());
        if (o.normal != Vec3{}) {
            const Plane face_plane{o.normal, dot(o.normal, face_centroid(m, f))};
            double dev = 0.0;
            for (Index v : fv) dev = std::max(dev, std::abs(face_plane.dist(m.positions[v])));
            o.planar = dev <= plane_eps;
            triangulate_face(fv_ids, V, o.normal, f_tris);
            std::uint32_t shared_pid = ~0u;
            for (int ax = 0; ax < 3; ++ax) {
                const double c0 = o.verts[0][ax];
                bool all_eq = true;
                for (std::size_t k = 1; k < o.verts.size(); ++k) {
                    if (o.verts[k][ax] != c0) { all_eq = false; break; }
                }
                if (all_eq) {
                    for (const AxisPlane& ap : axis_planes) {
                        if (ap.ax == ax && ap.c == c0) { shared_pid = ap.pid; break; }
                    }
                    if (shared_pid == ~0u) {
                        shared_pid = next_global_tri_id;
                        axis_planes.push_back({ax, c0, shared_pid});
                    }
                    break;
                }
            }
            bool exact_planar = (shared_pid != ~0u) || (f_tris.size() <= 1);
            if (!exact_planar) {
                exact_planar = true;
                const auto& t0 = f_tris[0];
                for (std::uint32_t vid : fv_ids) {
                    if (vid == t0[0] || vid == t0[1] || vid == t0[2]) continue;
                    if (GEO::PCK::orient_3d(ptr(V[t0[0]]), ptr(V[t0[1]]), ptr(V[t0[2]]), ptr(V[vid])) != GEO::ZERO) {
                        exact_planar = false;
                        break;
                    }
                }
            }
            if (shared_pid == ~0u) shared_pid = next_global_tri_id;
            for (const auto& t : f_tris) {
                Aabb tb;
                tb.expand(V[t[0]]);
                tb.expand(V[t[1]]);
                tb.expand(V[t[2]]);
                const std::uint32_t pid = exact_planar ? shared_pid : next_global_tri_id;
                ++next_global_tri_id;
                tris.push_back({{t[0], t[1], t[2]}, origin, pid, tb});
            }
        }
        o.tri_count = static_cast<std::uint32_t>(tris.size()) - o.first_tri;
        originals.push_back(std::move(o));
    }
}

// ---- Exact point pool & exact triangle-triangle intersection ----------------

struct EdgePlaneKey {
    std::uint32_t plane_tri;
    std::uint32_t u_min;
    std::uint32_t u_max;
    bool operator==(const EdgePlaneKey& o) const noexcept {
        return plane_tri == o.plane_tri && u_min == o.u_min && u_max == o.u_max;
    }
};

struct EdgePlaneKeyHash {
    std::size_t operator()(const EdgePlaneKey& k) const noexcept {
        std::uint64_t h = k.plane_tri;
        h ^= (static_cast<std::uint64_t>(k.u_min) << 32 | k.u_max) + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        return static_cast<std::size_t>(h);
    }
};

inline GEO::Sign ratio_cmp_no_fpcr(const GEO::expansion_nt& an, const GEO::expansion_nt& ad,
                                   const GEO::expansion_nt& bn, const GEO::expansion_nt& bd) {
    const GEO::Sign sd = GEO::sign_of_expansion_determinant(an.rep(), ad.rep(), bn.rep(), bd.rep());
    return GEO::Sign(sd * ad.rep().sign() * bd.rep().sign());
}

struct ExactPointPool {
    static constexpr std::size_t kBucketMask = 2047;
    std::vector<GEO::exact::vec3h> exact_pts;
    std::vector<Vec3> approx_pts;
    std::vector<std::uint32_t> orig_v_to_gid;  // ~0u until registered
    std::unordered_map<EdgePlaneKey, std::uint32_t, EdgePlaneKeyHash> edge_plane_cache;

    double inv_cell = 1.0;
    double match_dist2 = 0.0;
    std::vector<std::uint32_t> bucket_head;
    std::vector<std::uint32_t> bucket_next;
    std::vector<std::uint64_t> pt_cell_hash;
    std::vector<GEO::vec3E> tri_normals;
    std::vector<GEO::vec3> plane_p0;
    std::vector<std::uint8_t> has_tri_normal;

    ExactPointPool(const std::vector<Vec3>& V, double diag, std::size_t total_tris)
        : orig_v_to_gid(V.size(), ~0u),
          inv_cell(1.0 / std::max(1e-7 * diag, 1e-290)),
          match_dist2(1e-18 * diag * diag),
          bucket_head(kBucketMask + 1, ~0u),
          tri_normals(total_tris),
          plane_p0(total_tris),
          has_tri_normal(total_tris, 0) {
        exact_pts.reserve(256);
        approx_pts.reserve(256);
        bucket_next.reserve(256);
        pt_cell_hash.reserve(256);
        edge_plane_cache.reserve(256);
    }

    static std::uint64_t hash_cell(std::int64_t x, std::int64_t y, std::int64_t z) noexcept {
        std::uint64_t h = static_cast<std::uint64_t>(x) * 0x9e3779b185ebca87ull;
        h ^= static_cast<std::uint64_t>(y) * 0xc2b2ae3d27d4eb4full + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        h ^= static_cast<std::uint64_t>(z) * 0x165667b19e3779f9ull + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        return h;
    }

    std::uint32_t find_or_insert(GEO::exact::vec3h&& p, const Vec3& approx) {
        const double sx = approx.x * inv_cell, sy = approx.y * inv_cell, sz = approx.z * inv_cell;
        const auto cx = static_cast<std::int64_t>(std::llround(sx));
        const auto cy = static_cast<std::int64_t>(std::llround(sy));
        const auto cz = static_cast<std::int64_t>(std::llround(sz));
        const int dx_lo = (sx - static_cast<double>(cx) < -0.45) ? -1 : 0;
        const int dx_hi = (sx - static_cast<double>(cx) > +0.45) ? +1 : 0;
        const int dy_lo = (sy - static_cast<double>(cy) < -0.45) ? -1 : 0;
        const int dy_hi = (sy - static_cast<double>(cy) > +0.45) ? +1 : 0;
        const int dz_lo = (sz - static_cast<double>(cz) < -0.45) ? -1 : 0;
        const int dz_hi = (sz - static_cast<double>(cz) > +0.45) ? +1 : 0;

        for (int dz = dz_lo; dz <= dz_hi; ++dz) {
            for (int dy = dy_lo; dy <= dy_hi; ++dy) {
                for (int dx = dx_lo; dx <= dx_hi; ++dx) {
                    const std::uint64_t ch = hash_cell(cx + dx, cy + dy, cz + dz);
                    for (std::uint32_t cand = bucket_head[ch & kBucketMask]; cand != ~0u; cand = bucket_next[cand]) {
                        if (pt_cell_hash[cand] != ch) continue;
                        const Vec3 d = approx - approx_pts[cand];
                        if (dot(d, d) > match_dist2) continue;
                        const auto& q = exact_pts[cand];
                        if (ratio_cmp_no_fpcr(p.x, p.w, q.x, q.w) == GEO::ZERO &&
                            ratio_cmp_no_fpcr(p.y, p.w, q.y, q.w) == GEO::ZERO &&
                            ratio_cmp_no_fpcr(p.z, p.w, q.z, q.w) == GEO::ZERO) {
                            return cand;
                        }
                    }
                }
            }
        }
        const auto gid = static_cast<std::uint32_t>(exact_pts.size());
        const std::uint64_t ch = hash_cell(cx, cy, cz);
        const std::size_t slot = ch & kBucketMask;
        exact_pts.push_back(std::move(p));
        approx_pts.push_back(approx);
        pt_cell_hash.push_back(ch);
        bucket_next.push_back(bucket_head[slot]);
        bucket_head[slot] = gid;
        return gid;
    }

    std::uint32_t intern_orig(std::uint32_t vid, const std::vector<Vec3>& V) {
        if (orig_v_to_gid[vid] != ~0u) return orig_v_to_gid[vid];
        GEO::exact::vec3h p(gvec(V[vid]));
        const std::uint32_t gid = find_or_insert(std::move(p), V[vid]);
        orig_v_to_gid[vid] = gid;
        return gid;
    }

    std::uint32_t intern_exact(GEO::exact::vec3h p) {
        p.optimize();
        const Vec3 approx = from_gvec(GEO::PCK::approximate(p));
        return find_or_insert(std::move(p), approx);
    }

    // Exact intersection of segment (V[u0], V[u1]) with plane(T), given orient_3d(T, u0) * orient_3d(T, u1) < 0.
    std::uint32_t plane_edge_isect(const InputTri& T, std::uint32_t u0, std::uint32_t u1, const std::vector<Vec3>& V) {
        if (u0 > u1) std::swap(u0, u1);
        const EdgePlaneKey key{T.global_id, u0, u1};
        const auto cit = edge_plane_cache.find(key);
        if (cit != edge_plane_cache.end()) return cit->second;

        const Vec3& va0 = V[T.v[0]];
        const Vec3& va1 = V[T.v[1]];
        const Vec3& va2 = V[T.v[2]];
        const GEO::vec3 q0 = gvec(V[u0]);
        const GEO::vec3 q1 = gvec(V[u1]);

        // Exact 2-limb shortcut when T lies on an exact axis-aligned coordinate plane X[ax] == c.
        for (int ax = 0; ax < 3; ++ax) {
            const double c = va0[ax];
            if (va1[ax] == c && va2[ax] == c) {
                const GEO::expansion_nt num(GEO::expansion_nt::DIFF, c, q0[static_cast<GEO::coord_index_t>(ax)]);
                const GEO::expansion_nt den(GEO::expansion_nt::DIFF, q1[static_cast<GEO::coord_index_t>(ax)],
                                            q0[static_cast<GEO::coord_index_t>(ax)]);
                const GEO::exact::rational t(num, den);
                GEO::exact::vec3h P = GEO::mix(t, q0, q1);
                const std::uint32_t gid = intern_exact(std::move(P));
                edge_plane_cache.emplace(key, gid);
                return gid;
            }
        }

        if (!has_tri_normal[T.global_id]) {
            const GEO::vec3 p0 = gvec(va0);
            const GEO::vec3 p1 = gvec(va1);
            const GEO::vec3 p2 = gvec(va2);
            plane_p0[T.global_id] = p0;
            tri_normals[T.global_id] = GEO::triangle_normal<GEO::vec3E>(p0, p1, p2);
            has_tri_normal[T.global_id] = 1;
        }
        const GEO::vec3E& N = tri_normals[T.global_id];
        const GEO::expansion_nt num = GEO::dot(GEO::make_vec3<GEO::vec3E>(q0, plane_p0[T.global_id]), N);
        const GEO::expansion_nt den = GEO::dot(GEO::make_vec3<GEO::vec3E>(q0, q1), N);
        const GEO::exact::rational t(num, den);
        GEO::exact::vec3h P = GEO::mix(t, q0, q1);
        const std::uint32_t gid = intern_exact(std::move(P));
        edge_plane_cache.emplace(key, gid);
        return gid;
    }
};

struct CoplanarPartner {
    std::uint32_t opp_tri;
    bool same_normal;
};

struct TriCutData {
    std::vector<std::uint32_t> points;  // isolated global exact point IDs
    std::vector<std::pair<std::uint32_t, std::uint32_t>> segs;  // constraint pairs of global exact point IDs
    std::vector<CoplanarPartner> coplanar;

    [[nodiscard]] bool active() const noexcept {
        return !points.empty() || !segs.empty() || !coplanar.empty();
    }
    void add_point(std::uint32_t gid) { points.push_back(gid); }
    void add_seg(std::uint32_t a, std::uint32_t b) {
        if (a == b) {
            points.push_back(a);
            return;
        }
        if (a > b) std::swap(a, b);
        segs.emplace_back(a, b);
    }
};

inline int orient3d_v(const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& d) noexcept {
    return static_cast<int>(GEO::PCK::orient_3d(ptr(a), ptr(b), ptr(c), ptr(d)));
}

inline int cmp_coord(const GEO::exact::vec3h& a, const GEO::exact::vec3h& b, GEO::coord_index_t ax) {
    return static_cast<int>(ratio_cmp_no_fpcr(a[ax], a.w, b[ax], b.w));
}

inline int cmp_pool_coord(const ExactPointPool& pool, std::uint32_t ga, std::uint32_t gb, GEO::coord_index_t ax) {
    if (ga == gb) return 0;
    const int iax = static_cast<int>(ax);
    const double da = pool.approx_pts[ga][iax];
    const double db = pool.approx_pts[gb][iax];
    const double bound = 64.0 * 1.1102230246251565e-16 * (std::abs(da) + std::abs(db)) + 1e-300;
    if (da - db > bound) return 1;
    if (da - db < -bound) return -1;
    return cmp_coord(pool.exact_pts[ga], pool.exact_pts[gb], ax);
}

// Exact 2D Cyrus-Beck clip of 3D coplanar segment (V[u0], V[u1]) against closed CCW 2D triangle `ccw_tri`
// on axes (u_ax, v_ax). Adds resulting segment or point to `cuts_self` and `cuts_other`.
void clip_coplanar_edge(std::uint32_t u0, std::uint32_t u1, const std::array<std::uint32_t, 3>& ccw_tri,
                        int u_ax, int v_ax, ExactPointPool& pool, const std::vector<Vec3>& V,
                        TriCutData& cuts_target, TriCutData& cuts_source) {
    if (u0 > u1) std::swap(u0, u1);  // canonicalize edge direction so shared edges compute identical vec3h!
    const double q0_2d[2] = {V[u0][u_ax], V[u0][v_ax]};
    const double q1_2d[2] = {V[u1][u_ax], V[u1][v_ax]};
    const GEO::vec2 gq0(q0_2d[0], q0_2d[1]);
    const GEO::vec2 gq1(q1_2d[0], q1_2d[1]);

    // Rational parameter bounds t in [0, 1], maintained with strictly POSITIVE denominators.
    GEO::expansion_nt lo_n(0.0), lo_d(1.0);
    GEO::expansion_nt hi_n(1.0), hi_d(1.0);
    bool lo_is_0 = true, lo_is_1 = false;
    bool hi_is_1 = true, hi_is_0 = false;

    for (std::size_t m = 0; m < 3; ++m) {
        const Vec3& pm3 = V[ccw_tri[m]];
        const Vec3& pn3 = V[ccw_tri[(m + 1) % 3]];
        const double em0[2] = {pm3[u_ax], pm3[v_ax]};
        const double em1[2] = {pn3[u_ax], pn3[v_ax]};

        const int s0 = static_cast<int>(GEO::PCK::orient_2d(em0, em1, q0_2d));
        const int s1 = static_cast<int>(GEO::PCK::orient_2d(em0, em1, q1_2d));
        if (s0 < 0 && s1 < 0) return;
        if (s0 >= 0 && s1 >= 0) continue;

        const GEO::vec2 gm0(em0[0], em0[1]);
        const GEO::vec2 gm1(em1[0], em1[1]);
        const GEO::vec2E edge_dir = GEO::make_vec2<GEO::vec2E>(gm0, gm1);
        const GEO::expansion_nt a0 = GEO::det(edge_dir, GEO::make_vec2<GEO::vec2E>(gm0, gq0));
        const GEO::expansion_nt a1 = GEO::det(edge_dir, GEO::make_vec2<GEO::vec2E>(gm0, gq1));
        // Signed area a(t) = (1 - t)*a0 + t*a1 = 0 => t = a0 / (a0 - a1).
        if (s0 < 0) {
            // Entering half-plane (s0 < 0, s1 >= 0): a0 < 0, a1 >= 0 => num = -a0 > 0, den = a1 - a0 > 0.
            if (s1 == 0) {
                lo_n = GEO::expansion_nt(1.0);
                lo_d = GEO::expansion_nt(1.0);
                lo_is_0 = false;
                lo_is_1 = true;
            } else if (!lo_is_1) {
                GEO::expansion_nt tn = a0;
                tn.negate();
                GEO::expansion_nt td = a1 - a0;
                tn.optimize();
                td.optimize();
                if (lo_is_0 || GEO::Numeric::ratio_compare(tn, td, lo_n, lo_d) == GEO::POSITIVE) {
                    lo_n = std::move(tn);
                    lo_d = std::move(td);
                    lo_is_0 = false;
                }
            }
        } else {
            // Exiting half-plane (s0 >= 0, s1 < 0): a0 >= 0, a1 < 0 => num = a0 >= 0, den = a0 - a1 > 0.
            if (s0 == 0) {
                hi_n = GEO::expansion_nt(0.0);
                hi_d = GEO::expansion_nt(1.0);
                hi_is_1 = false;
                hi_is_0 = true;
            } else if (!hi_is_0) {
                GEO::expansion_nt tn = a0;
                GEO::expansion_nt td = a0 - a1;
                tn.optimize();
                td.optimize();
                if (hi_is_1 || GEO::Numeric::ratio_compare(tn, td, hi_n, hi_d) == GEO::NEGATIVE) {
                    hi_n = std::move(tn);
                    hi_d = std::move(td);
                    hi_is_1 = false;
                }
            }
        }
    }

    const GEO::Sign cmp = GEO::Numeric::ratio_compare(lo_n, lo_d, hi_n, hi_d);
    if (cmp == GEO::POSITIVE) return;

    auto eval_pt = [&](bool is_0, bool is_1, const GEO::expansion_nt& n, const GEO::expansion_nt& d) -> std::uint32_t {
        if (is_0) return pool.intern_orig(u0, V);
        if (is_1) return pool.intern_orig(u1, V);
        const GEO::exact::rational t(n, d);
        return pool.intern_exact(GEO::mix(t, gvec(V[u0]), gvec(V[u1])));
    };

    const std::uint32_t gid0 = eval_pt(lo_is_0, lo_is_1, lo_n, lo_d);
    if (cmp == GEO::ZERO) {
        cuts_target.add_point(gid0);
        cuts_source.add_point(gid0);
        return;
    }
    const std::uint32_t gid1 = eval_pt(hi_is_0, hi_is_1, hi_n, hi_d);
    cuts_target.add_seg(gid0, gid1);
    cuts_source.add_seg(gid0, gid1);
}

void intersect_triangle_pair(std::uint32_t ia, std::uint32_t ib, const std::vector<InputTri>& tris_a,
                             const std::vector<InputTri>& tris_b, const std::vector<Vec3>& V, ExactPointPool& pool,
                             std::vector<TriCutData>& cuts_a, std::vector<TriCutData>& cuts_b) {
    const InputTri& ta = tris_a[ia];
    const InputTri& tb = tris_b[ib];

    const Vec3& a0 = V[ta.v[0]];
    const Vec3& a1 = V[ta.v[1]];
    const Vec3& a2 = V[ta.v[2]];
    const Vec3& b0 = V[tb.v[0]];
    const Vec3& b1 = V[tb.v[1]];
    const Vec3& b2 = V[tb.v[2]];

    const int sb[3] = {orient3d_v(a0, a1, a2, b0), orient3d_v(a0, a1, a2, b1), orient3d_v(a0, a1, a2, b2)};
    if ((sb[0] > 0 && sb[1] > 0 && sb[2] > 0) || (sb[0] < 0 && sb[1] < 0 && sb[2] < 0)) return;

    const int sa[3] = {orient3d_v(b0, b1, b2, a0), orient3d_v(b0, b1, b2, a1), orient3d_v(b0, b1, b2, a2)};
    if ((sa[0] > 0 && sa[1] > 0 && sa[2] > 0) || (sa[0] < 0 && sa[1] < 0 && sa[2] < 0)) return;

    // Coplanar triangles.
    if (sb[0] == 0 && sb[1] == 0 && sb[2] == 0) {
        const GEO::coord_index_t dom = GEO::PCK::triangle_normal_axis(gvec(a0), gvec(a1), gvec(a2));
        const int u_ax = (static_cast<int>(dom) + 1) % 3;
        const int v_ax = (static_cast<int>(dom) + 2) % 3;

        std::array<std::uint32_t, 3> qa{ta.v[0], ta.v[1], ta.v[2]};
        std::array<std::uint32_t, 3> qb{tb.v[0], tb.v[1], tb.v[2]};
        auto o2 = [&](std::uint32_t i, std::uint32_t j, std::uint32_t k) noexcept -> int {
            const double pi[2] = {V[i][u_ax], V[i][v_ax]};
            const double pj[2] = {V[j][u_ax], V[j][v_ax]};
            const double pk[2] = {V[k][u_ax], V[k][v_ax]};
            return static_cast<int>(GEO::PCK::orient_2d(pi, pj, pk));
        };

        const int opa = o2(qa[0], qa[1], qa[2]);
        const int opb = o2(qb[0], qb[1], qb[2]);
        if (opa == 0 || opb == 0) return;
        if (opa < 0) std::swap(qa[1], qa[2]);
        if (opb < 0) std::swap(qb[1], qb[2]);

        // Closed SAT check: if strictly separated by an edge (< 0 for all 3), disjoint!
        // Open SAT check: if separated with <= 0 for all 3, open interiors do not overlap.
        bool open_disjoint = false;
        for (std::size_t e = 0; e < 3; ++e) {
            const int c0 = o2(qa[e], qa[(e + 1) % 3], qb[0]);
            const int c1 = o2(qa[e], qa[(e + 1) % 3], qb[1]);
            const int c2 = o2(qa[e], qa[(e + 1) % 3], qb[2]);
            if (c0 < 0 && c1 < 0 && c2 < 0) return;
            if (c0 <= 0 && c1 <= 0 && c2 <= 0) open_disjoint = true;
        }
        for (std::size_t e = 0; e < 3; ++e) {
            const int c0 = o2(qb[e], qb[(e + 1) % 3], qa[0]);
            const int c1 = o2(qb[e], qb[(e + 1) % 3], qa[1]);
            const int c2 = o2(qb[e], qb[(e + 1) % 3], qa[2]);
            if (c0 < 0 && c1 < 0 && c2 < 0) return;
            if (c0 <= 0 && c1 <= 0 && c2 <= 0) open_disjoint = true;
        }

        if (!open_disjoint) {
            const bool same_n = (opa == opb);
            cuts_a[ia].coplanar.push_back({ib, same_n});
            cuts_b[ib].coplanar.push_back({ia, same_n});
        }

        for (std::size_t e = 0; e < 3; ++e) {
            clip_coplanar_edge(tb.v[e], tb.v[(e + 1) % 3], qa, u_ax, v_ax, pool, V, cuts_a[ia], cuts_b[ib]);
            clip_coplanar_edge(ta.v[e], ta.v[(e + 1) % 3], qb, u_ax, v_ax, pool, V, cuts_b[ib], cuts_a[ia]);
        }
        return;
    }

    // Non-coplanar triangles: collect ONLY the exact piercing points in T_A cap T_B (zero throwaway expansions!).
    auto pt_in_tri_3d = [](const Vec3& p, const Vec3 t[3]) noexcept -> bool {
        const GEO::coord_index_t dom = GEO::PCK::triangle_normal_axis(gvec(t[0]), gvec(t[1]), gvec(t[2]));
        const int u = (static_cast<int>(dom) + 1) % 3;
        const int v = (static_cast<int>(dom) + 2) % 3;
        const double q[2] = {p[u], p[v]};
        const double q0[2] = {t[0][u], t[0][v]};
        const double q1[2] = {t[1][u], t[1][v]};
        const double q2[2] = {t[2][u], t[2][v]};
        const int s0 = static_cast<int>(GEO::PCK::orient_2d(q0, q1, q));
        const int s1 = static_cast<int>(GEO::PCK::orient_2d(q1, q2, q));
        const int s2 = static_cast<int>(GEO::PCK::orient_2d(q2, q0, q));
        return (s0 >= 0 && s1 >= 0 && s2 >= 0) || (s0 <= 0 && s1 <= 0 && s2 <= 0);
    };

    const Vec3 tri_a_pts[3] = {a0, a1, a2};
    const Vec3 tri_b_pts[3] = {b0, b1, b2};
    std::uint32_t hits[12];
    int nhits = 0;

    for (int i = 0; i < 3; ++i) {
        if (sa[i] == 0 && pt_in_tri_3d(tri_a_pts[i], tri_b_pts)) {
            hits[nhits++] = pool.intern_orig(ta.v[i], V);
        }
        if (sb[i] == 0 && pt_in_tri_3d(tri_b_pts[i], tri_a_pts)) {
            hits[nhits++] = pool.intern_orig(tb.v[i], V);
        }
        const int j = (i + 1) % 3;
        if (sa[i] * sa[j] < 0) {
            const int o0 = orient3d_v(tri_a_pts[i], tri_a_pts[j], b0, b1);
            const int o1 = orient3d_v(tri_a_pts[i], tri_a_pts[j], b1, b2);
            if (o0 * o1 >= 0) {
                const int o2 = orient3d_v(tri_a_pts[i], tri_a_pts[j], b2, b0);
                if ((o0 >= 0 && o1 >= 0 && o2 >= 0) || (o0 <= 0 && o1 <= 0 && o2 <= 0)) {
                    hits[nhits++] = pool.plane_edge_isect(tb, ta.v[i], ta.v[j], V);
                }
            }
        }
        if (sb[i] * sb[j] < 0) {
            const int o0 = orient3d_v(tri_b_pts[i], tri_b_pts[j], a0, a1);
            const int o1 = orient3d_v(tri_b_pts[i], tri_b_pts[j], a1, a2);
            if (o0 * o1 >= 0) {
                const int o2 = orient3d_v(tri_b_pts[i], tri_b_pts[j], a2, a0);
                if ((o0 >= 0 && o1 >= 0 && o2 >= 0) || (o0 <= 0 && o1 <= 0 && o2 <= 0)) {
                    hits[nhits++] = pool.plane_edge_isect(ta, tb.v[i], tb.v[j], V);
                }
            }
        }
    }
    if (nhits == 0) return;

    std::uint32_t first_gid = hits[0], second_gid = hits[0];
    GEO::coord_index_t ax = 0;
    bool have_span = false;
    for (int h = 1; h < nhits && !have_span; ++h) {
        if (hits[h] == first_gid) continue;
        for (GEO::coord_index_t k = 0; k < 3; ++k) {
            if (cmp_pool_coord(pool, first_gid, hits[h], k) != 0) {
                ax = k;
                second_gid = hits[h];
                have_span = true;
                break;
            }
        }
    }
    if (!have_span) {
        cuts_a[ia].add_point(first_gid);
        cuts_b[ib].add_point(first_gid);
        return;
    }

    std::uint32_t lo = first_gid, hi = second_gid;
    if (cmp_pool_coord(pool, lo, hi, ax) > 0) std::swap(lo, hi);
    for (int h = 1; h < nhits; ++h) {
        const std::uint32_t g = hits[h];
        if (g == lo || g == hi) continue;
        if (cmp_pool_coord(pool, g, lo, ax) < 0) lo = g;
        else if (cmp_pool_coord(pool, g, hi, ax) > 0) hi = g;
    }
    cuts_a[ia].add_seg(lo, hi);
    cuts_b[ib].add_seg(lo, hi);
}

// ---- Ray-parity BVH + exact orient_3d_SOS classifier ---------------------

struct RayBvh {
    struct Node {
        Aabb box;
        std::uint32_t left = 0;   // if count > 0, first index in order
        std::uint32_t count = 0;  // 0 = internal (children at left, left + 1)
    };
    const std::vector<InputTri>* tris = nullptr;
    const std::vector<Vec3>* V = nullptr;
    Aabb total_bounds;
    std::vector<std::uint32_t> order;
    std::vector<Node> nodes;

    void build(const std::vector<InputTri>& t, const std::vector<Vec3>& verts, const Aabb& query_bounds) {
        tris = &t;
        V = &verts;
        order.clear();
        nodes.clear();
        total_bounds = Aabb{};
        if (t.empty() || !query_bounds.valid()) return;
        for (std::size_t i = 0; i < t.size(); ++i) {
            const Aabb& tb = t[i].box;
            total_bounds.expand(tb.lo);
            total_bounds.expand(tb.hi);
            // Only triangles overlapping the +X ray shadow of query_bounds can ever be hit.
            if (tb.hi.x >= query_bounds.lo.x &&
                tb.hi.y >= query_bounds.lo.y && tb.lo.y <= query_bounds.hi.y &&
                tb.hi.z >= query_bounds.lo.z && tb.lo.z <= query_bounds.hi.z) {
                order.push_back(static_cast<std::uint32_t>(i));
            }
        }
        if (order.empty()) return;
        nodes.reserve(order.size() * 2);
        nodes.push_back(Node{});
        build_rec(0, 0, static_cast<std::uint32_t>(order.size()));
    }

    void build_rec(std::uint32_t ni, std::uint32_t begin, std::uint32_t end) {
        Aabb b, cb;
        for (std::uint32_t i = begin; i < end; ++i) {
            const Aabb& tb = (*tris)[order[i]].box;
            b.expand(tb.lo);
            b.expand(tb.hi);
            cb.expand(tb.center());
        }
        nodes[ni].box = b;
        const std::uint32_t cnt = end - begin;
        if (cnt <= 8) {
            nodes[ni].left = begin;
            nodes[ni].count = cnt;
            return;
        }
        const Vec3 ext = cb.extent();
        const int axis = (ext.x >= ext.y && ext.x >= ext.z) ? 0 : (ext.y >= ext.z ? 1 : 2);
        const std::uint32_t mid = begin + cnt / 2;
        std::nth_element(order.begin() + begin, order.begin() + mid, order.begin() + end,
                         [&](std::uint32_t x, std::uint32_t y) {
                             return (*tris)[x].box.center()[axis] < (*tris)[y].box.center()[axis];
                         });
        const auto left_child = static_cast<std::uint32_t>(nodes.size());
        nodes.push_back(Node{});
        nodes.push_back(Node{});
        nodes[ni].left = left_child;
        nodes[ni].count = 0;
        build_rec(left_child, begin, mid);
        build_rec(left_child + 1, mid, end);
    }

    // Exact point-in-closed-manifold test via +X ray parity with PCK::orient_3d_SOS (SOS_LEXICO).
    [[nodiscard]] bool contains_point(Vec3 p, double diag) const noexcept {
        if (nodes.empty()) return false;
        if (p.x < total_bounds.lo.x || p.x > total_bounds.hi.x ||
            p.y < total_bounds.lo.y || p.y > total_bounds.hi.y ||
            p.z < total_bounds.lo.z || p.z > total_bounds.hi.z) {
            return false;
        }
        const Vec3 q{total_bounds.hi.x + diag + 1.0, p.y, p.z};

        std::uint32_t crossings = 0;
        std::uint32_t stack[64];
        int sp_top = 0;
        stack[sp_top++] = 0;
        while (sp_top > 0) {
            const Node& nd = nodes[stack[--sp_top]];
            if (nd.box.hi.x < p.x || nd.box.lo.y > p.y || nd.box.hi.y < p.y ||
                nd.box.lo.z > p.z || nd.box.hi.z < p.z) {
                continue;
            }
            if (nd.count == 0) {
                stack[sp_top++] = nd.left;
                stack[sp_top++] = nd.left + 1;
                continue;
            }
            for (std::uint32_t k = 0; k < nd.count; ++k) {
                const InputTri& T = (*tris)[order[nd.left + k]];
                if (T.box.hi.x < p.x || T.box.lo.y > p.y || T.box.hi.y < p.y ||
                    T.box.lo.z > p.z || T.box.hi.z < p.z) {
                    continue;
                }
                const Vec3& a = (*V)[T.v[0]];
                const Vec3& b = (*V)[T.v[1]];
                const Vec3& c = (*V)[T.v[2]];
                const GEO::Sign sp = GEO::PCK::orient_3d_SOS(ptr(a), ptr(b), ptr(c), ptr(p));
                const GEO::Sign sq = GEO::PCK::orient_3d_SOS(ptr(a), ptr(b), ptr(c), ptr(q));
                if (sp == sq) continue;
                if (GEO::PCK::orient_3d_SOS(ptr(p), ptr(q), ptr(a), ptr(b)) == sq &&
                    GEO::PCK::orient_3d_SOS(ptr(p), ptr(q), ptr(b), ptr(c)) == sq &&
                    GEO::PCK::orient_3d_SOS(ptr(p), ptr(q), ptr(c), ptr(a)) == sq) {
                    ++crossings;
                }
            }
        }
        return (crossings & 1u) != 0u;
    }
};

// Zero-allocation exact 2D Constrained Triangulation built on GEO::CDTBase2d:
// sound IEEE-754 floating-point filter on approximated coordinates, falling back
// to exact GEO::PCK::orient_2d(vec2HE) on near-collinear / degenerate triples.
class FastExactCDT2d final : public GEO::CDTBase2d {
public:
    FastExactCDT2d() {
        set_delaunay(false);
        exact_incircle_ = false;
        exact_intersections_ = true;
    }

    void clear() override {
        exact_.clear();
        approx_.clear();
        id_.clear();
        constraints_.clear();
        max_coord_ = 0.0;
        GEO::CDTBase2d::clear();
    }

    void init_triangle(const GEO::exact::vec2h& p0, const std::array<double, 2>& a0, std::uint32_t id0,
                       const GEO::exact::vec2h& p1, const std::array<double, 2>& a1, std::uint32_t id1,
                       const GEO::exact::vec2h& p2, const std::array<double, 2>& a2, std::uint32_t id2) {
        clear();
        push_pt(p0, a0, id0);
        push_pt(p1, a1, id1);
        push_pt(p2, a2, id2);
        GEO::CDTBase2d::create_enclosing_triangle(0, 1, 2);
    }

    GEO::index_t insert_pt(const GEO::exact::vec2h& p, const std::array<double, 2>& ap, std::uint32_t gid) {
        push_pt(p, ap, gid);
        const GEO::index_t idx = static_cast<GEO::index_t>(exact_.size() - 1);
        const GEO::index_t lv = GEO::CDTBase2d::insert(idx);
        if (exact_.size() > nv()) {
            exact_.pop_back();
            approx_.pop_back();
            id_.pop_back();
        } else if (id_[lv] == ~0u) {
            id_[lv] = gid;
        }
        return lv;
    }

    void add_constraint(GEO::index_t v0, GEO::index_t v1) {
        constraints_.push_back({v0, v1});
        GEO::CDTBase2d::insert_constraint(v0, v1);
    }

    [[nodiscard]] const GEO::exact::vec2h& point(GEO::index_t v) const noexcept { return exact_[v]; }
    [[nodiscard]] std::array<double, 2> approx(GEO::index_t v) const noexcept { return approx_[v]; }
    [[nodiscard]] std::uint32_t vertex_id(GEO::index_t v) const noexcept { return id_[v]; }

    void save(const std::string&) const override {}

protected:
    GEO::Sign orient2d(GEO::index_t i, GEO::index_t j, GEO::index_t k) const override {
        const auto& pi = approx_[i];
        const auto& pj = approx_[j];
        const auto& pk = approx_[k];
        const double ux = pj[0] - pi[0], uy = pj[1] - pi[1];
        const double vx = pk[0] - pi[0], vy = pk[1] - pi[1];
        const double det = ux * vy - uy * vx;
        const double span = std::abs(ux) + std::abs(uy) + std::abs(vx) + std::abs(vy);
        const double bound =
            128.0 * 1.1102230246251565e-16 * max_coord_ * span +
            4096.0 * 1.232595164407831e-32 * max_coord_ * max_coord_;
        if (det > bound) return GEO::POSITIVE;
        if (det < -bound) return GEO::NEGATIVE;
        const auto& a = exact_[i];
        const auto& b = exact_[j];
        const auto& c = exact_[k];
        const GEO::Sign sd = GEO::sign_of_expansion_determinant(
            a.x.rep(), a.y.rep(), a.w.rep(),
            b.x.rep(), b.y.rep(), b.w.rep(),
            c.x.rep(), c.y.rep(), c.w.rep());
        return GEO::Sign(sd * a.w.rep().sign() * b.w.rep().sign() * c.w.rep().sign());
    }

    GEO::Sign incircle(GEO::index_t i, GEO::index_t j, GEO::index_t k, GEO::index_t l) const override {
        return GEO::PCK::incircle_2d_SOS(exact_[i], exact_[j], exact_[k], exact_[l]);
    }

    GEO::index_t create_intersection(GEO::index_t e1, GEO::index_t i, GEO::index_t j,
                                     GEO::index_t e2, GEO::index_t k, GEO::index_t l) override {
        (void)i;
        (void)j;
        (void)k;
        (void)l;
        const GEO::index_t a = constraints_[e1].first, b = constraints_[e1].second;
        const GEO::index_t c = constraints_[e2].first, d = constraints_[e2].second;
        const GEO::exact::vec2h U = exact_[b] - exact_[a];
        const GEO::exact::vec2h W = exact_[d] - exact_[c];
        const GEO::exact::vec2h D = exact_[c] - exact_[a];
        const GEO::exact::rational t(GEO::det2x2(D.x, D.y, W.x, W.y) * U.w,
                                     GEO::det2x2(U.x, U.y, W.x, W.y) * D.w);
        GEO::exact::vec2h P = GEO::mix(t, exact_[a], exact_[b]);
        P.optimize();
        const GEO::vec2 ap = GEO::PCK::approximate(P);
        push_pt(P, {ap.x, ap.y}, ~0u);
        const auto x = static_cast<GEO::index_t>(exact_.size() - 1);
        v2T_.push_back(GEO::NO_INDEX);
        ++nv_;
        return x;
    }

private:
    void push_pt(const GEO::exact::vec2h& p, const std::array<double, 2>& ap, std::uint32_t gid) {
        exact_.push_back(p);
        approx_.push_back(ap);
        id_.push_back(gid);
        max_coord_ = std::max({max_coord_, std::abs(ap[0]), std::abs(ap[1])});
    }

    std::vector<GEO::exact::vec2h> exact_;
    std::vector<std::array<double, 2>> approx_;
    std::vector<std::uint32_t> id_;
    std::vector<std::pair<GEO::index_t, GEO::index_t>> constraints_;
    double max_coord_ = 0.0;
};

enum class PatchClass : std::uint8_t { Outside, Inside, CoplanarSame, CoplanarOpp };

struct PatchDecision {
    bool keep = false;
    bool flip = false;
};

PatchDecision decide_patch(std::uint8_t mesh_id, PatchClass cls, BooleanOp op) noexcept {
    if (mesh_id == 0) {  // Mesh A
        switch (op) {
            case BooleanOp::Union:
                return {cls == PatchClass::Outside || cls == PatchClass::CoplanarSame, false};
            case BooleanOp::Intersection:
                return {cls == PatchClass::Inside || cls == PatchClass::CoplanarSame, false};
            case BooleanOp::Difference:
                return {cls == PatchClass::Outside || cls == PatchClass::CoplanarOpp, false};
        }
    } else {  // Mesh B
        switch (op) {
            case BooleanOp::Union:
                return {cls == PatchClass::Outside, false};
            case BooleanOp::Intersection:
                return {cls == PatchClass::Inside, false};
            case BooleanOp::Difference:
                return {cls == PatchClass::Inside, true};
        }
    }
    return {};
}

struct EmittedTri {
    Vec3 p[3];
    std::uint32_t v_orig[3];  // ~0u when synthesized by CDT
    std::uint32_t origin;
};

// Classify uncut triangles of `self_tris` in connected components across shared mesh edges,
// then corefine and classify active triangles via FastExactCDT2d.
void process_shell(std::uint8_t mesh_id, const std::vector<InputTri>& self_tris,
                   const std::vector<InputTri>& opp_tris, const std::vector<TriCutData>& self_cuts,
                   const RayBvh& opp_bvh, const std::vector<Vec3>& V, const ExactPointPool& pool,
                   const std::unordered_map<Vec3, std::uint32_t, Vec3BitsHash, Vec3BitsEq>& vmap,
                   BooleanOp op, double diag, std::vector<std::uint8_t>& origin_was_cut,
                   std::vector<std::uint32_t>& uncut_origin_kept_tris, std::vector<EmittedTri>& emitted) {
    const std::size_t nt = self_tris.size();
    if (nt == 0) return;

    auto safe_interior_point = [&](Vec3 p0, Vec3 p1, Vec3 p2) noexcept -> Vec3 {
        Vec3 c = (p0 + p1 + p2) * (1.0 / 3.0);
        if (vmap.find(c) != vmap.end()) {
            c = p0 * 0.2917 + p1 * 0.3371 + p2 * (1.0 - 0.2917 - 0.3371);
        }
        return c;
    };

    // 1. Disjoint-set union over inactive (!active()) triangles across shared mesh edges (flat sort, zero map allocs).
    std::vector<std::int8_t> uncut_inside(nt, -1);
    {
        std::vector<std::uint32_t> dsu(nt);
        std::iota(dsu.begin(), dsu.end(), 0u);
        auto find_root = [&](std::uint32_t x) noexcept {
            while (dsu[x] != x) {
                dsu[x] = dsu[dsu[x]];
                x = dsu[x];
            }
            return x;
        };

        std::vector<std::pair<std::uint64_t, std::uint32_t>> edges;
        edges.reserve(nt * 3);
        for (std::size_t t = 0; t < nt; ++t) {
            if (self_cuts[t].active()) {
                origin_was_cut[self_tris[t].origin] = 1;
                continue;
            }
            for (std::size_t e = 0; e < 3; ++e) {
                std::uint32_t a = self_tris[t].v[e], b = self_tris[t].v[(e + 1) % 3];
                if (a > b) std::swap(a, b);
                edges.emplace_back((static_cast<std::uint64_t>(a) << 32) | b, static_cast<std::uint32_t>(t));
            }
        }
        std::sort(edges.begin(), edges.end());
        for (std::size_t i = 1; i < edges.size(); ++i) {
            if (edges[i].first == edges[i - 1].first) {
                const std::uint32_t ra = find_root(edges[i - 1].second);
                const std::uint32_t rb = find_root(edges[i].second);
                if (ra != rb) dsu[rb] = ra;
            }
        }

        std::vector<double> best_area2(nt, -1.0);
        std::vector<std::uint32_t> best_tri(nt, ~0u);
        for (std::size_t t = 0; t < nt; ++t) {
            if (self_cuts[t].active()) continue;
            const std::uint32_t r = find_root(static_cast<std::uint32_t>(t));
            const Vec3& a = V[self_tris[t].v[0]];
            const Vec3& b = V[self_tris[t].v[1]];
            const Vec3& c = V[self_tris[t].v[2]];
            const Vec3 cr = cross(b - a, c - a);
            const double a2 = dot(cr, cr);
            if (a2 > best_area2[r]) {
                best_area2[r] = a2;
                best_tri[r] = static_cast<std::uint32_t>(t);
            }
        }
        for (std::size_t t = 0; t < nt; ++t) {
            if (self_cuts[t].active()) continue;
            const std::uint32_t r = find_root(static_cast<std::uint32_t>(t));
            if (uncut_inside[r] == -1) {
                const InputTri& rep = self_tris[best_tri[r]];
                const Vec3 probe = safe_interior_point(V[rep.v[0]], V[rep.v[1]], V[rep.v[2]]);
                uncut_inside[r] = opp_bvh.contains_point(probe, diag) ? 1 : 0;
            }
            uncut_inside[t] = uncut_inside[r];
        }
    }

    // 2. Emit kept uncut triangles and process active (cut / coplanar) triangles via FastExactCDT2d.
    FastExactCDT2d cdt;
    std::vector<std::pair<std::uint32_t, GEO::index_t>> gid_to_lv;
    std::vector<std::pair<GEO::index_t, GEO::index_t>> unique_cnstrs;
    std::vector<PatchClass> sub_class;
    std::vector<std::uint8_t> sub_visited;
    std::vector<GEO::index_t> pocket;

    for (std::size_t ti = 0; ti < nt; ++ti) {
        const InputTri& T = self_tris[ti];
        const TriCutData& cut = self_cuts[ti];

        if (!cut.active()) {
            const PatchClass cls = uncut_inside[ti] ? PatchClass::Inside : PatchClass::Outside;
            const PatchDecision dec = decide_patch(mesh_id, cls, op);
            if (dec.keep) {
                ++uncut_origin_kept_tris[T.origin];
                if (dec.flip) {
                    emitted.push_back({{V[T.v[0]], V[T.v[2]], V[T.v[1]]}, {T.v[0], T.v[2], T.v[1]}, T.origin});
                } else {
                    emitted.push_back({{V[T.v[0]], V[T.v[1]], V[T.v[2]]}, {T.v[0], T.v[1], T.v[2]}, T.origin});
                }
            }
            continue;
        }

        const Vec3& a0 = V[T.v[0]];
        const Vec3& a1 = V[T.v[1]];
        const Vec3& a2 = V[T.v[2]];
        const GEO::coord_index_t dom = GEO::PCK::triangle_normal_axis(gvec(a0), gvec(a1), gvec(a2));
        const GEO::coord_index_t u_ax = (dom + 1) % 3;
        const GEO::coord_index_t v_ax = (dom + 2) % 3;
        const int ua = static_cast<int>(u_ax);
        const int va = static_cast<int>(v_ax);
        const Vec3 tri_n_raw = cross(a1 - a0, a2 - a0);

        auto to_exact_2d = [&](std::uint32_t gid) -> GEO::exact::vec2h {
            const GEO::exact::vec3h& p3 = pool.exact_pts[gid];
            return GEO::exact::vec2h(p3[u_ax], p3[v_ax], p3.w);
        };
        auto to_approx_2d = [&](std::uint32_t gid) noexcept -> std::array<double, 2> {
            const Vec3& p = pool.approx_pts[gid];
            return {p[ua], p[va]};
        };
        auto orig_exact_2d = [&](const Vec3& p) -> GEO::exact::vec2h {
            return GEO::exact::vec2h(p[ua], p[va], 1.0);
        };

        gid_to_lv.clear();
        unique_cnstrs.clear();

        const std::uint32_t c0_gid = pool.orig_v_to_gid[T.v[0]];
        const std::uint32_t c1_gid = pool.orig_v_to_gid[T.v[1]];
        const std::uint32_t c2_gid = pool.orig_v_to_gid[T.v[2]];

        cdt.init_triangle(orig_exact_2d(a0), {a0[ua], a0[va]}, c0_gid,
                          orig_exact_2d(a1), {a1[ua], a1[va]}, c1_gid,
                          orig_exact_2d(a2), {a2[ua], a2[va]}, c2_gid);
        if (c0_gid != ~0u) gid_to_lv.emplace_back(c0_gid, 0);
        if (c1_gid != ~0u) gid_to_lv.emplace_back(c1_gid, 1);
        if (c2_gid != ~0u) gid_to_lv.emplace_back(c2_gid, 2);

        auto ensure_cdt_vertex = [&](std::uint32_t gid) -> GEO::index_t {
            for (const auto& kv : gid_to_lv) {
                if (kv.first == gid) return kv.second;
            }
            const GEO::index_t lv = cdt.insert_pt(to_exact_2d(gid), to_approx_2d(gid), gid);
            gid_to_lv.emplace_back(gid, lv);
            return lv;
        };

        for (std::uint32_t gid : cut.points) {
            (void)ensure_cdt_vertex(gid);
        }
        for (const auto& [g0, g1] : cut.segs) {
            GEO::index_t lv0 = ensure_cdt_vertex(g0);
            GEO::index_t lv1 = ensure_cdt_vertex(g1);
            if (lv0 == lv1) continue;
            if (lv0 > lv1) std::swap(lv0, lv1);
            unique_cnstrs.emplace_back(lv0, lv1);
        }
        std::sort(unique_cnstrs.begin(), unique_cnstrs.end());
        unique_cnstrs.erase(std::unique(unique_cnstrs.begin(), unique_cnstrs.end()), unique_cnstrs.end());
        for (const auto& [lv0, lv1] : unique_cnstrs) {
            cdt.add_constraint(lv0, lv1);
        }

        // Lift CDT local vertex to 3D Vec3 (canonical pool coordinate if registered, else plane lift).
        auto cdt_vertex_3d = [&](GEO::index_t lv) -> Vec3 {
            if (lv == 0) return a0;
            if (lv == 1) return a1;
            if (lv == 2) return a2;
            const std::uint32_t gid = cdt.vertex_id(lv);
            if (gid != ~0u) {
                return pool.approx_pts[gid];
            }
            const auto p2 = cdt.approx(lv);
            double qc[3]{};
            const int d_ax = static_cast<int>(dom);
            qc[ua] = p2[0];
            qc[va] = p2[1];
            qc[d_ax] = a0[d_ax] - (tri_n_raw[ua] * (p2[0] - a0[ua]) + tri_n_raw[va] * (p2[1] - a0[va])) / tri_n_raw[d_ax];
            return {qc[0], qc[1], qc[2]};
        };

        const GEO::index_t sub_n = cdt.nT();
        sub_class.assign(sub_n, PatchClass::Outside);
        sub_visited.assign(sub_n, 0);

        // 2a. Exact 2D classification of coplanar sub-triangles.
        if (!cut.coplanar.empty()) {
            for (GEO::index_t st = 0; st < sub_n; ++st) {
                const GEO::exact::vec2h& p0 = cdt.point(cdt.Tv(st, 0));
                const GEO::exact::vec2h& p1 = cdt.point(cdt.Tv(st, 1));
                const GEO::exact::vec2h& p2 = cdt.point(cdt.Tv(st, 2));
                for (const CoplanarPartner& cp : cut.coplanar) {
                    const InputTri& opp = opp_tris[cp.opp_tri];
                    GEO::exact::vec2h qb[3] = {
                        orig_exact_2d(V[opp.v[0]]),
                        orig_exact_2d(V[opp.v[1]]),
                        orig_exact_2d(V[opp.v[2]]),
                    };
                    if (GEO::PCK::orient_2d(qb[0], qb[1], qb[2]) == GEO::NEGATIVE) {
                        std::swap(qb[1], qb[2]);
                    }
                    bool inside_opp = true;
                    for (int e = 0; e < 3 && inside_opp; ++e) {
                        const auto& e0 = qb[e];
                        const auto& e1 = qb[(e + 1) % 3];
                        if (GEO::PCK::orient_2d(e0, e1, p0) == GEO::NEGATIVE ||
                            GEO::PCK::orient_2d(e0, e1, p1) == GEO::NEGATIVE ||
                            GEO::PCK::orient_2d(e0, e1, p2) == GEO::NEGATIVE) {
                            inside_opp = false;
                        }
                    }
                    if (inside_opp) {
                        sub_class[st] = cp.same_normal ? PatchClass::CoplanarSame : PatchClass::CoplanarOpp;
                        sub_visited[st] = 1;
                        break;
                    }
                }
            }
        }

        // 2b. Group remaining non-coplanar sub-triangles into pockets across unconstrained CDT edges
        // and classify each pocket via its largest-area sub-triangle.
        for (GEO::index_t st = 0; st < sub_n; ++st) {
            if (sub_visited[st]) continue;
            pocket.clear();
            sub_visited[st] = 1;
            pocket.push_back(st);
            GEO::index_t best_st = st;
            double best_a2 = -1.0;

            for (std::size_t qi = 0; qi < pocket.size(); ++qi) {
                const GEO::index_t cur = pocket[qi];
                const Vec3 q0 = cdt_vertex_3d(cdt.Tv(cur, 0));
                const Vec3 q1 = cdt_vertex_3d(cdt.Tv(cur, 1));
                const Vec3 q2 = cdt_vertex_3d(cdt.Tv(cur, 2));
                const Vec3 cr = cross(q1 - q0, q2 - q0);
                const double area_sq = dot(cr, cr);
                if (area_sq > best_a2) {
                    best_a2 = area_sq;
                    best_st = cur;
                }
                for (GEO::index_t le = 0; le < 3; ++le) {
                    if (cdt.Tedge_cnstr_nb(cur, le) > 0) continue;
                    const GEO::index_t nb = cdt.Tadj(cur, le);
                    if (nb != GEO::NO_INDEX && !sub_visited[nb]) {
                        sub_visited[nb] = 1;
                        pocket.push_back(nb);
                    }
                }
            }

            const Vec3 p0 = cdt_vertex_3d(cdt.Tv(best_st, 0));
            const Vec3 p1 = cdt_vertex_3d(cdt.Tv(best_st, 1));
            const Vec3 p2 = cdt_vertex_3d(cdt.Tv(best_st, 2));
            const Vec3 probe = safe_interior_point(p0, p1, p2);
            const PatchClass cls = opp_bvh.contains_point(probe, diag) ? PatchClass::Inside : PatchClass::Outside;
            for (GEO::index_t id : pocket) sub_class[id] = cls;
        }

        // 2c. Emit kept sub-triangles (preserving T's outward orientation, or flipped if Difference B).
        for (GEO::index_t st = 0; st < sub_n; ++st) {
            const PatchDecision dec = decide_patch(mesh_id, sub_class[st], op);
            if (!dec.keep) continue;
            const Vec3 p0 = cdt_vertex_3d(cdt.Tv(st, 0));
            const Vec3 p1 = cdt_vertex_3d(cdt.Tv(st, 1));
            const Vec3 p2 = cdt_vertex_3d(cdt.Tv(st, 2));
            if (dec.flip) {
                emitted.push_back({{p0, p2, p1}, {~0u, ~0u, ~0u}, T.origin});
            } else {
                emitted.push_back({{p0, p1, p2}, {~0u, ~0u, ~0u}, T.origin});
            }
        }
    }
}

// ---- Coplanar fragment merging -------------------------------------------
//
// After stitching, the fragments of one planar input face tile a region of
// that face. Their union is rebuilt as one boundary loop (interior edges
// dropped), stitch vertices no other face needs are removed, and a loop that
// is not convex is cut back into convex pieces by diagonals between its own
// vertices, so every vertex a neighbouring face uses stays on the boundary
// (watertight) and no vertex moves or is created (exact).

std::uint64_t edge_key(Index a, Index b) noexcept { return (static_cast<std::uint64_t>(a) << 32) | b; }

struct P2 { double x, y; };
double cross2(P2 o, P2 a, P2 b) noexcept { return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x); }

// Orientation-preserving 2D projection along the dominant axis of n.
P2 project(Vec3 p, Vec3 n) noexcept {
    const double ax = std::abs(n.x), ay = std::abs(n.y), az = std::abs(n.z);
    if (ax >= ay && ax >= az) return n.x > 0 ? P2{p.y, p.z} : P2{p.z, p.y};
    if (ay >= az) return n.y > 0 ? P2{p.z, p.x} : P2{p.x, p.z};
    return n.z > 0 ? P2{p.x, p.y} : P2{p.y, p.x};
}

// The boundary of the union of `group`'s faces as one loop (zero hash-table allocs).
// Fails if the union has holes, several components, pinch vertices or repeated edges.
bool merge_loop(const std::vector<std::vector<Index>>& faces, const std::vector<std::uint32_t>& group,
                std::vector<Index>& loop, std::vector<std::uint64_t>& directed,
                std::vector<std::pair<Index, Index>>& next) {
    directed.clear();
    for (std::uint32_t f : group) {
        const auto& fv = faces[f];
        for (std::size_t i = 0; i < fv.size(); ++i)
            directed.push_back(edge_key(fv[i], fv[(i + 1) % fv.size()]));
    }
    std::sort(directed.begin(), directed.end());
    if (std::adjacent_find(directed.begin(), directed.end()) != directed.end()) return false;

    next.clear();
    Index start = ~Index{0};
    for (std::uint64_t ek : directed) {
        const auto a = static_cast<Index>(ek >> 32), b = static_cast<Index>(ek & 0xffffffffu);
        if (std::binary_search(directed.begin(), directed.end(), edge_key(b, a))) continue;
        next.emplace_back(a, b);
        start = std::min(start, a);
    }
    const std::size_t boundary = next.size();
    if (boundary < 3) return false;
    std::sort(next.begin(), next.end());
    for (std::size_t i = 1; i < boundary; ++i) {
        if (next[i].first == next[i - 1].first) return false;  // pinch vertex
    }
    loop.clear();
    Index v = start;
    do {
        loop.push_back(v);
        const auto it = std::lower_bound(next.begin(), next.end(), std::make_pair(v, Index{0}));
        if (it == next.end() || it->first != v || loop.size() > boundary) return false;
        v = it->second;
    } while (v != start);
    return loop.size() == boundary;  // otherwise holes or several pieces
}

// Convex partition of a simple CCW polygon without new vertices: ear clipping
// that takes the ear with the largest minimum angle, then Hertel-Mehlhorn
// removal of diagonals, shortest first. False if clipping gets stuck.
bool convex_pieces(const std::vector<P2>& p, std::vector<std::vector<int>>& pieces) {
    const int n = static_cast<int>(p.size());
    auto P = [&](int i) { return p[static_cast<std::size_t>(i)]; };
    double scale = 0.0;
    for (const P2& q : p) scale = std::max({scale, std::abs(q.x - p[0].x), std::abs(q.y - p[0].y)});
    const double eps = 1e-13 * scale * scale;
    std::vector<int> idx(static_cast<std::size_t>(n));
    std::iota(idx.begin(), idx.end(), 0);
    std::vector<std::array<int, 3>> tris;
    auto max_cos = [](P2 a, P2 b, P2 c) {
        auto cosat = [](P2 o, P2 u, P2 w) {
            const double ux = u.x - o.x, uy = u.y - o.y, wx = w.x - o.x, wy = w.y - o.y;
            return (ux * wx + uy * wy) / std::sqrt((ux * ux + uy * uy) * (wx * wx + wy * wy));
        };
        return std::max({cosat(a, b, c), cosat(b, c, a), cosat(c, a, b)});
    };
    while (idx.size() > 3) {
        const std::size_t k = idx.size();
        std::size_t best = k;
        double best_cos = 2.0;
        for (std::size_t i = 0; i < k; ++i) {
            const std::size_t ip = (i + k - 1) % k, in = (i + 1) % k;
            const P2 a = P(idx[ip]), b = P(idx[i]), c = P(idx[in]);
            if (cross2(a, b, c) <= eps) continue;  // reflex or straight tip
            bool blocked = false;
            for (std::size_t j = 0; j < k && !blocked; ++j) {
                if (j == i || j == ip || j == in) continue;
                const P2 q = P(idx[j]);
                blocked = cross2(a, b, q) >= -eps && cross2(b, c, q) >= -eps && cross2(c, a, q) >= -eps;
            }
            if (blocked) continue;
            const double mc = max_cos(a, b, c);
            if (mc < best_cos) { best_cos = mc; best = i; }
        }
        if (best == k) return false;
        tris.push_back({idx[(best + k - 1) % k], idx[best], idx[(best + 1) % k]});
        idx.erase(idx.begin() + static_cast<std::ptrdiff_t>(best));
    }
    if (cross2(P(idx[0]), P(idx[1]), P(idx[2])) <= eps) return false;
    tris.push_back({idx[0], idx[1], idx[2]});

    pieces.clear();
    std::unordered_map<std::uint64_t, int> owner;  // directed edge -> piece
    auto key = [](int a, int b) { return edge_key(static_cast<Index>(a), static_cast<Index>(b)); };
    std::vector<std::pair<double, std::pair<int, int>>> diags;
    for (const auto& t : tris) {
        const int id = static_cast<int>(pieces.size());
        pieces.push_back({t[0], t[1], t[2]});
        for (std::size_t e = 0; e < 3; ++e) {
            const int a = t[e], b = t[(e + 1) % 3];
            owner[key(a, b)] = id;
            if (a < b && b - a != 1 && !(a == 0 && b == n - 1)) {
                const double dx = P(a).x - P(b).x, dy = P(a).y - P(b).y;
                diags.push_back({dx * dx + dy * dy, {a, b}});
            }
        }
    }
    std::sort(diags.begin(), diags.end());
    std::vector<int> merged;
    for (const auto& d : diags) {
        const int a = d.second.first, b = d.second.second;
        const int pi = owner.at(key(a, b)), qi = owner.at(key(b, a));
        std::vector<int>& A = pieces[static_cast<std::size_t>(pi)];
        std::vector<int>& B = pieces[static_cast<std::size_t>(qi)];
        std::rotate(A.begin(), std::find(A.begin(), A.end(), b), A.end());  // b ... a
        std::rotate(B.begin(), std::find(B.begin(), B.end(), a), B.end());  // a ... b
        merged.assign(A.begin(), A.end());
        merged.insert(merged.end(), B.begin() + 1, B.end() - 1);
        const std::size_t mk = merged.size();
        auto convex_at = [&](std::size_t i) {
            return cross2(P(merged[(i + mk - 1) % mk]), P(merged[i]), P(merged[(i + 1) % mk])) >= -eps;
        };
        if (!convex_at(0) || !convex_at(A.size() - 1)) continue;
        owner.erase(key(a, b));
        owner.erase(key(b, a));
        for (std::size_t i = 0; i < mk; ++i) owner[key(merged[i], merged[(i + 1) % mk])] = pi;
        A = merged;
        B.clear();
    }
    pieces.erase(std::remove_if(pieces.begin(), pieces.end(), [](const auto& v) { return v.empty(); }), pieces.end());
    return true;
}

// Drops every vertex that exactly two faces use, consecutively in both
// between the same two neighbours, and that lies on the segment between those
// neighbours (within tol): both faces lose it, so the mesh stays watertight.
void remove_free_collinear(std::vector<std::vector<Index>>& faces, const std::vector<Vec3>& pos, double tol) {
    const std::size_t nv = pos.size();
    std::vector<std::uint32_t> uses(nv, 0);
    std::vector<std::array<std::uint32_t, 2>> fs(nv, {~0u, ~0u});
    for (std::size_t f = 0; f < faces.size(); ++f)
        for (Index v : faces[f]) {
            if (uses[v] < 2) fs[v][uses[v]] = static_cast<std::uint32_t>(f);
            ++uses[v];
        }
    const double tol2 = tol * tol;
    auto locate = [](const std::vector<Index>& fv, Index v) {
        return static_cast<std::size_t>(std::find(fv.begin(), fv.end(), v) - fv.begin());
    };
    for (bool changed = true; changed;) {
        changed = false;
        for (std::size_t v = 0; v < nv; ++v) {
            if (uses[v] != 2 || fs[v][0] == fs[v][1]) continue;
            auto& F = faces[fs[v][0]];
            auto& G = faces[fs[v][1]];
            if (F.size() < 4 || G.size() < 4) continue;
            const std::size_t i = locate(F, static_cast<Index>(v)), j = locate(G, static_cast<Index>(v));
            const Index p = F[(i + F.size() - 1) % F.size()], n = F[(i + 1) % F.size()];
            if (G[(j + 1) % G.size()] != p || G[(j + G.size() - 1) % G.size()] != n) continue;
            const Vec3 a = pos[p], ab = pos[n] - a, av = pos[v] - a;
            const double len2 = dot(ab, ab);
            if (!(len2 > 0.0)) continue;
            const double t = dot(av, ab) / len2;
            if (t <= 0.0 || t >= 1.0) continue;
            const Vec3 off = av - ab * t;
            if (dot(off, off) > tol2) continue;
            F.erase(F.begin() + static_cast<std::ptrdiff_t>(i));
            G.erase(G.begin() + static_cast<std::ptrdiff_t>(j));
            uses[v] = 0;
            changed = true;
        }
    }
}

void collapse_collinear(std::vector<std::vector<Index>>& faces, std::vector<std::uint8_t>& needs_pieces,
                        const std::vector<std::uint32_t>& face_tag, const std::vector<Original>& originals,
                        const std::vector<Vec3>& pos, double tol, bool b_flipped) {
    const std::size_t nv = pos.size();
    std::vector<std::vector<std::uint32_t>> inc(nv);
    for (std::size_t f = 0; f < faces.size(); ++f)
        for (Index v : faces[f]) inc[v].push_back(static_cast<std::uint32_t>(f));
    const double tol2 = tol * tol;

    struct Cand { double len2; Index x; std::uint32_t w; };
    std::vector<Cand> cands;
    for (std::size_t f = 0; f < faces.size(); ++f) {
        const auto& fv = faces[f];
        const std::size_t k = fv.size();
        if (k < 4) continue;
        for (std::size_t i = 0; i < k; ++i) {
            const Index p = fv[(i + k - 1) % k], x = fv[i], n = fv[(i + 1) % k];
            const Vec3 a = pos[p], ab = pos[n] - a, ax = pos[x] - a;
            const double len2 = dot(ab, ab);
            if (!(len2 > 0.0)) continue;
            const double t = dot(ax, ab) / len2;
            if (t <= 0.0 || t >= 1.0) continue;
            const Vec3 off = ax - ab * t;
            if (dot(off, off) > tol2) continue;
            const double s = std::min(t, 1.0 - t);
            cands.push_back({s * s * len2, x, static_cast<std::uint32_t>(f)});
        }
    }
    std::sort(cands.begin(), cands.end(), [](const Cand& u, const Cand& v) {
        return u.len2 != v.len2 ? u.len2 < v.len2 : u.x < v.x;
    });

    std::vector<std::vector<Index>> repl;
    std::vector<P2> q;
    for (const Cand& cd : cands) {
        const Index x = cd.x;
        auto& W = faces[cd.w];
        const std::size_t k = W.size();
        if (k < 4) continue;
        const std::size_t i = static_cast<std::size_t>(std::find(W.begin(), W.end(), x) - W.begin());
        if (i == k) continue;
        const Index p = W[(i + k - 1) % k], n = W[(i + 1) % k];
        {
            const Vec3 a = pos[p], ab = pos[n] - a, ax = pos[x] - a;
            const double len2 = dot(ab, ab);
            if (!(len2 > 0.0)) continue;
            const double t = dot(ax, ab) / len2;
            if (t <= 0.0 || t >= 1.0) continue;
            const Vec3 off = ax - ab * t;
            if (dot(off, off) > tol2) continue;
        }
        const Vec3 dp = pos[p] - pos[x], dn = pos[n] - pos[x];
        const Index c = dot(dp, dp) <= dot(dn, dn) ? p : n;
        bool ok = true;
        Vec3 fan_n{};
        bool have_n = false;
        repl.clear();
        const auto& fx = inc[x];
        for (std::uint32_t f : fx) {
            if (f == cd.w) continue;
            const auto& F = faces[f];
            if (std::count(F.begin(), F.end(), x) != 1) { ok = false; break; }
            const Original& o = originals[face_tag[f]];
            if (!o.planar) { ok = false; break; }
            const Vec3 nf = (b_flipped && o.mesh_id == 1) ? -o.normal : o.normal;
            if (std::abs(dot(nf, pos[c] - pos[x])) > tol) { ok = false; break; }
            if (!have_n) { fan_n = nf; have_n = true; }
            else if (std::abs(dot(nf, fan_n)) < 1.0 - 1e-12) { ok = false; break; }
            std::vector<Index> G;
            G.reserve(F.size());
            for (Index v : F) {
                const Index u = v == x ? c : v;
                if (!G.empty() && G.back() == u) continue;
                G.push_back(u);
            }
            while (G.size() > 1 && G.front() == G.back()) G.pop_back();
            if (G.size() == 2) {
                if (F.size() != 3) { ok = false; break; }
                G.clear();
                repl.push_back(std::move(G));
                continue;
            }
            if (G.size() < 3 || std::count(G.begin(), G.end(), c) != 1) { ok = false; break; }
            const std::size_t gk = G.size();
            const std::size_t ci = static_cast<std::size_t>(std::find(G.begin(), G.end(), c) - G.begin());
            q.clear();
            for (Index v : G) q.push_back(project(pos[v], nf));
            double area = 0.0, scale = 0.0;
            for (std::size_t j = 0; j < gk; ++j) {
                const P2 a0 = q[j], b0 = q[(j + 1) % gk];
                area += a0.x * b0.y - a0.y * b0.x;
                scale = std::max({scale, std::abs(a0.x - q[0].x), std::abs(a0.y - q[0].y)});
            }
            const double eps = 1e-13 * scale * scale;
            if (!(area > eps)) { ok = false; break; }
            auto proper = [&](P2 a0, P2 b0, P2 c0, P2 d0) {
                const double d1 = cross2(a0, b0, c0), d2 = cross2(a0, b0, d0);
                const double d3 = cross2(c0, d0, a0), d4 = cross2(c0, d0, b0);
                return ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) &&
                       ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps));
            };
            for (std::size_t e = 0; e < 2 && ok; ++e) {
                const std::size_t s0 = e == 0 ? (ci + gk - 1) % gk : ci, s1 = (s0 + 1) % gk;
                for (std::size_t j = 0; j < gk && ok; ++j) {
                    const std::size_t j1 = (j + 1) % gk;
                    if (j == s0 || j1 == s0 || j == s1) continue;
                    if (proper(q[s0], q[s1], q[j], q[j1])) ok = false;
                    if (ok && j != s0 && j != s1) {
                        const double cr = cross2(q[s0], q[s1], q[j]);
                        if (std::abs(cr) <= eps) {
                            const double dx = q[s1].x - q[s0].x, dy = q[s1].y - q[s0].y;
                            const double t = ((q[j].x - q[s0].x) * dx + (q[j].y - q[s0].y) * dy) / (dx * dx + dy * dy);
                            if (t > 0.0 && t < 1.0) ok = false;
                        }
                    }
                }
            }
            if (ok) repl.push_back(std::move(G));
        }
        if (!ok || !have_n) continue;
        {
            std::vector<std::uint64_t> de;
            auto add_edges = [&](const std::vector<Index>& G) {
                const std::size_t gk = G.size();
                for (std::size_t j = 0; j < gk; ++j) {
                    const Index a0 = G[j], b0 = G[(j + 1) % gk];
                    if (a0 == c || b0 == c) de.push_back(edge_key(a0, b0));
                }
            };
            std::size_t r = 0;
            for (std::uint32_t f : fx) {
                if (f == cd.w) {
                    std::vector<Index> G = W;
                    G.erase(G.begin() + static_cast<std::ptrdiff_t>(i));
                    add_edges(G);
                } else {
                    add_edges(repl[r++]);
                }
            }
            for (std::uint32_t h : inc[c])
                if (std::find(fx.begin(), fx.end(), h) == fx.end()) add_edges(faces[h]);
            std::sort(de.begin(), de.end());
            if (std::adjacent_find(de.begin(), de.end()) != de.end()) continue;
            for (std::uint64_t e : de) {
                const std::uint64_t rev = (e << 32) | (e >> 32);
                if (!std::binary_search(de.begin(), de.end(), rev)) { ok = false; break; }
            }
            if (!ok) continue;
        }
        std::size_t r = 0;
        for (std::uint32_t f : fx) {
            if (f == cd.w) continue;
            faces[f] = std::move(repl[r++]);
            needs_pieces[f] = 1;
            if (std::find(inc[c].begin(), inc[c].end(), f) == inc[c].end()) inc[c].push_back(f);
        }
        W.erase(W.begin() + static_cast<std::ptrdiff_t>(i));
        inc[x].clear();
    }
}

// Replaces the fragments of each planar input face by convex pieces of their
// union (see above). Faces of non-planar inputs and unions that are not one
// simple loop keep their fragments.
void merge_fragments(Mesh& m, const std::vector<std::uint32_t>& tags, const std::vector<Original>& originals,
                     double collinear_tol, bool b_flipped) {
    const std::size_t nf = m.face_count();
    std::vector<std::vector<Index>> faces(nf);
    for (std::size_t f = 0; f < nf; ++f) faces[f].assign(m.face(f).begin(), m.face(f).end());
    std::vector<std::uint32_t> order(nf);
    std::iota(order.begin(), order.end(), 0u);
    std::stable_sort(order.begin(), order.end(), [&](std::uint32_t x, std::uint32_t y) { return tags[x] < tags[y]; });

    // 1. One loop per origin (in the slot of its first fragment).
    std::vector<std::uint8_t> merged(nf, 0);
    std::vector<std::uint32_t> group;
    std::vector<Index> loop;
    std::vector<std::uint64_t> loop_directed;
    std::vector<std::pair<Index, Index>> loop_next;
    for (std::size_t s = 0; s < nf;) {
        std::size_t e = s;
        while (e < nf && tags[order[e]] == tags[order[s]]) ++e;
        if (e - s > 1 && originals[tags[order[s]]].planar) {
            group.assign(order.begin() + static_cast<std::ptrdiff_t>(s), order.begin() + static_cast<std::ptrdiff_t>(e));
            std::sort(group.begin(), group.end());
            if (merge_loop(faces, group, loop, loop_directed, loop_next)) {
                faces[group[0]] = loop;
                merged[group[0]] = 1;
                for (std::size_t k = 1; k < group.size(); ++k) faces[group[k]].clear();
            }
        }
        s = e;
    }
    std::vector<std::vector<Index>> live;
    std::vector<std::uint8_t> live_merged;
    std::vector<std::uint32_t> live_tag;
    for (std::size_t f = 0; f < nf; ++f)
        if (!faces[f].empty()) {
            live.push_back(std::move(faces[f]));
            live_merged.push_back(merged[f]);
            live_tag.push_back(tags[f]);
        }

    // 2. Stitch vertices only the two faces beside them use...
    remove_free_collinear(live, m.positions, collinear_tol);
    // ... and stitch vertices on a straight edge, collapsed onto a neighbour.
    collapse_collinear(live, live_merged, live_tag, originals, m.positions, collinear_tol, b_flipped);
    remove_free_collinear(live, m.positions, collinear_tol);

    // 3. Convex pieces of merged loops that are not convex.
    Mesh out;
    out.positions = std::move(m.positions);
    std::vector<P2> p2;
    std::vector<std::vector<int>> pieces;
    std::vector<Index> piece;
    for (std::size_t f = 0; f < live.size(); ++f) {
        const auto& fv = live[f];
        if (fv.empty()) continue;
        bool done = false;
        if (live_merged[f]) {
            const Vec3 n = originals[live_tag[f]].normal;
            p2.clear();
            for (Index v : fv) p2.push_back(project(out.positions[v], n));
            double area = 0.0;
            for (std::size_t i = 0; i < p2.size(); ++i) {
                const P2 a = p2[i], b = p2[(i + 1) % p2.size()];
                area += a.x * b.y - a.y * b.x;
            }
            if (area < 0)
                for (P2& q : p2) q.x = -q.x;
            bool convex = true;
            double scale = 0.0;
            for (const P2& q : p2) scale = std::max({scale, std::abs(q.x - p2[0].x), std::abs(q.y - p2[0].y)});
            const double eps = 1e-13 * scale * scale;
            for (std::size_t i = 0; i < p2.size() && convex; ++i)
                convex = cross2(p2[(i + p2.size() - 1) % p2.size()], p2[i], p2[(i + 1) % p2.size()]) >= -eps;
            if (!convex && convex_pieces(p2, pieces)) {
                for (const auto& pc : pieces) {
                    piece.clear();
                    for (int i : pc) piece.push_back(fv[static_cast<std::size_t>(i)]);
                    out.add_face(piece);
                }
                done = true;
            }
        }
        if (!done) out.add_face(fv);
    }
    m = std::move(out);
}

// Zone-restricted T-junction repair: only vertices inside `zone` (expanded by tolerance)
// can split edges, and only edges overlapping `zone` need checking.
std::size_t repair_t_junctions_in_zone(Mesh& m, double tolerance, const Aabb& zone) {
    if (!zone.valid()) return 0;
    const Aabb z{zone.lo - Vec3{tolerance, tolerance, tolerance}, zone.hi + Vec3{tolerance, tolerance, tolerance}};
    const std::size_t nv = m.vertex_count();
    std::vector<Index> by_x;
    by_x.reserve(nv);
    for (std::size_t i = 0; i < nv; ++i) {
        const Vec3& p = m.positions[i];
        if (p.x >= z.lo.x && p.x <= z.hi.x && p.y >= z.lo.y && p.y <= z.hi.y && p.z >= z.lo.z && p.z <= z.hi.z) {
            by_x.push_back(static_cast<Index>(i));
        }
    }
    if (by_x.empty()) return 0;
    std::sort(by_x.begin(), by_x.end(), [&](Index a, Index b) { return m.positions[a].x < m.positions[b].x; });
    const std::size_t nz = by_x.size();
    std::vector<double> xs(nz);
    for (std::size_t i = 0; i < nz; ++i) xs[i] = m.positions[by_x[i]].x;

    const double tol2 = tolerance * tolerance;
    std::size_t inserted = 0;
    std::vector<Index> offsets{0}, indices;
    indices.reserve(m.corner_count());
    std::vector<std::pair<double, Index>> on_edge;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const Index a = fv[i], b = fv[(i + 1) % fv.size()];
            indices.push_back(a);
            const Vec3 pa = m.positions[a], pb = m.positions[b];
            if (std::max(pa.x, pb.x) < z.lo.x || std::min(pa.x, pb.x) > z.hi.x ||
                std::max(pa.y, pb.y) < z.lo.y || std::min(pa.y, pb.y) > z.hi.y ||
                std::max(pa.z, pb.z) < z.lo.z || std::min(pa.z, pb.z) > z.hi.z) {
                continue;
            }
            const Vec3 ab = pb - pa;
            const double len2 = dot(ab, ab);
            if (!(len2 > tol2)) continue;
            const double lo = std::min(pa.x, pb.x) - tolerance, hi = std::max(pa.x, pb.x) + tolerance;
            on_edge.clear();
            for (auto k = std::lower_bound(xs.begin(), xs.end(), lo) - xs.begin();
                 k < static_cast<std::ptrdiff_t>(nz) && xs[static_cast<std::size_t>(k)] <= hi; ++k) {
                const Index v = by_x[static_cast<std::size_t>(k)];
                if (v == a || v == b) continue;
                const Vec3 ap = m.positions[v] - pa;
                const double t = dot(ap, ab) / len2;
                if (t <= 0.0 || t >= 1.0) continue;
                const Vec3 d = ap - ab * t;
                if (dot(d, d) <= tol2) on_edge.emplace_back(t, v);
            }
            std::sort(on_edge.begin(), on_edge.end());
            for (const auto& [t, v] : on_edge) indices.push_back(v);
            inserted += on_edge.size();
        }
        offsets.push_back(static_cast<Index>(indices.size()));
    }
    m.face_offsets = std::move(offsets);
    m.face_indices = std::move(indices);
    return inserted;
}

}  // namespace

std::size_t repair_t_junctions(Mesh& m, double tolerance) {
    const std::size_t nv = m.vertex_count();
    std::vector<Index> by_x(nv);
    std::iota(by_x.begin(), by_x.end(), Index{0});
    std::sort(by_x.begin(), by_x.end(), [&](Index a, Index b) { return m.positions[a].x < m.positions[b].x; });
    std::vector<double> xs(nv);
    for (std::size_t i = 0; i < nv; ++i) xs[i] = m.positions[by_x[i]].x;

    const double tol2 = tolerance * tolerance;
    std::size_t inserted = 0;
    std::vector<Index> offsets{0}, indices;
    indices.reserve(m.corner_count());
    std::vector<std::pair<double, Index>> on_edge;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const Index a = fv[i], b = fv[(i + 1) % fv.size()];
            indices.push_back(a);
            const Vec3 pa = m.positions[a], pb = m.positions[b];
            const Vec3 ab = pb - pa;
            const double len2 = dot(ab, ab);
            if (!(len2 > tol2)) continue;
            const double lo = std::min(pa.x, pb.x) - tolerance, hi = std::max(pa.x, pb.x) + tolerance;
            on_edge.clear();
            for (auto k = std::lower_bound(xs.begin(), xs.end(), lo) - xs.begin();
                 k < static_cast<std::ptrdiff_t>(nv) && xs[static_cast<std::size_t>(k)] <= hi; ++k) {
                const Index v = by_x[static_cast<std::size_t>(k)];
                if (v == a || v == b) continue;
                const Vec3 ap = m.positions[v] - pa;
                const double t = dot(ap, ab) / len2;
                if (t <= 0.0 || t >= 1.0) continue;
                const Vec3 d = ap - ab * t;
                if (dot(d, d) <= tol2) on_edge.emplace_back(t, v);
            }
            std::sort(on_edge.begin(), on_edge.end());
            for (const auto& [t, v] : on_edge) indices.push_back(v);
            inserted += on_edge.size();
        }
        offsets.push_back(static_cast<Index>(indices.size()));
    }
    m.face_offsets = std::move(offsets);
    m.face_indices = std::move(indices);
    return inserted;
}

Mesh mesh_boolean(const Mesh& a, const Mesh& b, BooleanOp op, const BooleanParams& params) {
    if (a.face_count() == 0 && b.face_count() == 0) return {};

    const Aabb box_a = bounds(a);
    const Aabb box_b = bounds(b);
    Aabb box = box_a;
    if (box_b.valid()) {
        box.expand(box_b.lo);
        box.expand(box_b.hi);
    }
    const double diag = std::max(box.diagonal(), 1e-300);

    // Disjoint bounding boxes: zero faces can cross or enclose the other solid.
    const bool disjoint_boxes =
        box_a.valid() && box_b.valid() &&
        (box_a.hi.x < box_b.lo.x || box_b.hi.x < box_a.lo.x ||
         box_a.hi.y < box_b.lo.y || box_b.hi.y < box_a.lo.y ||
         box_a.hi.z < box_b.lo.z || box_b.hi.z < box_a.lo.z);
    if (disjoint_boxes && params.merge_uncut_faces) {
        if (op == BooleanOp::Intersection) return {};
        if (op == BooleanOp::Difference) {
            Mesh out = a;
            compact_vertices(out);
            return out;
        }
        Mesh out = a;
        const auto base = static_cast<Index>(out.positions.size());
        out.positions.insert(out.positions.end(), b.positions.begin(), b.positions.end());
        std::vector<Index> poly;
        for (std::size_t f = 0; f < b.face_count(); ++f) {
            poly.clear();
            for (Index v : b.face(f)) poly.push_back(base + v);
            out.add_face(poly);
        }
        compact_vertices(out);
        return out;
    }

    detail::ensure_exact_kernel();

    const double plane_eps = params.plane_epsilon_rel * diag;
    std::vector<Vec3> V;
    V.reserve(a.vertex_count() + b.vertex_count());
    std::unordered_map<Vec3, std::uint32_t, Vec3BitsHash, Vec3BitsEq> vmap;
    vmap.reserve(a.vertex_count() + b.vertex_count());

    std::vector<Original> originals;
    originals.reserve(a.face_count() + b.face_count());
    std::vector<InputTri> tris_a, tris_b;
    tris_a.reserve(a.corner_count());
    tris_b.reserve(b.corner_count());
    std::uint32_t next_global_tri_id = 0;

    load_mesh_tris(a, 0, plane_eps, V, vmap, originals, tris_a, next_global_tri_id);
    load_mesh_tris(b, 1, plane_eps, V, vmap, originals, tris_b, next_global_tri_id);

    // Broad phase: Zomorodian-Edelsbrunner bipartite 3D box intersection, restricted
    // to triangles whose AABBs overlap the opposite mesh's bounding box.
    auto overlaps = [](const Aabb& x, const Aabb& y) noexcept {
        return x.lo.x <= y.hi.x && x.hi.x >= y.lo.x &&
               x.lo.y <= y.hi.y && x.hi.y >= y.lo.y &&
               x.lo.z <= y.hi.z && x.hi.z >= y.lo.z;
    };
    std::vector<Aabb> boxes_a, boxes_b;
    std::vector<std::uint32_t> idx_a, idx_b;
    for (std::size_t i = 0; i < tris_a.size(); ++i) {
        if (overlaps(tris_a[i].box, box_b)) {
            boxes_a.push_back(tris_a[i].box);
            idx_a.push_back(static_cast<std::uint32_t>(i));
        }
    }
    for (std::size_t i = 0; i < tris_b.size(); ++i) {
        if (overlaps(tris_b[i].box, box_a)) {
            boxes_b.push_back(tris_b[i].box);
            idx_b.push_back(static_cast<std::uint32_t>(i));
        }
    }

    ExactPointPool pool(V, diag, next_global_tri_id);
    std::vector<TriCutData> cuts_a(tris_a.size()), cuts_b(tris_b.size());
    boxes_intersections(boxes_a, boxes_b, [&](std::uint32_t ca, std::uint32_t cb) {
        intersect_triangle_pair(idx_a[ca], idx_b[cb], tris_a, tris_b, V, pool, cuts_a, cuts_b);
    });

    RayBvh bvh_a, bvh_b;
    bvh_a.build(tris_a, V, box_b);
    bvh_b.build(tris_b, V, box_a);

    std::vector<std::uint8_t> origin_was_cut(originals.size(), 0);
    std::vector<std::uint32_t> uncut_origin_kept_tris(originals.size(), 0);
    std::vector<EmittedTri> emitted_tris;
    emitted_tris.reserve(tris_a.size() + tris_b.size());

    process_shell(0, tris_a, tris_b, cuts_a, bvh_b, V, pool, vmap, op, diag, origin_was_cut, uncut_origin_kept_tris,
                  emitted_tris);
    process_shell(1, tris_b, tris_a, cuts_b, bvh_a, V, pool, vmap, op, diag, origin_was_cut, uncut_origin_kept_tris,
                  emitted_tris);

    if (emitted_tris.empty()) return {};

    // Re-assemble: untouched input polygons outside the cut zone are emitted whole
    // as their original quads/n-gons reusing pre-shared V vertex IDs; cut polygons
    // emit their CDT sub-triangles and get merged back into convex polygons below.
    std::vector<std::uint8_t> orig_emitted(originals.size(), 0);
    std::vector<Index> v_to_out(V.size(), ~Index{0});
    Mesh out;
    auto emit_shared_v = [&](std::uint32_t vid) -> Index {
        if (v_to_out[vid] == ~Index{0}) {
            v_to_out[vid] = out.add_vertex(V[vid]);
        }
        return v_to_out[vid];
    };
    std::vector<Index> poly;
    std::vector<std::uint32_t> tags;
    for (const EmittedTri& et : emitted_tris) {
        const std::uint32_t o = et.origin;
        const Original& orig = originals[o];
        poly.clear();
        if (params.merge_uncut_faces && !origin_was_cut[o] && uncut_origin_kept_tris[o] == orig.tri_count &&
            orig.tri_count > 1) {
            if (orig_emitted[o]) continue;
            orig_emitted[o] = 1;
            const bool flipped = (op == BooleanOp::Difference && orig.mesh_id == 1);
            for (std::size_t i = 0; i < orig.vids.size(); ++i) {
                poly.push_back(emit_shared_v(orig.vids[flipped ? orig.vids.size() - 1 - i : i]));
            }
        } else if (et.v_orig[0] != ~0u) {
            poly.push_back(emit_shared_v(et.v_orig[0]));
            poly.push_back(emit_shared_v(et.v_orig[1]));
            poly.push_back(emit_shared_v(et.v_orig[2]));
        } else {
            poly.push_back(out.add_vertex(et.p[0]));
            poly.push_back(out.add_vertex(et.p[1]));
            poly.push_back(out.add_vertex(et.p[2]));
        }
        out.add_face(poly);
        tags.push_back(o);
    }

    if (out.face_count() == 0) return out;
    const double tol = params.weld_epsilon_rel * diag;
    weld_vertices(out, tol, &tags);
    Aabb active_zone;
    if (box_a.valid() && box_b.valid()) {
        active_zone.lo = {std::max(box_a.lo.x, box_b.lo.x), std::max(box_a.lo.y, box_b.lo.y),
                          std::max(box_a.lo.z, box_b.lo.z)};
        active_zone.hi = {std::min(box_a.hi.x, box_b.hi.x), std::min(box_a.hi.y, box_b.hi.y),
                          std::min(box_a.hi.z, box_b.hi.z)};
    }
    repair_t_junctions_in_zone(out, tol, active_zone);
    remove_collapsed_faces(out, &tags);
    if (params.merge_uncut_faces && params.merge_coplanar_fragments) {
        merge_fragments(out, tags, originals, plane_eps, op == BooleanOp::Difference);
    }
    compact_vertices(out);
    return out;
}

}  // namespace pg
