#pragma once

#include "pg/mesh.hpp"

#include <cstdint>
#include <span>
#include <vector>

namespace pg {

enum class ExtrudeMode : std::uint8_t {
    Region,      // connected selected faces move together; walls only on the region boundary
    Individual,  // every selected face gets its own walls
};

struct ExtrudeParams {
    double distance = 0.1;
    ExtrudeMode mode = ExtrudeMode::Region;
    // Region mode: scale vertex offsets so every cap face moves `distance` along
    // its own normal (shell offset), capped at max_thickness_scale at sharp corners.
    bool even_thickness = true;
    double max_thickness_scale = 4.0;
};

// Extrudes the faces with face_mask[f] != 0. On a closed, consistently oriented
// input the result stays closed and consistently oriented: each selected face
// becomes a cap and each region-boundary edge (a -> b) gets a wall quad
// (a, b, b', a'). An empty mask returns the input unchanged.
[[nodiscard]] Mesh extrude(const Mesh& m, std::span<const std::uint8_t> face_mask, const ExtrudeParams& params);

// Selection helpers (face masks).
[[nodiscard]] std::vector<std::uint8_t> select_all(const Mesh& m);
[[nodiscard]] std::vector<std::uint8_t> select_by_normal(const Mesh& m, Vec3 direction, double min_cos);

}  // namespace pg
