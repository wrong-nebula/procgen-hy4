#pragma once

#include "pg/mesh.hpp"

#include <cstdint>

namespace pg {

enum class BooleanOp : std::uint8_t { Union, Intersection, Difference };

struct BooleanParams {
    // Planarity / collinearity tolerance, relative to the bounding-box diagonal
    // of both inputs (used when re-merging coplanar cut fragments).
    double plane_epsilon_rel = 1e-9;
    // Weld / T-junction tolerance after rounding exact intersection coordinates
    // back to IEEE-754 double, relative to the same diagonal.
    double weld_epsilon_rel = 1e-8;
    // Emit input faces the boolean never cut as their original polygon (quads,
    // n-gons) instead of triangulated fragments.
    bool merge_uncut_faces = true;
    // Replace the fragments of each cut planar input face by convex pieces of
    // their union (no new vertices; stitch vertices no other face uses are
    // dropped), so cut slivers do not reach the output. Needs merge_uncut_faces:
    // with it off the output is the raw stitched triangulated fragments.
    bool merge_coplanar_fragments = true;
};

// Solid boolean of two closed, consistently oriented meshes (local exact-predicate
// corefinement v2).
//
// Broad phase (Zomorodian-Edelsbrunner bipartite 3D box intersection) restricts
// work strictly to faces whose AABBs overlap the other mesh; uncut polygons
// outside the intersection zone stay untouched. Intersecting faces are split
// using exact PCK predicates and homogeneous expansion constructions, locally
// retriangulated via exact 2D Constrained Delaunay (CDTBase2d), classified by
// exact orient_3d_SOS ray parity + coplanar normal alignment, stitched, and
// merged back into clean convex polygons.
[[nodiscard]] Mesh mesh_boolean(const Mesh& a, const Mesh& b, BooleanOp op, const BooleanParams& params = {});

// Insert vertices lying on face edges into those edges. Returns how many
// insertions were made. Used by mesh_boolean; useful for any stitching.
std::size_t repair_t_junctions(Mesh& m, double tolerance);

}  // namespace pg
