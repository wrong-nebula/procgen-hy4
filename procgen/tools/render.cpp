#include "render.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <numbers>
#include <stdexcept>

namespace pgtools {
namespace {

using pg::Vec3;
constexpr double kNear = 0.05;  // near-plane distance in world units

struct View {
    Vec3 eye, right, up, fwd;
    double focal;
    int w, h;
    // World -> (pixel x, pixel y, depth); depth <= 0 means behind the eye.
    [[nodiscard]] Vec3 project(Vec3 p) const {
        const Vec3 d = p - eye;
        const double z = pg::dot(d, fwd);
        const double s = focal / std::max(z, 1e-9);
        return {w * 0.5 + pg::dot(d, right) * s, h * 0.5 - pg::dot(d, up) * s, z};
    }
};

View make_view(const Camera& c, int w, int h) {
    const double yaw = c.yaw_deg * std::numbers::pi / 180, pitch = c.pitch_deg * std::numbers::pi / 180;
    const Vec3 dir{std::cos(pitch) * std::cos(yaw), std::cos(pitch) * std::sin(yaw), std::sin(pitch)};
    View v;
    v.eye = c.target + dir * c.distance;
    v.fwd = pg::normalize_or(c.target - v.eye, {1, 0, 0});
    v.right = pg::normalize_or(pg::cross(v.fwd, {0, 0, 1}), {0, 1, 0});
    v.up = pg::cross(v.right, v.fwd);
    v.focal = 0.5 * h / std::tan(0.5 * c.fov_deg * std::numbers::pi / 180);
    v.w = w;
    v.h = h;
    return v;
}

std::uint32_t crc_table[256];
void init_crc() {
    for (std::uint32_t n = 0; n < 256; ++n) {
        std::uint32_t c = n;
        for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xedb88320u ^ (c >> 1) : c >> 1;
        crc_table[n] = c;
    }
}
std::uint32_t crc(const unsigned char* p, std::size_t n, std::uint32_t c = 0xffffffffu) {
    for (std::size_t i = 0; i < n; ++i) c = crc_table[(c ^ p[i]) & 0xff] ^ (c >> 8);
    return c;
}
void be32(std::vector<unsigned char>& o, std::uint32_t v) {
    for (int s = 24; s >= 0; s -= 8) o.push_back(static_cast<unsigned char>(v >> s));
}
void chunk(std::FILE* f, const char* type, const std::vector<unsigned char>& data) {
    std::vector<unsigned char> buf;
    buf.reserve(12 + data.size());  // length + type + data + crc, no regrowth
    be32(buf, static_cast<std::uint32_t>(data.size()));
    buf.insert(buf.end(), type, type + 4);
    buf.insert(buf.end(), data.begin(), data.end());
    be32(buf, crc(buf.data() + 4, buf.size() - 4) ^ 0xffffffffu);
    std::fwrite(buf.data(), 1, buf.size(), f);
}

}  // namespace

Image render(const std::vector<Item>& items, const Camera& cam, int width, int height, double wire_alpha) {
    const int ss = 2, W = width * ss, H = height * ss;
    const View v = make_view(cam, W, H);
    std::vector<double> depth(static_cast<std::size_t>(W) * H, std::numeric_limits<double>::infinity());
    std::vector<Rgb> color(static_cast<std::size_t>(W) * H);
    for (int y = 0; y < H; ++y)  // soft vertical gradient background
        for (int x = 0; x < W; ++x) {
            const double t = static_cast<double>(y) / H;
            color[static_cast<std::size_t>(y) * W + x] = {0.93 - 0.12 * t, 0.94 - 0.11 * t, 0.96 - 0.08 * t};
        }
    const Vec3 key = pg::normalize_or(v.right * -0.4 + v.up * 0.8 - v.fwd * 0.6, {0, 0, 1});
    const Vec3 fill = pg::normalize_or(v.right * 0.7 - v.up * 0.2 - v.fwd * 0.3, {0, 0, 1});

    for (const Item& it : items) {
        const pg::Mesh& m = *it.mesh;
        std::vector<Vec3> sp(m.vertex_count());
        for (std::size_t i = 0; i < sp.size(); ++i) sp[i] = v.project(m.positions[i]);
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            const auto fv = m.face(f);
            Vec3 n = pg::face_normal(m, f);
            const Vec3 to_eye = pg::normalize_or(v.eye - pg::face_centroid(m, f), {0, 0, 1});
            if (pg::dot(n, to_eye) < 0) n = -n;  // two-sided (open meshes)
            const double lam = 0.22 + 0.62 * std::max(0.0, pg::dot(n, key)) + 0.22 * std::max(0.0, pg::dot(n, fill));
            const double rim = 0.10 * std::pow(1.0 - std::max(0.0, pg::dot(n, to_eye)), 3);
            const Rgb c{std::min(1.0, it.color.r * lam + rim), std::min(1.0, it.color.g * lam + rim),
                        std::min(1.0, it.color.b * lam + rim)};
            for (std::size_t k = 1; k + 1 < fv.size(); ++k) {
                const Vec3 a = sp[fv[0]], b = sp[fv[k]], d = sp[fv[k + 1]];
                if (a.z <= kNear || b.z <= kNear || d.z <= kNear) continue;
                const double area = (b.x - a.x) * (d.y - a.y) - (b.y - a.y) * (d.x - a.x);
                if (std::abs(area) < 1e-12) continue;
                const int x0 = std::max(0, static_cast<int>(std::floor(std::min({a.x, b.x, d.x}))));
                const int x1 = std::min(W - 1, static_cast<int>(std::ceil(std::max({a.x, b.x, d.x}))));
                const int y0 = std::max(0, static_cast<int>(std::floor(std::min({a.y, b.y, d.y}))));
                const int y1 = std::min(H - 1, static_cast<int>(std::ceil(std::max({a.y, b.y, d.y}))));
                for (int y = y0; y <= y1; ++y)
                    for (int x = x0; x <= x1; ++x) {
                        const double px = x + 0.5, py = y + 0.5;
                        const double w0 = ((b.x - px) * (d.y - py) - (b.y - py) * (d.x - px)) / area;
                        const double w1 = ((d.x - px) * (a.y - py) - (d.y - py) * (a.x - px)) / area;
                        const double w2 = 1.0 - w0 - w1;
                        if (w0 < 0 || w1 < 0 || w2 < 0) continue;
                        const double z = 1.0 / (w0 / a.z + w1 / b.z + w2 / d.z);
                        const std::size_t i = static_cast<std::size_t>(y) * W + x;
                        if (z < depth[i]) { depth[i] = z; color[i] = c; }
                    }
            }
        }
    }
    // Wireframe: edges drawn where they are (nearly) the visible surface.
    for (const Item& it : items) {
        if (!it.wire) continue;
        const pg::Mesh& m = *it.mesh;
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            const auto fv = m.face(f);
            for (std::size_t k = 0; k < fv.size(); ++k) {
                const Vec3 a = v.project(m.positions[fv[k]]), b = v.project(m.positions[fv[(k + 1) % fv.size()]]);
                if (a.z <= kNear || b.z <= kNear) continue;
                const double span = std::max(std::abs(b.x - a.x), std::abs(b.y - a.y));
                if (!(span < 4.0 * (W + H))) continue;  // off-screen or degenerate projection
                const int steps = static_cast<int>(std::ceil(span)) + 1;
                for (int s = 0; s <= steps; ++s) {
                    const double t = static_cast<double>(s) / steps;
                    const int x = static_cast<int>(a.x + (b.x - a.x) * t), y = static_cast<int>(a.y + (b.y - a.y) * t);
                    if (x < 0 || y < 0 || x >= W || y >= H) continue;
                    const double z = 1.0 / ((1 - t) / a.z + t / b.z);
                    const std::size_t i = static_cast<std::size_t>(y) * W + x;
                    if (z <= depth[i] * 1.004 + 1e-6) {
                        Rgb& c = color[i];
                        c = {c.r * (1 - wire_alpha) + 0.08 * wire_alpha, c.g * (1 - wire_alpha) + 0.09 * wire_alpha,
                             c.b * (1 - wire_alpha) + 0.12 * wire_alpha};
                    }
                }
            }
        }
    }
    Image img{width, height, std::vector<unsigned char>(static_cast<std::size_t>(width) * height * 3)};
    for (int y = 0; y < height; ++y)
        for (int x = 0; x < width; ++x) {
            Rgb acc{0, 0, 0};
            for (int dy = 0; dy < ss; ++dy)
                for (int dx = 0; dx < ss; ++dx) {
                    const Rgb& c = color[static_cast<std::size_t>(y * ss + dy) * W + (x * ss + dx)];
                    acc = {acc.r + c.r, acc.g + c.g, acc.b + c.b};
                }
            const double k = 1.0 / (ss * ss);
            auto px = [&](double c) { return static_cast<unsigned char>(std::clamp(std::pow(c * k, 1 / 1.15) * 255.0, 0.0, 255.0)); };
            const std::size_t o = (static_cast<std::size_t>(y) * width + x) * 3;
            img.rgb[o] = px(acc.r);
            img.rgb[o + 1] = px(acc.g);
            img.rgb[o + 2] = px(acc.b);
        }
    return img;
}

Image hconcat(const std::vector<Image>& imgs, int gap) {
    Image out;
    for (const Image& i : imgs) { out.w += i.w; out.h = std::max(out.h, i.h); }
    out.w += gap * static_cast<int>(imgs.size() - 1);
    out.rgb.assign(static_cast<std::size_t>(out.w) * out.h * 3, 255);
    int x0 = 0;
    for (const Image& i : imgs) {
        for (int y = 0; y < i.h; ++y)
            std::copy_n(i.rgb.begin() + static_cast<std::ptrdiff_t>(y) * i.w * 3, i.w * 3,
                        out.rgb.begin() + (static_cast<std::ptrdiff_t>(y) * out.w + x0) * 3);
        x0 += i.w + gap;
    }
    return out;
}

void save_png(const Image& img, const std::string& path) {
    init_crc();
    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) throw std::runtime_error("save_png: cannot open " + path);
    const unsigned char sig[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    std::fwrite(sig, 1, 8, f);
    std::vector<unsigned char> ihdr;
    be32(ihdr, static_cast<std::uint32_t>(img.w));
    be32(ihdr, static_cast<std::uint32_t>(img.h));
    ihdr.insert(ihdr.end(), {8, 2, 0, 0, 0});
    chunk(f, "IHDR", ihdr);
    // Raw scanlines (filter 0) in stored (uncompressed) deflate blocks.
    std::vector<unsigned char> raw;
    for (int y = 0; y < img.h; ++y) {
        raw.push_back(0);
        raw.insert(raw.end(), img.rgb.begin() + static_cast<std::ptrdiff_t>(y) * img.w * 3,
                   img.rgb.begin() + static_cast<std::ptrdiff_t>(y + 1) * img.w * 3);
    }
    std::vector<unsigned char> z{0x78, 0x01};
    for (std::size_t i = 0; i < raw.size(); i += 65535) {
        const std::size_t n = std::min<std::size_t>(65535, raw.size() - i);
        z.push_back(i + n == raw.size() ? 1 : 0);
        z.push_back(static_cast<unsigned char>(n & 0xff));
        z.push_back(static_cast<unsigned char>(n >> 8));
        z.push_back(static_cast<unsigned char>(~n & 0xff));
        z.push_back(static_cast<unsigned char>((~n >> 8) & 0xff));
        z.insert(z.end(), raw.begin() + static_cast<std::ptrdiff_t>(i), raw.begin() + static_cast<std::ptrdiff_t>(i + n));
    }
    std::uint32_t s1 = 1, s2 = 0;
    for (unsigned char c : raw) { s1 = (s1 + c) % 65521; s2 = (s2 + s1) % 65521; }
    be32(z, (s2 << 16) | s1);
    chunk(f, "IDAT", z);
    chunk(f, "IEND", {});
    std::fclose(f);
}

}  // namespace pgtools
