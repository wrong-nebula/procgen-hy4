#include "pg/quality.hpp"

#include "pg/topology.hpp"

#include <cmath>

namespace pg {

QualityReport analyze(const Mesh& m, const QualityOptions& opts) {
    QualityReport r;
    r.vertices = m.vertex_count();
    r.faces = m.face_count();
    r.indices_valid = indices_valid(m);
    if (!r.indices_valid) return r;  // nothing below is safe on broken indices

    const Aabb box = bounds(m);
    const double diag = box.diagonal();
    const double degenerate_area = opts.degenerate_area_rel * diag * diag;
    const Vec3 origin = box.center();  // volume relative to the centre: less cancellation

    // Faces: area, signed volume, degeneracy, polygon class.
    for (std::size_t f = 0; f < r.faces; ++f) {
        const auto fv = m.face(f);
        if (fv.size() == 3) ++r.tri_faces;
        else if (fv.size() == 4) ++r.quad_faces;
        else if (fv.size() > 4) ++r.ngon_faces;
        bool repeated = false;
        for (std::size_t i = 0; i < fv.size() && !repeated; ++i)
            for (std::size_t j = i + 1; j < fv.size(); ++j)
                if (fv[i] == fv[j]) { repeated = true; break; }
        const double area = face_area(m, f);
        if (repeated || area <= degenerate_area) ++r.degenerate_faces;
        r.area += area;
        const Vec3 p0 = m.positions[fv[0]] - origin;
        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
            r.volume += dot(p0, cross(m.positions[fv[i]] - origin, m.positions[fv[i + 1]] - origin));
        }
    }
    r.volume /= 6.0;

    // Edges: manifoldness, orientation, length distribution (Welford), vertex valence.
    const auto hes = sorted_half_edges(m);
    std::vector<std::uint32_t> valence(r.vertices, 0);
    double mean = 0.0, m2 = 0.0;
    double lmin = std::numeric_limits<double>::infinity(), lmax = 0.0;
    for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
        ++r.edges;
        if (g.size() == 1) ++r.boundary_edges;
        else if (g.size() > 2) ++r.nonmanifold_edges;
        else if (g[0].from == g[1].from) ++r.inconsistent_edges;
        ++valence[g[0].lo()];
        ++valence[g[0].hi()];
        const double len = length(m.positions[g[0].hi()] - m.positions[g[0].lo()]);
        const double k = static_cast<double>(r.edges);
        const double delta = len - mean;
        mean += delta / k;
        m2 += delta * (len - mean);
        lmin = std::min(lmin, len);
        lmax = std::max(lmax, len);
    });
    if (r.edges > 0) {
        r.edge_lengths = {r.edges, lmin, lmax, mean, std::sqrt(m2 / static_cast<double>(r.edges))};
    }

    std::vector<std::uint8_t> used(r.vertices, 0);
    std::vector<std::uint8_t> on_boundary(r.vertices, 0);
    std::vector<std::uint8_t> on_crease(r.vertices, 0);
    std::vector<double> angle_sum(r.vertices, 0.0);
    for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
        if (g.size() != 2) {
            on_boundary[g[0].lo()] = 1;
            on_boundary[g[0].hi()] = 1;
        }
        if (m.edge_crease(g[0].lo(), g[0].hi()) >= 1.0f) {
            on_crease[g[0].lo()] = 1;
            on_crease[g[0].hi()] = 1;
        }
    });
    for (std::size_t f = 0; f < r.faces; ++f) {
        const auto fv = m.face(f);
        const std::size_t k = fv.size();
        for (std::size_t i = 0; i < k; ++i) {
            const Index v = fv[i];
            used[v] = 1;
            const Vec3 p = m.positions[v];
            const Vec3 e1 = normalize_or(m.positions[fv[(i + 1) % k]] - p, Vec3{1, 0, 0});
            const Vec3 e2 = normalize_or(m.positions[fv[(i + k - 1) % k]] - p, Vec3{0, 1, 0});
            angle_sum[v] += std::acos(std::clamp(dot(e1, e2), -1.0, 1.0));
        }
    }
    constexpr double kTwoPi = 6.28318530717958647692;
    for (std::size_t v = 0; v < r.vertices; ++v) {
        if (!used[v]) continue;
        ++r.referenced_vertices;
        if (valence[v] == 3) ++r.poles_valence_3;
        else if (valence[v] == 5) ++r.poles_valence_5;
        else if (valence[v] >= 6) ++r.high_valence_poles;
        if (valence[v] != 4 && !on_boundary[v] && !on_crease[v] && m.vertex_crease(static_cast<Index>(v)) < 1.0f) {
            const double defect = std::abs(kTwoPi - angle_sum[v]);
            r.max_ev_curvature = std::max(r.max_ev_curvature, defect);
            if (defect > 1.2) ++r.pinched_poles;
        }
    }
    r.creased_edges = m.edge_creases.size();
    r.creased_vertices = m.vertex_creases.size();
    r.euler = static_cast<std::int64_t>(r.referenced_vertices) - static_cast<std::int64_t>(r.edges) +
              static_cast<std::int64_t>(r.faces);

    if (opts.check_self_intersections) {
        r.self_intersections_checked = true;
        r.self_intersections = find_self_intersections(m, opts.self_intersection);
    }
    return r;
}

}  // namespace pg
