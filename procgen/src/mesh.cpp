#include "pg/mesh.hpp"

#include <algorithm>

namespace pg {

void Mesh::set_edge_crease(Index a, Index b, float sharpness) {
    if (a == b) return;
    const std::uint64_t k = edge_key(a, b);
    const float s = std::clamp(sharpness, 0.0f, kCreaseInfinite);
    auto it = std::lower_bound(edge_creases.begin(), edge_creases.end(), k,
                               [](const EdgeCrease& c, std::uint64_t key) { return c.key < key; });
    if (it != edge_creases.end() && it->key == k) {
        if (s > 0.0f) it->sharpness = s;
        else edge_creases.erase(it);
    } else if (s > 0.0f) {
        edge_creases.insert(it, EdgeCrease{k, s});
    }
}

void Mesh::set_vertex_crease(Index v, float sharpness) {
    const float s = std::clamp(sharpness, 0.0f, kCreaseInfinite);
    auto it = std::lower_bound(vertex_creases.begin(), vertex_creases.end(), v,
                               [](const VertexCrease& c, Index idx) { return c.vertex < idx; });
    if (it != vertex_creases.end() && it->vertex == v) {
        if (s > 0.0f) it->sharpness = s;
        else vertex_creases.erase(it);
    } else if (s > 0.0f) {
        vertex_creases.insert(it, VertexCrease{v, s});
    }
}

float Mesh::edge_crease(Index a, Index b) const noexcept {
    if (edge_creases.empty() || a == b) return 0.0f;
    const std::uint64_t k = edge_key(a, b);
    const auto it = std::lower_bound(edge_creases.begin(), edge_creases.end(), k,
                                     [](const EdgeCrease& c, std::uint64_t key) { return c.key < key; });
    return (it != edge_creases.end() && it->key == k) ? it->sharpness : 0.0f;
}

float Mesh::vertex_crease(Index v) const noexcept {
    if (vertex_creases.empty()) return 0.0f;
    const auto it = std::lower_bound(vertex_creases.begin(), vertex_creases.end(), v,
                                     [](const VertexCrease& c, Index idx) { return c.vertex < idx; });
    return (it != vertex_creases.end() && it->vertex == v) ? it->sharpness : 0.0f;
}

void Mesh::normalize_creases() {
    if (!edge_creases.empty()) {
        std::sort(edge_creases.begin(), edge_creases.end(),
                  [](const EdgeCrease& x, const EdgeCrease& y) { return x.key < y.key; });
        std::size_t out = 0;
        for (std::size_t i = 0; i < edge_creases.size();) {
            std::size_t j = i + 1;
            float s = std::clamp(edge_creases[i].sharpness, 0.0f, kCreaseInfinite);
            while (j < edge_creases.size() && edge_creases[j].key == edge_creases[i].key) {
                s = std::max(s, std::clamp(edge_creases[j].sharpness, 0.0f, kCreaseInfinite));
                ++j;
            }
            const auto lo = static_cast<Index>(edge_creases[i].key >> 32);
            const auto hi = static_cast<Index>(edge_creases[i].key & 0xffffffffu);
            if (s > 0.0f && lo != hi && hi < positions.size()) {
                edge_creases[out++] = {edge_creases[i].key, s};
            }
            i = j;
        }
        edge_creases.resize(out);
    }
    if (!vertex_creases.empty()) {
        std::sort(vertex_creases.begin(), vertex_creases.end(),
                  [](const VertexCrease& x, const VertexCrease& y) { return x.vertex < y.vertex; });
        std::size_t out = 0;
        for (std::size_t i = 0; i < vertex_creases.size();) {
            std::size_t j = i + 1;
            float s = std::clamp(vertex_creases[i].sharpness, 0.0f, kCreaseInfinite);
            while (j < vertex_creases.size() && vertex_creases[j].vertex == vertex_creases[i].vertex) {
                s = std::max(s, std::clamp(vertex_creases[j].sharpness, 0.0f, kCreaseInfinite));
                ++j;
            }
            if (s > 0.0f && vertex_creases[i].vertex < positions.size()) {
                vertex_creases[out++] = {vertex_creases[i].vertex, s};
            }
            i = j;
        }
        vertex_creases.resize(out);
    }
}

Vec3 face_area_vector(const Mesh& m, std::size_t f) noexcept {
    const auto fv = m.face(f);
    if (fv.size() < 3) return {};
    const Vec3 p0 = m.positions[fv[0]];
    Vec3 n{};
    for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
        n += cross(m.positions[fv[i]] - p0, m.positions[fv[i + 1]] - p0);
    }
    return n;
}

Vec3 face_centroid(const Mesh& m, std::size_t f) noexcept {
    const auto fv = m.face(f);
    Vec3 c{};
    for (Index v : fv) c += m.positions[v];
    return fv.empty() ? c : c / static_cast<double>(fv.size());
}

Aabb bounds(const Mesh& m) noexcept {
    Aabb box;
    for (const Vec3& p : m.positions) box.expand(p);
    return box;
}

bool indices_valid(const Mesh& m) noexcept {
    if (m.face_offsets.empty() || m.face_offsets.front() != 0) return false;
    if (m.face_offsets.back() != m.face_indices.size()) return false;
    for (std::size_t f = 0; f + 1 < m.face_offsets.size(); ++f) {
        if (m.face_offsets[f + 1] < m.face_offsets[f] + 3) return false;
    }
    const std::size_t nv = m.positions.size();
    for (Index v : m.face_indices) {
        if (v >= nv) return false;
    }
    return true;
}

Mesh merge(const Mesh& a, const Mesh& b) {
    Mesh out;
    out.reserve(a.vertex_count() + b.vertex_count(), a.face_count() + b.face_count(),
                a.corner_count() + b.corner_count());
    out.positions = a.positions;
    out.positions.insert(out.positions.end(), b.positions.begin(), b.positions.end());
    out.face_offsets = a.face_offsets;
    out.face_indices = a.face_indices;
    const auto vbase = static_cast<Index>(a.vertex_count());
    const auto cbase = static_cast<Index>(a.corner_count());
    for (std::size_t f = 1; f < b.face_offsets.size(); ++f) out.face_offsets.push_back(b.face_offsets[f] + cbase);
    for (Index v : b.face_indices) out.face_indices.push_back(v + vbase);
    if (!a.edge_creases.empty() || !b.edge_creases.empty()) {
        out.edge_creases = a.edge_creases;
        out.edge_creases.reserve(a.edge_creases.size() + b.edge_creases.size());
        for (const EdgeCrease& c : b.edge_creases) {
            const auto lo = static_cast<Index>(c.key >> 32) + vbase;
            const auto hi = static_cast<Index>(c.key & 0xffffffffu) + vbase;
            out.edge_creases.push_back({edge_key(lo, hi), c.sharpness});
        }
        out.normalize_creases();
    }
    if (!a.vertex_creases.empty() || !b.vertex_creases.empty()) {
        out.vertex_creases = a.vertex_creases;
        out.vertex_creases.reserve(a.vertex_creases.size() + b.vertex_creases.size());
        for (const VertexCrease& c : b.vertex_creases) {
            out.vertex_creases.push_back({c.vertex + vbase, c.sharpness});
        }
        out.normalize_creases();
    }
    return out;
}

void translate(Mesh& m, Vec3 offset) noexcept {
    for (Vec3& p : m.positions) p += offset;
}

}  // namespace pg
