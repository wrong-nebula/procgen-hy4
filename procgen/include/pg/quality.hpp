#pragma once

#include "pg/mesh.hpp"

#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

namespace pg {

struct EdgeLengthStats {
    std::size_t count = 0;
    double min = 0.0, max = 0.0, mean = 0.0, stddev = 0.0;
    // max / min; large ratios flag slivers and poor tessellation.
    [[nodiscard]] double ratio() const noexcept { return min > 0.0 ? max / min : 0.0; }
};

struct SelfIntersectionOptions {
    // Orientation tests treat |orient3d| <= tolerance_rel * diag^3 as zero, so
    // touching or coplanar contact is not reported; only proper crossings are.
    // When tolerance_rel <= 0.0 and exact_predicates is true, exact filtered
    // predicates (PCK::orient_3d / PCK::orient_2d) decide every sign.
    double tolerance_rel = 1e-12;
    bool exact_predicates = true;
    // When true, also report coplanar triangle pairs whose 2D interiors overlap
    // (or edges cross) via exact 2D PCK::orient_2d projection, as well as
    // improper edge-edge crossings.
    bool include_coplanar = false;
};

struct SelfIntersectionResult {
    std::size_t triangles = 0;
    std::size_t candidate_pairs = 0;  // pairs surviving the AABB broad phase
    std::size_t intersecting_pairs = 0;
};

// Triangle pairs (fan-triangulated faces) that properly cross. Pairs from the
// same face or sharing a vertex index are skipped: adjacency is not intersection.
SelfIntersectionResult find_self_intersections(const Mesh& m, const SelfIntersectionOptions& opts = {},
                                               std::vector<std::pair<Index, Index>>* face_pairs = nullptr);

struct QualityOptions {
    bool check_self_intersections = true;
    double degenerate_area_rel = 1e-14;  // face area <= rel * diag^2 is degenerate
    SelfIntersectionOptions self_intersection{};
};

struct QualityReport {
    bool indices_valid = true;
    std::size_t vertices = 0, referenced_vertices = 0, faces = 0, edges = 0;
    std::size_t boundary_edges = 0;      // used by exactly one face
    std::size_t nonmanifold_edges = 0;   // used by three or more faces
    std::size_t inconsistent_edges = 0;  // two faces traverse it in the same direction
    std::size_t degenerate_faces = 0;    // repeated corner or ~zero area
    std::size_t tri_faces = 0, quad_faces = 0, ngon_faces = 0;
    std::size_t poles_valence_3 = 0, poles_valence_5 = 0, high_valence_poles = 0;  // valence >= 6
    std::size_t creased_edges = 0, creased_vertices = 0;
    std::size_t pinched_poles = 0;       // uncreased EVs with sharp Gaussian angle defect > 1.2 rad
    double max_ev_curvature = 0.0;       // max |angle defect| (rad) at uncreased extraordinary vertices
    std::int64_t euler = 0;              // V - E + F over referenced vertices
    double area = 0.0;
    double volume = 0.0;  // signed, divergence theorem; meaningful when watertight
    EdgeLengthStats edge_lengths{};
    bool self_intersections_checked = false;
    SelfIntersectionResult self_intersections{};

    [[nodiscard]] double quad_ratio() const noexcept {
        return faces > 0 ? static_cast<double>(quad_faces) / static_cast<double>(faces) : 0.0;
    }

    [[nodiscard]] bool watertight() const noexcept {
        return indices_valid && boundary_edges == 0 && nonmanifold_edges == 0 && inconsistent_edges == 0;
    }
    [[nodiscard]] bool clean() const noexcept {
        return watertight() && degenerate_faces == 0 &&
               (!self_intersections_checked || self_intersections.intersecting_pairs == 0);
    }
};

[[nodiscard]] QualityReport analyze(const Mesh& m, const QualityOptions& opts = {});

}  // namespace pg
