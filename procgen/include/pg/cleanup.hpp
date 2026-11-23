#pragma once

#include "pg/mesh.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace pg {

struct CleanupStats {
    std::size_t merged_vertices = 0;
    std::size_t removed_faces = 0;
    std::size_t removed_vertices = 0;
};

// Merge vertices closer than `tolerance` (deterministic: each vertex joins the
// lowest-index representative within range), drop repeated corners and faces
// that collapse below three corners, then compact unreferenced vertices.
// `face_tags`, when given, holds one value per face and is compacted alongside
// the faces that survive.
CleanupStats weld_vertices(Mesh& m, double tolerance, std::vector<std::uint32_t>* face_tags = nullptr);

// Remove consecutive duplicate corners and faces with < 3 distinct corners.
std::size_t remove_collapsed_faces(Mesh& m, std::vector<std::uint32_t>* face_tags = nullptr);

// Drop vertices no face references; returns how many were removed.
std::size_t compact_vertices(Mesh& m);

}  // namespace pg
