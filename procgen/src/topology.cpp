#include "pg/topology.hpp"

#include <algorithm>

namespace pg {

std::vector<HalfEdge> sorted_half_edges(const Mesh& m, std::span<const std::uint8_t> face_mask) {
    const std::size_t nf = m.face_count();
    const bool masked = !face_mask.empty();
    std::vector<HalfEdge> hes;
    hes.reserve(m.corner_count());
    for (std::size_t f = 0; f < nf; ++f) {
        if (masked && !face_mask[f]) continue;
        const Index begin = m.face_offsets[f];
        const Index end = m.face_offsets[f + 1];
        for (Index c = begin; c < end; ++c) {
            const Index a = m.face_indices[c];
            const Index b = m.face_indices[c + 1 == end ? begin : c + 1];
            hes.push_back({edge_key(a, b), static_cast<Index>(f), a});
        }
    }
    std::sort(hes.begin(), hes.end(), [](const HalfEdge& x, const HalfEdge& y) {
        return x.key != y.key ? x.key < y.key : x.face < y.face;
    });
    return hes;
}

}  // namespace pg
