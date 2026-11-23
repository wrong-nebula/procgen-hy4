// Selection predicates, face-level mesh operations (chamfer selection, faceExtrude,
// faceDivide, deleteFaces) and curve editing tools (curveFillet, curveTrim,
// curveOffset, sampleCurve).
#include "doc_internal.hpp"

#include "pg/closest.hpp"
#include "pg/ops/bevel.hpp"
#include "pg/ops/extrude.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/topology.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <numbers>
#include <optional>
#include <set>
#include <vector>

namespace pg::doc::detail {
namespace {

Part make_mesh_part(Mesh mesh, const std::shared_ptr<const std::string>& source, const Part* like = nullptr) {
    Proto proto;
    proto.mesh = std::move(mesh);
    Part p;
    if (like) p = *like;
    p.proto = std::make_shared<const Proto>(std::move(proto));
    p.source = source;
    p.transform = Mat4::identity();
    return p;
}

Mesh baked_part(const Part& part) {
    Geometry g;
    g.parts.push_back(part);
    return flatten(g);
}

struct SelectionSpec {
    bool active = false;
    bool has_normal = false;
    Vec3 normal{0, 0, 1};
    double min_cos = 0.0;

    bool has_box = false;
    Vec3 box_lo{}, box_hi{};
    bool contain = false;

    bool has_indices = false;
    std::set<int> indices;

    bool has_direction = false;
    Vec3 direction{0, 0, 1};
    double min_dir_cos = 0.0;

    bool edge_both = false;  // mode: "any" (default) or "both" for normal matching on edges

    bool has_material = false;
    std::uint16_t material_slot = 0;

    bool has_occluded = false;
    bool want_occluded = true;
    double occlude_dist = 0.25;

    bool has_plane = false;
    Vec3 plane_n{0, 0, 1};
    double plane_d = 0.0;
    double plane_tol = 0.01;

    bool has_radius = false;
    int radius_axis = 2;  // 0=x, 1=y, 2=z
    Vec3 radius_center{0, 0, 0};
    double radius_min = 0.0, radius_max = 1e30;

    bool has_zrange = false;
    double z_lo = -1e30, z_hi = 1e30;

    bool invert = false;

    [[nodiscard]] bool matches_part(const Part& p) const {
        return !has_material || p.material == material_slot;
    }

    [[nodiscard]] bool in_box(Vec3 q) const {
        return q.x >= box_lo.x - 1e-7 && q.x <= box_hi.x + 1e-7 &&
               q.y >= box_lo.y - 1e-7 && q.y <= box_hi.y + 1e-7 &&
               q.z >= box_lo.z - 1e-7 && q.z <= box_hi.z + 1e-7;
    }

    [[nodiscard]] bool matches_point_geo(Vec3 q) const {
        if (has_plane && std::abs(dot(q, plane_n) - plane_d) > plane_tol + 1e-7) return false;
        if (has_zrange && (q.z < z_lo - 1e-7 || q.z > z_hi + 1e-7)) return false;
        if (has_radius) {
            const Vec3 d = q - radius_center;
            const double r = radius_axis == 0 ? std::hypot(d.y, d.z)
                           : (radius_axis == 1 ? std::hypot(d.x, d.z) : std::hypot(d.x, d.y));
            if (r < radius_min - 1e-7 || r > radius_max + 1e-7) return false;
        }
        return true;
    }

    [[nodiscard]] std::vector<std::uint8_t> select_faces(const Mesh& m, const ClosestIndex* occluder = nullptr) const {
        const std::size_t nf = m.face_count();
        if (!active) return std::vector<std::uint8_t>(nf, 1);
        if (has_occluded && !occluder) fail("selection.occluded requires a second occluder geometry input");
        std::vector<std::uint8_t> mask(nf, 0);
        for (std::size_t f = 0; f < nf; ++f) {
            bool ok = true;
            if (has_indices && !indices.count(static_cast<int>(f))) ok = false;
            if (ok && has_normal) {
                const Vec3 fn = face_normal(m, f);
                if (dot(fn, normal) < min_cos) ok = false;
            }
            if (ok && has_box) {
                if (contain) {
                    for (Index v : m.face(f))
                        if (!in_box(m.positions[v])) { ok = false; break; }
                } else {
                    if (!in_box(face_centroid(m, f))) ok = false;
                }
            }
            if (ok && (has_plane || has_radius || has_zrange)) {
                if (contain) {
                    for (Index v : m.face(f))
                        if (!matches_point_geo(m.positions[v])) { ok = false; break; }
                } else {
                    if (!matches_point_geo(face_centroid(m, f))) ok = false;
                }
            }
            if (ok && has_occluded && occluder) {
                const Vec3 c = face_centroid(m, f);
                const Vec3 fn = face_normal(m, f);
                if (length(fn) <= 1e-12) {
                    ok = false;
                } else {
                    const bool hit = occluder->any_hit(c + fn * 1e-4, fn, 0.0, occlude_dist, false);
                    if (hit != want_occluded) ok = false;
                }
            }
            if (invert) ok = !ok;
            mask[f] = ok ? 1 : 0;
        }
        return mask;
    }

    [[nodiscard]] bool matches_edge(Index e, Vec3 p0, Vec3 p1, Vec3 fn0, Vec3 fn1) const {
        if (!active) return true;
        bool ok = true;
        if (has_indices && !indices.count(static_cast<int>(e))) ok = false;
        if (ok && has_normal) {
            const bool m0 = dot(fn0, normal) >= min_cos;
            const bool m1 = dot(fn1, normal) >= min_cos;
            ok = edge_both ? (m0 && m1) : (m0 || m1);
        }
        if (ok && has_direction) {
            const Vec3 ed = normalize_or(p1 - p0, Vec3{});
            if (std::abs(dot(ed, direction)) < min_dir_cos) ok = false;
        }
        if (ok && has_box) {
            if (contain) ok = in_box(p0) && in_box(p1);
            else ok = in_box((p0 + p1) * 0.5);
        }
        if (ok && (has_plane || has_radius || has_zrange)) {
            if (contain) ok = matches_point_geo(p0) && matches_point_geo(p1);
            else ok = matches_point_geo((p0 + p1) * 0.5);
        }
        return invert ? !ok : ok;
    }
};

SelectionSpec parse_selection(const Options& parent) {
    SelectionSpec s;
    const json::Value* sv = parent.get("selection");
    if (!sv) return s;
    if (!sv->is_object()) fail("selection must be an object");
    s.active = true;
    const Options o{sv, parent.controls, parent.point};

    if (o.get("normal")) {
        const Vec3 raw = o.vector("normal", {0, 0, 1});
        const double len = length(raw);
        if (!(len > 1e-12)) fail("selection.normal must be non-zero");
        s.normal = raw / len;
        const double deg = o.scalar("angle", 30.0f, 0.0f, 180.0f);
        s.min_cos = std::cos(deg * std::numbers::pi / 180.0) - 1e-9;
        s.has_normal = true;
    }
    if (const json::Value* bv = o.get("box")) {
        if (!bv->is_array() || bv->array.size() != 2) fail("selection.box must be [[x0,y0,z0], [x1,y1,z1]]");
        const Vec3 a = o.vector(bv->array[0]), b = o.vector(bv->array[1]);
        s.box_lo = {std::min(a.x, b.x), std::min(a.y, b.y), std::min(a.z, b.z)};
        s.box_hi = {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)};
        s.contain = o.integer("contain", 0, 0, 1) != 0;
        s.has_box = true;
    }
    if (o.get("indices")) {
        for (const json::Value& iv : o.list("indices", 0, 65536)) {
            const float fv = o.number(iv);
            if (!(fv >= 0 && fv <= 1e7f && std::round(fv) == fv)) fail("selection.indices entries must be non-negative integers");
            s.indices.insert(static_cast<int>(fv));
        }
        s.has_indices = true;
    }
    if (o.get("direction")) {
        const Vec3 raw = o.vector("direction", {0, 0, 1});
        const double len = length(raw);
        if (!(len > 1e-12)) fail("selection.direction must be non-zero");
        s.direction = raw / len;
        const double deg = o.scalar("dirAngle", 15.0f, 0.0f, 90.0f);
        s.min_dir_cos = std::cos(deg * std::numbers::pi / 180.0) - 1e-9;
        s.has_direction = true;
    }
    if (const json::Value* mv = o.get("mode")) {
        if (!mv->is_string() || (mv->string != "any" && mv->string != "both"))
            fail("selection.mode must be any or both");
        s.edge_both = mv->string == "both";
    }
    if (const json::Value* mat = o.get("material")) {
        if (!mat->is_string()) fail("selection.material must be a material slot name");
        s.material_slot = slot_id(mat->string);
        s.has_material = true;
    }
    if (o.get("occluded")) {
        s.want_occluded = o.integer("occluded", 1, 0, 1) != 0;
        s.occlude_dist = o.scalar("occludeDistance", 0.25f, 0.0001f, 10000.0f);
        s.has_occluded = true;
    }
    if (const json::Value* pv = o.get("plane")) {
        if (!pv->is_object()) fail("selection.plane must be an object {normal, dist|origin, tol}");
        const Options po{pv, parent.controls, parent.point};
        const Vec3 raw = po.vector("normal", {0, 0, 1});
        const double len = length(raw);
        if (!(len > 1e-12)) fail("selection.plane.normal must be non-zero");
        s.plane_n = raw / len;
        if (po.get("origin")) s.plane_d = dot(po.vector("origin", {0, 0, 0}), s.plane_n);
        else s.plane_d = po.scalar("dist", 0.0f, -1e6f, 1e6f);
        s.plane_tol = po.scalar("tol", 0.01f, 0.0f, 1e6f);
        s.has_plane = true;
    }
    if (const json::Value* rv = o.get("radius")) {
        if (!rv->is_object()) fail("selection.radius must be an object {axis, center, min, max}");
        const Options ro{rv, parent.controls, parent.point};
        if (const json::Value* ax = ro.get("axis")) {
            if (!ax->is_string() || (ax->string != "x" && ax->string != "y" && ax->string != "z"))
                fail("selection.radius.axis must be x, y or z");
            s.radius_axis = ax->string == "x" ? 0 : (ax->string == "y" ? 1 : 2);
        }
        s.radius_center = ro.vector("center", {0, 0, 0});
        s.radius_min = ro.scalar("min", 0.0f, 0.0f, 1e6f);
        s.radius_max = ro.scalar("max", 1e6f, 0.0f, 1e6f);
        s.has_radius = true;
    }
    if (const json::Value* zv = o.get("zRange")) {
        if (!zv->is_array() || zv->array.size() != 2) fail("selection.zRange must be [z0, z1]");
        const double a = o.number(zv->array[0]), b = o.number(zv->array[1]);
        s.z_lo = std::min(a, b);
        s.z_hi = std::max(a, b);
        s.has_zrange = true;
    }
    s.invert = o.integer("invert", 0, 0, 1) != 0;
    return s;
}

std::optional<ClosestIndex> build_occluder(const std::vector<std::shared_ptr<const Geometry>>& inputs) {
    if (inputs.size() < 2) return std::nullopt;
    const Mesh occ = flatten(*inputs[1]);
    if (occ.face_count() == 0) fail("occluder second input has no surface faces");
    return std::optional<ClosestIndex>(std::in_place, occ);
}

// Inset + extrude individual faces (keeps mesh closed when cap_out == nullptr).
Mesh inset_extrude_individual(const Mesh& m, std::span<const std::uint8_t> mask, double inset_dist, double scale_f,
                              double offset_dist, Mesh* cap_out) {
    Mesh out;
    out.positions = m.positions;
    if (cap_out) cap_out->positions.clear();

    std::vector<Index> cap;
    std::vector<Vec3> edge_inward;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        const std::size_t k = fv.size();
        if (!mask[f] || k < 3) {
            out.add_face(fv);
            continue;
        }
        const Vec3 fn = face_normal(m, f);
        if (length(fn) <= 1e-12) {
            out.add_face(fv);
            continue;
        }
        const Vec3 centroid = face_centroid(m, f);

        edge_inward.resize(k);
        double min_alt = 1e30;
        for (std::size_t i = 0; i < k; ++i) {
            const Vec3 p0 = m.positions[fv[i]], p1 = m.positions[fv[(i + 1) % k]];
            const Vec3 mi = normalize_or(cross(fn, p1 - p0), Vec3{});
            edge_inward[i] = mi;
            if (length(mi) > 0.5) min_alt = std::min(min_alt, std::max(0.0, dot(centroid - p0, mi)));
        }
        const double eff_inset = inset_dist > 0.0 ? std::min(inset_dist, 0.45 * min_alt) : 0.0;
        const bool moves = eff_inset > 1e-12 || std::abs(scale_f - 1.0) > 1e-9 || std::abs(offset_dist) > 1e-12;
        if (!moves && !cap_out) {
            out.add_face(fv);
            continue;
        }

        std::vector<Vec3> inner(k);
        for (std::size_t i = 0; i < k; ++i) {
            Vec3 q = m.positions[fv[i]];
            if (eff_inset > 1e-12) {
                const Vec3 m_prev = edge_inward[(i + k - 1) % k], m_curr = edge_inward[i];
                const double det = dot(m_prev, cross(m_curr, fn));
                Vec3 delta = std::abs(det) > 1e-6 ? (cross(m_curr, fn) * eff_inset + cross(fn, m_prev) * eff_inset) / det
                                                  : m_curr * eff_inset;
                const double dl = length(delta);
                if (dl > 4.0 * eff_inset) delta = delta * (4.0 * eff_inset / dl);
                q += delta;
            }
            inner[i] = q;
        }
        if (std::abs(scale_f - 1.0) > 1e-9) {
            Vec3 ic{};
            for (const Vec3& q : inner) ic += q;
            ic = ic / static_cast<double>(k);
            for (Vec3& q : inner) q = ic + (q - ic) * scale_f;
        }
        for (Vec3& q : inner) q += fn * offset_dist;

        if (moves) {
            cap.resize(k);
            for (std::size_t i = 0; i < k; ++i) cap[i] = out.add_vertex(inner[i]);
            for (std::size_t i = 0; i < k; ++i) {
                const std::size_t j = (i + 1) % k;
                out.add_face({fv[i], fv[j], cap[j], cap[i]});
            }
            if (cap_out) {
                std::vector<Index> cverts(k);
                for (std::size_t i = 0; i < k; ++i) cverts[i] = cap_out->add_vertex(inner[i]);
                cap_out->add_face(cverts);
            } else {
                out.add_face(cap);
            }
        } else if (cap_out) {
            std::vector<Index> cverts(k);
            for (std::size_t i = 0; i < k; ++i) cverts[i] = cap_out->add_vertex(inner[i]);
            cap_out->add_face(cverts);
        }
    }
    return out;
}

// Ear-clip a planar polygon that may contain 180-degree collinear vertices along split edges,
// guaranteeing every emitted triangle has strictly positive area and preserves all boundary sub-edges.
void emit_polygon_clean(Mesh& out, const std::vector<Index>& poly, Vec3 ref_normal) {
    if (poly.size() < 3) return;
    if (poly.size() == 3) {
        out.add_face(poly);
        return;
    }
    // Check whether fan from poly[0] has any near-collinear triangle; if not, keep single N-gon!
    const Vec3 p0 = out.positions[poly[0]];
    double min_fan_sin = 1.0;
    for (std::size_t i = 1; i + 1 < poly.size(); ++i) {
        const Vec3 e1 = out.positions[poly[i]] - p0, e2 = out.positions[poly[i + 1]] - p0;
        const double l1 = length(e1), l2 = length(e2);
        if (l1 <= 1e-12 || l2 <= 1e-12) { min_fan_sin = 0.0; break; }
        const double s = dot(cross(e1, e2), ref_normal) / (l1 * l2);
        min_fan_sin = std::min(min_fan_sin, s);
    }
    if (min_fan_sin > 1e-4) {
        out.add_face(poly);
        return;
    }
    // Try rotating poly so a non-collinear corner is at index 0 first (keeps clean quads/pentagons intact when possible).
    for (std::size_t rot = 1; rot < poly.size(); ++rot) {
        const Vec3 rp0 = out.positions[poly[rot]];
        double rmin = 1.0;
        for (std::size_t i = 1; i + 1 < poly.size(); ++i) {
            const Vec3 e1 = out.positions[poly[(rot + i) % poly.size()]] - rp0;
            const Vec3 e2 = out.positions[poly[(rot + i + 1) % poly.size()]] - rp0;
            const double l1 = length(e1), l2 = length(e2);
            if (l1 <= 1e-12 || l2 <= 1e-12) { rmin = 0.0; break; }
            rmin = std::min(rmin, dot(cross(e1, e2), ref_normal) / (l1 * l2));
        }
        if (rmin > 1e-4) {
            std::vector<Index> rotated(poly.size());
            for (std::size_t i = 0; i < poly.size(); ++i) rotated[i] = poly[(rot + i) % poly.size()];
            out.add_face(rotated);
            return;
        }
    }

    // General ear clipping in tangent plane of ref_normal (skips 180-deg straight vertices).
    const Vec3 ref = std::abs(ref_normal.z) < 0.9 ? Vec3{0, 0, 1} : Vec3{1, 0, 0};
    const Vec3 tu = normalize_or(cross(ref, ref_normal), {1, 0, 0});
    const Vec3 tv = cross(ref_normal, tu);
    auto proj = [&](Index v) -> std::pair<double, double> {
        const Vec3 d = out.positions[v] - p0;
        return {dot(d, tu), dot(d, tv)};
    };
    auto turn2 = [&](Index a, Index b, Index c) {
        const auto [ax, ay] = proj(a);
        const auto [bx, by] = proj(b);
        const auto [cx, cy] = proj(c);
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    };

    std::vector<Index> rem = poly;
    while (rem.size() > 3) {
        const std::size_t m = rem.size();
        int best = -1;
        double best_area = 0.0;
        for (std::size_t i = 0; i < m; ++i) {
            const Index a = rem[(i + m - 1) % m], b = rem[i], c = rem[(i + 1) % m];
            const double area2 = turn2(a, b, c);
            if (!(area2 > 1e-12)) continue;
            bool inside = false;
            for (std::size_t j = 0; j < m; ++j) {
                const Index v = rem[j];
                if (v == a || v == b || v == c) continue;
                if (turn2(a, b, v) >= -1e-12 && turn2(b, c, v) >= -1e-12 && turn2(c, a, v) >= -1e-12) {
                    inside = true;
                    break;
                }
            }
            if (!inside && area2 > best_area) {
                best_area = area2;
                best = static_cast<int>(i);
            }
        }
        if (best < 0) break;
        const auto bi = static_cast<std::size_t>(best);
        out.add_face({rem[(bi + m - 1) % m], rem[bi], rem[(bi + 1) % m]});
        rem.erase(rem.begin() + best);
    }
    if (rem.size() >= 3) out.add_face(rem);
}

// CGA-style planar face subdivision (`faceDivide`) with watertight neighbor-edge stitching.
Mesh divide_faces(const Mesh& m, std::span<const std::uint8_t> mask, const std::string& axis_mode, Vec3 custom_axis,
                  const std::vector<double>& sizes_spec, double repeat_pitch) {
    Mesh out;
    out.positions = m.positions;

    struct EdgeSplit {
        double t;  // parameter along canonical (lo -> hi) in (0, 1)
        Index vid;
    };
    std::map<std::uint64_t, std::vector<EdgeSplit>> splits_by_edge;
    // Map every vertex lying on an original edge back to its parameter t on that canonical edge.
    struct EdgeParam {
        std::uint64_t key;
        Index lo, hi;
    };
    std::map<std::pair<Index, Index>, EdgeParam> subedge_origin;
    std::map<std::pair<std::uint64_t, Index>, double> vertex_t;

    auto register_orig_edge = [&](Index a, Index b) {
        const Index lo = std::min(a, b), hi = std::max(a, b);
        const std::uint64_t k = edge_key(lo, hi);
        vertex_t[{k, lo}] = 0.0;
        vertex_t[{k, hi}] = 1.0;
        subedge_origin[{a, b}] = {k, lo, hi};
        subedge_origin[{b, a}] = {k, lo, hi};
    };

    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 0; i < fv.size(); ++i) register_orig_edge(fv[i], fv[(i + 1) % fv.size()]);
    }

    auto get_split_vertex = [&](Index lo, Index hi, double t) -> Index {
        t = std::clamp(t, 1e-6, 1.0 - 1e-6);
        const std::uint64_t k = edge_key(lo, hi);
        auto& vec = splits_by_edge[k];
        for (const EdgeSplit& es : vec) {
            if (std::abs(es.t - t) <= 1e-6) return es.vid;
        }
        const Vec3 p = out.positions[lo] + (out.positions[hi] - out.positions[lo]) * t;
        const Index vid = out.add_vertex(p);
        vec.push_back({t, vid});
        vertex_t[{k, vid}] = t;
        subedge_origin[{lo, vid}] = {k, lo, hi};
        subedge_origin[{vid, lo}] = {k, lo, hi};
        subedge_origin[{vid, hi}] = {k, lo, hi};
        subedge_origin[{hi, vid}] = {k, lo, hi};
        return vid;
    };

    struct PendingFace {
        std::vector<Index> verts;
        Vec3 normal;
    };
    std::vector<PendingFace> pending;
    pending.reserve(m.face_count() * 2);

    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        const Vec3 fn = face_normal(m, f);
        if (!mask[f] || fv.size() < 3 || length(fn) <= 1e-12) {
            pending.push_back({std::vector<Index>(fv.begin(), fv.end()), fn});
            continue;
        }

        Vec3 ax = custom_axis;
        if (axis_mode == "vertical") {
            // Project +Z into face plane; fall back to +Y for horizontal faces.
            Vec3 tang = Vec3{0, 0, 1} - fn * dot(Vec3{0, 0, 1}, fn);
            if (length(tang) <= 1e-6) tang = Vec3{0, 1, 0} - fn * dot(Vec3{0, 1, 0}, fn);
            ax = normalize_or(tang, {0, 0, 1});
        } else if (axis_mode == "horizontal") {
            Vec3 tang = cross(Vec3{0, 0, 1}, fn);
            if (length(tang) <= 1e-6) tang = Vec3{1, 0, 0} - fn * dot(Vec3{1, 0, 0}, fn);
            ax = normalize_or(tang, {1, 0, 0});
        } else {
            Vec3 tang = custom_axis - fn * dot(custom_axis, fn);
            ax = normalize_or(length(tang) > 1e-6 ? tang : custom_axis, {1, 0, 0});
        }

        double s_min = 1e300, s_max = -1e300;
        for (Index v : fv) {
            const double s = dot(out.positions[v], ax);
            s_min = std::min(s_min, s);
            s_max = std::max(s_max, s);
        }
        const double span = s_max - s_min;
        if (!(span > 1e-6)) {
            pending.push_back({std::vector<Index>(fv.begin(), fv.end()), fn});
            continue;
        }

        // Compute cut coordinates in (s_min, s_max).
        std::vector<double> cuts;
        if (!sizes_spec.empty()) {
            double fixed_sum = 0.0, flex_sum = 0.0;
            for (double sz : sizes_spec) {
                if (sz > 0.0) fixed_sum += sz;
                else if (sz < 0.0) flex_sum += -sz;
            }
            const double fixed_scale = fixed_sum > span ? (span / fixed_sum) : 1.0;
            const double rem_span = std::max(0.0, span - fixed_sum * fixed_scale);
            double acc = s_min;
            for (std::size_t i = 0; i + 1 < sizes_spec.size(); ++i) {
                const double sz = sizes_spec[i];
                const double seg = sz > 0.0 ? sz * fixed_scale : (flex_sum > 0.0 ? rem_span * (-sz / flex_sum) : 0.0);
                acc += seg;
                if (acc > s_min + 1e-5 && acc < s_max - 1e-5) cuts.push_back(acc);
            }
        } else if (repeat_pitch > 0.0) {
            const int n_div = std::max(1, static_cast<int>(std::lround(span / repeat_pitch)));
            for (int i = 1; i < n_div; ++i) cuts.push_back(s_min + span * (static_cast<double>(i) / n_div));
        }

        if (cuts.empty()) {
            pending.push_back({std::vector<Index>(fv.begin(), fv.end()), fn});
            continue;
        }

        // Pre-insert all cut intersections onto this face's original boundary edges so neighboring faces share exact vertices.
        std::vector<Index> refined;
        refined.reserve(fv.size() + cuts.size() * 2);
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const Index a = fv[i], b = fv[(i + 1) % fv.size()];
            refined.push_back(a);
            const double sa = dot(out.positions[a], ax), sb = dot(out.positions[b], ax);
            if (std::abs(sb - sa) <= 1e-9) continue;
            const Index lo = std::min(a, b), hi = std::max(a, b);
            const double slo = a == lo ? sa : sb, shi = a == lo ? sb : sa;
            struct Hit { double u_ab; Index vid; };
            std::vector<Hit> hits;
            for (double c : cuts) {
                if ((c > std::min(sa, sb) + 1e-6) && (c < std::max(sa, sb) - 1e-6)) {
                    const double t_canon = (c - slo) / (shi - slo);
                    const double u_ab = (c - sa) / (sb - sa);
                    hits.push_back({u_ab, get_split_vertex(lo, hi, t_canon)});
                }
            }
            std::sort(hits.begin(), hits.end(), [](const Hit& x, const Hit& y) { return x.u_ab < y.u_ab; });
            for (const Hit& h : hits) {
                if (refined.back() != h.vid && h.vid != b) refined.push_back(h.vid);
            }
        }

        // Clip `refined` sequentially across slabs [b0, b1] using Sutherland-Hodgman.
        // Cache interior cut vertices along each cut plane so the two slabs sharing cut c_r share the exact interior edge!
        std::map<std::pair<std::size_t, std::uint64_t>, Index> interior_cache;
        auto clip_halfspace = [&](const std::vector<Index>& poly, double c_val, double sign, std::size_t cut_id) {
            std::vector<Index> res;
            if (poly.size() < 3) return res;
            auto eval_d = [&](Index v) { return (dot(out.positions[v], ax) - c_val) * sign; };
            for (std::size_t i = 0; i < poly.size(); ++i) {
                const Index u = poly[i], v = poly[(i + 1) % poly.size()];
                const double du = eval_d(u), dv = eval_d(v);
                const bool in_u = du >= -1e-7;
                if (in_u) res.push_back(u);
                if ((du > 1e-7 && dv < -1e-7) || (du < -1e-7 && dv > 1e-7)) {
                    const std::uint64_t ek = edge_key(std::min(u, v), std::max(u, v));
                    auto it = interior_cache.find({cut_id, ek});
                    Index mid = 0;
                    if (it != interior_cache.end()) {
                        mid = it->second;
                    } else {
                        // Check if (u, v) lies on an original mesh edge first.
                        auto oit = subedge_origin.find({u, v});
                        if (oit != subedge_origin.end()) {
                            const EdgeParam ep = oit->second;
                            const double slo = dot(out.positions[ep.lo], ax), shi = dot(out.positions[ep.hi], ax);
                            mid = get_split_vertex(ep.lo, ep.hi, (c_val - slo) / (shi - slo));
                        } else {
                            const double alpha = du / (du - dv);
                            mid = out.add_vertex(out.positions[u] + (out.positions[v] - out.positions[u]) * alpha);
                        }
                        interior_cache[{cut_id, ek}] = mid;
                    }
                    if (res.empty() || res.back() != mid) res.push_back(mid);
                }
            }
            // Deduplicate consecutive identical vertices around wrap.
            std::vector<Index> clean;
            for (Index v : res)
                if (clean.empty() || clean.back() != v) clean.push_back(v);
            if (clean.size() >= 2 && clean.front() == clean.back()) clean.pop_back();
            return clean;
        };

        std::vector<Index> remainder = refined;
        for (std::size_t ci = 0; ci <= cuts.size(); ++ci) {
            std::vector<Index> slab = remainder;
            if (ci < cuts.size()) {
                slab = clip_halfspace(remainder, cuts[ci], -1.0, ci);
                remainder = clip_halfspace(remainder, cuts[ci], +1.0, ci);
            }
            if (slab.size() >= 3) pending.push_back({std::move(slab), fn});
        }
    }

    // Sort edge splits canonically and splice any missing split vertices into every face sharing a split original edge.
    for (auto& [k, vec] : splits_by_edge) {
        std::sort(vec.begin(), vec.end(), [](const EdgeSplit& a, const EdgeSplit& b) { return a.t < b.t; });
    }

    for (const PendingFace& pf : pending) {
        std::vector<Index> stitched;
        stitched.reserve(pf.verts.size() + 4);
        for (std::size_t i = 0; i < pf.verts.size(); ++i) {
            const Index a = pf.verts[i], b = pf.verts[(i + 1) % pf.verts.size()];
            stitched.push_back(a);
            auto oit = subedge_origin.find({a, b});
            if (oit == subedge_origin.end()) continue;
            const EdgeParam ep = oit->second;
            auto sit = splits_by_edge.find(ep.key);
            if (sit == splits_by_edge.end() || sit->second.empty()) continue;
            const auto ta_it = vertex_t.find({ep.key, a});
            const auto tb_it = vertex_t.find({ep.key, b});
            if (ta_it == vertex_t.end() || tb_it == vertex_t.end()) continue;
            const double ta = ta_it->second, tb = tb_it->second;
            const double tlo = std::min(ta, tb) + 1e-6, thi = std::max(ta, tb) - 1e-6;
            if (ta < tb) {
                for (const EdgeSplit& es : sit->second)
                    if (es.t > tlo && es.t < thi) stitched.push_back(es.vid);
            } else {
                for (auto rit = sit->second.rbegin(); rit != sit->second.rend(); ++rit)
                    if (rit->t > tlo && rit->t < thi) stitched.push_back(rit->vid);
            }
        }
        emit_polygon_clean(out, stitched, pf.normal);
    }
    return out;
}

Mesh delete_selected_faces(const Mesh& m, std::span<const std::uint8_t> mask) {
    Mesh out;
    std::vector<Index> remap(m.vertex_count(), ~Index{0});
    std::vector<Index> poly;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        if (mask[f]) continue;
        const auto fv = m.face(f);
        poly.resize(fv.size());
        for (std::size_t i = 0; i < fv.size(); ++i) {
            Index& dst = remap[fv[i]];
            if (dst == ~Index{0}) dst = out.add_vertex(m.positions[fv[i]]);
            poly[i] = dst;
        }
        out.add_face(poly);
    }
    return out;
}

// --- Curve operations ---------------------------------------------------------

std::vector<double> curve_cum_lengths(const Curve& c) {
    std::vector<double> s(c.segments() + 1, 0.0);
    for (std::size_t i = 0; i < c.segments(); ++i) {
        s[i + 1] = s[i] + length(c.points[(i + 1) % c.points.size()] - c.points[i]);
    }
    return s;
}

Vec3 eval_curve_at_distance(const Curve& c, const std::vector<double>& s, double dist) {
    const std::size_t segs = c.segments();
    const double total = s[segs];
    if (!(total > 1e-12)) return c.points.front();
    dist = std::clamp(dist, 0.0, total);
    const auto it = std::upper_bound(s.begin(), s.begin() + static_cast<std::ptrdiff_t>(segs), dist);
    const std::size_t seg = std::min(segs - 1, static_cast<std::size_t>(std::max<std::ptrdiff_t>(0, (it - s.begin()) - 1)));
    const double seg_len = s[seg + 1] - s[seg];
    const double w = seg_len > 1e-12 ? (dist - s[seg]) / seg_len : 0.0;
    const Vec3 a = c.points[seg], b = c.points[(seg + 1) % c.points.size()];
    return a + (b - a) * w;
}

Curve fillet_curve(const Curve& c, double radius, int arc_segs, const std::set<int>* corner_filter) {
    const std::size_t n = c.points.size();
    if (n < 3 || radius <= 1e-9) return c;
    Curve out;
    out.closed = c.closed;
    for (std::size_t i = 0; i < n; ++i) {
        if (!c.closed && (i == 0 || i + 1 == n)) {
            out.points.push_back(c.points[i]);
            continue;
        }
        if (corner_filter && !corner_filter->count(static_cast<int>(i))) {
            out.points.push_back(c.points[i]);
            continue;
        }
        const Vec3 prev = c.points[(i + n - 1) % n], curr = c.points[i], next = c.points[(i + 1) % n];
        const Vec3 to_prev = prev - curr, to_next = next - curr;
        const double l_prev = length(to_prev), l_next = length(to_next);
        if (!(l_prev > 1e-8 && l_next > 1e-8)) {
            out.points.push_back(curr);
            continue;
        }
        const Vec3 u = to_prev / l_prev, v = to_next / l_next;
        const double cos_th = std::clamp(dot(u, v), -1.0, 1.0);
        const double theta = std::acos(cos_th);
        if (theta < 1e-3 || std::numbers::pi - theta < 1e-3) {
            out.points.push_back(curr);
            continue;
        }
        const double tan_half = std::tan(0.5 * theta);
        const double trim_req = radius / tan_half;
        // Never consume more than 49% of either adjacent segment so consecutive corner fillets cannot cross.
        const double prev_cap = (!c.closed && i == 1 && (!corner_filter || !corner_filter->count(0))) ? 0.95 * l_prev : 0.49 * l_prev;
        const double next_cap = (!c.closed && i + 2 == n && (!corner_filter || !corner_filter->count(static_cast<int>(n - 1)))) ? 0.95 * l_next : 0.49 * l_next;
        const double trim = std::min({trim_req, prev_cap, next_cap});
        const double r_eff = trim * tan_half;
        const Vec3 pa = curr + u * trim, pb = curr + v * trim;
        const Vec3 bis = normalize_or(u + v, Vec3{});
        const Vec3 center = curr + bis * (r_eff / std::sin(0.5 * theta));
        const Vec3 ra = pa - center, rb = pb - center;
        const Vec3 axis = normalize_or(cross(ra, rb), Vec3{0, 0, 1});
        const double sweep = std::numbers::pi - theta;
        for (int k = 0; k <= arc_segs; ++k) {
            const double ang = sweep * (static_cast<double>(k) / arc_segs);
            const double ca = std::cos(ang), sa = std::sin(ang);
            const Vec3 rot = ra * ca + cross(axis, ra) * sa + axis * (dot(axis, ra) * (1.0 - ca));
            const Vec3 q = center + rot;
            if (out.points.empty() || length(q - out.points.back()) > 1e-9) out.points.push_back(q);
        }
    }
    if (out.closed && out.points.size() >= 2 && length(out.points.front() - out.points.back()) <= 1e-9) out.points.pop_back();
    return out;
}

Curve trim_curve(const Curve& c, double t0, double t1, bool rev) {
    Curve cur = c;
    if (t0 == 0.0 && t1 == 1.0) {
        if (rev) std::reverse(cur.points.begin(), cur.points.end());
        return cur;
    }
    if (!(t1 > t0 + 1e-7)) fail("curveTrim requires end > start");
    const std::vector<double> s = curve_cum_lengths(cur);
    const double total = s.back();
    if (!(total > 1e-9)) fail("curveTrim input curve has zero length");
    const double d0 = t0 * total, d1 = t1 * total;
    Curve out;
    out.closed = false;
    out.points.push_back(eval_curve_at_distance(cur, s, d0));
    for (std::size_t i = 1; i < cur.segments(); ++i) {
        if (s[i] > d0 + 1e-7 && s[i] < d1 - 1e-7) out.points.push_back(cur.points[i]);
    }
    const Vec3 pend = eval_curve_at_distance(cur, s, d1);
    if (length(pend - out.points.back()) > 1e-9) out.points.push_back(pend);
    if (out.points.size() < 2) out.points.push_back(pend);
    if (rev) std::reverse(out.points.begin(), out.points.end());
    return out;
}

Curve offset_curve(const Curve& c, double dist, Vec3 plane_up) {
    const std::size_t n = c.points.size();
    if (n < 2 || std::abs(dist) <= 1e-12) return c;
    const Vec3 up = normalize_or(plane_up, {0, 0, 1});
    const std::size_t segs = c.segments();
    std::vector<Vec3> seg_n(segs);
    for (std::size_t i = 0; i < segs; ++i) {
        const Vec3 tang = normalize_or(c.points[(i + 1) % n] - c.points[i], {1, 0, 0});
        seg_n[i] = normalize_or(cross(up, tang), {0, 1, 0});
    }
    Curve out;
    out.closed = c.closed;
    out.points.resize(n);
    for (std::size_t i = 0; i < n; ++i) {
        if (!c.closed && i == 0) {
            out.points[i] = c.points[i] + seg_n[0] * dist;
        } else if (!c.closed && i + 1 == n) {
            out.points[i] = c.points[i] + seg_n[segs - 1] * dist;
        } else {
            const Vec3 n0 = seg_n[(i + segs - 1) % segs], n1 = seg_n[i % segs];
            const double denom = std::max(0.25, 1.0 + dot(n0, n1));
            out.points[i] = c.points[i] + (n0 + n1) * (dist / denom);
        }
    }
    return out;
}

void emit_curves_out(std::vector<Curve> curves, const std::shared_ptr<const std::string>& source, Geometry& out) {
    for (Curve& c : curves) {
        Part edges;
        edges.proto = curve_proto(c);
        edges.source = source;
        out.parts.push_back(std::move(edges));
        out.points.insert(out.points.end(), c.points.begin(), c.points.end());
        out.curves.push_back(std::move(c));
    }
}

}  // namespace

bool evaluate_mesh_curve(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs,
                         const std::shared_ptr<const std::string>& source, Geometry& out) {
    if (type == "chamfer") {
        if (inputs.size() != 1) fail("Expected one geometry input");
        const SelectionSpec sel = parse_selection(o);
        BevelParams bp;
        bp.width = o.scalar("width", 0.01f, 0.000001f, 1000.0f);
        bp.min_angle_deg = o.scalar("minAngle", 30.0f, 0.0f, 180.0f);
        bp.segments = static_cast<std::uint32_t>(o.integer("segments", 1, 1, 16));
        bp.profile = o.scalar("profile", 0.5f, 0.0f, 1.0f);
        bp.clamp_overlap = o.integer("clampOverlap", 0, 0, 1) != 0;
        bp.crease_sharpness = o.scalar("crease", 0.0f, 0.0f, 10.0f);
        if (sel.active) {
            bp.edge_filter = [&sel](Index e, Vec3 p0, Vec3 p1, Vec3 fn0, Vec3 fn1) {
                return sel.matches_edge(e, p0, p1, fn0, fn1);
            };
        }
        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            out.parts.push_back(make_mesh_part(bevel(baked_part(p), bp), source, &p));
        }
        return true;
    }

    if (type == "crease") {
        if (inputs.size() != 1) fail("crease expects one geometry input");
        const SelectionSpec sel = parse_selection(o);
        const float sharpness = o.scalar("sharpness", 3.0f, 0.0f, 10.0f);
        const bool has_vsharp = o.get("vertexSharpness") != nullptr;
        const float vsharp = has_vsharp ? o.scalar("vertexSharpness", 0.0f, 0.0f, 10.0f) : 0.0f;
        const bool has_vweight = o.get("vertexWeight") != nullptr;
        const double vweight = has_vweight ? o.scalar("vertexWeight", 1.0f, 0.05f, 20.0f) : 1.0;
        const double min_angle_deg = o.scalar("minAngle", 30.0f, 0.0f, 180.0f);
        const double cos_limit = std::cos(min_angle_deg * std::numbers::pi / 180.0) + 1e-9;
        std::string mode = "max";
        if (const json::Value* mv = o.get("mode")) {
            if (!mv->is_string() || (mv->string != "set" && mv->string != "add" && mv->string != "max"))
                fail("crease mode must be set, add or max");
            mode = mv->string;
        }
        auto combine = [&](float cur, float val) -> float {
            if (mode == "set") return std::clamp(val, 0.0f, kCreaseInfinite);
            if (mode == "add") return std::clamp(cur + val, 0.0f, kCreaseInfinite);
            return std::clamp(std::max(cur, val), 0.0f, kCreaseInfinite);
        };

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            // Match selection in world space, write crease tags onto a copy of the prototype mesh (preserving UVs & details).
            const Mesh world = baked_part(p);
            const auto hes = sorted_half_edges(world);
            std::vector<Vec3> fn(world.face_count());
            for (std::size_t f = 0; f < world.face_count(); ++f) fn[f] = face_normal(world, f);

            Proto copy = *p.proto;
            copy.mesh.normalize_creases();
            std::vector<std::uint8_t> touched_v(copy.mesh.vertex_count(), 0);
            Index eid = 0;
            for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
                const Index cur_eid = eid++;
                if (g.size() != 2) return;
                const Vec3 fn0 = fn[g[0].face], fn1 = fn[g[1].face];
                bool ok = (min_angle_deg <= 1e-6) || (dot(fn0, fn1) <= cos_limit);
                if (ok && sel.active) {
                    const Vec3 p0 = world.positions[g[0].lo()], p1 = world.positions[g[0].hi()];
                    ok = sel.matches_edge(cur_eid, p0, p1, fn0, fn1);
                }
                if (ok) {
                    const Index a = g[0].lo(), b = g[0].hi();
                    const float cur = copy.mesh.edge_crease(a, b);
                    copy.mesh.set_edge_crease(a, b, combine(cur, sharpness));
                    if (a < touched_v.size()) touched_v[a] = 1;
                    if (b < touched_v.size()) touched_v[b] = 1;
                }
            });
            if (has_vsharp) {
                for (std::size_t v = 0; v < touched_v.size(); ++v) {
                    if (!touched_v[v]) continue;
                    const float cur = copy.mesh.vertex_crease(static_cast<Index>(v));
                    copy.mesh.set_vertex_crease(static_cast<Index>(v), combine(cur, vsharp));
                }
            }
            if (has_vweight) {
                for (std::size_t v = 0; v < touched_v.size(); ++v) {
                    if (!touched_v[v]) continue;
                    copy.mesh.set_vertex_weight(static_cast<Index>(v), vweight);
                }
            }
            Part np = p;
            np.proto = std::make_shared<const Proto>(std::move(copy));
            np.source = source;
            out.parts.push_back(std::move(np));
        }
        return true;
    }

    if (type == "shrinkwrap") {
        if (inputs.size() != 2) fail("shrinkwrap expects two inputs: geometry and target surface");
        const SelectionSpec sel = parse_selection(o);
        const double offset = o.scalar("offset", 0.0f, -1000.0f, 1000.0f);
        const bool has_max = o.get("maxDistance") != nullptr;
        const double max_dist = has_max ? o.scalar("maxDistance", 1e6f, 0.0f, 1e6f) : 1e30;
        std::string method = "nearest";
        if (const json::Value* mv = o.get("method")) {
            if (!mv->is_string() || (mv->string != "nearest" && mv->string != "project"))
                fail("shrinkwrap method must be nearest or project");
            method = mv->string;
        }
        Vec3 proj_dir{0, 0, -1};
        if (method == "project") {
            proj_dir = normalize_or(o.vector("direction", {0, 0, -1}), {0, 0, -1});
        }
        const Mesh target_mesh = flatten(*inputs[1]);
        if (target_mesh.face_count() == 0) fail("shrinkwrap target has no faces");
        const ClosestIndex target_idx(target_mesh);

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            Mesh world = baked_part(p);
            std::vector<std::uint8_t> vmask(world.vertex_count(), sel.active ? 0 : 1);
            if (sel.active) {
                const std::vector<std::uint8_t> fmask = sel.select_faces(world);
                for (std::size_t f = 0; f < world.face_count(); ++f) {
                    if (!fmask[f]) continue;
                    for (Index v : world.face(f)) vmask[v] = 1;
                }
            }
            for (std::size_t v = 0; v < world.vertex_count(); ++v) {
                if (!vmask[v]) continue;
                const Vec3 q = world.positions[v];
                if (method == "project") {
                    RaycastHit hit{};
                    if (target_idx.raycast(q, proj_dir, 0.0, max_dist, hit, false)) {
                        world.positions[v] = hit.point + hit.normal * offset;
                    }
                } else {
                    ClosestHit ch{};
                    if (target_idx.closest(q, max_dist, ch)) {
                        const Vec3 nrm = face_normal(target_mesh, ch.face);
                        world.positions[v] = ch.point + nrm * offset;
                    }
                }
            }
            Proto proto;
            proto.mesh = std::move(world);
            if (p.proto->uv.size() == proto.mesh.corner_count()) proto.uv = p.proto->uv;
            Part np = p;
            np.proto = std::make_shared<const Proto>(std::move(proto));
            np.transform = Mat4::identity();
            np.source = source;
            out.parts.push_back(std::move(np));
        }
        return true;
    }

    if (type == "transferNormals") {
        if (inputs.size() != 2) fail("transferNormals expects two inputs: geometry and source proxy surface");
        const SelectionSpec sel = parse_selection(o);
        const bool has_max = o.get("maxDistance") != nullptr;
        const double max_dist = has_max ? o.scalar("maxDistance", 1e6f, 0.0f, 1e6f) : 1e30;
        const double blend = o.scalar("blend", 1.0f, 0.0f, 1.0f);

        // Flatten source proxy mesh AND build per-corner smooth world normals (preserving proxy's limit/FWN normals when present).
        Mesh src_mesh;
        std::vector<Vec3> src_cnorm;
        for (const Part& sp : inputs[1]->parts) {
            if (sp.proto->mesh.face_count() == 0) continue;
            const Mesh world = baked_part(sp);
            const auto vbase = static_cast<Index>(src_mesh.vertex_count());
            src_mesh.positions.insert(src_mesh.positions.end(), world.positions.begin(), world.positions.end());
            const bool has_pn = sp.proto->normals.size() == sp.proto->mesh.corner_count();
            // Compute world-space area-weighted vertex normals as smooth fallback when proxy has no precomputed normals.
            std::vector<Vec3> vnorm;
            if (!has_pn) {
                vnorm.assign(world.vertex_count(), Vec3{0, 0, 0});
                for (std::size_t f = 0; f < world.face_count(); ++f) {
                    const Vec3 av = face_area_vector(world, f);
                    for (Index v : world.face(f)) vnorm[v] += av;
                }
                for (Vec3& vn : vnorm) vn = normalize_or(vn, Vec3{0, 0, 1});
            }
            const Mat4& M = sp.transform;
            const Vec3 m_orig = M.point({0, 0, 0});
            auto xform_n = [&](Vec3 n) {
                return normalize_or(M.point(n) - m_orig, Vec3{0, 0, 1});
            };
            for (std::size_t f = 0; f < world.face_count(); ++f) {
                const Index b = world.face_offsets[f], e = world.face_offsets[f + 1];
                std::vector<Index> fv;
                fv.reserve(e - b);
                for (Index c = b; c < e; ++c) {
                    fv.push_back(vbase + world.face_indices[c]);
                    if (has_pn) {
                        const auto& fn = sp.proto->normals[c];
                        src_cnorm.push_back(xform_n({fn[0], fn[1], fn[2]}));
                    } else {
                        src_cnorm.push_back(vnorm[world.face_indices[c]]);
                    }
                }
                src_mesh.add_face(fv);
            }
        }
        if (src_mesh.face_count() == 0) fail("transferNormals source proxy has no surface faces");

        // Precompute fan triangle -> (c0, c1, c2) corner indices matching ClosestIndex scan order!
        struct FanTri { Index c0, c1, c2; };
        std::vector<FanTri> fan_tris;
        fan_tris.reserve(src_mesh.corner_count());
        for (std::size_t f = 0; f < src_mesh.face_count(); ++f) {
            const Index b = src_mesh.face_offsets[f], e = src_mesh.face_offsets[f + 1];
            const Vec3 a = src_mesh.positions[src_mesh.face_indices[b]];
            for (Index c = b + 1; c + 1 < e; ++c) {
                const Vec3 pb = src_mesh.positions[src_mesh.face_indices[c]];
                const Vec3 pc = src_mesh.positions[src_mesh.face_indices[c + 1]];
                if (length(cross(pb - a, pc - a)) <= 1e-20) continue;
                fan_tris.push_back({b, c, c + 1});
            }
        }
        const ClosestIndex bvh(src_mesh);

        auto sample_smooth_normal = [&](const ClosestHit& hit) -> Vec3 {
            if (hit.triangle >= fan_tris.size()) return hit.normal;
            const FanTri& ft = fan_tris[hit.triangle];
            const Vec3 a = src_mesh.positions[src_mesh.face_indices[ft.c0]];
            const Vec3 b = src_mesh.positions[src_mesh.face_indices[ft.c1]];
            const Vec3 c = src_mesh.positions[src_mesh.face_indices[ft.c2]];
            const Vec3 v0 = b - a, v1 = c - a, v2 = hit.point - a;
            const double d00 = dot(v0, v0), d01 = dot(v0, v1), d11 = dot(v1, v1);
            const double d20 = dot(v2, v0), d21 = dot(v2, v1);
            const double denom = d00 * d11 - d01 * d01;
            double u = 0.0, v = 0.0;
            if (std::abs(denom) > 1e-20) {
                u = std::clamp((d11 * d20 - d01 * d21) / denom, 0.0, 1.0);
                v = std::clamp((d00 * d21 - d01 * d20) / denom, 0.0, 1.0);
                if (u + v > 1.0) { const double s = u + v; u /= s; v /= s; }
            }
            const double w0 = std::max(0.0, 1.0 - u - v);
            const Vec3 n = src_cnorm[ft.c0] * w0 + src_cnorm[ft.c1] * u + src_cnorm[ft.c2] * v;
            return normalize_or(n, hit.normal);
        };

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            Mesh world = baked_part(p);
            const std::vector<std::uint8_t> fmask = sel.select_faces(world);
            const std::size_t nc = world.corner_count();
            // Start from existing normals (if present) or area-weighted smooth vertex normals.
            std::vector<Vec3> cur_n(nc);
            if (p.proto->normals.size() == nc) {
                const Mat4& M = p.transform;
                const Vec3 m_orig = M.point({0, 0, 0});
                for (std::size_t c = 0; c < nc; ++c) {
                    const auto& fn = p.proto->normals[c];
                    cur_n[c] = normalize_or(M.point({fn[0], fn[1], fn[2]}) - m_orig, {0, 0, 1});
                }
            } else {
                std::vector<Vec3> vn(world.vertex_count(), Vec3{0, 0, 0});
                for (std::size_t f = 0; f < world.face_count(); ++f) {
                    const Vec3 av = face_area_vector(world, f);
                    for (Index v : world.face(f)) vn[v] += av;
                }
                for (std::size_t f = 0; f < world.face_count(); ++f) {
                    const Vec3 fn = face_normal(world, f);
                    for (Index c = world.face_offsets[f]; c < world.face_offsets[f + 1]; ++c)
                        cur_n[c] = normalize_or(vn[world.face_indices[c]], fn);
                }
            }

            std::vector<std::array<float, 3>> out_n(nc);
            for (std::size_t f = 0; f < world.face_count(); ++f) {
                for (Index c = world.face_offsets[f]; c < world.face_offsets[f + 1]; ++c) {
                    Vec3 n = cur_n[c];
                    if (fmask[f]) {
                        ClosestHit hit{};
                        if (bvh.closest(world.positions[world.face_indices[c]], max_dist, hit)) {
                            const Vec3 tn = sample_smooth_normal(hit);
                            n = normalize_or(n * (1.0 - blend) + tn * blend, tn);
                        }
                    }
                    out_n[c] = {static_cast<float>(n.x), static_cast<float>(n.y), static_cast<float>(n.z)};
                }
            }

            Proto proto;
            proto.mesh = std::move(world);
            if (p.proto->uv.size() == proto.mesh.corner_count()) proto.uv = p.proto->uv;
            proto.normals = std::move(out_n);
            Part np = p;
            np.proto = std::make_shared<const Proto>(std::move(proto));
            np.transform = Mat4::identity();
            np.smooth = true;
            np.source = source;
            out.parts.push_back(std::move(np));
        }
        return true;
    }

    if (type == "loopCut") {
        if (inputs.size() != 1) fail("loopCut expects one geometry input");
        const SelectionSpec sel = parse_selection(o);
        const bool has_margin = o.get("margin") != nullptr;
        const double margin = has_margin ? o.scalar("margin", 0.02f, 0.00001f, 1000.0f) : 0.0;
        std::vector<double> frac_cuts;
        if (o.get("positions")) {
            for (const json::Value& v : o.list("positions", 1, 32)) {
                const double t = o.number(v);
                if (!(t > 0.0 && t < 1.0)) fail("loopCut positions must lie strictly in (0, 1)");
                frac_cuts.push_back(t);
            }
            std::sort(frac_cuts.begin(), frac_cuts.end());
        } else if (!has_margin) {
            const int ncuts = o.integer("cuts", 1, 1, 32);
            for (int k = 1; k <= ncuts; ++k) frac_cuts.push_back(static_cast<double>(k) / static_cast<double>(ncuts + 1));
        }
        const float loop_crease = o.scalar("crease", 0.0f, 0.0f, 10.0f);
        const bool propagate = o.integer("propagate", 1, 0, 1) != 0;

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            const Mesh m = baked_part(p);
            const std::size_t nf = m.face_count();
            std::vector<Vec3> fn(nf);
            for (std::size_t f = 0; f < nf; ++f) fn[f] = face_normal(m, f);

            // Build unique undirected edges and quad-face adjacency.
            const auto hes = sorted_half_edges(m);
            struct EdgeRec {
                Index lo, hi;
                std::vector<std::pair<Index, int>> quad_uses;  // (face, local_edge 0..3)
                std::vector<Index> faces;
            };
            std::vector<EdgeRec> edges;
            std::map<std::uint64_t, Index> edge_by_key;
            for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
                const auto eid = static_cast<Index>(edges.size());
                EdgeRec er{g[0].lo(), g[0].hi(), {}, {}};
                for (const HalfEdge& he : g) {
                    er.faces.push_back(he.face);
                    const auto fv = m.face(he.face);
                    if (fv.size() == 4) {
                        for (int k = 0; k < 4; ++k) {
                            if (fv[static_cast<std::size_t>(k)] == he.from && fv[static_cast<std::size_t>((k + 1) % 4)] == he.to()) {
                                er.quad_uses.push_back({he.face, k});
                                break;
                            }
                        }
                    }
                }
                edge_by_key[g[0].key] = eid;
                edges.push_back(std::move(er));
            });
            const std::size_t ne = edges.size();

            // Seed edges matching selection, then BFS-propagate across opposite edges of quads
            // while maintaining consistent orientation (+1: lo->hi, -1: hi->lo) so cuts stay parallel.
            std::vector<std::int8_t> cut_dir(ne, 0);
            std::vector<Index> queue;
            for (Index e = 0; e < ne; ++e) {
                const EdgeRec& er = edges[e];
                const Vec3 fn0 = er.faces.empty() ? Vec3{0, 0, 1} : fn[er.faces[0]];
                const Vec3 fn1 = er.faces.size() >= 2 ? fn[er.faces[1]] : fn0;
                if (!sel.active || sel.matches_edge(e, m.positions[er.lo], m.positions[er.hi], fn0, fn1)) {
                    cut_dir[e] = 1;
                    queue.push_back(e);
                }
            }
            if (propagate) {
                for (std::size_t qi = 0; qi < queue.size(); ++qi) {
                    const Index e = queue[qi];
                    const Index a = cut_dir[e] > 0 ? edges[e].lo : edges[e].hi;
                    const Index b = cut_dir[e] > 0 ? edges[e].hi : edges[e].lo;
                    for (const auto& [f, loc] : edges[e].quad_uses) {
                        const auto fv = m.face(f);
                        const Index u0 = fv[static_cast<std::size_t>(loc)];
                        const Index u1 = fv[static_cast<std::size_t>((loc + 1) % 4)];
                        const Index o0 = fv[static_cast<std::size_t>((loc + 3) % 4)];
                        const Index o1 = fv[static_cast<std::size_t>((loc + 2) % 4)];
                        // Opposite edge runs from o0 -> o1 matching u0 -> u1; flip if (a->b) ran opposite to (u0->u1).
                        const Index want_from = (a == u0 && b == u1) ? o0 : o1;
                        const Index want_to = (a == u0 && b == u1) ? o1 : o0;
                        const Index oe = edge_by_key.at(edge_key(want_from, want_to));
                        if (cut_dir[oe] == 0) {
                            cut_dir[oe] = (want_from == edges[oe].lo) ? 1 : -1;
                            queue.push_back(oe);
                        }
                    }
                }
            }
            if (queue.empty()) {
                out.parts.push_back(p);
                continue;
            }

            Mesh res;
            res.positions = m.positions;
            res.vertex_creases = m.vertex_creases;
            // For each cut edge e, create split vertices ordered in edge's cut_dir (a -> b).
            std::vector<std::vector<Index>> edge_splits(ne);
            for (Index e = 0; e < ne; ++e) {
                const float orig_s = m.edge_crease(edges[e].lo, edges[e].hi);
                if (cut_dir[e] == 0) {
                    if (orig_s > 0.0f) res.edge_creases.push_back({edge_key(edges[e].lo, edges[e].hi), orig_s});
                    continue;
                }
                const Index a = cut_dir[e] > 0 ? edges[e].lo : edges[e].hi;
                const Index b = cut_dir[e] > 0 ? edges[e].hi : edges[e].lo;
                const Vec3 pa = m.positions[a], pb = m.positions[b];
                const double L = length(pb - pa);
                std::vector<double> ts;
                if (has_margin) {
                    const double t0 = L > 1e-9 ? std::clamp(margin / L, 0.02, 0.45) : 0.25;
                    ts = {t0, 1.0 - t0};
                } else {
                    ts = frac_cuts;
                }
                edge_splits[e].reserve(ts.size());
                Index prev_v = a;
                for (double t : ts) {
                    const Index vid = res.add_vertex(pa + (pb - pa) * t);
                    edge_splits[e].push_back(vid);
                    if (orig_s > 0.0f) res.edge_creases.push_back({edge_key(prev_v, vid), orig_s});
                    prev_v = vid;
                }
                if (orig_s > 0.0f) res.edge_creases.push_back({edge_key(prev_v, b), orig_s});
            }

            // Helper returning the full vertex chain [u, ..., v] along directed edge (u -> v).
            auto chain = [&](Index u, Index v) -> std::vector<Index> {
                const Index e = edge_by_key.at(edge_key(u, v));
                std::vector<Index> ch;
                ch.push_back(u);
                if (cut_dir[e] != 0) {
                    const Index a = cut_dir[e] > 0 ? edges[e].lo : edges[e].hi;
                    if (u == a) ch.insert(ch.end(), edge_splits[e].begin(), edge_splits[e].end());
                    else ch.insert(ch.end(), edge_splits[e].rbegin(), edge_splits[e].rend());
                }
                ch.push_back(v);
                return ch;
            };

            for (std::size_t f = 0; f < nf; ++f) {
                const auto fv = m.face(f);
                if (fv.size() == 4) {
                    const auto c0 = chain(fv[0], fv[1]);  // bottom u: 0..nx
                    const auto c2 = chain(fv[3], fv[2]);  // top u:    0..nx
                    const auto c3 = chain(fv[0], fv[3]);  // left v:   0..ny
                    const auto c1 = chain(fv[1], fv[2]);  // right v:  0..ny
                    const std::size_t nx = c0.size() - 1, ny = c3.size() - 1;
                    if (c0.size() == c2.size() && c3.size() == c1.size() && (nx > 1 || ny > 1)) {
                        const std::size_t W = nx + 1, H = ny + 1;
                        std::vector<Index> grid(W * H, ~Index{0});
                        auto at = [&](std::size_t gx, std::size_t gy) -> Index& { return grid[gy * W + gx]; };
                        for (std::size_t gx = 0; gx <= nx; ++gx) { at(gx, 0) = c0[gx]; at(gx, ny) = c2[gx]; }
                        for (std::size_t gy = 0; gy <= ny; ++gy) { at(0, gy) = c3[gy]; at(nx, gy) = c1[gy]; }
                        for (std::size_t gy = 1; gy < ny; ++gy) {
                            const double v = static_cast<double>(gy) / static_cast<double>(ny);
                            const Vec3 pl = res.positions[at(0, gy)], pr = res.positions[at(nx, gy)];
                            const Vec3 p00 = res.positions[at(0, 0)], p10 = res.positions[at(nx, 0)];
                            const Vec3 p01 = res.positions[at(0, ny)], p11 = res.positions[at(nx, ny)];
                            for (std::size_t gx = 1; gx < nx; ++gx) {
                                const double u = static_cast<double>(gx) / static_cast<double>(nx);
                                const Vec3 pb = res.positions[at(gx, 0)], pt = res.positions[at(gx, ny)];
                                const Vec3 q = (pl * (1.0 - u) + pr * u) + (pb * (1.0 - v) + pt * v) -
                                               ((p00 * (1.0 - u) + p10 * u) * (1.0 - v) + (p01 * (1.0 - u) + p11 * u) * v);
                                at(gx, gy) = res.add_vertex(q);
                            }
                        }
                        for (std::size_t gy = 0; gy < ny; ++gy)
                            for (std::size_t gx = 0; gx < nx; ++gx)
                                res.add_face({at(gx, gy), at(gx + 1, gy), at(gx + 1, gy + 1), at(gx, gy + 1)});
                        if (loop_crease > 0.0f) {
                            for (std::size_t gx = 1; gx < nx; ++gx)
                                for (std::size_t gy = 0; gy < ny; ++gy)
                                    res.edge_creases.push_back({edge_key(at(gx, gy), at(gx, gy + 1)), loop_crease});
                            for (std::size_t gy = 1; gy < ny; ++gy)
                                for (std::size_t gx = 0; gx < nx; ++gx)
                                    res.edge_creases.push_back({edge_key(at(gx, gy), at(gx + 1, gy)), loop_crease});
                        }
                        continue;
                    }
                }
                // Non-quad or unevenly cut face: splice any split edge vertices cleanly to stay watertight.
                std::vector<Index> poly;
                for (std::size_t i = 0; i < fv.size(); ++i) {
                    const auto ch = chain(fv[i], fv[(i + 1) % fv.size()]);
                    poly.insert(poly.end(), ch.begin(), ch.end() - 1);
                }
                res.add_face(poly);
            }
            res.normalize_creases();
            out.parts.push_back(make_mesh_part(std::move(res), source, &p));
        }
        return true;
    }

    if (type == "faceExtrude") {
        if (inputs.empty() || inputs.size() > 2) fail("faceExtrude expects 1 input (or 2 with occluder)");
        const SelectionSpec sel = parse_selection(o);
        const std::optional<ClosestIndex> occ = build_occluder(inputs);
        const double offset_dist = o.get("distance") ? o.scalar("distance", 0.0f, -1000.0f, 1000.0f)
                                                     : o.scalar("offset", 0.0f, -1000.0f, 1000.0f);
        const double inset_dist = o.scalar("inset", 0.0f, 0.0f, 1000.0f);
        const double scale_f = o.scalar("scale", 1.0f, 0.01f, 100.0f);
        const bool default_ind = inset_dist > 0.0 || std::abs(scale_f - 1.0) > 1e-9;
        const bool individual = o.integer("individual", default_ind ? 1 : 0, 0, 1) != 0;
        const bool even_thick = o.integer("evenThickness", 1, 0, 1) != 0;
        const json::Value* cap_slot_v = o.get("capSlot");
        const bool split_cap = cap_slot_v != nullptr;
        if (split_cap && !cap_slot_v->is_string()) fail("faceExtrude capSlot must be a material slot name");
        const std::uint16_t cap_mat = split_cap ? slot_id(cap_slot_v->string) : 0;

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            const Mesh world = baked_part(p);
            const std::vector<std::uint8_t> mask = sel.select_faces(world, occ ? &*occ : nullptr);
            if (individual || inset_dist > 0.0 || std::abs(scale_f - 1.0) > 1e-9 || split_cap) {
                Mesh caps;
                Mesh body = inset_extrude_individual(world, mask, inset_dist, scale_f, offset_dist, split_cap ? &caps : nullptr);
                if (body.face_count() > 0) out.parts.push_back(make_mesh_part(std::move(body), source, &p));
                if (split_cap && caps.face_count() > 0) {
                    Part cp = make_mesh_part(std::move(caps), source, &p);
                    cp.material = cap_mat;
                    out.parts.push_back(std::move(cp));
                }
            } else {
                ExtrudeParams ep;
                ep.distance = offset_dist;
                ep.mode = ExtrudeMode::Region;
                ep.even_thickness = even_thick;
                out.parts.push_back(make_mesh_part(extrude(world, mask, ep), source, &p));
            }
        }
        return true;
    }

    if (type == "faceDivide") {
        if (inputs.empty() || inputs.size() > 2) fail("faceDivide expects 1 input (or 2 with occluder)");
        const SelectionSpec sel = parse_selection(o);
        const std::optional<ClosestIndex> occ = build_occluder(inputs);
        std::string axis_mode = "vertical";
        Vec3 custom_axis{0, 0, 1};
        if (const json::Value* av = o.get("axis")) {
            if (av->is_string()) {
                if (av->string != "vertical" && av->string != "horizontal")
                    fail("faceDivide axis string must be vertical or horizontal");
                axis_mode = av->string;
            } else {
                custom_axis = o.vector(*av);
                if (!(length(custom_axis) > 1e-9)) fail("faceDivide axis vector must be non-zero");
                axis_mode = "custom";
            }
        }
        std::vector<double> sizes_spec;
        if (o.get("sizes")) {
            for (const json::Value& v : o.list("sizes", 2, 256)) {
                const double s = o.number(v);
                if (std::abs(s) <= 1e-9) fail("faceDivide sizes entries must be non-zero (>0 metres or <0 flex weight)");
                sizes_spec.push_back(s);
            }
        }
        const double repeat_pitch = o.scalar("repeat", 0.0f, 0.0f, 10000.0f);
        if (sizes_spec.empty() && !(repeat_pitch > 0.0)) fail("faceDivide needs sizes [...] or repeat > 0");

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            const Mesh world = baked_part(p);
            const std::vector<std::uint8_t> mask = sel.select_faces(world, occ ? &*occ : nullptr);
            out.parts.push_back(make_mesh_part(divide_faces(world, mask, axis_mode, custom_axis, sizes_spec, repeat_pitch), source, &p));
        }
        return true;
    }

    if (type == "deleteFaces") {
        if (inputs.empty() || inputs.size() > 2) fail("deleteFaces expects 1 input (or 2 with occluder)");
        const SelectionSpec sel = parse_selection(o);
        const std::optional<ClosestIndex> occ = build_occluder(inputs);
        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                out.parts.push_back(p);
                continue;
            }
            const Mesh world = baked_part(p);
            const std::vector<std::uint8_t> mask = sel.select_faces(world, occ ? &*occ : nullptr);
            Mesh kept = delete_selected_faces(world, mask);
            if (kept.face_count() > 0) out.parts.push_back(make_mesh_part(std::move(kept), source, &p));
        }
        return true;
    }

    if (type == "quadFillHole" || type == "bridgeLoops") {
        // Shared helper: extract closed directed boundary loops (each half-edge u->v belongs to exactly 1 face)
        // along with per-vertex outward exit tangents lying in the adjacent face plane.
        struct BLoop {
            std::vector<Index> verts;
            std::vector<Vec3> exit_tan;
            Vec3 center{};
            Vec3 normal{};
        };
        auto extract_boundary_loops = [](const Mesh& m) -> std::vector<BLoop> {
            const auto hes = sorted_half_edges(m);
            std::map<Index, std::vector<std::pair<Index, Index>>> adj;  // u -> [(v, face)]
            for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
                if (g.size() == 1) adj[g[0].from].push_back({g[0].to(), g[0].face});
            });
            std::set<std::pair<Index, Index>> visited;
            std::vector<BLoop> loops;
            for (const auto& [start_u, edges_u] : adj) {
                for (const auto& [first_v, first_f] : edges_u) {
                    if (visited.count({start_u, first_v})) continue;
                    BLoop lp;
                    std::vector<Vec3> edge_tans;
                    Index u = start_u, v = first_v, f = first_f;
                    bool closed = false;
                    for (std::size_t guard = 0; guard <= adj.size() + 2; ++guard) {
                        if (!visited.insert({u, v}).second) break;
                        lp.verts.push_back(u);
                        const Vec3 fn = face_normal(m, f);
                        const Vec3 ev = normalize_or(m.positions[v] - m.positions[u], Vec3{1, 0, 0});
                        edge_tans.push_back(normalize_or(cross(ev, fn), Vec3{0, 0, 1}));
                        if (v == start_u) { closed = true; break; }
                        auto it = adj.find(v);
                        if (it == adj.end()) break;
                        bool found = false;
                        for (const auto& [nv, nf] : it->second) {
                            if (!visited.count({v, nv})) { u = v; v = nv; f = nf; found = true; break; }
                        }
                        if (!found) break;
                    }
                    if (!closed || lp.verts.size() < 3) continue;
                    const std::size_t k = lp.verts.size();
                    lp.exit_tan.resize(k);
                    Vec3 c{};
                    for (std::size_t i = 0; i < k; ++i) {
                        c += m.positions[lp.verts[i]];
                        lp.exit_tan[i] = normalize_or(edge_tans[i] + edge_tans[(i + k - 1) % k], edge_tans[i]);
                    }
                    lp.center = c / static_cast<double>(k);
                    Vec3 n{};
                    for (std::size_t i = 0; i < k; ++i)
                        n += cross(m.positions[lp.verts[i]] - lp.center, m.positions[lp.verts[(i + 1) % k]] - lp.center);
                    // Note: boundary loop walks CW around the hole opening -> -n points outward from hole!
                    lp.normal = normalize_or(-n, Vec3{0, 0, 1});
                    loops.push_back(std::move(lp));
                }
            }
            return loops;
        };

        if (type == "quadFillHole") {
            if (inputs.size() != 1) fail("quadFillHole expects one geometry input");
            const SelectionSpec sel = parse_selection(o);
            const double bulge = o.scalar("bulge", 0.0f, -10.0f, 10.0f);
            const int offset_shift = o.integer("offset", 0, -4096, 4096);
            for (const Part& p : inputs[0]->parts) {
                if (p.proto->mesh.face_count() == 0 || !sel.matches_part(p)) {
                    out.parts.push_back(p);
                    continue;
                }
                Mesh world = baked_part(p);
                const auto loops = extract_boundary_loops(world);
                for (const BLoop& lp : loops) {
                    if (sel.active && !sel.matches_point_geo(lp.center)) continue;
                    const std::size_t m = lp.verts.size();
                    if (m < 4 || (m % 2) != 0) continue;
                    // Reverse boundary loop so new Coons patch traverses every boundary edge opposite to existing face!
                    std::vector<Index> R(m);
                    for (std::size_t i = 0; i < m; ++i) {
                        const std::size_t idx = (m - 1 - i + static_cast<std::size_t>((offset_shift % static_cast<int>(m) + static_cast<int>(m)) % static_cast<int>(m))) % m;
                        R[i] = lp.verts[idx];
                    }
                    const std::size_t half = m / 2;
                    const std::size_t a = half / 2;       // columns (>= 1)
                    const std::size_t b = half - a;       // rows (>= 1), 2*(a + b) == m
                    std::vector<std::vector<Index>> grid(b + 1, std::vector<Index>(a + 1, 0));
                    for (std::size_t c = 0; c <= a; ++c) grid[0][c] = R[c];
                    for (std::size_t r = 1; r <= b; ++r) grid[r][a] = R[a + r];
                    for (std::size_t c = 1; c <= a; ++c) grid[b][a - c] = R[a + b + c];
                    for (std::size_t r = 1; r < b; ++r) grid[b - r][0] = R[2 * a + b + r];

                    const Vec3 c00 = world.positions[grid[0][0]], c10 = world.positions[grid[0][a]];
                    const Vec3 c11 = world.positions[grid[b][a]], c01 = world.positions[grid[b][0]];
                    for (std::size_t r = 1; r < b; ++r) {
                        const double v = static_cast<double>(r) / static_cast<double>(b);
                        for (std::size_t c = 1; c < a; ++c) {
                            const double u = static_cast<double>(c) / static_cast<double>(a);
                            const Vec3 B = world.positions[grid[0][c]], T = world.positions[grid[b][c]];
                            const Vec3 L = world.positions[grid[r][0]], Rg = world.positions[grid[r][a]];
                            Vec3 pos = B * (1.0 - v) + T * v + L * (1.0 - u) + Rg * u
                                       - (c00 * ((1.0 - u) * (1.0 - v)) + c10 * (u * (1.0 - v))
                                          + c11 * (u * v) + c01 * ((1.0 - u) * v));
                            if (std::abs(bulge) > 1e-9) {
                                const double dome = std::sin(std::numbers::pi * u) * std::sin(std::numbers::pi * v);
                                pos += lp.normal * (bulge * dome);
                            }
                            grid[r][c] = world.add_vertex(pos);
                        }
                    }
                    for (std::size_t r = 0; r < b; ++r)
                        for (std::size_t c = 0; c < a; ++c)
                            world.add_face({grid[r][c], grid[r][c + 1], grid[r + 1][c + 1], grid[r + 1][c]});
                }
                out.parts.push_back(make_mesh_part(std::move(world), source, &p));
            }
            return true;
        }

        // bridgeLoops: bridge two open boundary loops (from 1 or 2 geometry inputs) into one watertight all-quad solid!
        if (inputs.empty() || inputs.size() > 2) fail("bridgeLoops expects 1 or 2 geometry inputs");
        const int segs = o.integer("segments", 4, 1, 64);
        const bool smooth_bridge = o.integer("smooth", 1, 0, 1) != 0;
        const double tension = o.scalar("tension", 0.45f, 0.0f, 2.0f);
        const double profile_scale = o.scalar("profileScale", 1.0f, 0.1f, 4.0f);
        const int twist = o.integer("twist", 0, -512, 512);

        Mesh combined = flatten(*inputs[0]);
        if (inputs.size() == 2) {
            const Mesh m2 = flatten(*inputs[1]);
            const auto vbase = static_cast<Index>(combined.vertex_count());
            combined.positions.insert(combined.positions.end(), m2.positions.begin(), m2.positions.end());
            for (const EdgeCrease& ec : m2.edge_creases) {
                const Index a = static_cast<Index>(ec.key >> 32) + vbase;
                const Index b = static_cast<Index>(ec.key & 0xffffffffu) + vbase;
                combined.edge_creases.push_back({edge_key(a, b), ec.sharpness});
            }
            for (const VertexCrease& vc : m2.vertex_creases)
                combined.vertex_creases.push_back({vc.vertex + vbase, vc.sharpness});
            for (std::size_t f = 0; f < m2.face_count(); ++f) {
                const auto fv = m2.face(f);
                std::vector<Index> nf(fv.size());
                for (std::size_t i = 0; i < fv.size(); ++i) nf[i] = fv[i] + vbase;
                combined.add_face(nf);
            }
            combined.normalize_creases();
        }
        auto loops = extract_boundary_loops(combined);
        if (loops.size() < 2) fail("bridgeLoops requires at least 2 open boundary loops");

        // Select the closest pair of matching-length boundary loops.
        std::size_t ia = 0, ib = 1;
        double best_pair_d = std::numeric_limits<double>::infinity();
        bool found_pair = false;
        for (std::size_t i = 0; i < loops.size(); ++i) {
            for (std::size_t j = i + 1; j < loops.size(); ++j) {
                if (loops[i].verts.size() != loops[j].verts.size()) continue;
                const double d = length(loops[i].center - loops[j].center);
                if (d < best_pair_d) { best_pair_d = d; ia = i; ib = j; found_pair = true; }
            }
        }
        if (!found_pair) fail("bridgeLoops could not find two boundary loops with equal vertex count");

        const BLoop& la = loops[ia];
        const BLoop& lb = loops[ib];
        const std::size_t n = la.verts.size();
        // Reverse loop A so quad (RA[k], RA[k+1], B[k+1], B[k]) traverses both A and B opposite to existing faces!
        std::vector<Index> ra(n);
        std::vector<Vec3> ta(n);
        for (std::size_t k = 0; k < n; ++k) {
            ra[k] = la.verts[(n - k) % n];
            ta[k] = la.exit_tan[(n - k) % n];
        }
        std::size_t best_shift = 0;
        double best_cost = std::numeric_limits<double>::infinity();
        for (std::size_t sh = 0; sh < n; ++sh) {
            double cost = 0.0;
            for (std::size_t k = 0; k < n; ++k) {
                const Vec3 d = combined.positions[ra[k]] - combined.positions[lb.verts[(k + sh) % n]];
                cost += dot(d, d);
            }
            if (cost < best_cost) { best_cost = cost; best_shift = sh; }
        }
        const std::size_t shift = static_cast<std::size_t>((static_cast<int>(best_shift) + twist % static_cast<int>(n) + static_cast<int>(n)) % static_cast<int>(n));

        std::vector<std::vector<Index>> rings(static_cast<std::size_t>(segs) + 1, std::vector<Index>(n));
        for (std::size_t k = 0; k < n; ++k) {
            rings[0][k] = ra[k];
            rings[static_cast<std::size_t>(segs)][k] = lb.verts[(k + shift) % n];
        }
        for (int s = 1; s < segs; ++s) {
            const double t = static_cast<double>(s) / static_cast<double>(segs);
            const double t2 = t * t, t3 = t2 * t;
            const double h00 = 2.0 * t3 - 3.0 * t2 + 1.0;
            const double h10 = t3 - 2.0 * t2 + t;
            const double h01 = -2.0 * t3 + 3.0 * t2;
            const double h11 = t3 - t2;
            std::vector<Vec3> pts(n);
            Vec3 ctr{};
            for (std::size_t k = 0; k < n; ++k) {
                const Vec3 p0 = combined.positions[ra[k]];
                const Vec3 p1 = combined.positions[lb.verts[(k + shift) % n]];
                const double dist = length(p1 - p0);
                if (smooth_bridge) {
                    const Vec3 m0 = ta[k] * (dist * tension);
                    const Vec3 m1 = (-lb.exit_tan[(k + shift) % n]) * (dist * tension);
                    pts[k] = p0 * h00 + m0 * h10 + p1 * h01 + m1 * h11;
                } else {
                    pts[k] = p0 * (1.0 - t) + p1 * t;
                }
                ctr += pts[k];
            }
            ctr = ctr / static_cast<double>(n);
            const double sc = 1.0 + (profile_scale - 1.0) * std::sin(std::numbers::pi * t);
            for (std::size_t k = 0; k < n; ++k) {
                const Vec3 q = ctr + (pts[k] - ctr) * sc;
                rings[static_cast<std::size_t>(s)][k] = combined.add_vertex(q);
            }
        }
        for (int s = 0; s < segs; ++s) {
            for (std::size_t k = 0; k < n; ++k) {
                const std::size_t kn = (k + 1) % n;
                combined.add_face({rings[static_cast<std::size_t>(s)][k],
                                   rings[static_cast<std::size_t>(s)][kn],
                                   rings[static_cast<std::size_t>(s + 1)][kn],
                                   rings[static_cast<std::size_t>(s + 1)][k]});
            }
        }
        const Part* ref_p = !inputs[0]->parts.empty() ? &inputs[0]->parts[0] : nullptr;
        out.parts.push_back(make_mesh_part(std::move(combined), source, ref_p));
        return true;
    }

    if (type == "displaceDetails") {
        if (inputs.size() != 1) fail("displaceDetails expects one geometry input");
        const double scale = o.scalar("scale", 1.0f, -100.0f, 100.0f);
        const int sub_levels = o.integer("levels", 0, 0, 3);
        const bool clear_details = o.integer("clearDetails", 1, 0, 1) != 0;
        const float smooth_angle = o.scalar("smoothAngle", 55.0f, 0.0f, 180.0f);

        for (const Part& p : inputs[0]->parts) {
            if (p.proto->mesh.face_count() == 0 || p.proto->details.empty()) {
                out.parts.push_back(p);
                continue;
            }
            DetailIndex didx(std::span<const Detail>(p.proto->details));
            Mesh m = p.proto->mesh;
            std::vector<std::array<float, 2>> uv = p.proto->uv;
            std::vector<Vec3> cnorm;
            if (sub_levels > 0) {
                SubdivideOptions so;
                so.levels = static_cast<std::uint32_t>(sub_levels);
                so.limit_snap = true;
                so.compute_limit_normals = true;
                so.crease_method = CreaseMethod::Chaikin;
                SubdivideResult sr = catmull_clark_full(m, uv, so);
                m = std::move(sr.mesh);
                uv = std::move(sr.uv);
                cnorm = std::move(sr.corner_normals);
            } else if (p.proto->normals.size() == m.corner_count()) {
                cnorm.resize(m.corner_count());
                for (std::size_t c = 0; c < m.corner_count(); ++c) {
                    const auto& n = p.proto->normals[c];
                    cnorm[c] = normalize_or({n[0], n[1], n[2]}, {0, 0, 1});
                }
            }

            // Compute smooth vertex normals (averaging analytical limit normals when present, else area-weighted).
            std::vector<Vec3> vnorm(m.vertex_count(), Vec3{0, 0, 0});
            if (cnorm.size() == m.corner_count()) {
                for (std::size_t c = 0; c < m.corner_count(); ++c) vnorm[m.face_indices[c]] += cnorm[c];
            } else {
                for (std::size_t f = 0; f < m.face_count(); ++f) {
                    const Vec3 av = face_area_vector(m, f);
                    for (Index v : m.face(f)) vnorm[v] += av;
                }
            }
            for (Vec3& n : vnorm) n = normalize_or(n, Vec3{0, 0, 1});

            // Evaluate exact 0x8007 DetailIndex height at every vertex along its smooth/limit normal!
            for (std::size_t v = 0; v < m.vertex_count(); ++v) {
                const DetailSample ds = didx.sample(m.positions[v], vnorm[v]);
                if (std::abs(ds.height) > 1e-9) {
                    m.positions[v] += vnorm[v] * (ds.height * scale);
                }
            }

            Proto proto;
            proto.mesh = std::move(m);
            if (uv.size() == proto.mesh.corner_count()) proto.uv = std::move(uv);
            if (!clear_details) proto.details = p.proto->details;
            Part np = p;
            np.proto = std::make_shared<const Proto>(std::move(proto));
            np.smooth = true;
            np.smooth_angle_deg = smooth_angle;
            np.normal_weight = 3;  // areaAngle FWN
            np.source = source;
            out.parts.push_back(std::move(np));
        }
        return true;
    }

    if (type == "curveFillet") {
        if (inputs.size() != 1 || inputs[0]->curves.empty()) fail("curveFillet expects one curve input");
        const double radius = o.scalar("radius", 0.05f, 0.0f, 1000.0f);
        const int segs = o.integer("segments", 6, 1, 64);
        std::set<int> corners;
        const bool has_corners = o.get("corners") != nullptr;
        if (has_corners) {
            for (const json::Value& v : o.list("corners", 1, 4096)) {
                const float fv = o.number(v);
                if (!(fv >= 0 && std::round(fv) == fv)) fail("curveFillet corners must be non-negative integers");
                corners.insert(static_cast<int>(fv));
            }
        }
        std::vector<Curve> res;
        for (const Curve& c : inputs[0]->curves) res.push_back(fillet_curve(c, radius, segs, has_corners ? &corners : nullptr));
        emit_curves_out(std::move(res), source, out);
        return true;
    }

    if (type == "curveTrim") {
        if (inputs.size() != 1 || inputs[0]->curves.empty()) fail("curveTrim expects one curve input");
        const double start = o.scalar("start", 0.0f, 0.0f, 1.0f);
        const double end = o.scalar("end", 1.0f, 0.0f, 1.0f);
        const bool rev = o.integer("reverse", 0, 0, 1) != 0;
        std::vector<Curve> res;
        for (const Curve& c : inputs[0]->curves) res.push_back(trim_curve(c, start, end, rev));
        emit_curves_out(std::move(res), source, out);
        return true;
    }

    if (type == "curveOffset") {
        if (inputs.size() != 1 || inputs[0]->curves.empty()) fail("curveOffset expects one curve input");
        const double dist = o.scalar("offset", 0.05f, -1000.0f, 1000.0f);
        const Vec3 up = o.vector("up", {0, 0, 1});
        std::vector<Curve> res;
        for (const Curve& c : inputs[0]->curves) res.push_back(offset_curve(c, dist, up));
        emit_curves_out(std::move(res), source, out);
        return true;
    }

    if (type == "sampleCurve") {
        if (inputs.size() != 1 || inputs[0]->curves.empty()) fail("sampleCurve expects one curve input");
        std::vector<double> fractions;
        if (o.get("positions")) {
            for (const json::Value& v : o.list("positions", 1, 4096)) {
                const double t = o.number(v);
                if (!(t >= 0.0 && t <= 1.0)) fail("sampleCurve positions must lie in [0, 1]");
                fractions.push_back(t);
            }
        }
        const double pitch = o.scalar("pitch", 0.0f, 0.0f, 10000.0f);
        const int count = o.integer("count", 0, 0, 4096);
        const bool centred = o.integer("centred", 1, 0, 1) != 0;
        if (fractions.empty() && !(pitch > 0.0) && count <= 0)
            fail("sampleCurve needs positions [...], pitch > 0, or count > 0");

        for (const Curve& c : inputs[0]->curves) {
            const std::vector<double> s = curve_cum_lengths(c);
            const double total = s.back();
            if (!fractions.empty()) {
                for (double t : fractions) out.points.push_back(eval_curve_at_distance(c, s, t * total));
            } else if (pitch > 0.0) {
                const int n = std::max(1, static_cast<int>(std::floor(total / pitch)));
                const double span = (n - 1) * pitch;
                const double d0 = centred ? 0.5 * (total - span) : 0.0;
                for (int i = 0; i < n; ++i) out.points.push_back(eval_curve_at_distance(c, s, d0 + i * pitch));
            } else {
                for (int i = 0; i < count; ++i) {
                    const double t = c.closed ? static_cast<double>(i) / count
                                              : (count == 1 ? 0.5 : static_cast<double>(i) / (count - 1));
                    out.points.push_back(eval_curve_at_distance(c, s, t * total));
                }
            }
        }
        return true;
    }

    return false;
}

void apply_hierarchical_subdiv_edit(const json::Value& edit_v, const Options& parent_o, Mesh& m) {
    if (m.face_count() == 0) return;
    const Options eo{&edit_v, parent_o.controls, parent_o.point};
    const SelectionSpec sel = parse_selection(eo);
    const std::vector<std::uint8_t> fmask = sel.select_faces(m);

    // 1. Tangent-frame hierarchical face sculpt (inset / offset / scale / crease) at subdivision level `l`.
    const bool has_inset = eo.get("inset") != nullptr || eo.get("scale") != nullptr;
    const bool has_offset = eo.get("offset") != nullptr || eo.get("distance") != nullptr;
    if (has_inset || has_offset) {
        const double inset_dist = eo.scalar("inset", 0.0f, 0.0f, 100.0f);
        const double scale_f = eo.scalar("scale", 1.0f, 0.01f, 10.0f);
        const double offset_dist = eo.get("offset") ? eo.scalar("offset", 0.0f, -100.0f, 100.0f)
                                                    : eo.scalar("distance", 0.0f, -100.0f, 100.0f);
        const bool individual = eo.integer("individual", 1, 0, 1) != 0;
        const std::size_t old_nv = m.vertex_count();
        if (individual || inset_dist > 0.0 || std::abs(scale_f - 1.0) > 1e-6) {
            m = inset_extrude_individual(m, fmask, inset_dist, scale_f, offset_dist, nullptr);
        } else {
            ExtrudeParams ex;
            ex.distance = offset_dist;
            ex.even_thickness = true;
            m = extrude(m, fmask, ex);
        }
        // Optional rim crease on newly created boundary edges of the hierarchical sculpt!
        if (eo.get("crease")) {
            const float csharp = eo.scalar("crease", 2.0f, 0.0f, 10.0f);
            if (csharp > 0.0f) {
                const auto hes = sorted_half_edges(m);
                for_each_edge_group(std::span<const HalfEdge>(hes), [&](std::span<const HalfEdge> g) {
                    if (g.size() != 2) return;
                    const Index a = g[0].lo(), b = g[0].hi();
                    if (a >= old_nv || b >= old_nv) {
                        const double dn = dot(face_normal(m, g[0].face), face_normal(m, g[1].face));
                        if (dn < 0.85) m.set_edge_crease(a, b, std::max(m.edge_crease(a, b), csharp));
                    }
                });
                m.normalize_creases();
            }
        }
    }

    // 2. Local smooth-normal vertex displacement & sinusoidal cast fluting at subdivision level `l`.
    const double n_off = eo.scalar("normalOffset", 0.0f, -100.0f, 100.0f);
    const json::Value* flv = eo.get("flutes");
    if (std::abs(n_off) > 1e-9 || flv != nullptr) {
        std::vector<std::uint8_t> vmask(m.vertex_count(), 0);
        std::vector<Vec3> vnorm(m.vertex_count(), Vec3{0, 0, 0});
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            const Vec3 av = face_area_vector(m, f);
            for (Index v : m.face(f)) {
                vnorm[v] += av;
                if (fmask[f]) vmask[v] = 1;
            }
        }
        for (Vec3& n : vnorm) n = normalize_or(n, Vec3{0, 0, 1});

        int f_axis = 2, f_count = 0;
        double f_amp = 0.0, f_phase = 0.0;
        Vec3 f_ctr{0, 0, 0};
        if (flv && flv->is_object()) {
            const Options fo{flv, parent_o.controls, parent_o.point};
            if (const json::Value* ax = fo.get("axis"); ax && ax->is_string())
                f_axis = ax->string == "x" ? 0 : (ax->string == "y" ? 1 : 2);
            f_count = fo.integer("count", 12, 1, 256);
            f_amp = fo.scalar("amplitude", 0.008f, -10.0f, 10.0f);
            f_phase = fo.scalar("phase", 0.0f, -1000.0f, 1000.0f);
            f_ctr = fo.vector("center", {0, 0, 0});
        }
        for (std::size_t v = 0; v < m.vertex_count(); ++v) {
            if (!vmask[v]) continue;
            double d = n_off;
            if (f_count > 0 && std::abs(f_amp) > 1e-9) {
                const Vec3 q = m.positions[v] - f_ctr;
                const double ang = f_axis == 0 ? std::atan2(q.z, q.y)
                                 : (f_axis == 1 ? std::atan2(q.z, q.x) : std::atan2(q.y, q.x));
                d += f_amp * std::cos(static_cast<double>(f_count) * ang + f_phase);
            }
            m.positions[v] += vnorm[v] * d;
        }
    }
}

}  // namespace pg::doc::detail
