#pragma once

#include "pg/mesh.hpp"

#include <cstdint>
#include <vector>

namespace pg {

struct ScatterParams {
    std::uint32_t count = 100;
    // Poisson-disk radius: no two accepted points closer than this (0 = plain
    // area-uniform random). Dart throwing; stops at `count` or after
    // count * max_attempts candidates, whichever comes first.
    double min_distance = 0.0;
    std::uint32_t max_attempts = 30;
    std::uint64_t seed = 1;
};

// Structure-of-arrays sample set.
struct ScatterPoints {
    std::vector<Vec3> positions;
    std::vector<Vec3> normals;  // unit face normal at the sample
    std::vector<Index> faces;   // source face
    [[nodiscard]] std::size_t size() const noexcept { return positions.size(); }
};

// Area-uniform samples on the surface (fan-triangulated faces). Deterministic in seed.
[[nodiscard]] ScatterPoints scatter_points(const Mesh& surface, const ScatterParams& params);

struct InstanceParams {
    double scale = 1.0;
    bool align_to_normal = true;  // instance +Z -> surface normal
    bool random_spin = false;     // random rotation about the normal (seeded)
    std::uint64_t seed = 1;
};

// One copy of `instance` per point (instance origin at the point). Each copy is
// a rigid transform plus uniform scale, so closed instances stay closed and keep
// their orientation; output volume = count * scale^3 * instance volume.
[[nodiscard]] Mesh instance_on_points(const Mesh& instance, const ScatterPoints& points, const InstanceParams& params);

}  // namespace pg
