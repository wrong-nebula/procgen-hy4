#pragma once
// Kit previewer. Renders what the engine will load: the input is a decoded
// .emesh pack (quantised vertices, packed normals, instance batches), drawn by
// a small deferred software rasteriser: G-buffer, shadow map from the key
// light, outlines from id/normal discontinuities, 2x2 supersampling.
//
// It is the stand-in backend until the engine can draw; `pgm render --backend
// engine` hands the same pack to the engine's own previewer instead.

#include "pg/emesh.hpp"
#include "render.hpp"

#include <string>

namespace pgtools {

struct CageEdge {
    pg::Vec3 a{}, b{};    // world-space Z-up endpoints
    float sharpness = 0;  // 0 = regular cage edge (cyan), > 0 = creased edge (amber/orange)
};

struct PreviewOptions {
    int width = 960, height = 720;
    std::string view = "iso";      // iso | opposite | front | side | top
    std::string pass = "beauty";   // beauty | clay | parts | materials | zebra | matcap | curvature
    bool perspective = false;      // orthographic by default: kit sheets, drawings
    double yaw_deg = 0, pitch_deg = 0;
    bool custom_angles = false;    // use yaw/pitch instead of the named view
    double margin = 0.08;          // fraction of the frame left empty around the model
    Rgb background{0.165, 0.165, 0.165};
    bool shadows = true;
    double outline = 0.45;         // 0 = off
    bool has_section = false;      // clip half-space dot(section_normal, p_zup) <= section_offset
    pg::Vec3 section_normal{0, 1, 0};
    double section_offset = 0.0;
    bool has_frame = false;        // pixel-lock camera to [frame_lo, frame_hi] (Z-up metres)
    pg::Vec3 frame_lo{}, frame_hi{};
    bool frame_expand = true;      // expand smoothly only if geometry overflows [frame_lo, frame_hi]
    bool show_details = true;      // false: suppress surface details (structural/shell pace-layer view)
    bool strip_glazing = false;    // true: omit glass/glazing/water/foliage slots (bare skeleton pace-layer view)
    int lod = 0;                   // LOD level to render (0 = finest, clamped per mesh)
    const std::vector<CageEdge>* cage_edges = nullptr;  // optional SDS control cage wireframe overlay (--wire-cage)
    const pg::emesh::Pack* ghost_pack = nullptr;  // optional retained last-valid pack drawn as calm translucent clay
    double ghost_opacity = 0.28;
    bool translucent = true;       // two-pass translucent glass/glazing/water in beauty pass
    bool contact_ao = true;        // screen-space cavity / contact ambient occlusion
    bool record_instance_map = false;  // populate PreviewInfo::instance_map (width * height, 1-based instance id)
};

struct PreviewInfo {
    std::size_t triangles = 0, instances = 0, meshes = 0;
    double metres_per_pixel = 0;
    pg::Vec3 lo{}, hi{};  // Z-up world bounds of what was drawn
    pg::Vec3 camera_center{};
    bool frame_overflowed = false;
    std::vector<std::uint32_t> instance_map;  // populated when PreviewOptions::record_instance_map is true
};

Image render_pack(const pg::emesh::Pack& pack, const PreviewOptions& options, PreviewInfo* info = nullptr);

// 5x7 bitmap text, for captions on kit sheets.
void draw_text(Image& img, int x, int y, const std::string& text, Rgb color, int scale = 2);
int text_width(const std::string& text, int scale = 2);

// Copies `src` into `dst` at (x, y), clipped.
void blit(Image& dst, const Image& src, int x, int y);
Image solid(int width, int height, Rgb color);

}  // namespace pgtools
