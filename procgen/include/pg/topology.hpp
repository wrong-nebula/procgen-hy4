#pragma once

#include "pg/mesh.hpp"

#include <span>
#include <vector>

namespace pg {

// One directed edge of one face. `key` packs the undirected edge (lo << 32 | hi)
// so that sorting groups every use of an edge together; `from` tells direction.
struct HalfEdge {
    std::uint64_t key;
    Index face;
    Index from;

    [[nodiscard]] constexpr Index lo() const noexcept { return static_cast<Index>(key >> 32); }
    [[nodiscard]] constexpr Index hi() const noexcept { return static_cast<Index>(key & 0xffffffffu); }
    [[nodiscard]] constexpr Index to() const noexcept { return from == lo() ? hi() : lo(); }
    [[nodiscard]] constexpr bool forward() const noexcept { return from == lo(); }
};

// All half-edges sorted by undirected key. With an optional face mask only
// faces with mask[f] != 0 contribute.
[[nodiscard]] std::vector<HalfEdge> sorted_half_edges(const Mesh& m, std::span<const std::uint8_t> face_mask = {});

// Calls fn(std::span<const HalfEdge>) once per undirected edge.
template <class Fn>
void for_each_edge_group(std::span<const HalfEdge> hes, Fn&& fn) {
    std::size_t i = 0;
    while (i < hes.size()) {
        std::size_t j = i + 1;
        while (j < hes.size() && hes[j].key == hes[i].key) ++j;
        fn(hes.subspan(i, j - i));
        i = j;
    }
}

}  // namespace pg
