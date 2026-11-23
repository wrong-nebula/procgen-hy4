#pragma once
// Tiny dependency-free mesh renderer for visual QA: z-buffered flat-shaded
// triangles, a depth-tested wireframe overlay, 2x2 supersampling, PNG output.

#include "pg/mesh.hpp"

#include <string>
#include <vector>

namespace pgtools {

struct Rgb { double r, g, b; };

struct Item {
    const pg::Mesh* mesh;
    Rgb color;
    bool wire = true;
};

struct Camera {
    pg::Vec3 target{0, 0, 0};
    double yaw_deg = 35, pitch_deg = 30, distance = 4, fov_deg = 35;
};

struct Image {
    int w = 0, h = 0;
    std::vector<unsigned char> rgb;
};

Image render(const std::vector<Item>& items, const Camera& cam, int width, int height, double wire_alpha = 0.55);
void save_png(const Image& img, const std::string& path);
// Side-by-side montage with a gap.
Image hconcat(const std::vector<Image>& imgs, int gap = 8);

}  // namespace pgtools
