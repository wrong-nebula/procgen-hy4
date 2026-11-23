#pragma once

#include "pg/math.hpp"

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <span>
#include <vector>

namespace pg {

using Index = std::uint32_t;

[[nodiscard]] constexpr std::uint64_t edge_key(Index a, Index b) noexcept {
    const Index lo = a < b ? a : b;
    const Index hi = a < b ? b : a;
    return (static_cast<std::uint64_t>(lo) << 32) | hi;
}

// Sparse subdivision-surface sharpness tags (OpenSubdiv scale: 0 = smooth,
// 1..9 = semi-sharp, >= 10 = infinitely sharp).
constexpr float kCreaseInfinite = 10.0f;

struct EdgeCrease {
    std::uint64_t key = 0;  // edge_key(v0, v1)
    float sharpness = 0.0f;
    friend constexpr auto operator<=>(const EdgeCrease&, const EdgeCrease&) = default;
};

struct VertexCrease {
    Index vertex = 0;
    float sharpness = 0.0f;
    friend constexpr auto operator<=>(const VertexCrease&, const VertexCrease&) = default;
};

// Polygon mesh in compressed-row layout. Faces are counter-clockwise when seen
// from outside. There are no per-element objects: positions, face offsets and
// corner indices are flat arrays, so operations stream over memory and
// adjacency is derived on demand (see topology.hpp) instead of being maintained.
struct Mesh {
    std::vector<Vec3> positions;
    std::vector<Index> face_offsets{0};  // face f = face_indices[offsets[f], offsets[f+1])
    std::vector<Index> face_indices;
    // Sparse sharpness tags; empty for ordinary polygonal meshes.
    std::vector<EdgeCrease> edge_creases;
    std::vector<VertexCrease> vertex_creases;
    // Optional rational homogeneous weights (NURBS/NURSS); empty == uniform 1.0.
    std::vector<double> vertex_weights;

    [[nodiscard]] std::size_t vertex_count() const noexcept { return positions.size(); }
    [[nodiscard]] std::size_t face_count() const noexcept { return face_offsets.size() - 1; }
    [[nodiscard]] std::size_t corner_count() const noexcept { return face_indices.size(); }
    [[nodiscard]] std::span<const Index> face(std::size_t f) const noexcept {
        return {face_indices.data() + face_offsets[f], face_offsets[f + 1] - face_offsets[f]};
    }

    Index add_vertex(Vec3 p) {
        positions.push_back(p);
        return static_cast<Index>(positions.size() - 1);
    }
    void add_face(std::span<const Index> verts) {
        face_indices.insert(face_indices.end(), verts.begin(), verts.end());
        face_offsets.push_back(static_cast<Index>(face_indices.size()));
    }
    void add_face(std::initializer_list<Index> verts) { add_face(std::span<const Index>(verts.begin(), verts.size())); }
    void reserve(std::size_t verts, std::size_t faces, std::size_t corners) {
        positions.reserve(verts);
        face_offsets.reserve(faces + 1);
        face_indices.reserve(corners);
    }

    void set_edge_crease(Index a, Index b, float sharpness);
    void set_vertex_crease(Index v, float sharpness);
    [[nodiscard]] float edge_crease(Index a, Index b) const noexcept;
    [[nodiscard]] float vertex_crease(Index v) const noexcept;
    void set_vertex_weight(Index v, double w) {
        if (v >= positions.size()) return;
        if (vertex_weights.size() < positions.size()) vertex_weights.resize(positions.size(), 1.0);
        vertex_weights[v] = w > 1e-4 ? w : 1e-4;
    }
    [[nodiscard]] double vertex_weight(Index v) const noexcept {
        return v < vertex_weights.size() ? vertex_weights[v] : 1.0;
    }
    void normalize_creases();
};

// Sum of fan cross products relative to the first vertex: 2 * area * normal for
// planar faces, and translation invariant (stable far from the origin).
[[nodiscard]] Vec3 face_area_vector(const Mesh& m, std::size_t f) noexcept;
[[nodiscard]] inline double face_area(const Mesh& m, std::size_t f) noexcept { return 0.5 * length(face_area_vector(m, f)); }
[[nodiscard]] inline Vec3 face_normal(const Mesh& m, std::size_t f) noexcept {
    return normalize_or(face_area_vector(m, f), Vec3{});
}
[[nodiscard]] Vec3 face_centroid(const Mesh& m, std::size_t f) noexcept;
[[nodiscard]] Aabb bounds(const Mesh& m) noexcept;

// Structural validity: monotonic offsets, >= 3 corners per face, indices in range.
[[nodiscard]] bool indices_valid(const Mesh& m) noexcept;

[[nodiscard]] Mesh merge(const Mesh& a, const Mesh& b);
void translate(Mesh& m, Vec3 offset) noexcept;

}  // namespace pg
