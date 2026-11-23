#pragma once

#include "pg/math.hpp"
#include "pg/mesh.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <utility>
#include <vector>

namespace pg {

inline constexpr Index kInvalidIndex = std::numeric_limits<Index>::max();

struct DpNnsOptions {
    // Enable level-consistent parallel BRIO Delaunay construction when supported.
    bool parallel = true;
    // Target points per seed-grid cell (controls grid resolution for O(1) walk seeding).
    std::size_t points_per_cell = 6;
    // Maximum total seed-grid cells (caps memory overhead on sparse volumes).
    std::size_t max_grid_cells = 32768;
    // Reorder batch queries by 30-bit 3D Morton code for cache locality.
    bool morton_batch_reorder = true;
};

struct DpNnsHit {
    Index index = kInvalidIndex;  // index into the original input `points` span
    double distance_sq = 0.0;
    std::uint32_t visited = 0;    // vertices evaluated along the Delaunay walk
};

struct DpNnsStats {
    std::size_t input_points = 0;
    std::size_t unique_sites = 0;
    std::size_t directed_edges = 0;
    std::size_t grid_cells = 0;
    std::size_t memory_bytes = 0;
};

// Accelerated Delaunay-based Nearest Neighbor Search (DP-NNS, docs/DP_NNS_SPEC.md):
// builds a flat CSR Delaunay adjacency graph from 3D/2D exact-predicate BRIO Delaunay
// triangulation plus a flood-filled 3D uniform seed grid for O(1) query initialization.
// Construction is single-owner; const query methods are thread-safe across concurrent
// reader threads (`query`, `query_knn`, `query_batch` allocate scratch locally or take none).
class DpNnsIndex {
public:
    DpNnsIndex() = default;
    explicit DpNnsIndex(std::span<const Vec3> points, const DpNnsOptions& opts = {});

    void build(std::span<const Vec3> points, const DpNnsOptions& opts = {});

    [[nodiscard]] bool empty() const noexcept { return input_count_ == 0; }
    [[nodiscard]] std::size_t size() const noexcept { return input_count_; }
    [[nodiscard]] DpNnsStats stats() const noexcept;

    // Exact 1-NN query. Returns `index = kInvalidIndex` iff the index is empty.
    [[nodiscard]] DpNnsHit query(Vec3 q) const noexcept;

    // Exact k-NN query in ascending distance order (up to `min(k, size())` hits).
    [[nodiscard]] std::vector<DpNnsHit> query_knn(Vec3 q, std::size_t k) const;

    // Batch 1-NN queries (optionally Morton-reordered internally for L1/L2 locality).
    [[nodiscard]] std::vector<DpNnsHit> query_batch(std::span<const Vec3> queries, bool morton_reorder = true) const;

private:
    enum class Mode : std::uint8_t { Empty, Linear, Graph };

    struct Site {
        Vec3 pos{};
        Index head_original = kInvalidIndex;  // first input index at this coordinate
    };

    Mode mode_ = Mode::Empty;
    std::size_t input_count_ = 0;
    std::vector<Site> sites_;
    // Linked list over input indices sharing identical coordinates (`next_original_[orig]`).
    std::vector<Index> next_original_;
    // Flat CSR undirected Delaunay graph over `sites_`: neighbors of `s` are
    // `neighbors_[offsets_[s] .. offsets_[s + 1])`.
    std::vector<std::uint32_t> offsets_;
    std::vector<std::uint32_t> neighbors_;

    // Uniform 3D seed grid over `bounds_`.
    Aabb bounds_{};
    Vec3 inv_cell_{};
    std::uint32_t gx_ = 0, gy_ = 0, gz_ = 0;
    std::vector<std::uint32_t> cell_seed_;

    [[nodiscard]] std::uint32_t seed_for(Vec3 q) const noexcept;
    [[nodiscard]] std::pair<std::uint32_t, std::uint32_t> walk_site(Vec3 q, std::uint32_t start) const noexcept;
};

// ---- Integration primitives (docs/DP_NNS_SPEC.md §2) -------------------------------

// Find all unordered index pairs `(i, j)` (`i < j`) in `points` within Euclidean `radius`.
// Uses `DpNnsIndex` graph expansion around each site to detect near-duplicates / close pairs.
[[nodiscard]] std::vector<std::pair<Index, Index>> find_close_pairs_dpnns(
    std::span<const Vec3> points, double radius, const DpNnsOptions& opts = {});

// Evaluate exact unsigned distance to the nearest surface/point sample for every query point
// (high-value primitive for coarse SDF initialization and volumetric clearance checks).
[[nodiscard]] std::vector<double> sample_unsigned_distance_field(
    std::span<const Vec3> surface_samples, std::span<const Vec3> volume_queries,
    const DpNnsOptions& opts = {});

}  // namespace pg
