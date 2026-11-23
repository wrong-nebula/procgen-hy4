#pragma once
// Closest point on a mesh surface.
//
// The reference is a scan of every triangle (`closest_point_scan`). `ClosestIndex` gives the SAME
// answer, bit for bit, from a bounding-volume tree built once per mesh: the same point, normal,
// distance and triangle, including which triangle wins when several are equally close. It is what
// the detail generators project with (docs/DETAILING.md); the measurements behind it are in
// docs/experiments/closest-point-index.md.
//
// The answer, for a query point p, a largest distance and a `metric` (a uniform scale from mesh
// units to the caller's, 1 by default):
//   - faces are fanned from their first vertex, in face order; a triangle whose normal cannot be
//     taken (|ab x ac| <= 1e-20) is left out;
//   - each triangle's distance is length(q - p) * metric with q = closest_on_triangle(p, a, b, c);
//   - the first triangle, in that order, that attains the smallest distance wins;
//   - there is a hit when that distance is <= max_distance.

#include "pg/mesh.hpp"

#include <cstdint>
#include <span>
#include <vector>

namespace pg {

// The point of triangle abc nearest to p (Ericson, Real-Time Collision Detection, 5.1.5). Defined
// here and always inlined: every caller then computes it the same way, and a loop over triangles
// does not pass twelve doubles through memory per call (which made the scan half as slow again).
[[nodiscard, gnu::always_inline]] inline Vec3 closest_on_triangle(Vec3 p, Vec3 a, Vec3 b, Vec3 c) noexcept {
    const Vec3 ab = b - a, ac = c - a, ap = p - a;
    const double d1 = dot(ab, ap), d2 = dot(ac, ap);
    if (d1 <= 0 && d2 <= 0) return a;
    const Vec3 bp = p - b;
    const double d3 = dot(ab, bp), d4 = dot(ac, bp);
    if (d3 >= 0 && d4 <= d3) return b;
    const double vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0) return a + ab * (d1 / (d1 - d3));
    const Vec3 cp = p - c;
    const double d5 = dot(ab, cp), d6 = dot(ac, cp);
    if (d6 >= 0 && d5 <= d6) return c;
    const double vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0) return a + ac * (d2 / (d2 - d6));
    const double va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)));
    const double denom = 1.0 / (va + vb + vc);
    return a + ab * (vb * denom) + ac * (vc * denom);
}

struct ClosestHit {
    Vec3 point{}, normal{};      // on the surface; the unit normal of the triangle it lies on
    double distance = 0.0;       // length(point - query) * metric
    std::uint32_t face = 0;      // the polygon the triangle belongs to
    std::uint32_t triangle = 0;  // the triangle's number in scan order
};

struct RaycastHit {
    Vec3 point{}, normal{};      // surface hit point; unit outward normal of the triangle
    double t = 0.0;              // ray parameter: origin + dir * t (world distance when |dir| == 1)
    double u = 0.0, v = 0.0;     // barycentrics on (a, b, c): a + (b - a)*u + (c - a)*v
    std::uint32_t face = 0;      // polygon index in the mesh
    std::uint32_t triangle = 0;  // fan triangle index in scan order
    bool front_face = true;      // true when dot(dir, normal) < 0
};

// Shared Möller–Trumbore ray-triangle test used by both `raycast_scan` and `ClosestIndex::raycast`
// so the BVH and the full scan agree bit for bit.
[[nodiscard, gnu::always_inline]] inline bool ray_triangle_intersect(
    Vec3 origin, Vec3 dir, Vec3 a, Vec3 b, Vec3 c, double t_min, double t_max, bool cull_backfaces,
    double& t_out, double& u_out, double& v_out) noexcept {
    const Vec3 ab = b - a, ac = c - a;
    const Vec3 pvec = cross(dir, ac);
    const double det = dot(ab, pvec);
    if (cull_backfaces) {
        if (!(det > 1e-20)) return false;
    } else {
        if (!(std::abs(det) > 1e-20)) return false;
    }
    const double inv_det = 1.0 / det;
    const Vec3 tvec = origin - a;
    const double u = dot(tvec, pvec) * inv_det;
    if (!(u >= 0.0 && u <= 1.0)) return false;
    const Vec3 qvec = cross(tvec, ab);
    const double v = dot(dir, qvec) * inv_det;
    if (!(v >= 0.0 && u + v <= 1.0)) return false;
    const double t = dot(ac, qvec) * inv_det;
    if (!(t >= t_min && t <= t_max)) return false;
    t_out = t;
    u_out = u;
    v_out = v;
    return true;
}

// The reference: every triangle, in order. `hit` is meaningful only when this returns true.
[[nodiscard]] bool closest_point_scan(const Mesh& m, Vec3 p, double max_distance, ClosestHit& hit, double metric = 1.0) noexcept;
[[nodiscard]] bool raycast_scan(const Mesh& m, Vec3 origin, Vec3 dir, double t_min, double t_max, RaycastHit& hit,
                                bool cull_backfaces = false) noexcept;

class ClosestIndex {
public:
    struct Options {
        enum class Split : std::uint8_t {
            SurfaceArea,  // binned surface-area heuristic along the axis the triangle centres spread most on
            Median,       // equal counts either side, same axis
        };
        Split split = Split::SurfaceArea;
        int leaf_size = 4;      // triangles per leaf, 1..8
        bool all_axes = false;  // SurfaceArea: evaluate all three axes instead of the longest (slower build)
    };

    // Not a stable interface: exposed so benchmarks can build other layouts over the same tree.
    struct Triangle {
        Vec3 a, b, c;                // exactly the mesh's values: the distance must come out the same
        std::uint32_t order, face;   // number in scan order; polygon
    };
    // One cache line holding BOTH children's boxes (single precision, rounded outwards), so a visit
    // decides on two subtrees without touching them.
    struct Node {
        float lo[2][3], hi[2][3];
        std::uint32_t child[2];  // a node, or kLeaf | (count - 1) << 28 | first triangle
        float kappa[2];          // conditioning of the worst triangle below (see closest.cpp)
    };
    static constexpr std::uint32_t kLeaf = 0x80000000u;

    ClosestIndex() = default;
    explicit ClosestIndex(const Mesh& m) : ClosestIndex(m, Options{}) {}
    ClosestIndex(const Mesh& m, Options options);

    // Same result as closest_point_scan on the mesh the index was built from. `hit` is written only
    // when there is one. Safe to call from several threads at once.
    [[nodiscard]] bool closest(Vec3 p, double max_distance, ClosestHit& hit, double metric = 1.0) const noexcept;

    // Exact ray intersection matching `raycast_scan` bit for bit (smallest `t` in [t_min, t_max],
    // lowest scan-order triangle on an exact `t` tie). Thread-safe across concurrent callers.
    [[nodiscard]] bool raycast(Vec3 origin, Vec3 dir, double t_min, double t_max, RaycastHit& hit,
                               bool cull_backfaces = false) const noexcept;

    // Early-out occlusion / support probe: returns true iff any triangle is hit in [t_min, t_max].
    [[nodiscard]] bool any_hit(Vec3 origin, Vec3 dir, double t_min, double t_max,
                               bool cull_backfaces = false) const noexcept;

    [[nodiscard]] std::size_t triangle_count() const noexcept { return triangles_.size(); }
    [[nodiscard]] bool empty() const noexcept { return triangles_.empty(); }
    [[nodiscard]] std::span<const Triangle> triangles() const noexcept { return triangles_; }
    [[nodiscard]] std::span<const Node> nodes() const noexcept { return nodes_; }
    [[nodiscard]] std::uint32_t root() const noexcept { return root_; }
    [[nodiscard]] Aabb bounds() const noexcept { return bounds_; }
    [[nodiscard]] std::size_t memory_bytes() const noexcept { return triangles_.size() * sizeof(Triangle) + nodes_.size() * sizeof(Node); }

private:
    std::vector<Triangle> triangles_;  // leaf order (scan order when there is no tree)
    std::vector<Node> nodes_;          // empty: every query tests every triangle
    std::uint32_t root_ = 0;
    Aabb bounds_;
    double root_kappa_ = 0.0, diagonal_ = 0.0, rounding_ = 0.0;
};

}  // namespace pg
