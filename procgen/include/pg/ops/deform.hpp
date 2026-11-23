#pragma once

#include "pg/mesh.hpp"

#include <cstdint>
#include <span>
#include <vector>

namespace pg {

// Point deformers. They move vertices only, so topology (and therefore
// watertightness and orientation) is preserved exactly; what changes is
// geometry, which callers check with analyze() (volume drift, intersections).

// Rotate about +Z by `radians_per_unit * z`. Volume preserving (unit Jacobian).
void twist(std::span<Vec3> points, double radians_per_unit) noexcept;

// Barr bend: the X axis is bent into an arc in the XZ plane with curvature k
// (radius 1/k), neutral plane z = 0. Volume scales locally by (1 - k z), so a
// solid symmetric about z = 0 keeps its volume.
void bend(std::span<Vec3> points, double curvature) noexcept;

// Scale XY by (1 + k z).
void taper(std::span<Vec3> points, double scale_per_unit) noexcept;

struct NoiseParams {
    double amplitude = 0.05;
    double frequency = 4.0;
    std::uint64_t seed = 1;
    std::uint32_t octaves = 3;  // fBm, each octave doubles frequency, halves amplitude
};

// Gradient (Perlin-style) noise in [-1, 1], C2 continuous (quintic fade).
[[nodiscard]] double gradient_noise(Vec3 p, std::uint64_t seed) noexcept;

// Area-weighted vertex normals (zero for unreferenced vertices).
[[nodiscard]] std::vector<Vec3> vertex_normals(const Mesh& m);

// Displace each vertex along its normal by fBm noise. Organic surface detail.
void displace_noise(Mesh& m, const NoiseParams& params);

}  // namespace pg
