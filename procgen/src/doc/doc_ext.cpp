// Extension nodes: the procgen operations exposed in the document format.
// These are a superset of the reference node set (the Swift evaluator does not
// know them), so levels that need lathes, terrain, subdivision, organic
// displacement, deformers, scattering or chamfers can be authored as graphs.
//
//   revolve      profile [[r, z]...], sides, flip        lathe about +Z
//   heightfield  size [x, y], divisions [nx, ny], heights [...] | noise {...}, base, skirt
//   subdivide    levels                                   Catmull–Clark
//   displace     amplitude, frequency, seed, octaves      fBm along normals
//   deform       kind twist|bend|taper, amount            world-space deformer
//   scatter      count, minDistance, seed                 surface points (for instance)
//   chamfer      width, minAngle                          bevel sharp edges of a closed solid
//   bake                                                  merge the input into one prototype per material:
//                                                         a module becomes a few large meshes instead of
//                                                         hundreds of small instances (fewer engine batches)
//   curve        point [x, y, z] in @t, segments, closed  parametric curve (points + curve)
//   tube         radius, sides, up                        round tube along each input curve
//   loft         closed, thickness, flip                  skin through the input curves
//   patch        point [x, y, z] in @u @v, divisions, panels, inset, closedU, closedV,
//                thickness, flip                          parametric surface, optionally split
//                                                         into separate panels (glazing)
#include "doc_internal.hpp"
#include "doc_expr.hpp"
#include "../voronoi_cells.hpp"

#include "pg/closest.hpp"
#include "pg/ops/bevel.hpp"
#include "pg/ops/deform.hpp"
#include "pg/ops/scatter.hpp"
#include "pg/ops/subdivide.hpp"

#include <cmath>
#include <algorithm>
#include <map>
#include <numbers>
#include <set>

namespace pg::doc::detail {
namespace {

struct Chart {  // a UV per vertex from the builder, turned into corner UVs by assign_chart
    std::vector<std::array<double, 2>> uv;
    std::vector<char> free_u;
    double wrap_u = 0, wrap_v = 0;
};

Part part_of(Mesh mesh, const std::shared_ptr<const std::string>& source, const Part* like = nullptr, const Chart* chart = nullptr) {
    Proto proto;
    proto.mesh = std::move(mesh);
    if (chart) assign_chart(proto, chart->uv, chart->wrap_u, chart->wrap_v, chart->free_u.empty() ? nullptr : &chart->free_u);
    Part p;
    if (like) p = *like;
    p.proto = std::make_shared<const Proto>(std::move(proto));
    p.source = source;
    p.transform = Mat4::identity();
    return p;
}

// World-space copy of one placement (winding fixed under mirroring).
Mesh baked(const Part& part) {
    Geometry g;
    g.parts.push_back(part);
    return flatten(g);
}

Mesh revolve(const Options& o, Chart& chart) {
    std::vector<std::pair<double, double>> profile;
    for (const json::Value& e : o.list("profile", 2, 512)) {
        if (!e.is_array() || e.array.size() != 2) fail("Expected [radius, z] profile points");
        const double r = o.number(e.array[0]);
        if (r < 0) fail("Revolve radius must be >= 0");
        profile.emplace_back(r, o.number(e.array[1]));
    }
    const int sides = o.integer("sides", 32, 3, 512);
    const bool flip = o.integer("flip", 0, 0, 1) != 0;
    const double cap_inset = o.scalar("capInset", 0.15f, 0.02f, 0.45f);
    std::string cap_mode = "pole";
    if (const json::Value* cv = o.get("cap")) {
        if (!cv->is_string()) fail("revolve cap must be pole, inset or quad");
        cap_mode = cv->string;
        if (cap_mode != "pole" && cap_mode != "inset" && cap_mode != "quad")
            fail("revolve cap must be pole, inset or quad");
    }
    if (cap_mode == "quad" && (sides < 4 || (sides % 2) != 0))
        fail("revolve cap quad requires an even sides >= 4");
    const double eps = 1e-9;
    // A profile that ends where it starts is a closed loop: the last point is dropped and the
    // loop wraps, so a ring section off the axis (a pipe wall, a bored hub) turns into a closed solid.
    const bool loop = profile.size() >= 4 && std::abs(profile.front().first - profile.back().first) <= eps &&
                      std::abs(profile.front().second - profile.back().second) <= eps;
    if (loop) {
        profile.pop_back();
        for (const auto& [r, z] : profile)
            if (r <= eps) fail("A closed revolve profile must stay off the axis (radius > 0)");
    }
    // In quad/inset cap mode, check if the first or last point is a flat polar cap (r == 0)
    // so we replace the valence-N triangle fan with a clean quad-grid or inset-ring cap.
    const bool replace_start_pole = !loop && cap_mode != "pole" && profile.size() >= 2 &&
                                    profile.front().first <= eps && profile[1].first > eps;
    const bool replace_end_pole = !loop && cap_mode != "pole" && profile.size() >= 2 &&
                                  profile.back().first <= eps && profile[profile.size() - 2].first > eps;
    Mesh m;
    std::vector<Index> first(profile.size());
    std::vector<bool> pole(profile.size());
    for (std::size_t i = 0; i < profile.size(); ++i) {
        const auto [r, z] = profile[i];
        pole[i] = r <= eps;
        first[i] = static_cast<Index>(m.vertex_count());
        if (pole[i]) {
            if ((i == 0 && replace_start_pole) || (i + 1 == profile.size() && replace_end_pole)) continue;
            m.add_vertex({0, 0, z});
            continue;
        }
        for (int k = 0; k < sides; ++k) {
            const double a = 2.0 * std::numbers::pi * k / sides;
            m.add_vertex({r * std::cos(a), r * std::sin(a), z});
        }
    }
    // Chart: round the axis at the widest radius, along the meridian by its length.
    double widest = 0, meridian = 0;
    for (const auto& [r, z] : profile) widest = std::max(widest, r);
    chart.wrap_u = 2.0 * std::numbers::pi * widest;
    std::vector<double> ring_meridian(profile.size(), 0.0);
    for (std::size_t i = 0; i < profile.size(); ++i) {
        if (i) meridian += std::hypot(profile[i].first - profile[i - 1].first, profile[i].second - profile[i - 1].second);
        ring_meridian[i] = meridian;
        if (pole[i]) {
            if ((i == 0 && replace_start_pole) || (i + 1 == profile.size() && replace_end_pole)) continue;
            chart.uv.push_back({0, meridian});
            chart.free_u.push_back(1);
            continue;
        }
        for (int k = 0; k < sides; ++k) {
            chart.uv.push_back({chart.wrap_u * k / sides, meridian});
            chart.free_u.push_back(0);
        }
    }
    // Signed area of the profile closed along the axis decides the outward side:
    // a profile running up the outside (r > 0, z increasing) is positive.
    double area = 0;
    const std::size_t segments = loop ? profile.size() : profile.size() - 1;
    auto next = [&](std::size_t i) { return (i + 1) % profile.size(); };
    for (std::size_t i = 0; i < segments; ++i) area += (profile[i].first + profile[next(i)].first) * (profile[next(i)].second - profile[i].second);
    const bool reverse = (area < 0) != flip;
    auto face = [&](std::initializer_list<Index> v) {
        std::vector<Index> f(v);
        if (reverse) std::reverse(f.begin(), f.end());
        m.add_face(f);
    };
    const auto n = static_cast<Index>(sides);
    for (std::size_t i = 0; i < segments; ++i) {
        const std::size_t b = next(i);
        if (pole[i] && pole[b]) continue;
        if (i == 0 && replace_start_pole) continue;
        if (b + 1 == profile.size() && replace_end_pole) continue;
        for (Index k = 0; k < n; ++k) {
            const Index j = (k + 1) % n;
            if (pole[i]) face({first[i], first[b] + j, first[b] + k});
            else if (pole[b]) face({first[i] + k, first[i] + j, first[b]});
            else face({first[i] + k, first[i] + j, first[b] + j, first[b] + k});
        }
    }
    auto emit_revolve_cap = [&](Index rim_base, double pole_z, double cap_meridian, bool at_start) {
        const bool flip_w = at_start ? !reverse : reverse;
        if (cap_mode == "inset") {
            const Index in_base = static_cast<Index>(m.vertex_count());
            for (Index k = 0; k < n; ++k) {
                const Vec3 rp = m.positions[rim_base + k];
                m.add_vertex({rp.x * (1.0 - cap_inset), rp.y * (1.0 - cap_inset), rp.z * (1.0 - cap_inset) + pole_z * cap_inset});
                chart.uv.push_back({chart.wrap_u * k / sides, cap_meridian});
                chart.free_u.push_back(0);
            }
            std::vector<Index> core(n);
            for (Index k = 0; k < n; ++k) {
                const Index j = (k + 1) % n;
                if (flip_w) m.add_face({rim_base + j, rim_base + k, in_base + k, in_base + j});
                else m.add_face({rim_base + k, rim_base + j, in_base + j, in_base + k});
                core[k] = flip_w ? (in_base + ((n - k) % n)) : (in_base + k);
            }
            m.add_face(core);
        } else {
            const int half = sides / 2;
            const int b = half / 2;
            const int a = half - b;
            const int W = a + 1, H = b + 1;
            std::vector<Index> grid(static_cast<std::size_t>(W) * static_cast<std::size_t>(H), ~Index{0});
            auto at = [&](int gx, int gy) -> Index& {
                return grid[static_cast<std::size_t>(gy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(gx)];
            };
            int k = 0;
            for (int x = 0; x < a; ++x) at(x, 0) = rim_base + static_cast<Index>(k++);
            for (int y = 0; y < b; ++y) at(a, y) = rim_base + static_cast<Index>(k++);
            for (int x = a; x > 0; --x) at(x, b) = rim_base + static_cast<Index>(k++);
            for (int y = b; y > 0; --y) at(0, y) = rim_base + static_cast<Index>(k++);
            for (int gy = 1; gy < b; ++gy) {
                const double v = static_cast<double>(gy) / static_cast<double>(b);
                const Vec3 p_left = m.positions[at(0, gy)], p_right = m.positions[at(a, gy)];
                const Vec3 p_00 = m.positions[at(0, 0)], p_a0 = m.positions[at(a, 0)];
                const Vec3 p_0b = m.positions[at(0, b)], p_ab = m.positions[at(a, b)];
                for (int gx = 1; gx < a; ++gx) {
                    const double u = static_cast<double>(gx) / static_cast<double>(a);
                    const Vec3 p_bot = m.positions[at(gx, 0)], p_top = m.positions[at(gx, b)];
                    Vec3 q = (p_left * (1.0 - u) + p_right * u) + (p_bot * (1.0 - v) + p_top * v) -
                             ((p_00 * (1.0 - u) + p_a0 * u) * (1.0 - v) + (p_0b * (1.0 - u) + p_ab * u) * v);
                    const double r_frac = std::min(1.0, std::hypot(2.0 * u - 1.0, 2.0 * v - 1.0));
                    q.z = pole_z * (1.0 - r_frac) + q.z * r_frac;
                    at(gx, gy) = m.add_vertex(q);
                    chart.uv.push_back({0.0, cap_meridian});
                    chart.free_u.push_back(1);
                }
            }
            for (int gy = 0; gy < b; ++gy)
                for (int gx = 0; gx < a; ++gx) {
                    const Index i0 = at(gx, gy), i1 = at(gx + 1, gy), i2 = at(gx + 1, gy + 1), i3 = at(gx, gy + 1);
                    if (flip_w) m.add_face({i0, i3, i2, i1});
                    else m.add_face({i0, i1, i2, i3});
                }
        }
    };
    if (replace_start_pole) emit_revolve_cap(first[1], profile.front().second, ring_meridian[0], true);
    if (replace_end_pole) emit_revolve_cap(first[profile.size() - 2], profile.back().second, ring_meridian.back(), false);
    return m;
}

Mesh heightfield(const Options& o) {
    const json::Value* size_v = o.get("size");
    const json::Value* div_v = o.get("divisions");
    if (!size_v || !size_v->is_array() || size_v->array.size() != 2 || !div_v || !div_v->is_array() || div_v->array.size() != 2)
        fail("heightfield needs size [x, y] and divisions [nx, ny]");
    const double sx = o.number(size_v->array[0]), sy = o.number(size_v->array[1]);
    const float fx = o.number(div_v->array[0]), fy = o.number(div_v->array[1]);
    if (!(sx > 0 && sy > 0 && fx >= 1 && fy >= 1 && fx <= 1024 && fy <= 1024 && std::round(fx) == fx && std::round(fy) == fy))
        fail("Invalid heightfield size or divisions");
    const int nx = static_cast<int>(fx), ny = static_cast<int>(fy);
    const std::size_t count = static_cast<std::size_t>(nx + 1) * static_cast<std::size_t>(ny + 1);
    const double base = o.scalar("base", 0, -1e5f, 1e5f);
    const double skirt = o.scalar("skirt", 0, 0, 1e5f);
    std::vector<double> h(count, base);
    if (o.get("heights")) {
        const auto& list = o.list("heights", count, count);
        for (std::size_t i = 0; i < count; ++i) h[i] = base + o.number(list[i]);
    } else if (const json::Value* noise = o.get("noise")) {
        const Options n{noise, o.controls};
        const double amplitude = n.scalar("amplitude", 1, 0, 1e4f), frequency = n.scalar("frequency", 0.1f, 0, 1e4f);
        const int octaves = n.integer("octaves", 4, 1, 12), seed = n.integer("seed", 1, 0, 1'000'000);
        for (int j = 0; j <= ny; ++j)
            for (int i = 0; i <= nx; ++i) {
                const double x = (static_cast<double>(i) / nx - 0.5) * sx, y = (static_cast<double>(j) / ny - 0.5) * sy;
                double amp = amplitude, freq = frequency, sum = 0;
                for (int k = 0; k < octaves; ++k) {
                    sum += amp * gradient_noise({x * freq, y * freq, 0.37 * k}, static_cast<std::uint64_t>(seed + k));
                    amp *= 0.5;
                    freq *= 2.0;
                }
                h[static_cast<std::size_t>(j) * static_cast<std::size_t>(nx + 1) + static_cast<std::size_t>(i)] = base + sum;
            }
    }
    Mesh m;
    const auto row = static_cast<Index>(nx + 1);
    for (int j = 0; j <= ny; ++j)
        for (int i = 0; i <= nx; ++i)
            m.add_vertex({(static_cast<double>(i) / nx - 0.5) * sx, (static_cast<double>(j) / ny - 0.5) * sy,
                          h[static_cast<std::size_t>(j) * row + static_cast<std::size_t>(i)]});
    for (Index j = 0; j < static_cast<Index>(ny); ++j)
        for (Index i = 0; i < static_cast<Index>(nx); ++i) {
            const Index a = j * row + i;
            m.add_face({a, a + 1, a + 1 + row, a + row});
        }
    if (skirt > 0) {
        // Close the block: walls down to a flat bottom `skirt` below the lowest point.
        double low = h[0];
        for (double v : h) low = std::min(low, v);
        const double zb = low - skirt;
        const auto bottom = static_cast<Index>(m.vertex_count());
        for (int j = 0; j <= ny; ++j)
            for (int i = 0; i <= nx; ++i) m.add_vertex({(static_cast<double>(i) / nx - 0.5) * sx, (static_cast<double>(j) / ny - 0.5) * sy, zb});
        auto top = [&](int i, int j) { return static_cast<Index>(j) * row + static_cast<Index>(i); };
        for (int i = 0; i < nx; ++i) {
            m.add_face({top(i + 1, 0), top(i, 0), top(i, 0) + bottom, top(i + 1, 0) + bottom});
            m.add_face({top(i, ny), top(i + 1, ny), top(i + 1, ny) + bottom, top(i, ny) + bottom});
        }
        for (int j = 0; j < ny; ++j) {
            m.add_face({top(0, j), top(0, j + 1), top(0, j + 1) + bottom, top(0, j) + bottom});
            m.add_face({top(nx, j + 1), top(nx, j), top(nx, j) + bottom, top(nx, j + 1) + bottom});
        }
        for (Index j = 0; j < static_cast<Index>(ny); ++j)
            for (Index i = 0; i < static_cast<Index>(nx); ++i) {
                const Index a = bottom + j * row + i;
                m.add_face({a, a + row, a + 1 + row, a + 1});
            }
    }
    return m;
}

// Surface through a grid of cu x cv points (index j * cu + i), or with
// `thickness` a closed shell offset half the thickness to each side along the
// vertex normals, with walls along every open edge.
Mesh grid_shell(const std::vector<Vec3>& pts, int cu, int cv, bool closed_u, bool closed_v, double thickness, bool flip, Chart& chart) {
    const int nu = closed_u ? cu : cu - 1, nv = closed_v ? cv : cv - 1;
    if (nu < 1 || nv < 1) fail("A surface needs at least one cell in each direction");
    {   // Chart: arc length along each grid line (rows give u, columns give v).
        auto p = [&](int i, int j) { return pts[static_cast<std::size_t>(j) * static_cast<std::size_t>(cu) + static_cast<std::size_t>(i)]; };
        chart.uv.assign(pts.size(), {0, 0});
        double loop_u = 0, loop_v = 0;
        for (int j = 0; j < cv; ++j) {
            double s = 0;
            for (int i = 0; i < cu; ++i) {
                if (i) s += length(p(i, j) - p(i - 1, j));
                chart.uv[static_cast<std::size_t>(j) * static_cast<std::size_t>(cu) + static_cast<std::size_t>(i)][0] = s;
            }
            loop_u += s + length(p(0, j) - p(cu - 1, j));
        }
        for (int i = 0; i < cu; ++i) {
            double s = 0;
            for (int j = 0; j < cv; ++j) {
                if (j) s += length(p(i, j) - p(i, j - 1));
                chart.uv[static_cast<std::size_t>(j) * static_cast<std::size_t>(cu) + static_cast<std::size_t>(i)][1] = s;
            }
            loop_v += s + length(p(i, 0) - p(i, cv - 1));
        }
        chart.wrap_u = closed_u ? loop_u / cv : 0;
        chart.wrap_v = closed_v ? loop_v / cu : 0;
        if (thickness > 0) chart.uv.insert(chart.uv.end(), chart.uv.begin(), chart.uv.begin() + static_cast<std::ptrdiff_t>(pts.size()));  // both skins
    }
    auto at = [&](int i, int j) { return static_cast<Index>((j % cv) * cu + (i % cu)); };
    std::vector<std::array<Index, 4>> quads;
    quads.reserve(static_cast<std::size_t>(nu) * static_cast<std::size_t>(nv));
    for (int j = 0; j < nv; ++j)
        for (int i = 0; i < nu; ++i) {
            std::array<Index, 4> q{at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1)};
            if (flip) std::reverse(q.begin(), q.end());
            quads.push_back(q);
        }
    Mesh m;
    std::vector<Vec3> normal(pts.size(), Vec3{0, 0, 0});
    for (const auto& q : quads) {
        const Vec3 n = cross(pts[q[1]] - pts[q[0]], pts[q[3]] - pts[q[0]]) + cross(pts[q[3]] - pts[q[2]], pts[q[1]] - pts[q[2]]);
        if (!(dot(n, n) > 1e-24)) fail("Surface has a degenerate cell (coincident points)");
        for (Index v : q) normal[v] += n;
    }
    if (!(thickness > 0)) {
        m.positions = pts;
        for (const auto& q : quads) m.add_face({q[0], q[1], q[2], q[3]});
        return m;
    }
    const auto count = static_cast<Index>(pts.size());
    for (int side = 0; side < 2; ++side)
        for (std::size_t v = 0; v < pts.size(); ++v) {
            const double len = length(normal[v]);
            if (!(len > 1e-12)) fail("Surface has a point without a normal");
            m.add_vertex(pts[v] + normal[v] * ((side == 0 ? 0.5 : -0.5) * thickness / len));
        }
    std::set<std::pair<Index, Index>> directed;
    for (const auto& q : quads)
        for (int k = 0; k < 4; ++k) directed.insert({q[static_cast<std::size_t>(k)], q[static_cast<std::size_t>((k + 1) % 4)]});
    for (const auto& q : quads) {
        m.add_face({q[0], q[1], q[2], q[3]});
        // Reversed, but starting at the same corner: both skins then split a twisted cell along the
        // same diagonal and cannot cross each other.
        m.add_face({q[0] + count, q[3] + count, q[2] + count, q[1] + count});
        for (int k = 0; k < 4; ++k) {
            const Index a = q[static_cast<std::size_t>(k)], b = q[static_cast<std::size_t>((k + 1) % 4)];
            if (!directed.count({b, a})) m.add_face({b, a, a + count, b + count});
        }
    }
    return m;
}

int whole(const Options& o, const json::Value& v, int lo, int hi, const char* what) {
    const float n = o.number(v);
    if (!(n >= static_cast<float>(lo) && n <= static_cast<float>(hi)) || std::round(n) != n) fail(std::string(what) + " must be an integer in range");
    return static_cast<int>(n);
}
// Two-component option such as divisions [nu, nv]; `fallback` when absent.
std::array<const json::Value*, 2> pair_of(const Options& o, std::string_view key) {
    const json::Value* v = o.get(key);
    if (!v) return {nullptr, nullptr};
    if (!v->is_array() || v->array.size() != 2) fail(std::string(key) + " must be [u, v]");
    return {&v->array[0], &v->array[1]};
}

Curve parametric_curve(const Options& o) {
    const json::Value* point = o.get("point");
    if (!point || !point->is_array() || point->array.size() != 3) fail("curve needs point [x, y, z] in terms of @t");
    const int segments = o.integer("segments", 32, 1, 4096);
    Curve c;
    c.closed = o.integer("closed", 0, 0, 1) != 0;
    if (c.closed && segments < 3) fail("A closed curve needs at least three segments");
    const int count = c.closed ? segments : segments + 1;
    const CompiledVec3 compiled_pt(point, o.controls);
    c.points.reserve(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        Point pt;
        pt.index = i;
        pt.parametric = true;
        pt.u = static_cast<float>(i) / static_cast<float>(segments);
        c.points.push_back(compiled_pt.eval(&pt));
    }
    return c;
}

void parametric_patch(const Options& o, const std::shared_ptr<const std::string>& source, Geometry& out) {
    const json::Value* point = o.get("point");
    if (!point || !point->is_array() || point->array.size() != 3) fail("patch needs point [x, y, z] in terms of @u and @v");
    const auto div = pair_of(o, "divisions"), pan = pair_of(o, "panels"), ins = pair_of(o, "inset");
    const int du = div[0] ? whole(o, *div[0], 1, 1024, "divisions") : 8, dv = div[1] ? whole(o, *div[1], 1, 1024, "divisions") : 8;
    const int pu = pan[0] ? whole(o, *pan[0], 1, 512, "panels") : 1, pv = pan[1] ? whole(o, *pan[1], 1, 512, "panels") : 1;
    const double iu = ins[0] ? o.number(*ins[0]) : 0, iv = ins[1] ? o.number(*ins[1]) : 0;
    if (!(iu >= 0 && iu < 0.5 && iv >= 0 && iv < 0.5)) fail("inset is a fraction of one panel in [0, 0.5)");
    const bool closed_u = o.integer("closedU", 0, 0, 1) != 0, closed_v = o.integer("closedV", 0, 0, 1) != 0;
    if ((closed_u && (pu > 1 || iu > 0)) || (closed_v && (pv > 1 || iv > 0))) fail("A closed direction cannot be split into panels");
    if ((closed_u && du < 3) || (closed_v && dv < 3)) fail("A closed direction needs at least three divisions");
    const double thickness = o.scalar("thickness", 0, 0, 100);
    const bool flip = o.integer("flip", 0, 0, 1) != 0;
    if (static_cast<double>(du) * dv * pu * pv > 400000) fail("Geometry instance, point, or triangle budget exceeded");
    const int cu = closed_u ? du : du + 1, cv = closed_v ? dv : dv + 1;
    const CompiledVec3 compiled_pt(point, o.controls);
    std::vector<Vec3> pts(static_cast<std::size_t>(cu) * static_cast<std::size_t>(cv));
    for (int b = 0; b < pv; ++b)
        for (int a = 0; a < pu; ++a) {
            const double u0 = (a + iu) / pu, u1 = (a + 1 - iu) / pu, v0 = (b + iv) / pv, v1 = (b + 1 - iv) / pv;
            for (int j = 0; j < cv; ++j)
                for (int i = 0; i < cu; ++i) {
                    Point pt;
                    pt.index = b * pu + a;
                    pt.parametric = true;
                    pt.u = static_cast<float>(u0 + (u1 - u0) * i / du);
                    pt.v = static_cast<float>(v0 + (v1 - v0) * j / dv);
                    pts[static_cast<std::size_t>(j) * static_cast<std::size_t>(cu) + static_cast<std::size_t>(i)] =
                        compiled_pt.eval(&pt);
                }
            Chart chart;
            Mesh skin = grid_shell(pts, cu, cv, closed_u, closed_v, thickness, flip, chart);
            out.parts.push_back(part_of(std::move(skin), source, nullptr, &chart));
        }
}

}  // namespace

bool evaluate_extension(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs,
                        const std::shared_ptr<const std::string>& source, Geometry& out) {
    if (evaluate_detail(type, o, inputs, out)) return true;
    if (evaluate_mesh_curve(type, o, inputs, source, out)) return true;
    auto one = [&]() -> const Geometry& {
        if (inputs.size() != 1) fail("Expected one geometry input");
        return *inputs[0];
    };
    if (type == "revolve") {
        Chart chart;
        Mesh turned = revolve(o, chart);
        out.parts.push_back(part_of(std::move(turned), source, nullptr, &chart));
        return true;
    }
    if (type == "heightfield") {
        out.parts.push_back(part_of(heightfield(o), source));
        return true;
    }
    if (type == "subdivide" || type == "displace") {
        const Geometry& in = one();
        std::map<const Proto*, ProtoPtr> done;  // shared prototypes stay shared
        SubdivideOptions sopt;
        std::vector<std::uint32_t> lod_levels;
        std::vector<float> lod_distances;
        if (type == "subdivide") {
            sopt.levels = static_cast<std::uint32_t>(o.integer("levels", 1, 0, 6));
            sopt.linear_pre_subdiv = static_cast<std::uint32_t>(o.integer("linearPreSubdiv", 0, 0, 3));
            sopt.limit_snap = o.integer("limit", 0, 0, 1) != 0;
            sopt.compute_limit_normals = o.integer("limitNormals", 0, 0, 1) != 0;
            if (o.get("interpolate")) {
                const int iters = o.integer("interpolate", 0, 0, 16);
                sopt.interpolate_iters = (iters == 1) ? 5u : static_cast<std::uint32_t>(iters);
            }
            sopt.interpolate_weight = o.scalar("interpolateWeight", 1.15f, 0.3f, 1.8f);
            sopt.subdiv_normals = static_cast<std::uint8_t>(o.integer("subdivNormals", 0, 0, 2));
            sopt.tuned_ev = o.integer("tunedEV", 0, 0, 1) != 0;
            sopt.non_uniform = o.integer("nonUniform", 0, 0, 1) != 0;
            sopt.rational = o.integer("rational", 0, 0, 1) != 0;
            sopt.adaptive = o.integer("adaptive", 0, 0, 1) != 0;
            sopt.adaptive_angle_deg = o.scalar("adaptiveAngle", 1.5f, 0.05f, 20.0f);
            sopt.g2_fair = o.integer("g2Fair", 0, 0, 1) != 0;
            sopt.g2_weight = o.scalar("g2Weight", 0.65f, 0.0f, 1.0f);
            if (const json::Value* ev = o.get("edits")) {
                if (!ev->is_array()) fail("subdivide edits must be an array of {level, selection, ...} objects");
                const json::Value* edits_arr = ev;
                sopt.on_level = [edits_arr, &o](std::uint32_t level, Mesh& mesh) {
                    for (const json::Value& item : edits_arr->array) {
                        if (!item.is_object()) fail("subdivide edits entry must be an object");
                        const Options eo{&item, o.controls, o.point};
                        const int target_lvl = eo.integer("level", 1, 1, 6);
                        if (static_cast<std::uint32_t>(target_lvl) == level) {
                            apply_hierarchical_subdiv_edit(item, o, mesh);
                        }
                    }
                };
            }
            if (o.get("lodLevels")) {
                for (const json::Value& lv : o.list("lodLevels", 1, 4)) {
                    const float fv = o.number(lv);
                    if (!(fv >= 0 && fv <= 6 && std::round(fv) == fv)) fail("subdivide lodLevels entries must be integers in 0..6");
                    lod_levels.push_back(static_cast<std::uint32_t>(fv));
                }
                if (!o.get("levels")) sopt.levels = lod_levels.front();
            }
            if (o.get("lodDistances")) {
                for (const json::Value& dv : o.list("lodDistances", 1, 4)) {
                    const float fv = o.number(dv);
                    if (!(fv >= 0.0f && fv <= 1e5f)) fail("subdivide lodDistances entries must be >= 0");
                    lod_distances.push_back(fv);
                }
            }
            if (const json::Value* bv = o.get("boundary")) {
                if (!bv->is_string()) fail("subdivide boundary must be edgeAndCorner or edge");
                if (bv->string == "edgeAndCorner") sopt.boundary = BoundaryRule::EdgeAndCorner;
                else if (bv->string == "edge") sopt.boundary = BoundaryRule::EdgeOnly;
                else fail("subdivide boundary must be edgeAndCorner or edge");
            }
            if (const json::Value* cv = o.get("creaseMethod")) {
                if (!cv->is_string()) fail("subdivide creaseMethod must be uniform or chaikin");
                if (cv->string == "uniform") sopt.crease_method = CreaseMethod::Uniform;
                else if (cv->string == "chaikin") sopt.crease_method = CreaseMethod::Chaikin;
                else fail("subdivide creaseMethod must be uniform or chaikin");
            }
            if (const json::Value* tv = o.get("triSubdiv")) {
                if (!tv->is_string()) fail("subdivide triSubdiv must be catmark or smooth");
                if (tv->string == "catmark") sopt.tri_subdiv = TriSubdivRule::Catmark;
                else if (tv->string == "smooth") sopt.tri_subdiv = TriSubdivRule::Smooth;
                else fail("subdivide triSubdiv must be catmark or smooth");
            }
            if (const json::Value* uv = o.get("uvRule")) {
                if (!uv->is_string()) fail("subdivide uvRule must be cornersPlus1, boundaries, linear or smooth");
                if (uv->string == "cornersPlus1") sopt.uv_rule = UvSubdivRule::CornersPlus1;
                else if (uv->string == "boundaries") sopt.uv_rule = UvSubdivRule::Boundaries;
                else if (uv->string == "linear") sopt.uv_rule = UvSubdivRule::Linear;
                else if (uv->string == "smooth") sopt.uv_rule = UvSubdivRule::Smooth;
                else fail("subdivide uvRule must be cornersPlus1, boundaries, linear or smooth");
            }
        }
        NoiseParams np;
        if (type == "displace") {
            np.amplitude = o.scalar("amplitude", 0.05f, 0, 1e4f);
            np.frequency = o.scalar("frequency", 4, 0, 1e4f);
            np.seed = static_cast<std::uint64_t>(o.integer("seed", 1, 0, 1'000'000));
            np.octaves = static_cast<std::uint32_t>(o.integer("octaves", 3, 1, 12));
        }
        out = in;
        for (Part& p : out.parts) {
            if (p.proto->mesh.face_count() == 0) continue;
            ProtoPtr& r = done[p.proto.get()];
            if (!r) {
                Proto proto;
                if (type == "subdivide") {
                    SubdivideResult sr = catmull_clark_full(p.proto->mesh, p.proto->uv, sopt);
                    proto.mesh = std::move(sr.mesh);
                    proto.uv = std::move(sr.uv);
                    if (!sr.corner_normals.empty()) {
                        proto.normals.resize(sr.corner_normals.size());
                        for (std::size_t i = 0; i < sr.corner_normals.size(); ++i) {
                            const Vec3 n = sr.corner_normals[i];
                            proto.normals[i] = {static_cast<float>(n.x), static_cast<float>(n.y), static_cast<float>(n.z)};
                        }
                    }
                    if (sopt.levels > 0 || sopt.linear_pre_subdiv > 0) {
                        proto.sds_cage = p.proto->sds_cage ? p.proto->sds_cage : std::make_shared<const Mesh>(p.proto->mesh);
                    }
                    if (lod_levels.size() > 1) {
                        // Engine convention (mesh_format.hpp): lodDistance[i] is the max distance (metres) for LOD i;
                        // the last LOD has no upper limit (0.0f).
                        constexpr float kDefaultMaxDist[4] = {12.0f, 30.0f, 75.0f, 0.0f};
                        // Accept either engine-order [12, 30] / [12, 30, 0] or legacy leading-zero [0, 12, 30].
                        std::vector<float> max_dists = lod_distances;
                        if (max_dists.size() >= 2 && max_dists.front() == 0.0f && max_dists[1] > 0.0f) {
                            max_dists.erase(max_dists.begin());
                        }
                        proto.lod0_distance = !max_dists.empty() ? max_dists[0] : kDefaultMaxDist[0];
                        for (std::size_t li = 1; li < lod_levels.size(); ++li) {
                            SubdivideOptions lopt = sopt;
                            lopt.levels = lod_levels[li];
                            if (lopt.levels == 0 && lopt.linear_pre_subdiv == 0) {
                                lopt.limit_snap = false;
                                lopt.compute_limit_normals = false;
                            }
                            SubdivideResult lsr = catmull_clark_full(p.proto->mesh, p.proto->uv, lopt);
                            Proto::LodLevel ll;
                            ll.mesh = std::move(lsr.mesh);
                            ll.uv = std::move(lsr.uv);
                            if (!lsr.corner_normals.empty()) {
                                ll.normals.resize(lsr.corner_normals.size());
                                for (std::size_t i = 0; i < lsr.corner_normals.size(); ++i) {
                                    const Vec3 n = lsr.corner_normals[i];
                                    ll.normals[i] = {static_cast<float>(n.x), static_cast<float>(n.y), static_cast<float>(n.z)};
                                }
                            }
                            const bool is_last = (li + 1 == lod_levels.size());
                            ll.distance = is_last ? 0.0f : (li < max_dists.size() ? max_dists[li] : kDefaultMaxDist[li]);
                            proto.lods.push_back(std::move(ll));
                        }
                    }
                } else {
                    proto.mesh = p.proto->mesh;
                    proto.uv = p.proto->uv;  // same topology: the chart still fits
                    proto.details = p.proto->details;
                    displace_noise(proto.mesh, np);
                }
                r = std::make_shared<const Proto>(std::move(proto));
            }
            p.proto = r;
            if (type == "subdivide" && (sopt.compute_limit_normals || sopt.subdiv_normals > 0)) {
                p.smooth = true;
                if (p.smooth_angle_deg >= 179.99f) {
                    p.smooth_angle_deg = 55.0f;
                    p.normal_weight = 3;  // areaAngle FWN fallback for coarse LOD 0 cages
                }
            }
            p.source = source;
        }
        return true;
    }
    if (type == "deform") {
        const Geometry& in = one();
        const json::Value* kind = o.get("kind");
        const std::string k = kind && kind->is_string() ? kind->string : "twist";
        const double amount = o.scalar("amount", 0, -1e4f, 1e4f);
        if (k != "twist" && k != "bend" && k != "taper") fail("deform kind must be twist, bend or taper");
        for (const Part& p : in.parts) {
            if (p.proto->mesh.face_count() == 0) continue;
            Mesh m = baked(p);  // deformers are not affine: bake the placement first
            if (k == "twist") twist(m.positions, amount);
            else if (k == "bend") bend(m.positions, amount);
            else taper(m.positions, amount);
            out.parts.push_back(part_of(std::move(m), source, &p));
        }
        return true;
    }
    if (type == "scatter") {
        const Mesh surface = flatten(one());
        ScatterParams sp;
        sp.count = static_cast<std::uint32_t>(o.integer("count", 100, 1, 24000));
        sp.min_distance = o.scalar("minDistance", 0, 0, 1e5f);
        sp.seed = static_cast<std::uint64_t>(o.integer("seed", 1, 0, 1'000'000));
        out.points = scatter_points(surface, sp).positions;
        return true;
    }
    if (type == "bake") {
        const Geometry& in = one();
        // One merged prototype per (material, shading, UV rule), in first-appearance order.
        struct Key {
            std::uint16_t material;
            bool smooth;
            float smooth_angle_deg;
            std::uint8_t normal_weight;
            bool split_creases;
            std::int8_t uv_u, uv_v;
            float metres_per_tile;
            std::array<std::uint8_t, 4> tint;
            auto operator<=>(const Key&) const = default;
        };
        std::vector<Key> order;
        std::map<Key, Mesh> merged;
        std::map<Key, std::vector<std::array<float, 2>>> charts;
        std::map<Key, std::vector<std::array<float, 3>>> baked_normals;
        std::map<Key, std::vector<Detail>> details;
        std::map<Key, std::vector<std::shared_ptr<const std::string>>> face_sources;
        std::map<Key, Mesh> merged_cages;
        std::map<Key, std::vector<Proto::LodLevel>> merged_lods;
        bool any_chart = false;
        std::map<Key, bool> key_has_normals;
        std::map<Key, bool> key_has_cage;
        std::map<Key, std::size_t> key_max_lods;
        std::map<Key, float> key_lod0_dist;
        for (const Part& part : in.parts) {
            any_chart = any_chart || (!part.proto->uv.empty() && part.uv_u < 0);
            const Key k{part.material, part.smooth, part.smooth_angle_deg, part.normal_weight, part.split_creases,
                        part.uv_u, part.uv_v, part.metres_per_tile, part.tint};
            if (!part.proto->normals.empty() && part.proto->normals.size() == part.proto->mesh.corner_count()) {
                key_has_normals[k] = true;
            }
            if (part.proto->sds_cage) key_has_cage[k] = true;
            if (!part.proto->lods.empty()) {
                key_max_lods[k] = std::max(key_max_lods[k], part.proto->lods.size());
                if (part.proto->lod0_distance > 0.0f && !(key_lod0_dist[k] > 0.0f)) key_lod0_dist[k] = part.proto->lod0_distance;
            }
        }
        auto append_mesh_transformed = [&](const Mesh& src_m, const std::vector<std::array<float, 2>>& src_uv,
                                           const std::vector<std::array<float, 3>>& src_nrm, const Mat4& xform,
                                           bool want_uv, bool want_n, Mesh& dst_m,
                                           std::vector<std::array<float, 2>>* dst_uv,
                                           std::vector<std::array<float, 3>>* dst_nrm) {
            const auto vbase = static_cast<Index>(dst_m.vertex_count());
            for (const Vec3& p : src_m.positions) dst_m.positions.push_back(xform.point(p));
            for (const EdgeCrease& ec : src_m.edge_creases) {
                const auto lo = static_cast<Index>(ec.key >> 32) + vbase;
                const auto hi = static_cast<Index>(ec.key & 0xffffffffu) + vbase;
                dst_m.edge_creases.push_back({edge_key(lo, hi), ec.sharpness});
            }
            for (const VertexCrease& vc : src_m.vertex_creases) {
                dst_m.vertex_creases.push_back({vc.vertex + vbase, vc.sharpness});
            }
            const bool mir = xform.det3() < 0;
            const bool has_u = src_uv.size() == src_m.corner_count();
            const bool has_n = src_nrm.size() == src_m.corner_count();
            std::vector<Index> tmp;
            for (std::size_t f = 0; f < src_m.face_count(); ++f) {
                tmp.assign(src_m.face(f).begin(), src_m.face(f).end());
                for (Index& v : tmp) v += vbase;
                std::vector<std::array<float, 2>> cu;
                if (want_uv && dst_uv) {
                    cu.resize(tmp.size());
                    if (has_u) {
                        for (std::size_t i = 0; i < tmp.size(); ++i) cu[i] = src_uv[src_m.face_offsets[f] + i];
                    } else {
                        Vec3 n{};
                        for (std::size_t i = 1; i + 1 < tmp.size(); ++i)
                            n += cross(dst_m.positions[tmp[i]] - dst_m.positions[tmp[0]], dst_m.positions[tmp[i + 1]] - dst_m.positions[tmp[0]]);
                        const double ax = std::abs(n.x), ay = std::abs(n.y), az = std::abs(n.z);
                        for (std::size_t i = 0; i < tmp.size(); ++i) {
                            const Vec3 q = dst_m.positions[tmp[i]];
                            cu[i] = ax >= ay && ax >= az ? std::array<float, 2>{static_cast<float>(q.y), static_cast<float>(q.z)}
                                  : ay >= az ? std::array<float, 2>{static_cast<float>(q.x), static_cast<float>(q.z)}
                                             : std::array<float, 2>{static_cast<float>(q.x), static_cast<float>(q.y)};
                        }
                    }
                }
                std::vector<std::array<float, 3>> cn;
                if (want_n && dst_nrm) {
                    cn.resize(tmp.size());
                    if (has_n) {
                        for (std::size_t i = 0; i < tmp.size(); ++i) {
                            const auto& raw = src_nrm[src_m.face_offsets[f] + i];
                            Vec3 tn = normalize_or(xform.direction({raw[0], raw[1], raw[2]}), {0, 0, 1});
                            if (mir) tn = -tn;
                            cn[i] = {static_cast<float>(tn.x), static_cast<float>(tn.y), static_cast<float>(tn.z)};
                        }
                    } else {
                        Vec3 fn{};
                        for (std::size_t i = 1; i + 1 < tmp.size(); ++i)
                            fn += cross(dst_m.positions[tmp[i]] - dst_m.positions[tmp[0]], dst_m.positions[tmp[i + 1]] - dst_m.positions[tmp[0]]);
                        fn = normalize_or(fn, {0, 0, 1});
                        if (mir) fn = -fn;
                        for (std::size_t i = 0; i < tmp.size(); ++i)
                            cn[i] = {static_cast<float>(fn.x), static_cast<float>(fn.y), static_cast<float>(fn.z)};
                    }
                }
                if (mir) {
                    std::reverse(tmp.begin(), tmp.end());
                    if (want_uv && dst_uv) std::reverse(cu.begin(), cu.end());
                    if (want_n && dst_nrm) std::reverse(cn.begin(), cn.end());
                }
                dst_m.add_face(tmp);
                if (want_uv && dst_uv) dst_uv->insert(dst_uv->end(), cu.begin(), cu.end());
                if (want_n && dst_nrm) dst_nrm->insert(dst_nrm->end(), cn.begin(), cn.end());
            }
        };
        std::vector<Index> poly;
        for (const Part& part : in.parts) {
            const Mesh& m = part.proto->mesh;
            if (m.face_count() == 0) continue;  // curves and other loose geometry are construction aids
            const Key key{part.material, part.smooth, part.smooth_angle_deg, part.normal_weight, part.split_creases,
                          part.uv_u, part.uv_v, part.metres_per_tile, part.tint};
            auto [it, fresh] = merged.try_emplace(key);
            if (fresh) order.push_back(key);
            Mesh& out_mesh = it->second;
            const auto base = static_cast<Index>(out_mesh.vertex_count());
            for (const Vec3& p : m.positions) out_mesh.positions.push_back(part.transform.point(p));
            for (const EdgeCrease& ec : m.edge_creases) {
                const auto lo = static_cast<Index>(ec.key >> 32) + base;
                const auto hi = static_cast<Index>(ec.key & 0xffffffffu) + base;
                out_mesh.edge_creases.push_back({edge_key(lo, hi), ec.sharpness});
            }
            for (const VertexCrease& vc : m.vertex_creases) {
                out_mesh.vertex_creases.push_back({vc.vertex + base, vc.sharpness});
            }
            const bool mirrored = part.transform.det3() < 0;
            for (const Detail& d : part.proto->details) details[key].push_back(transformed_detail(d, part.transform));
            const bool charted = part.proto->uv.size() == m.corner_count();
            const bool has_inner_sources = part.proto->face_sources.size() == m.face_count();
            const bool want_nrm = key_has_normals[key];
            const bool has_nrm = part.proto->normals.size() == m.corner_count();
            std::vector<std::array<float, 2>>& chart = charts[key];
            std::vector<std::shared_ptr<const std::string>>& fsrc = face_sources[key];
            std::vector<std::array<float, 3>>& nrm_out = baked_normals[key];
            for (std::size_t f = 0; f < m.face_count(); ++f) {
                poly.assign(m.face(f).begin(), m.face(f).end());
                for (Index& v : poly) v += base;
                std::vector<std::array<float, 2>> corner(poly.size());
                if (any_chart && charted) {
                    for (std::size_t i = 0; i < poly.size(); ++i) corner[i] = part.proto->uv[m.face_offsets[f] + i];
                } else if (any_chart) {  // box projection in metres, by the face's dominant axis
                    Vec3 n{};
                    for (std::size_t i = 1; i + 1 < poly.size(); ++i)
                        n += cross(out_mesh.positions[poly[i]] - out_mesh.positions[poly[0]], out_mesh.positions[poly[i + 1]] - out_mesh.positions[poly[0]]);
                    const double ax = std::abs(n.x), ay = std::abs(n.y), az = std::abs(n.z);
                    for (std::size_t i = 0; i < poly.size(); ++i) {
                        const Vec3 q = out_mesh.positions[poly[i]];
                        corner[i] = ax >= ay && ax >= az ? std::array<float, 2>{static_cast<float>(q.y), static_cast<float>(q.z)}
                                    : ay >= az ? std::array<float, 2>{static_cast<float>(q.x), static_cast<float>(q.z)}
                                               : std::array<float, 2>{static_cast<float>(q.x), static_cast<float>(q.y)};
                    }
                }
                std::vector<std::array<float, 3>> cnrm;
                if (want_nrm) {
                    cnrm.resize(poly.size());
                    if (has_nrm) {
                        for (std::size_t i = 0; i < poly.size(); ++i) {
                            const auto& raw = part.proto->normals[m.face_offsets[f] + i];
                            Vec3 tn = normalize_or(part.transform.direction({raw[0], raw[1], raw[2]}), {0, 0, 1});
                            if (mirrored) tn = -tn;
                            cnrm[i] = {static_cast<float>(tn.x), static_cast<float>(tn.y), static_cast<float>(tn.z)};
                        }
                    } else {
                        Vec3 fn{};
                        for (std::size_t i = 1; i + 1 < poly.size(); ++i)
                            fn += cross(out_mesh.positions[poly[i]] - out_mesh.positions[poly[0]], out_mesh.positions[poly[i + 1]] - out_mesh.positions[poly[0]]);
                        fn = normalize_or(fn, {0, 0, 1});
                        if (mirrored) fn = -fn;
                        for (std::size_t i = 0; i < poly.size(); ++i)
                            cnrm[i] = {static_cast<float>(fn.x), static_cast<float>(fn.y), static_cast<float>(fn.z)};
                    }
                }
                if (mirrored) {
                    std::reverse(poly.begin(), poly.end());
                    std::reverse(corner.begin(), corner.end());
                    if (want_nrm) std::reverse(cnrm.begin(), cnrm.end());
                }
                out_mesh.add_face(poly);
                fsrc.push_back(has_inner_sources ? part.proto->face_sources[f] : part.source);
                if (any_chart) chart.insert(chart.end(), corner.begin(), corner.end());
                if (want_nrm) nrm_out.insert(nrm_out.end(), cnrm.begin(), cnrm.end());
            }
            if (key_has_cage[key]) {
                const Mesh& cm = part.proto->sds_cage ? *part.proto->sds_cage : m;
                append_mesh_transformed(cm, {}, {}, part.transform, false, false, merged_cages[key], nullptr, nullptr);
            }
            if (const std::size_t ml = key_max_lods[key]; ml > 0) {
                auto& dst_lods = merged_lods[key];
                if (dst_lods.size() < ml) dst_lods.resize(ml);
                for (std::size_t li = 0; li < ml; ++li) {
                    const bool has_ll = !part.proto->lods.empty();
                    const std::size_t pick = has_ll ? std::min(li, part.proto->lods.size() - 1) : 0;
                    const Mesh& lm = has_ll ? part.proto->lods[pick].mesh : m;
                    const auto& luv = has_ll ? part.proto->lods[pick].uv : part.proto->uv;
                    const auto& lnrm = has_ll ? part.proto->lods[pick].normals : part.proto->normals;
                    if (has_ll && !(dst_lods[li].distance > 0.0f)) dst_lods[li].distance = part.proto->lods[pick].distance;
                    const bool want_ln = has_ll && !lnrm.empty();
                    append_mesh_transformed(lm, luv, lnrm, part.transform, any_chart, want_ln && dst_lods[li].normals.size() == dst_lods[li].mesh.corner_count(),
                                            dst_lods[li].mesh, any_chart ? &dst_lods[li].uv : nullptr,
                                            want_ln ? &dst_lods[li].normals : nullptr);
                }
            }
        }
        for (const Key& key : order) {
            Mesh& mm = merged.at(key);
            mm.normalize_creases();
            Part part = part_of(std::move(mm), source);
            Proto extended = *part.proto;
            if (any_chart) extended.uv = std::move(charts.at(key));
            if (key_has_normals[key]) extended.normals = std::move(baked_normals.at(key));
            if (details.count(key)) extended.details = std::move(details.at(key));
            if (face_sources.count(key)) extended.face_sources = std::move(face_sources.at(key));
            if (key_has_cage[key]) {
                Mesh& cm = merged_cages[key];
                cm.normalize_creases();
                extended.sds_cage = std::make_shared<const Mesh>(std::move(cm));
            }
            if (key_max_lods[key] > 0) {
                for (auto& ll : merged_lods[key]) ll.mesh.normalize_creases();
                extended.lod0_distance = key_lod0_dist[key] > 0.0f ? key_lod0_dist[key] : 12.0f;
                extended.lods = std::move(merged_lods[key]);
            }
            part.proto = std::make_shared<const Proto>(std::move(extended));
            part.material = key.material;
            part.smooth = key.smooth;
            part.smooth_angle_deg = key.smooth_angle_deg;
            part.normal_weight = key.normal_weight;
            part.split_creases = key.split_creases;
            part.uv_u = key.uv_u;
            part.uv_v = key.uv_v;
            part.metres_per_tile = key.metres_per_tile;
            part.tint = key.tint;
            out.parts.push_back(std::move(part));
        }
        out.points = in.points;
        out.curves = in.curves;
        return true;
    }
    if (type == "curve") {
        Curve c = parametric_curve(o);
        Part edges;
        edges.proto = curve_proto(c);
        edges.source = source;
        out.parts.push_back(std::move(edges));
        out.points = c.points;
        out.curves.push_back(std::move(c));
        return true;
    }
    if (type == "tube") {
        const Geometry& in = one();
        if (in.curves.empty()) fail("Input must supply at least one curve");
        for (ProtoPtr& p : make_tube_sweep_options(in.curves, o)) {
            Part part;
            part.proto = std::move(p);
            part.source = source;
            out.parts.push_back(std::move(part));
        }
        return true;
    }
    if (type == "loft") {
        if (o.get("sections")) {
            if (!inputs.empty()) fail("loft with sections takes no curve inputs");
            Part pt;
            pt.proto = make_section_loft(o);
            pt.source = source;
            out.parts.push_back(std::move(pt));
            return true;
        }
        std::vector<const Curve*> curves;
        for (const auto& in : inputs)
            for (const Curve& c : in->curves) curves.push_back(&c);
        if (curves.size() < 2) fail("loft needs at least two curves or sections");
        const std::size_t width = curves[0]->points.size();
        const bool closed_u = curves[0]->closed;
        std::vector<Vec3> pts;
        for (const Curve* c : curves) {
            if (c->points.size() != width || c->closed != closed_u) fail("loft curves need the same point count and closure");
            pts.insert(pts.end(), c->points.begin(), c->points.end());
        }
        const bool closed_v = o.integer("closed", 0, 0, 1) != 0;
        if (closed_v && curves.size() < 3) fail("A closed loft needs at least three curves");
        Chart chart;
        Mesh skin = grid_shell(pts, static_cast<int>(width), static_cast<int>(curves.size()), closed_u, closed_v, o.scalar("thickness", 0, 0, 100),
                               o.integer("flip", 0, 0, 1) != 0, chart);
        out.parts.push_back(part_of(std::move(skin), source, nullptr, &chart));
        return true;
    }
    if (type == "patch") {
        parametric_patch(o, source, out);
        return true;
    }
    if (type == "raycast") {
        if (inputs.size() != 2) fail("raycast expects two inputs: surface, points");
        const Mesh target = flatten(*inputs[0]);
        if (target.face_count() == 0) fail("raycast first input has no surface faces");
        std::vector<Vec3> probes = inputs[1]->points;
        if (probes.empty()) {
            for (const Curve& c : inputs[1]->curves) probes.insert(probes.end(), c.points.begin(), c.points.end());
        }
        if (probes.empty()) fail("raycast second input supplies no points");

        const Vec3 raw_dir = o.vector("direction", {0.0, 0.0, -1.0});
        const double dir_len = length(raw_dir);
        if (!(dir_len > 1e-12)) fail("raycast direction must be non-zero");
        const Vec3 dir = raw_dir / dir_len;
        const double max_dist = o.scalar("maxDistance", 10000.0f, 0.0001f, 1000000.0f);
        const double offset = o.scalar("offset", 0.0f, -1000.0f, 1000.0f);
        const bool along_normal = o.integer("alongNormal", 0, 0, 1) != 0;
        const bool cull = o.integer("cullBackfaces", 0, 0, 1) != 0;
        const json::Value* mv = o.get("miss");
        const std::string miss = mv && mv->is_string() ? mv->string : (mv ? "?" : "drop");
        if (miss != "drop" && miss != "keep" && miss != "fail") fail("raycast miss must be drop, keep or fail");

        const ClosestIndex bvh(target);
        out.points.reserve(probes.size());
        for (const Vec3& p : probes) {
            RaycastHit hit{};
            if (bvh.raycast(p, dir, 0.0, max_dist, hit, cull)) {
                const Vec3 lift = along_normal ? hit.normal * offset : dir * (-offset);
                out.points.push_back(hit.point + lift);
            } else if (miss == "keep") {
                out.points.push_back(p);
            } else if (miss == "fail") {
                fail("raycast missed the target surface");
            }
        }
        return true;
    }
    if (type == "voronoi") {
        if (inputs.size() > 1) fail("voronoi takes zero inputs or one points input");
        VoronoiBoxOptions vo;
        vo.size = o.vector("size", {1.0, 1.0, 1.0});
        if (!(vo.size.x > 0.001 && vo.size.y > 0.001 && vo.size.z > 0.001)) fail("voronoi size must be > 0.001");
        vo.count = o.integer("count", 16, 1, 2048);
        vo.gap = o.scalar("gap", 0.0f, 0.0f, 100.0f);
        vo.shrink = o.scalar("shrink", 0.0f, 0.0f, 0.9f);
        vo.seed = o.integer("seed", 1, -1000000, 1000000);

        std::span<const Vec3> sites;
        if (!inputs.empty()) {
            if (inputs[0]->points.empty()) fail("voronoi input supplies no seed points");
            sites = inputs[0]->points;
        }
        VoronoiBoxResult vr = make_voronoi_box(sites, vo);
        if (vr.cells.empty()) fail("voronoi produced no cells (gap may exceed cell size)");
        out.parts.reserve(vr.cells.size());
        for (Mesh& cell_mesh : vr.cells) {
            out.parts.push_back(part_of(std::move(cell_mesh), source));
        }
        out.points = std::move(vr.centroids);
        return true;
    }
    return false;
}

}  // namespace pg::doc::detail
