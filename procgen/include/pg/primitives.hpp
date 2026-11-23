#pragma once

#include "pg/mesh.hpp"

#include <cstdint>

namespace pg {

// XY-plane quad grid centred on the origin, normal +Z. Open mesh.
[[nodiscard]] Mesh make_grid(std::uint32_t nx, std::uint32_t ny, double size_x, double size_y);

// Closed axis-aligned box centred on the origin with `segments` quads per edge.
[[nodiscard]] Mesh make_box(Vec3 size, std::uint32_t segments = 1);

// Closed cylinder around +Z, centred on the origin, n-gon caps.
[[nodiscard]] Mesh make_cylinder(std::uint32_t segments, double radius, double height);

}  // namespace pg
