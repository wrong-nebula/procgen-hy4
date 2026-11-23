#include "preview.hpp"

#include "pg/ops/deform.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numbers>
#include <thread>

namespace pgtools {
namespace {

using pg::Vec3;
using pg::cross;
using pg::dot;

struct Tri {
    Vec3 p[3], n[3];
    std::uint32_t slot, id, mesh;
};

// Engine Y-up <- viewer Z-up.
Vec3 from_view(Vec3 p, bool y_up) { return y_up ? Vec3{p.x, p.z, -p.y} : p; }

// World (engine space) -> mesh-local, for sampling surface details under an instance.
struct Inverse {
    double r[3][3], t[3], scale = 1;  // scale: mesh units -> metres
    bool valid = false;
    [[nodiscard]] Vec3 point(Vec3 p) const {
        const Vec3 d{p.x - t[0], p.y - t[1], p.z - t[2]};
        return {r[0][0] * d.x + r[0][1] * d.y + r[0][2] * d.z, r[1][0] * d.x + r[1][1] * d.y + r[1][2] * d.z, r[2][0] * d.x + r[2][1] * d.y + r[2][2] * d.z};
    }
};
Inverse invert(const std::array<float, 12>& m) {
    Inverse inv;
    const Vec3 a{m[0], m[4], m[8]}, b{m[1], m[5], m[9]}, c{m[2], m[6], m[10]};  // columns
    const double det = dot(a, cross(b, c));
    if (!(std::abs(det) > 1e-18)) return inv;
    const Vec3 rows[3] = {cross(b, c) / det, cross(c, a) / det, cross(a, b) / det};
    for (int i = 0; i < 3; ++i) {
        inv.r[i][0] = rows[i].x; inv.r[i][1] = rows[i].y; inv.r[i][2] = rows[i].z;
    }
    inv.t[0] = m[3]; inv.t[1] = m[7]; inv.t[2] = m[11];
    inv.scale = std::cbrt(std::abs(det));
    inv.valid = true;
    return inv;
}

// Engine Y-up -> viewer Z-up: inverse of (x, y, z) -> (x, z, -y).
Vec3 to_view(Vec3 p, bool y_up) { return y_up ? Vec3{p.x, -p.z, p.y} : p; }

struct Basis {
    Vec3 right, up, fwd;  // fwd points from the camera into the scene
};
Basis basis(double yaw_deg, double pitch_deg) {
    const double yaw = yaw_deg * std::numbers::pi / 180, pitch = pitch_deg * std::numbers::pi / 180;
    const Vec3 to_cam{std::cos(pitch) * std::cos(yaw), std::cos(pitch) * std::sin(yaw), std::sin(pitch)};
    Basis b;
    b.fwd = to_cam * -1.0;
    const Vec3 ref = std::abs(to_cam.z) > 0.999 ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
    b.right = pg::normalize_or(cross(b.fwd, ref), {1, 0, 0});
    b.up = cross(b.right, b.fwd);
    return b;
}

bool metallic(const std::string& name) {
    for (const char* k : {"iron", "bronze", "steel", "zinc", "glass", "glazing", "opal", "water"})
        if (name.find(k) != std::string::npos) return true;
    return false;
}

bool non_structural_skin(const std::string& name) {
    for (const char* k : {"glass", "glazing", "opal", "water", "foliage", "leaf", "canopy"})
        if (name.find(k) != std::string::npos) return true;
    return false;
}

bool translucent_slot(const std::string& name) {
    for (const char* k : {"glass", "glazing", "water"})
        if (name.find(k) != std::string::npos) return true;
    return false;
}

// Classic 5x7 column font (bit 0 = top row), ASCII 32..122.
constexpr unsigned char kFont[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, {0x00, 0x00, 0x5F, 0x00, 0x00}, {0x00, 0x07, 0x00, 0x07, 0x00}, {0x14, 0x7F, 0x14, 0x7F, 0x14},
    {0x24, 0x2A, 0x7F, 0x2A, 0x12}, {0x23, 0x13, 0x08, 0x64, 0x62}, {0x36, 0x49, 0x55, 0x22, 0x50}, {0x00, 0x05, 0x03, 0x00, 0x00},
    {0x00, 0x1C, 0x22, 0x41, 0x00}, {0x00, 0x41, 0x22, 0x1C, 0x00}, {0x14, 0x08, 0x3E, 0x08, 0x14}, {0x08, 0x08, 0x3E, 0x08, 0x08},
    {0x00, 0x50, 0x30, 0x00, 0x00}, {0x08, 0x08, 0x08, 0x08, 0x08}, {0x00, 0x60, 0x60, 0x00, 0x00}, {0x20, 0x10, 0x08, 0x04, 0x02},
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00}, {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
    {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39}, {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
    {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E}, {0x00, 0x36, 0x36, 0x00, 0x00}, {0x00, 0x56, 0x36, 0x00, 0x00},
    {0x08, 0x14, 0x22, 0x41, 0x00}, {0x14, 0x14, 0x14, 0x14, 0x14}, {0x00, 0x41, 0x22, 0x14, 0x08}, {0x02, 0x01, 0x51, 0x09, 0x06},
    {0x32, 0x49, 0x79, 0x41, 0x3E}, {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36}, {0x3E, 0x41, 0x41, 0x41, 0x22},
    {0x7F, 0x41, 0x41, 0x22, 0x1C}, {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01}, {0x3E, 0x41, 0x49, 0x49, 0x7A},
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, {0x00, 0x41, 0x7F, 0x41, 0x00}, {0x20, 0x40, 0x41, 0x3F, 0x01}, {0x7F, 0x08, 0x14, 0x22, 0x41},
    {0x7F, 0x40, 0x40, 0x40, 0x40}, {0x7F, 0x02, 0x0C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F}, {0x3E, 0x41, 0x41, 0x41, 0x3E},
    {0x7F, 0x09, 0x09, 0x09, 0x06}, {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46}, {0x46, 0x49, 0x49, 0x49, 0x31},
    {0x01, 0x01, 0x7F, 0x01, 0x01}, {0x3F, 0x40, 0x40, 0x40, 0x3F}, {0x1F, 0x20, 0x40, 0x20, 0x1F}, {0x3F, 0x40, 0x38, 0x40, 0x3F},
    {0x63, 0x14, 0x08, 0x14, 0x63}, {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43}, {0x00, 0x7F, 0x41, 0x41, 0x00},
    {0x02, 0x04, 0x08, 0x10, 0x20}, {0x00, 0x41, 0x41, 0x7F, 0x00}, {0x04, 0x02, 0x01, 0x02, 0x04}, {0x40, 0x40, 0x40, 0x40, 0x40},
    {0x00, 0x01, 0x02, 0x04, 0x00}, {0x20, 0x54, 0x54, 0x54, 0x78}, {0x7F, 0x48, 0x44, 0x44, 0x38}, {0x38, 0x44, 0x44, 0x44, 0x20},
    {0x38, 0x44, 0x44, 0x48, 0x7F}, {0x38, 0x54, 0x54, 0x54, 0x18}, {0x08, 0x7E, 0x09, 0x01, 0x02}, {0x0C, 0x52, 0x52, 0x52, 0x3E},
    {0x7F, 0x08, 0x04, 0x04, 0x78}, {0x00, 0x44, 0x7D, 0x40, 0x00}, {0x20, 0x40, 0x44, 0x3D, 0x00}, {0x7F, 0x10, 0x28, 0x44, 0x00},
    {0x00, 0x41, 0x7F, 0x40, 0x00}, {0x7C, 0x04, 0x18, 0x04, 0x78}, {0x7C, 0x08, 0x04, 0x04, 0x78}, {0x38, 0x44, 0x44, 0x44, 0x38},
    {0x7C, 0x14, 0x14, 0x14, 0x08}, {0x08, 0x14, 0x14, 0x18, 0x7C}, {0x7C, 0x08, 0x04, 0x04, 0x08}, {0x48, 0x54, 0x54, 0x54, 0x20},
    {0x04, 0x3F, 0x44, 0x40, 0x20}, {0x3C, 0x40, 0x40, 0x20, 0x7C}, {0x1C, 0x20, 0x40, 0x20, 0x1C}, {0x3C, 0x40, 0x30, 0x40, 0x3C},
    {0x44, 0x28, 0x10, 0x28, 0x44}, {0x0C, 0x50, 0x50, 0x50, 0x3C}, {0x44, 0x64, 0x54, 0x4C, 0x44},
};

}  // namespace

Image solid(int width, int height, Rgb c) {
    Image img{width, height, std::vector<unsigned char>(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3)};
    auto px = [](double v) { return static_cast<unsigned char>(std::clamp(v * 255.0, 0.0, 255.0)); };
    for (std::size_t i = 0; i < img.rgb.size(); i += 3) { img.rgb[i] = px(c.r); img.rgb[i + 1] = px(c.g); img.rgb[i + 2] = px(c.b); }
    return img;
}

void blit(Image& dst, const Image& src, int x0, int y0) {
    for (int y = 0; y < src.h; ++y) {
        const int dy = y0 + y;
        if (dy < 0 || dy >= dst.h) continue;
        for (int x = 0; x < src.w; ++x) {
            const int dx = x0 + x;
            if (dx < 0 || dx >= dst.w) continue;
            for (int k = 0; k < 3; ++k)
                dst.rgb[(static_cast<std::size_t>(dy) * static_cast<std::size_t>(dst.w) + static_cast<std::size_t>(dx)) * 3 + static_cast<std::size_t>(k)] =
                    src.rgb[(static_cast<std::size_t>(y) * static_cast<std::size_t>(src.w) + static_cast<std::size_t>(x)) * 3 + static_cast<std::size_t>(k)];
        }
    }
}

int text_width(const std::string& text, int scale) { return static_cast<int>(text.size()) * 6 * scale; }

void draw_text(Image& img, int x, int y, const std::string& text, Rgb color, int scale) {
    auto px = [](double v) { return static_cast<unsigned char>(std::clamp(v * 255.0, 0.0, 255.0)); };
    int cx = x;
    for (const char ch : text) {
        const int code = static_cast<unsigned char>(ch);
        if (code >= 32 && code <= 122) {
            const unsigned char* glyph = kFont[code - 32];
            for (int col = 0; col < 5; ++col)
                for (int row = 0; row < 7; ++row) {
                    if (!((glyph[col] >> row) & 1)) continue;
                    for (int sy = 0; sy < scale; ++sy)
                        for (int sx = 0; sx < scale; ++sx) {
                            const int X = cx + col * scale + sx, Y = y + row * scale + sy;
                            if (X < 0 || Y < 0 || X >= img.w || Y >= img.h) continue;
                            const std::size_t o = (static_cast<std::size_t>(Y) * static_cast<std::size_t>(img.w) + static_cast<std::size_t>(X)) * 3;
                            img.rgb[o] = px(color.r);
                            img.rgb[o + 1] = px(color.g);
                            img.rgb[o + 2] = px(color.b);
                        }
                }
        }
        cx += 6 * scale;
    }
}

Image render_pack(const pg::emesh::Pack& pack, const PreviewOptions& opt, PreviewInfo* info) {
    // ---- expand batches into world-space triangles (viewer is Z-up) ----
    std::vector<Tri> tris;
    pg::Aabb box;
    std::size_t instances = 0;
    // Surface details: an index per mesh that has any, and the inverse of each instance drawing one.
    std::vector<pg::doc::DetailIndex> detail_index(pack.meshes.size());
    if (opt.show_details) {
        for (std::size_t first = 0; first < pack.details.size();) {
            std::size_t last = first;
            std::vector<pg::doc::Detail> of_mesh;
            while (last < pack.details.size() && pack.details[last].mesh == pack.details[first].mesh) of_mesh.push_back(pack.details[last++].detail);
            if (pack.details[first].mesh < detail_index.size()) detail_index[pack.details[first].mesh] = pg::doc::DetailIndex(of_mesh);
            first = last;
        }
    }
    auto collect_pack_tris = [&](const pg::emesh::Pack& src_pack, std::vector<Tri>& dst_tris, pg::Aabb& dst_box,
                                 std::vector<Inverse>* dst_inv, const std::vector<pg::doc::DetailIndex>* d_idx,
                                 std::size_t* inst_count, bool filter_skin) {
        for (const auto& batch : src_pack.batches) {
            const auto& mesh = src_pack.meshes[batch.mesh];
            if (filter_skin && mesh.material < src_pack.slot_names.size() && non_structural_skin(src_pack.slot_names[mesh.material])) {
                continue;
            }
            for (std::uint32_t k = 0; k < batch.instance_count; ++k) {
                const auto& m = src_pack.transforms[batch.first_instance + k];
                if (inst_count) ++(*inst_count);
                if (dst_inv && d_idx && !(*d_idx)[batch.mesh].empty()) (*dst_inv)[batch.first_instance + k] = invert(m);
                auto xform = [&](Vec3 p) {
                    return Vec3{m[0] * p.x + m[1] * p.y + m[2] * p.z + m[3], m[4] * p.x + m[5] * p.y + m[6] * p.z + m[7],
                                m[8] * p.x + m[9] * p.y + m[10] * p.z + m[11]};
                };
                auto rotate = [&](Vec3 n) {  // adequate for rigid + uniform scale; renormalised
                    return pg::normalize_or({m[0] * n.x + m[1] * n.y + m[2] * n.z, m[4] * n.x + m[5] * n.y + m[6] * n.z, m[8] * n.x + m[9] * n.y + m[10] * n.z},
                                            {0, 0, 1});
                };
                std::uint32_t first_idx = mesh.first_index;
                std::uint32_t idx_count = mesh.index_count;
                std::uint32_t base_vtx = mesh.base_vertex;
                if (opt.lod > 0 && !mesh.lods.empty()) {
                    const std::size_t li = std::min<std::size_t>(static_cast<std::size_t>(opt.lod), mesh.lods.size() - 1);
                    first_idx = mesh.lods[li].first_index;
                    idx_count = mesh.lods[li].index_count;
                    base_vtx = mesh.lods[li].base_vertex;
                }
                for (std::uint32_t i = 0; i + 2 < idx_count; i += 3) {
                    Vec3 vp[3], vn[3];
                    for (int c = 0; c < 3; ++c) {
                        const std::size_t v = base_vtx + src_pack.indices[first_idx + i + static_cast<std::uint32_t>(c)];
                        vp[c] = to_view(xform(src_pack.positions[v]), src_pack.y_up);
                        vn[c] = to_view(rotate(src_pack.normals[v]), src_pack.y_up);
                    }
                    auto push_tri = [&](Vec3 p0, Vec3 n0, Vec3 p1, Vec3 n1, Vec3 p2, Vec3 n2) {
                        Tri t;
                        t.p[0] = p0; t.p[1] = p1; t.p[2] = p2;
                        t.n[0] = n0; t.n[1] = n1; t.n[2] = n2;
                        for (int c = 0; c < 3; ++c) dst_box.expand(t.p[c]);
                        t.slot = mesh.material;
                        t.id = batch.first_instance + k + 1;
                        t.mesh = batch.mesh;
                        dst_tris.push_back(t);
                    };
                    if (!opt.has_section) {
                        push_tri(vp[0], vn[0], vp[1], vn[1], vp[2], vn[2]);
                    } else {
                        Vec3 cp[4], cn[4];
                        int cnt = 0;
                        for (int c = 0; c < 3; ++c) {
                            const int nx_c = (c + 1) % 3;
                            const double da = dot(opt.section_normal, vp[c]) - opt.section_offset;
                            const double db = dot(opt.section_normal, vp[nx_c]) - opt.section_offset;
                            if (da <= 0.0) { cp[cnt] = vp[c]; cn[cnt] = vn[c]; ++cnt; }
                            if ((da <= 0.0) != (db <= 0.0)) {
                                const double alpha = da / (da - db);
                                cp[cnt] = vp[c] + (vp[nx_c] - vp[c]) * alpha;
                                cn[cnt] = pg::normalize_or(vn[c] + (vn[nx_c] - vn[c]) * alpha, vn[c]);
                                ++cnt;
                            }
                        }
                        for (int c = 1; c + 1 < cnt; ++c) push_tri(cp[0], cn[0], cp[c], cn[c], cp[c + 1], cn[c + 1]);
                    }
                }
            }
        }
    };
    std::vector<Inverse> inverse(pack.transforms.size());
    collect_pack_tris(pack, tris, box, &inverse, &detail_index, &instances, opt.strip_glazing);

    std::vector<Tri> ghost_tris;
    pg::Aabb ghost_box;
    if (opt.ghost_pack) {
        collect_pack_tris(*opt.ghost_pack, ghost_tris, ghost_box, nullptr, nullptr, nullptr, false);
    }

    if (info) {
        info->triangles = tris.size();
        info->instances = instances;
        info->meshes = pack.meshes.size();
        if (box.valid()) { info->lo = box.lo; info->hi = box.hi; }
        else if (ghost_box.valid()) { info->lo = ghost_box.lo; info->hi = ghost_box.hi; }
    }
    if (tris.empty() && ghost_tris.empty()) return solid(opt.width, opt.height, opt.background);

    // ---- camera (auto-fit or pixel-locked to declared [frame_lo, frame_hi]) ----
    double yaw = -52, pitch = 28;
    if (opt.custom_angles) { yaw = opt.yaw_deg; pitch = opt.pitch_deg; }
    else if (opt.view == "opposite") { yaw = 128; pitch = 28; }
    else if (opt.view == "front") { yaw = -90; pitch = 0; }
    else if (opt.view == "side") { yaw = 0; pitch = 0; }
    else if (opt.view == "top") { yaw = -90; pitch = 89.999; }
    const Basis cam = basis(yaw, pitch);
    const int ss = 2, W = opt.width * ss, H = opt.height * ss;

    pg::Aabb cam_box;
    double lo_r = 1e300, hi_r = -1e300, lo_u = 1e300, hi_u = -1e300;
    auto include_pt = [&](Vec3 p) {
        const double r = dot(p, cam.right), u = dot(p, cam.up);
        lo_r = std::min(lo_r, r); hi_r = std::max(hi_r, r);
        lo_u = std::min(lo_u, u); hi_u = std::max(hi_u, u);
    };

    if (opt.has_frame) {
        cam_box.expand(opt.frame_lo);
        cam_box.expand(opt.frame_hi);
        for (int mask = 0; mask < 8; ++mask) {
            include_pt({(mask & 1) ? opt.frame_hi.x : opt.frame_lo.x,
                        (mask & 2) ? opt.frame_hi.y : opt.frame_lo.y,
                        (mask & 4) ? opt.frame_hi.z : opt.frame_lo.z});
        }
        const double flo_r = lo_r, fhi_r = hi_r, flo_u = lo_u, fhi_u = hi_u;
        if (opt.frame_expand) {
            for (const Tri& t : tris)
                for (const Vec3& p : t.p) {
                    include_pt(p);
                    cam_box.expand(p);
                }
            for (const Tri& t : ghost_tris)
                for (const Vec3& p : t.p) {
                    include_pt(p);
                    cam_box.expand(p);
                }
        }
        if (info && (lo_r < flo_r - 1e-6 || hi_r > fhi_r + 1e-6 || lo_u < flo_u - 1e-6 || hi_u > fhi_u + 1e-6)) {
            info->frame_overflowed = true;
        }
    } else {
        if (box.valid()) { cam_box.expand(box.lo); cam_box.expand(box.hi); }
        if (ghost_box.valid()) { cam_box.expand(ghost_box.lo); cam_box.expand(ghost_box.hi); }
        for (const Tri& t : tris)
            for (const Vec3& p : t.p) include_pt(p);
        for (const Tri& t : ghost_tris)
            for (const Vec3& p : t.p) include_pt(p);
    }

    const double radius = std::max(0.5 * cam_box.diagonal(), 1e-4);
    const Vec3 box_center = cam_box.center();
    const Vec3 center = box_center + cam.right * (0.5 * (lo_r + hi_r) - dot(box_center, cam.right)) +
                        cam.up * (0.5 * (lo_u + hi_u) - dot(box_center, cam.up));
    if (info) info->camera_center = center;
    const double half_w = 0.5 * (hi_r - lo_r), half_h = 0.5 * (hi_u - lo_u);
    const double fit = 1.0 - 2.0 * opt.margin;
    const double scale = std::min(W * fit / (2.0 * std::max(half_w, 1e-9)), H * fit / (2.0 * std::max(half_h, 1e-9)));  // px per metre (ortho)
    const double fov = 30.0 * std::numbers::pi / 180;
    const double distance = radius / std::sin(fov / 2) * 1.05 / fit;
    const Vec3 eye = center - cam.fwd * (opt.perspective ? distance : radius * 4 + 1);
    const double focal = 0.5 * H / std::tan(fov / 2);
    auto project = [&](Vec3 p) {
        const Vec3 d = p - eye;
        const double z = dot(d, cam.fwd);
        const double s = opt.perspective ? focal / std::max(z, 1e-6) : scale;
        return Vec3{W * 0.5 + dot(d, cam.right) * s, H * 0.5 - dot(d, cam.up) * s, z};
    };
    auto unproject = [&](double px, double py, double z) {
        const double s = opt.perspective ? focal / std::max(z, 1e-6) : scale;
        return eye + cam.right * ((px - W * 0.5) / s) + cam.up * ((H * 0.5 - py) / s) + cam.fwd * z;
    };
    if (info) info->metres_per_pixel = opt.perspective ? 0 : ss / scale;

    // ---- shadow map (orthographic, from the key light) ----
    const Vec3 key = pg::normalize_or(cam.right * -0.55 + cam.up * 0.75 - cam.fwd * 0.45, {0, 0, 1});  // towards the light
    const Basis light = [&] {
        Basis b;
        b.fwd = key * -1.0;
        const Vec3 ref = std::abs(key.z) > 0.99 ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
        b.right = pg::normalize_or(cross(b.fwd, ref), {1, 0, 0});
        b.up = cross(b.right, b.fwd);
        return b;
    }();
    const int S = opt.shadows ? 2048 : 1;
    std::vector<float> shadow(static_cast<std::size_t>(S) * static_cast<std::size_t>(S), std::numeric_limits<float>::infinity());
    const double s_scale = S / (2.0 * radius * 1.02);
    auto to_light = [&](Vec3 p) {
        const Vec3 d = p - box_center;
        return Vec3{S * 0.5 + dot(d, light.right) * s_scale, S * 0.5 + dot(d, light.up) * s_scale, dot(d, light.fwd)};
    };
    auto raster = [](const Vec3& a, const Vec3& b, const Vec3& c, int w, int h, auto&& emit) {
        const double area = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
        if (std::abs(area) < 1e-12) return;
        const int x0 = std::max(0, static_cast<int>(std::floor(std::min({a.x, b.x, c.x})))), x1 = std::min(w - 1, static_cast<int>(std::ceil(std::max({a.x, b.x, c.x}))));
        const int y0 = std::max(0, static_cast<int>(std::floor(std::min({a.y, b.y, c.y})))), y1 = std::min(h - 1, static_cast<int>(std::ceil(std::max({a.y, b.y, c.y}))));
        for (int y = y0; y <= y1; ++y)
            for (int x = x0; x <= x1; ++x) {
                const double px = x + 0.5, py = y + 0.5;
                const double w0 = ((b.x - px) * (c.y - py) - (b.y - py) * (c.x - px)) / area;
                const double w1 = ((c.x - px) * (a.y - py) - (c.y - py) * (a.x - px)) / area;
                const double w2 = 1.0 - w0 - w1;
                if (w0 < 0 || w1 < 0 || w2 < 0) continue;
                emit(x, y, w0, w1, w2);
            }
    };
    const bool use_translucent = opt.translucent && opt.pass == "beauty";
    std::vector<char> slot_is_translucent(pack.slot_names.size(), 0);
    if (use_translucent) {
        for (std::size_t s = 0; s < pack.slot_names.size(); ++s)
            slot_is_translucent[s] = translucent_slot(pack.slot_names[s]) ? 1 : 0;
    }
    auto tri_translucent = [&](const Tri& t) -> bool {
        return use_translucent && t.slot < slot_is_translucent.size() && slot_is_translucent[t.slot];
    };

    if (opt.shadows)
        for (const Tri& t : tris) {
            if (tri_translucent(t)) continue;  // glass/water lets sunlight illuminate interior structures!
            const Vec3 a = to_light(t.p[0]), b = to_light(t.p[1]), c = to_light(t.p[2]);
            raster(a, b, c, S, S, [&](int x, int y, double w0, double w1, double w2) {
                const auto z = static_cast<float>(w0 * a.z + w1 * b.z + w2 * c.z);
                float& d = shadow[static_cast<std::size_t>(y) * static_cast<std::size_t>(S) + static_cast<std::size_t>(x)];
                if (z < d) d = z;
            });
        }

    // ---- Pass 1: Opaque G-buffer ----
    const std::size_t N = static_cast<std::size_t>(W) * static_cast<std::size_t>(H);
    std::vector<float> depth(N, std::numeric_limits<float>::infinity());
    std::vector<float> nx(N), ny(N), nz(N);
    std::vector<std::uint32_t> slot(N, 0), id(N, 0), mesh_at(N, 0);
    std::vector<const Tri*> trans_tris;
    if (use_translucent) trans_tris.reserve(tris.size() / 8);
    for (const Tri& t : tris) {
        if (tri_translucent(t)) {
            trans_tris.push_back(&t);
            continue;
        }
        const Vec3 a = project(t.p[0]), b = project(t.p[1]), c = project(t.p[2]);
        if (a.z <= 0 || b.z <= 0 || c.z <= 0) continue;
        raster(a, b, c, W, H, [&](int x, int y, double w0, double w1, double w2) {
            double z, u0 = w0, u1 = w1, u2 = w2;
            if (opt.perspective) {  // perspective-correct weights
                const double iz = w0 / a.z + w1 / b.z + w2 / c.z;
                z = 1.0 / iz;
                u0 = w0 / a.z * z; u1 = w1 / b.z * z; u2 = w2 / c.z * z;
            } else {
                z = w0 * a.z + w1 * b.z + w2 * c.z;
            }
            const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
            if (z >= depth[i]) return;
            depth[i] = static_cast<float>(z);
            const Vec3 n = t.n[0] * u0 + t.n[1] * u1 + t.n[2] * u2;
            nx[i] = static_cast<float>(n.x); ny[i] = static_cast<float>(n.y); nz[i] = static_cast<float>(n.z);
            slot[i] = t.slot;
            id[i] = t.id;
            mesh_at[i] = t.mesh;
        });
    }

    // ---- Multi-threaded Shading + Screen-Space Contact AO ----
    const Vec3 fill = pg::normalize_or(cam.right * 0.7 + cam.up * 0.1 - cam.fwd * 0.5, {0, 0, 1});
    const Rgb bg_linear{std::pow(opt.background.r, 1.6), std::pow(opt.background.g, 1.6), std::pow(opt.background.b, 1.6)};
    std::vector<Rgb> color(N, bg_linear);

    const unsigned n_threads = std::clamp(std::thread::hardware_concurrency(), 1u, 8u);
    auto run_rows = [&](int total_rows, auto&& fn) {
        if (n_threads <= 1 || total_rows < 64) {
            fn(0, total_rows);
            return;
        }
        std::vector<std::thread> pool;
        pool.reserve(n_threads);
        for (unsigned t_idx = 0; t_idx < n_threads; ++t_idx) {
            const int y0 = static_cast<int>((static_cast<std::size_t>(t_idx) * static_cast<std::size_t>(total_rows)) / n_threads);
            const int y1 = static_cast<int>((static_cast<std::size_t>(t_idx + 1) * static_cast<std::size_t>(total_rows)) / n_threads);
            pool.emplace_back([&, y0, y1] { fn(y0, y1); });
        }
        for (std::thread& th : pool) th.join();
    };

    const double ao_range = std::max(0.08 * radius, 0.04);
    run_rows(H, [&](int y_begin, int y_end) {
        for (int y = y_begin; y < y_end; ++y)
            for (int x = 0; x < W; ++x) {
                const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                if (!std::isfinite(depth[i])) continue;
                Vec3 n = pg::normalize_or({nx[i], ny[i], nz[i]}, {0, 0, 1});
                const Vec3 p = unproject(x + 0.5, y + 0.5, depth[i]);
                const Vec3 to_eye = opt.perspective ? pg::normalize_or(eye - p, cam.fwd * -1.0) : cam.fwd * -1.0;
                if (dot(n, to_eye) < 0) n = n * -1.0;  // two-sided
                std::uint32_t eff_slot = slot[i];
                double cavity = 0;
                if (const pg::doc::DetailIndex& details = detail_index[mesh_at[i]]; !details.empty() && inverse[id[i] - 1].valid) {
                    const Inverse& inv = inverse[id[i] - 1];
                    const Vec3 pl = inv.point(from_view(p, pack.y_up));
                    const Vec3 ne = from_view(n, pack.y_up);
                    const Vec3 nl = pg::normalize_or({inv.r[0][0] * ne.x + inv.r[0][1] * ne.y + inv.r[0][2] * ne.z, inv.r[1][0] * ne.x + inv.r[1][1] * ne.y + inv.r[1][2] * ne.z,
                                                      inv.r[2][0] * ne.x + inv.r[2][1] * ne.y + inv.r[2][2] * ne.z}, {0, 0, 1});
                    const Vec3 t1 = pg::normalize_or(cross(nl, std::abs(nl.z) < 0.9 ? Vec3{0, 0, 1} : Vec3{1, 0, 0}), {1, 0, 0}), t2 = cross(nl, t1);
                    const double pixel = (opt.perspective ? depth[i] / focal : 1.0 / scale) / inv.scale;
                    const double e = std::max(pixel, 1e-7);
                    const pg::doc::DetailSample here = details.sample(pl, nl);
                    const double du = (details.sample(pl + t1 * e, nl).height - details.sample(pl - t1 * e, nl).height) / (2 * e);
                    const double dv = (details.sample(pl + t2 * e, nl).height - details.sample(pl - t2 * e, nl).height) / (2 * e);
                    if (du != 0 || dv != 0) {
                        Vec3 g = t1 * du + t2 * dv;
                        if (const double steep = pg::length(g); steep > 4) g = g * (4 / steep);
                        const Vec3 bent = pg::normalize_or(nl - g, nl);
                        const Vec3 world{inv.r[0][0] * bent.x + inv.r[1][0] * bent.y + inv.r[2][0] * bent.z, inv.r[0][1] * bent.x + inv.r[1][1] * bent.y + inv.r[2][1] * bent.z,
                                         inv.r[0][2] * bent.x + inv.r[1][2] * bent.y + inv.r[2][2] * bent.z};
                        n = pg::normalize_or(to_view(world, pack.y_up), n);
                    }
                    if (here.material != 0xFFFF) eff_slot = here.material;
                    double shade = 0;
                    for (const double reach : {0.005, 0.016}) {
                        const double rho = reach / inv.scale;
                        const bool diagonal = reach > 0.01;
                        for (int k = 0; k < 4; ++k) {
                            const double a = (k + (diagonal ? 0.5 : 0.0)) * (std::numbers::pi / 2);
                            const double rise = details.sample(pl + (t1 * std::cos(a) + t2 * std::sin(a)) * rho, nl).height - here.height;
                            shade += std::clamp(rise / rho, 0.0, 1.0);
                        }
                    }
                    cavity = std::min(1.0, shade / 8.0 * 2.2);
                }
                if (opt.pass == "materials") {
                    std::uint32_t h = 2166136261u;
                    if (eff_slot < pack.slot_names.size())
                        for (const unsigned char c : pack.slot_names[eff_slot]) h = (h ^ c) * 16777619u;
                    color[i] = {0.25 + 0.7 * ((h >> 8) & 255) / 255.0, 0.25 + 0.7 * ((h >> 16) & 255) / 255.0, 0.25 + 0.7 * ((h >> 24) & 255) / 255.0};
                    continue;
                }
                if (opt.pass == "parts") {
                    std::uint32_t h = id[i] * 2654435761u;
                    color[i] = {0.25 + 0.7 * ((h >> 8) & 255) / 255.0, 0.25 + 0.7 * ((h >> 16) & 255) / 255.0, 0.25 + 0.7 * ((h >> 24) & 255) / 255.0};
                    continue;
                }

                // Screen-space contact / crease ambient occlusion (8 taps at 3px and 8px in supersampled space)
                double ssao = 1.0;
                if (opt.contact_ao) {
                    static constexpr int kTap[8][2] = {{3, 0}, {-3, 0}, {0, 3}, {0, -3}, {6, 6}, {-6, 6}, {6, -6}, {-6, -6}};
                    double occ = 0.0;
                    for (const auto& tp : kTap) {
                        const int sx = x + tp[0], sy = y + tp[1];
                        if (sx < 0 || sy < 0 || sx >= W || sy >= H) continue;
                        const std::size_t j = static_cast<std::size_t>(sy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(sx);
                        if (!std::isfinite(depth[j])) continue;
                        const double dz = static_cast<double>(depth[i]) - static_cast<double>(depth[j]);
                        if (dz > 0.0015 && dz < ao_range) {
                            const Vec3 q = unproject(sx + 0.5, sy + 0.5, depth[j]);
                            const Vec3 v = q - p;
                            const double dist = pg::length(v);
                            if (dist > 1e-6) {
                                const double ndv = dot(n, v / dist);
                                if (ndv > 0.12) occ += (ndv - 0.12) * (1.0 - dist / (ao_range * 1.6));
                            }
                        }
                    }
                    ssao = 1.0 - std::clamp(occ * 0.085, 0.0, 0.34);
                }

                double lit = 1.0;
                if (opt.shadows) {
                    const Vec3 q = to_light(p + n * (0.004 * radius));
                    double sum = 0;
                    for (int dy = -1; dy <= 1; ++dy)
                        for (int dx = -1; dx <= 1; ++dx) {
                            const int sx = std::clamp(static_cast<int>(q.x) + dx, 0, S - 1), sy = std::clamp(static_cast<int>(q.y) + dy, 0, S - 1);
                            sum += q.z - 0.006 * radius <= shadow[static_cast<std::size_t>(sy) * static_cast<std::size_t>(S) + static_cast<std::size_t>(sx)] ? 1.0 : 0.0;
                        }
                    lit = sum / 9.0;
                }
                Vec3 base = eff_slot < pack.slot_colors.size() ? pack.slot_colors[eff_slot] : Vec3{0.6, 0.6, 0.6};
                const bool metal = eff_slot < pack.slot_names.size() && metallic(pack.slot_names[eff_slot]);
                if (opt.pass == "zebra") {
                    // Analytical high-contrast glossy reflection stripes (Catmull-Clark / NURBS surface continuity diagnostic).
                    const double ndv = std::max(0.0, dot(n, to_eye));
                    const Vec3 refl = pg::normalize_or(n * (2.0 * ndv) - to_eye, n);
                    const double u = refl.z * 0.82 + refl.x * 0.45 + refl.y * 0.35;
                    const double wave = std::sin(u * 26.0);
                    // Smooth anti-aliased stripe transition with slight warm-ivory vs charcoal-slate tint + cavity darkening.
                    const double t = std::clamp(0.5 + wave * 3.5, 0.0, 1.0);
                    const double s_t = t * t * (3.0 - 2.0 * t);
                    const double cav_mul = (1.0 - 0.35 * cavity) * (0.78 + 0.22 * ssao);
                    const double rim = 0.06 * std::pow(1.0 - ndv, 3.0);
                    const Vec3 dark_band{0.10, 0.11, 0.13}, light_band{0.86, 0.88, 0.90};
                    const Vec3 c = (dark_band * (1.0 - s_t) + light_band * s_t) * cav_mul + Vec3{rim, rim, rim};
                    color[i] = {c.x, c.y, c.z};
                    continue;
                }
                if (opt.pass == "matcap") {
                    // Studio dark-gunmetal / ceramic matcap in view space: exposes FWN flat faces, bevel highlights & SDS poles.
                    const double vnx = dot(n, cam.right);
                    const double vny = dot(n, cam.up);
                    const double vnz = std::max(0.0, dot(n, to_eye));
                    const Vec3 k1 = pg::normalize_or({-0.42, 0.62, 0.66}, {0, 0, 1});
                    const Vec3 k2 = pg::normalize_or({0.68, 0.28, 0.68}, {0, 0, 1});
                    const Vec3 vn{vnx, vny, vnz};
                    const double d1 = std::max(0.0, dot(vn, k1));
                    const double d2 = std::max(0.0, dot(vn, k2));
                    const double sp1 = std::pow(d1, 28.0) * 0.72 + std::pow(d1, 6.0) * 0.18;
                    const double sp2 = std::pow(d2, 18.0) * 0.28;
                    const double fres = std::pow(1.0 - vnz, 3.2) * 0.32;
                    const double hemi = 0.5 + 0.5 * vny;
                    const double ao_m = (1.0 - 0.50 * cavity) * ssao;
                    const Vec3 base_m = (Vec3{0.12, 0.14, 0.17} * (1.0 - hemi) + Vec3{0.34, 0.37, 0.42} * hemi) * (0.45 + 0.55 * d1);
                    const Vec3 hi = Vec3{0.96, 0.97, 1.00} * sp1 + Vec3{0.68, 0.76, 0.88} * (sp2 + fres);
                    const Vec3 c = (base_m + hi) * ao_m;
                    color[i] = {c.x, c.y, c.z};
                    continue;
                }
                if (opt.pass == "curvature") {
                    // Signed mean-curvature false-color heatmap computed from smooth G-buffer normal divergence:
                    // cool blue = concave fillet/groove, neutral grey = dead-flat panel, warm amber/red = convex bevel/pole spike.
                    auto sample_n = [&](int sx, int sy, Vec3 fallback) -> Vec3 {
                        if (sx < 0 || sy < 0 || sx >= W || sy >= H) return fallback;
                        const std::size_t j = static_cast<std::size_t>(sy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(sx);
                        if (!std::isfinite(depth[j]) || id[j] != id[i]) return fallback;
                        if (std::abs(static_cast<double>(depth[j]) - static_cast<double>(depth[i])) > 0.08 * radius) return fallback;
                        Vec3 ns = pg::normalize_or({nx[j], ny[j], nz[j]}, fallback);
                        if (dot(ns, to_eye) < 0) ns = ns * -1.0;
                        return ns;
                    };
                    const int step = 2;
                    const Vec3 nL = sample_n(x - step, y, n), nR = sample_n(x + step, y, n);
                    const Vec3 nU = sample_n(x, y - step, n), nD = sample_n(x, y + step, n);
                    // Signed normal divergence: positive on convex surfaces (normal turns right as x increases, up as y decreases).
                    const double div_x = dot(nR - nL, cam.right);
                    const double div_y = dot(nU - nD, cam.up);
                    const double px_world = opt.perspective ? (depth[i] / focal) : (1.0 / scale);
                    const double kappa = (div_x + div_y) / std::max(1e-5, 2.0 * step * px_world);
                    // Normalize by object scale so small and large assemblies map symmetrically into [-1, +1].
                    const double k_norm = std::tanh(kappa * (0.045 * std::max(0.25, radius)));
                    const Vec3 flat_col{0.46, 0.48, 0.50};
                    Vec3 heat;
                    if (k_norm >= 0.0) {
                        const double t = k_norm;
                        const Vec3 amber{0.92, 0.62, 0.16}, hot{0.92, 0.16, 0.12};
                        heat = t < 0.5 ? (flat_col * (1.0 - 2.0 * t) + amber * (2.0 * t))
                                       : (amber * (2.0 - 2.0 * t) + hot * (2.0 * t - 1.0));
                    } else {
                        const double t = -k_norm;
                        const Vec3 cyan{0.18, 0.64, 0.86}, deep_blue{0.10, 0.24, 0.82};
                        heat = t < 0.5 ? (flat_col * (1.0 - 2.0 * t) + cyan * (2.0 * t))
                                       : (cyan * (2.0 - 2.0 * t) + deep_blue * (2.0 * t - 1.0));
                    }
                    const double shade = (0.72 + 0.28 * std::max(0.0, dot(n, key))) * (1.0 - 0.30 * cavity) * (0.85 + 0.15 * ssao);
                    color[i] = {heat.x * shade, heat.y * shade, heat.z * shade};
                    continue;
                }
                if (opt.pass == "clay") {
                    base = {0.72, 0.70, 0.66};
                    const double diffuse = std::max(0.0, dot(n, key)) * lit;
                    const double sky = 0.5 + 0.5 * n.z;
                    const Vec3 half = pg::normalize_or(key + to_eye, key);
                    const double spec = 0.06 * std::pow(std::max(0.0, dot(n, half)), 12.0) * lit;
                    const double light_amount = (0.20 + 0.22 * sky + 0.78 * diffuse + 0.16 * std::max(0.0, dot(n, fill))) * (1.0 - 0.55 * cavity) * ssao;
                    color[i] = {base.x * light_amount + spec, base.y * light_amount + spec, base.z * light_amount + spec};
                    continue;
                }
                if (eff_slot < pack.slot_names.size()) {
                    const std::string& name = pack.slot_names[eff_slot];
                    if (name.find("weathered-iron") != std::string::npos || name == "hangar-iron") {
                        const double rust = std::clamp(0.5 + 1.6 * pg::gradient_noise(p * 2.3, 11) + 0.6 * pg::gradient_noise(p * 9.0, 12) - 0.25 * n.z, 0.0, 1.0);
                        base = base * (1.0 - 0.75 * rust) + Vec3{0.52, 0.25, 0.12} * (0.75 * rust);
                    } else if (name.find("limestone") != std::string::npos || name == "stone" || name == "tile") {
                        base = base * (0.93 + 0.14 * pg::gradient_noise(p * 3.1, 21));
                    } else if (name.find("brick") != std::string::npos) {
                        base = base * (0.90 + 0.20 * pg::gradient_noise(p * 6.0, 31));
                    }
                }
                // Curvature-driven edge-wear & concave crevice micro-AO in beauty pass:
                auto sample_n_b = [&](int sx, int sy) -> Vec3 {
                    if (sx < 0 || sy < 0 || sx >= W || sy >= H) return n;
                    const std::size_t j = static_cast<std::size_t>(sy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(sx);
                    if (!std::isfinite(depth[j]) || id[j] != id[i]) return n;
                    if (std::abs(static_cast<double>(depth[j]) - static_cast<double>(depth[i])) > 0.06 * radius) return n;
                    Vec3 ns = pg::normalize_or({nx[j], ny[j], nz[j]}, n);
                    return dot(ns, to_eye) < 0 ? ns * -1.0 : ns;
                };
                const Vec3 bnL = sample_n_b(x - 2, y), bnR = sample_n_b(x + 2, y);
                const Vec3 bnU = sample_n_b(x, y - 2), bnD = sample_n_b(x, y + 2);
                const double b_px = opt.perspective ? (depth[i] / focal) : (1.0 / scale);
                const double b_kap = (dot(bnR - bnL, cam.right) + dot(bnU - bnD, cam.up)) / std::max(1e-5, 4.0 * b_px);
                const double b_kn = std::tanh(b_kap * (0.040 * std::max(0.25, radius)));
                const double edge_wear = std::clamp((b_kn - 0.18) / 0.82, 0.0, 1.0);
                const double crevice_dark = std::clamp((-b_kn - 0.10) / 0.90, 0.0, 1.0);

                // Subtle edge catchlight / wear tint on convex chamfers & crevice darkening in concave roots.
                const Vec3 wear_tint = metal ? (base * 0.45 + Vec3{0.55, 0.53, 0.48}) : (base * 0.72 + Vec3{0.26, 0.25, 0.22});
                base = base * (1.0 - 0.22 * edge_wear) + wear_tint * (0.22 * edge_wear);
                const double curv_ao = 1.0 - 0.24 * crevice_dark;

                const double ndv = std::max(0.0, dot(n, to_eye));
                const double fresnel = std::pow(1.0 - ndv, 3.8);
                const double diffuse = std::max(0.0, dot(n, key)) * lit;
                const double sky = 0.5 + 0.5 * n.z;
                const Vec3 half1 = pg::normalize_or(key + to_eye, key);
                const Vec3 half2 = pg::normalize_or(fill + to_eye, fill);
                const double sp1 = (metal ? 0.42 : 0.08) * std::pow(std::max(0.0, dot(n, half1)), metal ? 56.0 : 18.0) * lit;
                const double sp2 = (metal ? 0.16 : 0.03) * std::pow(std::max(0.0, dot(n, half2)), metal ? 28.0 : 12.0);
                const double rim_env = (metal ? 0.24 : 0.06) * fresnel * (0.45 + 0.55 * sky) * ssao;
                const Vec3 spec_col = metal ? (base * 0.60 + Vec3{0.40, 0.40, 0.40}) : Vec3{1.0, 0.99, 0.97};
                const double light_amount = (0.19 + 0.23 * sky + 0.78 * diffuse + 0.16 * std::max(0.0, dot(n, fill))) * (1.0 - 0.55 * cavity) * ssao * curv_ao;
                const Vec3 spec_total = spec_col * (sp1 + sp2 + rim_env + 0.06 * edge_wear * lit);
                color[i] = {base.x * light_amount + spec_total.x, base.y * light_amount + spec_total.y, base.z * light_amount + spec_total.z};
            }
    });

    // ---- Pass 2: Translucent Glass & Water Layer (Schlick Fresnel + sky reflection + sun glint) ----
    if (!trans_tris.empty()) {
        std::vector<float> tdepth(N, std::numeric_limits<float>::infinity());
        std::vector<float> tnx(N), tny(N), tnz(N);
        std::vector<std::uint32_t> tslot(N, 0), tid(N, 0);
        for (const Tri* tp : trans_tris) {
            const Tri& t = *tp;
            const Vec3 a = project(t.p[0]), b = project(t.p[1]), c = project(t.p[2]);
            if (a.z <= 0 || b.z <= 0 || c.z <= 0) continue;
            raster(a, b, c, W, H, [&](int x, int y, double w0, double w1, double w2) {
                double z, u0 = w0, u1 = w1, u2 = w2;
                if (opt.perspective) {
                    const double iz = w0 / a.z + w1 / b.z + w2 / c.z;
                    z = 1.0 / iz;
                    u0 = w0 / a.z * z; u1 = w1 / b.z * z; u2 = w2 / c.z * z;
                } else {
                    z = w0 * a.z + w1 * b.z + w2 * c.z;
                }
                const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                if (z >= depth[i] || z >= tdepth[i]) return;  // occluded by opaque geometry or nearer glass
                tdepth[i] = static_cast<float>(z);
                const Vec3 n = t.n[0] * u0 + t.n[1] * u1 + t.n[2] * u2;
                tnx[i] = static_cast<float>(n.x); tny[i] = static_cast<float>(n.y); tnz[i] = static_cast<float>(n.z);
                tslot[i] = t.slot;
                tid[i] = t.id;
            });
        }
        run_rows(H, [&](int y_begin, int y_end) {
            for (int y = y_begin; y < y_end; ++y)
                for (int x = 0; x < W; ++x) {
                    const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                    if (!std::isfinite(tdepth[i])) continue;
                    Vec3 gn = pg::normalize_or({tnx[i], tny[i], tnz[i]}, {0, 0, 1});
                    const Vec3 p = unproject(x + 0.5, y + 0.5, tdepth[i]);
                    const Vec3 to_eye = opt.perspective ? pg::normalize_or(eye - p, cam.fwd * -1.0) : cam.fwd * -1.0;
                    if (dot(gn, to_eye) < 0) gn = gn * -1.0;
                    const std::string& sname = tslot[i] < pack.slot_names.size() ? pack.slot_names[tslot[i]] : std::string();
                    const bool is_water = sname.find("water") != std::string::npos;
                    if (is_water) {
                        const double rip_x = pg::gradient_noise(p * 3.8, 71) * 0.06;
                        const double rip_y = pg::gradient_noise(p * 3.8, 72) * 0.06;
                        gn = pg::normalize_or(gn + Vec3{rip_x, rip_y, 0.0}, gn);
                    }
                    const Vec3 tint = tslot[i] < pack.slot_colors.size() ? pack.slot_colors[tslot[i]]
                                    : (is_water ? Vec3{0.10, 0.26, 0.30} : Vec3{0.58, 0.68, 0.72});
                    const double ndv = std::clamp(dot(gn, to_eye), 0.0, 1.0);
                    const double f0 = is_water ? 0.04 : 0.07;
                    const double fresnel = f0 + (1.0 - f0) * std::pow(1.0 - ndv, 4.0);
                    const Vec3 refl = gn * (2.0 * dot(gn, to_eye)) - to_eye;
                    const double sky_up = std::clamp(0.5 + 0.5 * refl.z, 0.0, 1.0);
                    const Vec3 env_col = Vec3{0.20, 0.22, 0.24} * (1.0 - sky_up) + Vec3{0.62, 0.72, 0.80} * sky_up;
                    const Vec3 half = pg::normalize_or(key + to_eye, key);
                    const double sun_spec = 0.75 * std::pow(std::max(0.0, dot(gn, half)), is_water ? 64.0 : 110.0);
                    const double base_alpha = is_water ? 0.48 : 0.20;
                    const double alpha = std::clamp(base_alpha + 0.52 * fresnel, 0.0, 0.78);
                    const Vec3 surf = tint * (0.35 + 0.45 * std::max(0.0, dot(gn, key))) * (1.0 - fresnel) + env_col * fresnel;
                    color[i] = {
                        color[i].r * (1.0 - alpha) + surf.x * alpha + sun_spec,
                        color[i].g * (1.0 - alpha) + surf.y * alpha + sun_spec,
                        color[i].b * (1.0 - alpha) + surf.z * alpha + sun_spec * 0.96,
                    };
                    if (!std::isfinite(depth[i])) {
                        depth[i] = tdepth[i];
                        id[i] = tid[i];
                        nx[i] = tnx[i]; ny[i] = tny[i]; nz[i] = tnz[i];
                    }
                }
        });
    }

    // Optional retained-last-valid ghost pass: pure translucent slate-clay surface shading (no outlines).
    if (!ghost_tris.empty() && opt.ghost_opacity > 0.0) {
        std::vector<float> gdepth(N, std::numeric_limits<float>::infinity());
        std::vector<float> gnx(N), gny(N), gnz(N);
        for (const Tri& t : ghost_tris) {
            const Vec3 a = project(t.p[0]), b = project(t.p[1]), c = project(t.p[2]);
            if (a.z <= 0 || b.z <= 0 || c.z <= 0) continue;
            raster(a, b, c, W, H, [&](int x, int y, double w0, double w1, double w2) {
                double z, u0 = w0, u1 = w1, u2 = w2;
                if (opt.perspective) {
                    const double iz = w0 / a.z + w1 / b.z + w2 / c.z;
                    z = 1.0 / iz;
                    u0 = w0 / a.z * z; u1 = w1 / b.z * z; u2 = w2 / c.z * z;
                } else {
                    z = w0 * a.z + w1 * b.z + w2 * c.z;
                }
                const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                if (z >= gdepth[i]) return;
                gdepth[i] = static_cast<float>(z);
                const Vec3 n = t.n[0] * u0 + t.n[1] * u1 + t.n[2] * u2;
                gnx[i] = static_cast<float>(n.x); gny[i] = static_cast<float>(n.y); gnz[i] = static_cast<float>(n.z);
            });
        }
        const double alpha_bg = std::clamp(opt.ghost_opacity, 0.0, 1.0);
        const double alpha_fg = std::clamp(opt.ghost_opacity * 0.25, 0.0, 1.0);
        for (int y = 0; y < H; ++y)
            for (int x = 0; x < W; ++x) {
                const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                if (!std::isfinite(gdepth[i])) continue;
                Vec3 gn = pg::normalize_or({gnx[i], gny[i], gnz[i]}, {0, 0, 1});
                const Vec3 p = unproject(x + 0.5, y + 0.5, gdepth[i]);
                const Vec3 to_eye = opt.perspective ? pg::normalize_or(eye - p, cam.fwd * -1.0) : cam.fwd * -1.0;
                if (dot(gn, to_eye) < 0) gn = gn * -1.0;
                const double g_amt = 0.24 + 0.24 * (0.5 + 0.5 * gn.z) + 0.62 * std::max(0.0, dot(gn, key));
                const Rgb gc{0.54 * g_amt, 0.61 * g_amt, 0.70 * g_amt};
                if (!std::isfinite(depth[i])) {
                    color[i] = {color[i].r * (1.0 - alpha_bg) + gc.r * alpha_bg,
                                color[i].g * (1.0 - alpha_bg) + gc.g * alpha_bg,
                                color[i].b * (1.0 - alpha_bg) + gc.b * alpha_bg};
                } else if (gdepth[i] + 1e-4f < depth[i]) {
                    color[i] = {color[i].r * (1.0 - alpha_fg) + gc.r * alpha_fg,
                                color[i].g * (1.0 - alpha_fg) + gc.g * alpha_fg,
                                color[i].b * (1.0 - alpha_fg) + gc.b * alpha_fg};
                }
            }
    }
    // Outlines where the part id changes or the surface turns sharply.
    if (opt.outline > 0) {
        std::vector<float> edge(N, 0.0f);
        for (int y = 1; y + 1 < H; ++y)
            for (int x = 1; x + 1 < W; ++x) {
                const std::size_t i = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                float e = 0;
                for (const std::size_t j : {i + 1, i + static_cast<std::size_t>(W)}) {
                    const bool a = std::isfinite(depth[i]), b = std::isfinite(depth[j]);
                    if (a != b) { e = 1; continue; }
                    if (!a) continue;
                    if (id[i] != id[j]) e = std::max(e, 0.8f);
                    const float d = nx[i] * nx[j] + ny[i] * ny[j] + nz[i] * nz[j];
                    if (d < 0.75f) e = std::max(e, 0.7f);
                }
                edge[i] = e;
            }
        for (std::size_t i = 0; i < N; ++i) {
            const double k = 1.0 - opt.outline * edge[i];
            color[i] = {color[i].r * k, color[i].g * k, color[i].b * k};
        }
    }

    if (info && opt.record_instance_map) {
        info->instance_map.resize(static_cast<std::size_t>(opt.width) * static_cast<std::size_t>(opt.height));
        for (int y = 0; y < opt.height; ++y)
            for (int x = 0; x < opt.width; ++x)
                info->instance_map[static_cast<std::size_t>(y) * static_cast<std::size_t>(opt.width) + static_cast<std::size_t>(x)] =
                    id[static_cast<std::size_t>(y * ss) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x * ss)];
    }

    // Optional Cinema 4D-style Isoline / SDS control-cage wireframe overlay (--wire-cage).
    // Regular cage edges draw in cool cyan; creased edges draw in warm amber/red.
    if (opt.cage_edges && !opt.cage_edges->empty()) {
        const float z_bias = static_cast<float>(0.025 * radius);
        for (const CageEdge& ce : *opt.cage_edges) {
            const Vec3 a = project(ce.a), b = project(ce.b);
            if (a.z <= 0 || b.z <= 0) continue;
            const double span = std::max(std::abs(b.x - a.x), std::abs(b.y - a.y));
            if (!(span < 4.0 * (W + H))) continue;
            const int steps = std::max(1, static_cast<int>(std::ceil(span)));
            const Rgb wire_col = ce.sharpness >= 8.0f ? Rgb{0.96, 0.22, 0.14}
                               : ce.sharpness > 0.0f  ? Rgb{0.98, 0.56, 0.10}
                                                      : Rgb{0.16, 0.72, 0.96};
            const int thick = ce.sharpness > 0.0f ? 1 : 0;  // creased edges slightly bolder
            for (int s = 0; s <= steps; ++s) {
                const double t = static_cast<double>(s) / static_cast<double>(steps);
                const int cx = static_cast<int>(std::lround(a.x + (b.x - a.x) * t));
                const int cy = static_cast<int>(std::lround(a.y + (b.y - a.y) * t));
                const double z = opt.perspective ? (1.0 / ((1.0 - t) / a.z + t / b.z))
                                                 : (a.z + (b.z - a.z) * t);
                for (int dy = -thick; dy <= thick; ++dy) {
                    for (int dx = -thick; dx <= thick; ++dx) {
                        const int x = cx + dx, y = cy + dy;
                        if (x < 0 || y < 0 || x >= W || y >= H) continue;
                        const std::size_t idx = static_cast<std::size_t>(y) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x);
                        const bool visible = !std::isfinite(depth[idx]) || static_cast<float>(z) <= depth[idx] + z_bias;
                        const double alpha = visible ? (ce.sharpness > 0.0f ? 0.92 : 0.78) : 0.16;
                        color[idx] = {color[idx].r * (1.0 - alpha) + wire_col.r * alpha,
                                      color[idx].g * (1.0 - alpha) + wire_col.g * alpha,
                                      color[idx].b * (1.0 - alpha) + wire_col.b * alpha};
                    }
                }
            }
        }
    }

    Image img{opt.width, opt.height, std::vector<unsigned char>(static_cast<std::size_t>(opt.width) * static_cast<std::size_t>(opt.height) * 3)};
    run_rows(opt.height, [&](int y_begin, int y_end) {
        for (int y = y_begin; y < y_end; ++y)
            for (int x = 0; x < opt.width; ++x) {
                Rgb acc{0, 0, 0};
                for (int dy = 0; dy < ss; ++dy)
                    for (int dx = 0; dx < ss; ++dx) {
                        const Rgb& c = color[static_cast<std::size_t>(y * ss + dy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(x * ss + dx)];
                        acc = {acc.r + c.r, acc.g + c.g, acc.b + c.b};
                    }
                auto px = [&](double c) { return static_cast<unsigned char>(std::clamp(std::pow(std::max(c / (ss * ss), 0.0), 1 / 1.6) * 255.0, 0.0, 255.0)); };
                const std::size_t o = (static_cast<std::size_t>(y) * static_cast<std::size_t>(opt.width) + static_cast<std::size_t>(x)) * 3;
                img.rgb[o] = px(acc.r);
                img.rgb[o + 1] = px(acc.g);
                img.rgb[o + 2] = px(acc.b);
            }
    });
    return img;
}

}  // namespace pgtools
