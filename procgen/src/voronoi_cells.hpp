#pragma once

#include "pg/mesh.hpp"

#include <cstdint>
#include <span>
#include <utility>
#include <vector>

namespace pg {

struct VoronoiBoxOptions {
    Vec3 size{1.0, 1.0, 1.0};   // centred box [-size/2, +size/2]
    int count = 16;             // generated sites when `sites` is empty
    double gap = 0.0;           // joint / mortar clearance between neighbouring cells
    double shrink = 0.0;        // additional uniform shrink [0, 0.9] toward each cell's centroid
    int seed = 1;
};

struct VoronoiBoxResult {
    std::vector<Mesh> cells;    // one closed convex polyhedron per non-empty cell
    std::vector<Vec3> centroids;
};

[[nodiscard]] VoronoiBoxResult make_voronoi_box(std::span<const Vec3> sites, const VoronoiBoxOptions& opts);

}  // namespace pg
