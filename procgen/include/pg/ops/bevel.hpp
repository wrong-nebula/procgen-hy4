#pragma once

#include "pg/mesh.hpp"

#include <cstdint>
#include <functional>

namespace pg {

struct BevelParams {
    double width = 0.05;          // distance from each beveled edge, measured in the adjacent faces
    double min_angle_deg = 30.0;  // bevel edges whose face normals differ by at least this
    std::uint32_t segments = 1;   // 1 = chamfer, 2 = support-loop pair / 2-seg bevel, 3+ = rounded fillet
    // Profile shape for segments >= 2: 0 = flat chamfer, 0.5 = circular arc, 1.0 = sharp support-loop hold.
    double profile = 0.5;
    bool clamp_overlap = false;   // clamp width so adjacent bevels cannot invert or self-intersect
    float crease_sharpness = 0.0f; // optional SDS crease weight applied along the bevel centre ridge
    // Optional per-edge filter: (edge_index, p0, p1, face_normal_0, face_normal_1) -> keep.
    std::function<bool(Index, Vec3, Vec3, Vec3, Vec3)> edge_filter;
};

// Edge bevel / chamfer / support-loop generator on a closed, consistently
// oriented manifold (throws std::invalid_argument otherwise).
// - segments == 1: single-segment chamfer (legacy exact behaviour).
// - segments >= 2, profile == 1.0: preserves the original sharp edge ridge and
//   places holding/support loops at `width` on both adjacent faces (all-quad
//   strips and quad corners at 3-way junctions — ideal for Catmull–Clark SDS).
// - segments >= 2, profile < 1.0: rational-arc multi-segment fillet with smooth
//   corner patching.
[[nodiscard]] Mesh bevel(const Mesh& m, const BevelParams& params);

}  // namespace pg
