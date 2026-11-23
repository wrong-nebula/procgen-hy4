#include "pg/ops/extrude.hpp"

#include "pg/topology.hpp"

#include <algorithm>
#include <stdexcept>

namespace pg {
namespace {

Mesh extrude_individual(const Mesh& m, std::span<const std::uint8_t> mask, double distance) {
    std::size_t sel_corners = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f)
        if (mask[f]) sel_corners += m.face(f).size();

    Mesh out;
    out.reserve(m.vertex_count() + sel_corners, m.face_count() + sel_corners, m.corner_count() + sel_corners * 4);
    out.positions = m.positions;
    std::vector<Index> cap;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        if (!mask[f]) { out.add_face(fv); continue; }
        const Vec3 off = face_normal(m, f) * distance;
        cap.resize(fv.size());
        for (std::size_t i = 0; i < fv.size(); ++i) cap[i] = out.add_vertex(m.positions[fv[i]] + off);
        out.add_face(cap);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const std::size_t j = (i + 1) % fv.size();
            out.add_face({fv[i], fv[j], cap[j], cap[i]});
        }
    }
    return out;
}

Mesh extrude_region(const Mesh& m, std::span<const std::uint8_t> mask, const ExtrudeParams& p) {
    const std::size_t nf = m.face_count();
    const std::size_t nv = m.vertex_count();

    std::vector<Vec3> normal(nf);
    for (std::size_t f = 0; f < nf; ++f)
        if (mask[f]) normal[f] = face_normal(m, f);

    // Region boundary: a selected half-edge whose opposite is not also selected.
    // Only selected faces enter the sort, so unselected geometry costs nothing.
    const auto hes = sorted_half_edges(m, mask);
    std::vector<std::pair<Index, Index>> walls;
    std::vector<std::uint8_t> split(nv, 0);
    for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
        for (const HalfEdge& h : g) {
            const bool twinned = std::any_of(g.begin(), g.end(), [&](const HalfEdge& k) { return k.from != h.from; });
            if (twinned) continue;
            walls.emplace_back(h.from, h.to());
            split[h.from] = split[h.to()] = 1;
        }
    });
    // A vertex shared with any unselected face must stay put for that face, even
    // around non-manifold fans where no boundary edge reaches it.
    for (std::size_t f = 0; f < nf; ++f)
        if (!mask[f])
            for (Index v : m.face(f)) split[v] = 1;

    // Per-vertex direction = mean of adjacent selected face normals.
    std::vector<Vec3> dir(nv);
    std::vector<Vec3> fallback(nv);
    std::vector<std::uint8_t> touched(nv, 0);
    for (std::size_t f = 0; f < nf; ++f) {
        if (!mask[f]) continue;
        for (Index v : m.face(f)) {
            dir[v] += normal[f];
            if (!touched[v]) fallback[v] = normal[f];
            touched[v] = 1;
        }
    }
    for (std::size_t v = 0; v < nv; ++v)
        if (touched[v]) dir[v] = normalize_or(dir[v], fallback[v]);

    std::vector<double> scale(nv, 1.0);
    if (p.even_thickness) {
        std::vector<double> min_cos(nv, 1.0);
        for (std::size_t f = 0; f < nf; ++f) {
            if (!mask[f]) continue;
            for (Index v : m.face(f)) min_cos[v] = std::min(min_cos[v], dot(dir[v], normal[f]));
        }
        const double floor_cos = 1.0 / std::max(1.0, p.max_thickness_scale);
        for (std::size_t v = 0; v < nv; ++v) scale[v] = 1.0 / std::max(min_cos[v], floor_cos);
    }

    Mesh out;
    out.reserve(nv + nv / 2, nf + walls.size(), m.corner_count() + walls.size() * 4);
    out.positions = m.positions;
    std::vector<Index> moved(nv);
    for (std::size_t v = 0; v < nv; ++v) {
        moved[v] = static_cast<Index>(v);
        if (!touched[v]) continue;
        const Vec3 q = m.positions[v] + dir[v] * (p.distance * scale[v]);
        if (split[v]) moved[v] = out.add_vertex(q);
        else out.positions[v] = q;
    }

    std::vector<Index> poly;
    for (std::size_t f = 0; f < nf; ++f) {
        const auto fv = m.face(f);
        if (!mask[f]) { out.add_face(fv); continue; }
        poly.assign(fv.begin(), fv.end());
        for (Index& v : poly) v = moved[v];
        out.add_face(poly);
    }
    for (const auto& [a, b] : walls) out.add_face({a, b, moved[b], moved[a]});
    return out;
}

}  // namespace

Mesh extrude(const Mesh& m, std::span<const std::uint8_t> face_mask, const ExtrudeParams& params) {
    if (face_mask.size() != m.face_count()) throw std::invalid_argument("extrude: mask size != face count");
    if (std::none_of(face_mask.begin(), face_mask.end(), [](std::uint8_t s) { return s != 0; })) return m;
    return params.mode == ExtrudeMode::Individual ? extrude_individual(m, face_mask, params.distance)
                                                  : extrude_region(m, face_mask, params);
}

std::vector<std::uint8_t> select_all(const Mesh& m) { return std::vector<std::uint8_t>(m.face_count(), 1); }

std::vector<std::uint8_t> select_by_normal(const Mesh& m, Vec3 direction, double min_cos) {
    const Vec3 d = normalize_or(direction, Vec3{0, 0, 1});
    std::vector<std::uint8_t> mask(m.face_count(), 0);
    for (std::size_t f = 0; f < m.face_count(); ++f) mask[f] = dot(face_normal(m, f), d) >= min_cos ? 1 : 0;
    return mask;
}

}  // namespace pg
