#pragma once

#include "pg/mesh.hpp"

#include <array>
#include <cstdint>
#include <functional>
#include <span>
#include <vector>

namespace pg {

enum class BoundaryRule : std::uint8_t {
    EdgeAndCorner,  // open boundaries smooth along edges; corners (1 incident face) stay sharp
    EdgeOnly,       // open boundaries smooth along edges; corners smooth too
};

enum class CreaseMethod : std::uint8_t {
    Uniform,  // DeRose-Kass-Truong: child edge sharpness = max(0, s - 1)
    Chaikin,  // OpenSubdiv Chaikin: smooth transition between adjacent varying crease weights
};

enum class TriSubdivRule : std::uint8_t {
    Catmark,  // classic Catmull-Clark weights on triangles
    Smooth,   // OpenSubdiv TRI_SUB_SMOOTH (0.470 face weight) to reduce triangle-fan rippling
};

enum class UvSubdivRule : std::uint8_t {
    CornersPlus1,  // smooth interior, cubic along UV seams, pinned at UV corners & junctions
    Boundaries,    // smooth interior, linear along all UV boundaries
    Linear,        // bilinear UV subdivision everywhere
    Smooth,        // Catmull-Clark smooth UVs everywhere
};

struct SubdivideOptions {
    std::uint32_t levels = 1;
    std::uint32_t linear_pre_subdiv = 0;  // bilinear refinement steps before Catmull-Clark
    BoundaryRule boundary = BoundaryRule::EdgeAndCorner;
    CreaseMethod crease_method = CreaseMethod::Uniform;
    TriSubdivRule tri_subdiv = TriSubdivRule::Catmark;
    UvSubdivRule uv_rule = UvSubdivRule::CornersPlus1;
    bool limit_snap = false;            // snap vertices to the exact Halstead-Kass-DeRose limit surface
    bool compute_limit_normals = false; // compute exact analytical limit normals per face corner
    std::uint32_t interpolate_iters = 0; // W-PIA progressive interpolation iterations (0 = off, ~5 = exact cage fit)
    double interpolate_weight = 1.15;    // W-PIA relaxation weight (0.5..1.6)
    std::uint8_t subdiv_normals = 0;     // 0 = off, 1 = EV-blended semi-sharp subdivision shading (Zhou-Kosinka 2023), 2 = full
    bool tuned_ev = false;               // Augsdorfer-Dodgson-Sabin curvature-tuned EV weights + Kosinka dart termination
    bool non_uniform = false;            // Sederberg NURSS / Li et al. 2020 chord-length non-uniform knot intervals
    bool rational = false;               // Homogeneous R^4 Rational Catmull-Clark (uses Mesh::vertex_weights)
    bool adaptive = false;               // Feature-adaptive watertight coarsening of strictly flat interior quad blocks
    double adaptive_angle_deg = 1.5;     // Max normal deviation (deg) for adaptive flat-block coarsening
    bool g2_fair = false;                // Karčiauskas-Peters quadratic osculating G2 EV completion + Gregory tangent fairing
    double g2_weight = 0.65;             // Blend strength (0..1) of 1-ring G2 osculating paraboloid projection
    // Optional Pixar hierarchical multiresolution edit hook invoked after completing each level `l` (1..levels).
    std::function<void(std::uint32_t level, Mesh& mesh)> on_level;
};

struct SubdivideResult {
    Mesh mesh;
    std::vector<std::array<float, 2>> uv;  // refined corner UVs (non-empty iff input UVs matched corner_count)
    std::vector<Vec3> corner_normals;      // per-corner analytical limit normals (when compute_limit_normals == true)
};

// Catmull–Clark subdivision. Any polygon mesh in, all-quad mesh out (each
// k-gon becomes k quads). Supports sparse semi-sharp edge/vertex creases
// (DeRose–Kass–Truong / OpenSubdiv), Chaikin crease transitions, linear
// pre-subdivision, boundary/triangle rules, face-varying UV subdivision, and
// exact limit-surface position + normal evaluation.
[[nodiscard]] Mesh catmull_clark(const Mesh& m, std::uint32_t levels = 1);
[[nodiscard]] Mesh catmull_clark(const Mesh& m, const SubdivideOptions& opts);
[[nodiscard]] SubdivideResult catmull_clark_full(const Mesh& m,
                                                 std::span<const std::array<float, 2>> corner_uv,
                                                 const SubdivideOptions& opts);

}  // namespace pg
