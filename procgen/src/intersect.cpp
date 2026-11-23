#include "pg/quality.hpp"

#include "exact_kernel.hpp"
#include "pg/boxes.hpp"

#include <Delaunay_psm.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <type_traits>
#include <utility>
#include <vector>

namespace pg {
namespace {

static_assert(sizeof(Vec3) == 3 * sizeof(double) && std::is_standard_layout_v<Vec3>);

struct Tri {
    Index v[3];
    Index face;
};

inline const double* ptr(const Vec3& p) noexcept { return &p.x; }

inline int sign_tol(double x, double tol) noexcept { return x > tol ? 1 : (x < -tol ? -1 : 0); }

inline double orient3d_fp(Vec3 a, Vec3 b, Vec3 c, Vec3 d) noexcept { return dot(b - a, cross(c - a, d - a)); }

inline int orient3d_exact(const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& d) noexcept {
    return static_cast<int>(GEO::PCK::orient_3d(ptr(a), ptr(b), ptr(c), ptr(d)));
}

// Segment pq properly crosses triangle abc (no touching, no coplanar contact).
bool segment_crosses_triangle_tol(Vec3 p, Vec3 q, Vec3 a, Vec3 b, Vec3 c, double tol) noexcept {
    const int sp = sign_tol(orient3d_fp(a, b, c, p), tol);
    const int sq = sign_tol(orient3d_fp(a, b, c, q), tol);
    if (sp == 0 || sq == 0 || sp == sq) return false;
    const int s1 = sign_tol(orient3d_fp(p, q, a, b), tol);
    const int s2 = sign_tol(orient3d_fp(p, q, b, c), tol);
    const int s3 = sign_tol(orient3d_fp(p, q, c, a), tol);
    return s1 != 0 && s1 == s2 && s2 == s3;
}

bool triangles_cross_tol(const Vec3* t0, const Vec3* t1, double tol) noexcept {
    for (int i = 0; i < 3; ++i) {
        if (segment_crosses_triangle_tol(t0[i], t0[(i + 1) % 3], t1[0], t1[1], t1[2], tol)) return true;
        if (segment_crosses_triangle_tol(t1[i], t1[(i + 1) % 3], t0[0], t0[1], t0[2], tol)) return true;
    }
    return false;
}

// Exact proper crossing using PCK::orient_3d. When `allow_edge_pierce` is true,
// also catches non-coplanar segments piercing a triangle edge transversely.
bool segment_crosses_triangle_exact(const Vec3& p, const Vec3& q, const Vec3& a, const Vec3& b, const Vec3& c,
                                    bool allow_edge_pierce) noexcept {
    const int sp = orient3d_exact(a, b, c, p);
    const int sq = orient3d_exact(a, b, c, q);
    if (sp == 0 || sq == 0 || sp == sq) return false;
    const int s1 = orient3d_exact(p, q, a, b);
    const int s2 = orient3d_exact(p, q, b, c);
    const int s3 = orient3d_exact(p, q, c, a);
    if (!allow_edge_pierce) {
        return s1 != 0 && s1 == s2 && s2 == s3;
    }
    // Non-strict inside/edge of triangle: all non-zero signs match and at least two are non-zero
    // (excludes piercing purely through a single vertex).
    const int nz = (s1 != 0) + (s2 != 0) + (s3 != 0);
    if (nz < 2) return false;
    const bool non_neg = s1 >= 0 && s2 >= 0 && s3 >= 0;
    const bool non_pos = s1 <= 0 && s2 <= 0 && s3 <= 0;
    return non_neg || non_pos;
}

// Exact 2D interior overlap test for two coplanar triangles via PCK::orient_2d.
// Returns true iff their 2D open interiors intersect (partial overlap, containment,
// or proper edge crossings in their shared plane); edge-only or vertex-only
// touching in the plane returns false.
bool coplanar_triangles_overlap_2d(const Vec3* t0, const Vec3* t1) noexcept {
    if (GEO::PCK::aligned_3d(ptr(t0[0]), ptr(t0[1]), ptr(t0[2])) ||
        GEO::PCK::aligned_3d(ptr(t1[0]), ptr(t1[1]), ptr(t1[2]))) {
        return false;
    }
    const Vec3 n = cross(t0[1] - t0[0], t0[2] - t0[0]);
    const double ax = std::abs(n.x), ay = std::abs(n.y), az = std::abs(n.z);
    const int dom = (ax >= ay && ax >= az) ? 0 : (ay >= az ? 1 : 2);
    const int u = (dom + 1) % 3;
    const int v = (dom + 2) % 3;

    std::array<std::array<double, 2>, 3> p{{{t0[0][u], t0[0][v]}, {t0[1][u], t0[1][v]}, {t0[2][u], t0[2][v]}}};
    std::array<std::array<double, 2>, 3> q{{{t1[0][u], t1[0][v]}, {t1[1][u], t1[1][v]}, {t1[2][u], t1[2][v]}}};

    const int op = static_cast<int>(GEO::PCK::orient_2d(p[0].data(), p[1].data(), p[2].data()));
    const int oq = static_cast<int>(GEO::PCK::orient_2d(q[0].data(), q[1].data(), q[2].data()));
    if (op == 0 || oq == 0) return false;
    if (op < 0) std::swap(p[1], p[2]);
    if (oq < 0) std::swap(q[1], q[2]);

    // Separating Axis Theorem on CCW triangles: open interiors are disjoint iff
    // at least one directed edge of p or q puts all 3 vertices of the other
    // triangle on or to its right (orient_2d <= 0).
    for (int i = 0; i < 3; ++i) {
        const double* e0 = p[static_cast<std::size_t>(i)].data();
        const double* e1 = p[static_cast<std::size_t>((i + 1) % 3)].data();
        if (GEO::PCK::orient_2d(e0, e1, q[0].data()) <= 0 &&
            GEO::PCK::orient_2d(e0, e1, q[1].data()) <= 0 &&
            GEO::PCK::orient_2d(e0, e1, q[2].data()) <= 0) {
            return false;
        }
    }
    for (int i = 0; i < 3; ++i) {
        const double* e0 = q[static_cast<std::size_t>(i)].data();
        const double* e1 = q[static_cast<std::size_t>((i + 1) % 3)].data();
        if (GEO::PCK::orient_2d(e0, e1, p[0].data()) <= 0 &&
            GEO::PCK::orient_2d(e0, e1, p[1].data()) <= 0 &&
            GEO::PCK::orient_2d(e0, e1, p[2].data()) <= 0) {
            return false;
        }
    }
    return true;
}

bool triangles_intersect(const Vec3* t0, const Vec3* t1, double tol, bool use_exact_3d,
                         bool include_coplanar) noexcept {
    if (use_exact_3d) {
        for (int i = 0; i < 3; ++i) {
            if (segment_crosses_triangle_exact(t0[i], t0[(i + 1) % 3], t1[0], t1[1], t1[2], include_coplanar)) {
                return true;
            }
            if (segment_crosses_triangle_exact(t1[i], t1[(i + 1) % 3], t0[0], t0[1], t0[2], include_coplanar)) {
                return true;
            }
        }
        if (include_coplanar &&
            orient3d_exact(t0[0], t0[1], t0[2], t1[0]) == 0 &&
            orient3d_exact(t0[0], t0[1], t0[2], t1[1]) == 0 &&
            orient3d_exact(t0[0], t0[1], t0[2], t1[2]) == 0) {
            return coplanar_triangles_overlap_2d(t0, t1);
        }
        return false;
    }

    if (triangles_cross_tol(t0, t1, tol)) return true;
    if (include_coplanar) {
        const bool cop0 = sign_tol(orient3d_fp(t0[0], t0[1], t0[2], t1[0]), tol) == 0 &&
                          sign_tol(orient3d_fp(t0[0], t0[1], t0[2], t1[1]), tol) == 0 &&
                          sign_tol(orient3d_fp(t0[0], t0[1], t0[2], t1[2]), tol) == 0;
        if (cop0) return coplanar_triangles_overlap_2d(t0, t1);
        for (int i = 0; i < 3; ++i) {
            if (segment_crosses_triangle_exact(t0[i], t0[(i + 1) % 3], t1[0], t1[1], t1[2], true)) return true;
            if (segment_crosses_triangle_exact(t1[i], t1[(i + 1) % 3], t0[0], t0[1], t0[2], true)) return true;
        }
    }
    return false;
}

inline bool share_vertex(const Tri& a, const Tri& b) noexcept {
    for (Index x : a.v)
        for (Index y : b.v)
            if (x == y) return true;
    return false;
}

}  // namespace

SelfIntersectionResult find_self_intersections(const Mesh& m, const SelfIntersectionOptions& opts,
                                               std::vector<std::pair<Index, Index>>* face_pairs) {
    SelfIntersectionResult result;
    std::vector<Tri> tris;
    tris.reserve(m.corner_count());
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
            tris.push_back({{fv[0], fv[i], fv[i + 1]}, static_cast<Index>(f)});
        }
    }
    result.triangles = tris.size();
    if (tris.size() < 2) return result;

    const bool use_exact_3d = opts.exact_predicates && opts.tolerance_rel <= 0.0;
    if (use_exact_3d || opts.include_coplanar) {
        detail::ensure_exact_kernel();
    }

    double tol = 0.0;
    if (!use_exact_3d) {
        const Aabb box = bounds(m);
        const double diag = box.diagonal();
        tol = std::max(0.0, opts.tolerance_rel) * diag * diag * diag;
    }

    const std::size_t n = tris.size();
    std::vector<Aabb> tb(n);
    for (std::size_t i = 0; i < n; ++i) {
        for (Index v : tris[i].v) tb[i].expand(m.positions[v]);
    }

    boxes_intersections(tb, [&](std::uint32_t ti, std::uint32_t tj) {
        const Tri& A = tris[ti];
        const Tri& B = tris[tj];
        if (A.face == B.face || share_vertex(A, B)) return;
        ++result.candidate_pairs;
        const Vec3 pa[3] = {m.positions[A.v[0]], m.positions[A.v[1]], m.positions[A.v[2]]};
        const Vec3 pb[3] = {m.positions[B.v[0]], m.positions[B.v[1]], m.positions[B.v[2]]};
        if (triangles_intersect(pa, pb, tol, use_exact_3d, opts.include_coplanar)) {
            ++result.intersecting_pairs;
            if (face_pairs) face_pairs->emplace_back(std::min(A.face, B.face), std::max(A.face, B.face));
        }
    });

    if (face_pairs && face_pairs->size() > 1) {
        std::sort(face_pairs->begin(), face_pairs->end());
    }
    return result;
}

}  // namespace pg
