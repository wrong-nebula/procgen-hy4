#include "pg/cleanup.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace pg {
namespace {

struct Cell {
    std::int64_t x, y, z;
    friend constexpr bool operator==(const Cell&, const Cell&) = default;
};

// Open-addressing hash grid: occupied cell -> head of a singly linked list of
// representative vertices (threaded through a per-vertex `next` array).
class CellGrid {
public:
    static constexpr Index kEmpty = ~Index{0};

    explicit CellGrid(std::size_t max_cells) {
        std::size_t cap = 16;
        while (cap < max_cells * 2) cap <<= 1;
        slots_.assign(cap, Slot{{0, 0, 0}, kEmpty});
        mask_ = cap - 1;
    }
    [[nodiscard]] Index head(const Cell& c) const noexcept {
        for (std::size_t i = hash(c) & mask_; slots_[i].head != kEmpty; i = (i + 1) & mask_)
            if (slots_[i].cell == c) return slots_[i].head;
        return kEmpty;
    }
    // Slot for c, created if absent. Never fills: capacity >= 2 * max_cells.
    Index& head_ref(const Cell& c) noexcept {
        std::size_t i = hash(c) & mask_;
        for (; slots_[i].head != kEmpty; i = (i + 1) & mask_)
            if (slots_[i].cell == c) return slots_[i].head;
        slots_[i].cell = c;
        return slots_[i].head;  // still kEmpty: caller links the first vertex
    }

private:
    struct Slot { Cell cell; Index head; };
    static std::size_t hash(const Cell& c) noexcept {
        std::uint64_t h = static_cast<std::uint64_t>(c.x) * 0x9E3779B97F4A7C15ull;
        h ^= static_cast<std::uint64_t>(c.y) * 0xC2B2AE3D27D4EB4Full;
        h ^= static_cast<std::uint64_t>(c.z) * 0x165667B19E3779F9ull;
        return static_cast<std::size_t>(h ^ (h >> 29));
    }
    std::vector<Slot> slots_;
    std::size_t mask_ = 0;
};

}  // namespace

std::size_t remove_collapsed_faces(Mesh& m, std::vector<std::uint32_t>* face_tags) {
    std::vector<Index> offsets{0};
    std::vector<Index> indices;
    offsets.reserve(m.face_offsets.size());
    indices.reserve(m.face_indices.size());
    std::size_t removed = 0;
    std::vector<Index> poly;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        poly.clear();
        for (Index v : m.face(f)) {
            if (poly.empty() || poly.back() != v) poly.push_back(v);
        }
        while (poly.size() > 1 && poly.front() == poly.back()) poly.pop_back();
        if (poly.size() < 3) { ++removed; continue; }
        if (face_tags) (*face_tags)[offsets.size() - 1] = (*face_tags)[f];
        indices.insert(indices.end(), poly.begin(), poly.end());
        offsets.push_back(static_cast<Index>(indices.size()));
    }
    if (face_tags) face_tags->resize(offsets.size() - 1);
    m.face_offsets = std::move(offsets);
    m.face_indices = std::move(indices);
    return removed;
}

std::size_t compact_vertices(Mesh& m) {
    constexpr Index unused = ~Index{0};
    std::vector<Index> remap(m.vertex_count(), unused);
    for (Index v : m.face_indices) remap[v] = 0;
    Index next = 0;
    for (std::size_t v = 0; v < remap.size(); ++v) {
        if (remap[v] != unused) {
            m.positions[next] = m.positions[v];
            remap[v] = next++;
        }
    }
    const std::size_t removed = m.positions.size() - next;
    m.positions.resize(next);
    for (Index& v : m.face_indices) v = remap[v];
    if (!m.edge_creases.empty()) {
        std::vector<EdgeCrease> kept;
        kept.reserve(m.edge_creases.size());
        for (const EdgeCrease& c : m.edge_creases) {
            const auto lo = static_cast<Index>(c.key >> 32);
            const auto hi = static_cast<Index>(c.key & 0xffffffffu);
            if (lo < remap.size() && hi < remap.size() && remap[lo] != unused && remap[hi] != unused && remap[lo] != remap[hi]) {
                kept.push_back({edge_key(remap[lo], remap[hi]), c.sharpness});
            }
        }
        m.edge_creases = std::move(kept);
        m.normalize_creases();
    }
    if (!m.vertex_creases.empty()) {
        std::vector<VertexCrease> kept;
        kept.reserve(m.vertex_creases.size());
        for (const VertexCrease& c : m.vertex_creases) {
            if (c.vertex < remap.size() && remap[c.vertex] != unused) {
                kept.push_back({remap[c.vertex], c.sharpness});
            }
        }
        m.vertex_creases = std::move(kept);
        m.normalize_creases();
    }
    return removed;
}

CleanupStats weld_vertices(Mesh& m, double tolerance, std::vector<std::uint32_t>* face_tags) {
    if (!(tolerance > 0.0)) throw std::invalid_argument("weld_vertices: tolerance must be > 0");
    const std::size_t nv = m.vertex_count();
    // Cells are 2*tolerance wide: every point within `tolerance` of p lies in
    // the 2x2x2 block of cells on p's side of its cell centre (8 probes, not 27).
    // Single streaming pass, no sort: vertices resolve in index order and only
    // representatives enter the grid, so every candidate is a lower-index
    // representative and the lowest one in range wins (deterministic).
    const double inv = 0.5 / tolerance;
    const double tol2 = tolerance * tolerance;
    CellGrid grid(nv);
    std::vector<Index> next(nv, CellGrid::kEmpty);
    std::vector<Index> rep(nv);
    CleanupStats stats;
    auto side = [](double t) -> std::int64_t { return t - std::floor(t) < 0.5 ? -1 : 1; };
    for (std::size_t v = 0; v < nv; ++v) {
        const Vec3 p = m.positions[v];
        const double tx = p.x * inv, ty = p.y * inv, tz = p.z * inv;
        const Cell c{static_cast<std::int64_t>(std::floor(tx)), static_cast<std::int64_t>(std::floor(ty)),
                     static_cast<std::int64_t>(std::floor(tz))};
        const std::int64_t sx = side(tx), sy = side(ty), sz = side(tz);
        Index best = static_cast<Index>(v);
        for (int k = 0; k < 8; ++k) {
            const Cell q{c.x + ((k & 1) ? sx : 0), c.y + ((k & 2) ? sy : 0), c.z + ((k & 4) ? sz : 0)};
            for (Index u = grid.head(q); u != CellGrid::kEmpty; u = next[u]) {
                if (u >= best) continue;
                const Vec3 d = m.positions[u] - p;
                if (dot(d, d) <= tol2) best = u;
            }
        }
        rep[v] = best;
        if (best != v) {
            ++stats.merged_vertices;
        } else {
            Index& head = grid.head_ref(c);
            next[v] = head;
            head = static_cast<Index>(v);
        }
    }
    for (Index& v : m.face_indices) v = rep[v];
    if (!m.edge_creases.empty()) {
        std::vector<EdgeCrease> remapped;
        remapped.reserve(m.edge_creases.size());
        for (const EdgeCrease& c : m.edge_creases) {
            const auto lo = static_cast<Index>(c.key >> 32);
            const auto hi = static_cast<Index>(c.key & 0xffffffffu);
            if (lo < rep.size() && hi < rep.size() && rep[lo] != rep[hi]) {
                remapped.push_back({edge_key(rep[lo], rep[hi]), c.sharpness});
            }
        }
        m.edge_creases = std::move(remapped);
        m.normalize_creases();
    }
    if (!m.vertex_creases.empty()) {
        std::vector<VertexCrease> remapped;
        remapped.reserve(m.vertex_creases.size());
        for (const VertexCrease& c : m.vertex_creases) {
            if (c.vertex < rep.size()) remapped.push_back({rep[c.vertex], c.sharpness});
        }
        m.vertex_creases = std::move(remapped);
        m.normalize_creases();
    }
    stats.removed_faces = remove_collapsed_faces(m, face_tags);
    stats.removed_vertices = compact_vertices(m);
    return stats;
}

}  // namespace pg
