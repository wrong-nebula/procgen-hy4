#include "pg/ops/subdivide.hpp"

#include "pg/topology.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <numbers>
#include <utility>
#include <vector>

namespace pg {
namespace {

constexpr float kInf = kCreaseInfinite;

inline bool is_sharp(float s) noexcept { return s > 0.0f; }
inline bool is_infinite(float s) noexcept { return s >= kInf; }
inline float dec_sharpness(float s) noexcept {
    if (s <= 0.0f) return 0.0f;
    if (s >= kInf) return kInf;
    return s > 1.0f ? s - 1.0f : 0.0f;
}

enum class VRule : std::uint8_t { Smooth, Crease, Corner };

struct UvChannel {
    std::vector<std::array<double, 2>> coords;  // unique UV values
    std::vector<Index> corner_uv;               // index into coords per mesh corner (size == nc)
};

UvChannel build_uv_channel(const Mesh& m, std::span<const std::array<float, 2>> raw_uv) {
    UvChannel ch;
    const std::size_t nc = m.corner_count();
    if (raw_uv.size() != nc) return ch;
    ch.corner_uv.resize(nc);
    // Group corners that share both mesh vertex and UV coordinates (within 1e-6).
    struct Key {
        Index v;
        std::int64_t uq, vq;
        auto operator<=>(const Key&) const = default;
    };
    std::map<Key, Index> remap;
    for (std::size_t c = 0; c < nc; ++c) {
        const double u = raw_uv[c][0], v = raw_uv[c][1];
        const Key k{m.face_indices[c], std::llround(u * 1e6), std::llround(v * 1e6)};
        const auto [it, fresh] = remap.try_emplace(k, static_cast<Index>(ch.coords.size()));
        if (fresh) ch.coords.push_back({u, v});
        ch.corner_uv[c] = it->second;
    }
    return ch;
}

// Subdivide face-varying UVs for one topological quad-split step.
UvChannel subdivide_uv_step(const Mesh& m, const UvChannel& in, const std::vector<Index>& corner_face,
                            const std::vector<Index>& corner_next, const std::vector<Index>& corner_edge,
                            const std::vector<float>& mesh_edge_sharp, std::size_t ne, UvSubdivRule rule,
                            bool linear_only) {
    UvChannel out;
    if (in.corner_uv.empty()) return out;
    const std::size_t nf = m.face_count(), nc = m.corner_count();
    const std::size_t nuv = in.coords.size();

    // Build unique UV edges by undirected (uv_a, uv_b).
    struct UKC {
        std::uint64_t key;
        Index corner;
    };
    std::vector<UKC> ukc(nc);
    for (Index c = 0; c < nc; ++c) {
        ukc[c] = {edge_key(in.corner_uv[c], in.corner_uv[corner_next[c]]), c};
    }
    std::sort(ukc.begin(), ukc.end(), [](const UKC& a, const UKC& b) { return a.key < b.key; });

    struct UEdge {
        Index u0, u1;
        Index f0, f1;
        Index uses;
        Index mesh_edge;
        bool seam;
    };
    std::vector<UEdge> uedges;
    uedges.reserve(nc / 2 + 1);
    std::vector<Index> corner_uedge(nc);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        const auto ueid = static_cast<Index>(uedges.size());
        UEdge ue{static_cast<Index>(ukc[i].key >> 32), static_cast<Index>(ukc[i].key & 0xffffffffu),
                 corner_face[ukc[i].corner], 0, 0, corner_edge[ukc[i].corner], false};
        for (; j < nc && ukc[j].key == ukc[i].key; ++j) {
            corner_uedge[ukc[j].corner] = ueid;
            if (ue.uses == 1) ue.f1 = corner_face[ukc[j].corner];
            ++ue.uses;
        }
        ue.seam = (ue.uses != 2) || (mesh_edge_sharp[ue.mesh_edge] >= 1.0f);
        uedges.push_back(ue);
        i = j;
    }
    const std::size_t nue = uedges.size();

    // Face UV centroids.
    std::vector<std::array<double, 2>> face_uv(nf, {0.0, 0.0});
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        double su = 0.0, sv = 0.0;
        for (Index c = b; c < e; ++c) {
            su += in.coords[in.corner_uv[c]][0];
            sv += in.coords[in.corner_uv[c]][1];
        }
        const double inv = 1.0 / static_cast<double>(e - b);
        face_uv[f] = {su * inv, sv * inv};
    }

    out.coords.resize(nuv + nue + nf);
    const bool all_linear = linear_only || rule == UvSubdivRule::Linear;

    // Accumulators for UV vertex update.
    std::vector<std::array<double, 2>> mid_sum(nuv, {0.0, 0.0}), face_sum(nuv, {0.0, 0.0}), seam_sum(nuv, {0.0, 0.0});
    std::vector<std::uint32_t> edge_cnt(nuv, 0), face_cnt(nuv, 0), seam_cnt(nuv, 0);

    for (std::size_t e = 0; e < nue; ++e) {
        const UEdge& ue = uedges[e];
        const auto p0 = in.coords[ue.u0], p1 = in.coords[ue.u1];
        const std::array<double, 2> mid{0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1])};
        const bool edge_linear = all_linear || ue.seam || (ue.uses != 2);
        if (edge_linear) {
            out.coords[nuv + e] = mid;
        } else {
            const auto fp0 = face_uv[ue.f0], fp1 = face_uv[ue.f1];
            out.coords[nuv + e] = {0.25 * (p0[0] + p1[0] + fp0[0] + fp1[0]),
                                   0.25 * (p0[1] + p1[1] + fp0[1] + fp1[1])};
        }
        mid_sum[ue.u0][0] += mid[0];
        mid_sum[ue.u0][1] += mid[1];
        mid_sum[ue.u1][0] += mid[0];
        mid_sum[ue.u1][1] += mid[1];
        ++edge_cnt[ue.u0];
        ++edge_cnt[ue.u1];
        if (ue.seam) {
            seam_sum[ue.u0][0] += p1[0];
            seam_sum[ue.u0][1] += p1[1];
            seam_sum[ue.u1][0] += p0[0];
            seam_sum[ue.u1][1] += p0[1];
            ++seam_cnt[ue.u0];
            ++seam_cnt[ue.u1];
        }
    }
    for (Index c = 0; c < nc; ++c) {
        const Index u = in.corner_uv[c];
        const auto fp = face_uv[corner_face[c]];
        face_sum[u][0] += fp[0];
        face_sum[u][1] += fp[1];
        ++face_cnt[u];
    }

    for (std::size_t u = 0; u < nuv; ++u) {
        const auto p = in.coords[u];
        if (all_linear) {
            out.coords[u] = p;
            continue;
        }
        if (seam_cnt[u] == 0 && edge_cnt[u] > 0 && face_cnt[u] > 0) {
            const double n = static_cast<double>(edge_cnt[u]);
            const double inv_f = 1.0 / static_cast<double>(face_cnt[u]);
            const double fu = face_sum[u][0] * inv_f, fv = face_sum[u][1] * inv_f;
            const double ru = mid_sum[u][0] / n, rv = mid_sum[u][1] / n;
            out.coords[u] = {(fu + 2.0 * ru + (n - 3.0) * p[0]) / n,
                             (fv + 2.0 * rv + (n - 3.0) * p[1]) / n};
        } else if (seam_cnt[u] == 2 && rule != UvSubdivRule::Boundaries && face_cnt[u] > 1) {
            out.coords[u] = {(6.0 * p[0] + seam_sum[u][0]) / 8.0, (6.0 * p[1] + seam_sum[u][1]) / 8.0};
        } else {
            out.coords[u] = p;  // pinned at UV corner, junction, or Boundaries mode
        }
    }
    for (std::size_t f = 0; f < nf; ++f) out.coords[nuv + nue + f] = face_uv[f];

    // Emit 4 UV indices per child quad, matching the mesh quad loop exactly.
    out.corner_uv.reserve(nc * 4);
    const auto uebase = static_cast<Index>(nuv);
    const auto ufbase = static_cast<Index>(nuv + nue);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            const Index prev = c == b ? e - 1 : c - 1;
            out.corner_uv.push_back(in.corner_uv[c]);
            out.corner_uv.push_back(uebase + corner_uedge[c]);
            out.corner_uv.push_back(ufbase + static_cast<Index>(f));
            out.corner_uv.push_back(uebase + corner_uedge[prev]);
        }
    }
    (void)ne;
    return out;
}

// Initialize crease-split face-weighted (area*angle) shading normals on the control cage
// and per-vertex extraordinary-vertex (EV) blend weights (Zhou-Boonstra-Kosinka 2023 Variant A).
void init_subdiv_normals(const Mesh& m, std::vector<Vec3>& cnorm, std::vector<double>& evw) {
    const std::size_t nv = m.vertex_count(), nf = m.face_count(), nc = m.corner_count();
    cnorm.assign(nc, Vec3{0, 0, 1});
    evw.assign(nv, 0.0);
    if (nv == 0 || nf == 0) return;

    std::vector<Index> cface(nc), cnext(nc), cprev(nc);
    std::vector<Vec3> fnorm(nf);
    std::vector<double> farea(nf, 0.0);
    std::vector<std::uint8_t> touches_nonquad(nv, 0);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        const Vec3 av = face_area_vector(m, f);
        farea[f] = 0.5 * length(av);
        fnorm[f] = normalize_or(av, Vec3{0, 0, 1});
        const bool nonquad = (e - b) != 4;
        for (Index c = b; c < e; ++c) {
            cface[c] = static_cast<Index>(f);
            cnext[c] = c + 1 == e ? b : c + 1;
            cprev[c] = c == b ? e - 1 : c - 1;
            if (nonquad) touches_nonquad[m.face_indices[c]] = 1;
        }
    }

    // Build half-edge twins & detect hard/creased/boundary edges (sharpness >= 1.0).
    constexpr Index none = ~Index{0};
    struct KC { std::uint64_t key; Index corner; };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(m.face_indices[c], m.face_indices[cnext[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });

    std::vector<Index> twin(nc, none);
    std::vector<std::uint8_t> edge_hard(nc, 1);
    std::vector<std::uint32_t> valence(nv, 0);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        while (j < nc && kc[j].key == kc[i].key) ++j;
        const auto v0 = static_cast<Index>(kc[i].key >> 32);
        const auto v1 = static_cast<Index>(kc[i].key & 0xffffffffu);
        ++valence[v0];
        ++valence[v1];
        if (j - i == 2 && m.face_indices[kc[i].corner] != m.face_indices[kc[i + 1].corner]) {
            const Index a = kc[i].corner, b = kc[i + 1].corner;
            twin[a] = b;
            twin[b] = a;
            const bool hard = m.edge_crease(v0, v1) >= 1.0f;
            edge_hard[a] = edge_hard[b] = hard ? 1 : 0;
        }
        i = j;
    }

    for (std::size_t v = 0; v < nv; ++v) {
        if (valence[v] != 4 || touches_nonquad[v]) evw[v] = 1.0;
    }

    // Union-Find on corners at each vertex across non-hard incident edges -> crease-split sectors!
    std::vector<Index> parent(nc);
    for (Index c = 0; c < nc; ++c) parent[c] = c;
    auto find_root = [&](Index x) {
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];
            x = parent[x];
        }
        return x;
    };
    auto unite = [&](Index a, Index b) {
        a = find_root(a);
        b = find_root(b);
        if (a != b) parent[b] = a;
    };
    for (Index c = 0; c < nc; ++c) {
        if (!edge_hard[c] && twin[c] != none) {
            // Corner c is at v with outgoing edge c->cnext[c]; twin[c] is at cnext[c], so cnext[twin[c]] is at v!
            const Index other_at_v = cnext[twin[c]];
            if (m.face_indices[other_at_v] == m.face_indices[c]) unite(c, other_at_v);
        }
    }

    std::vector<Vec3> acc(nc, Vec3{0, 0, 0});
    for (Index c = 0; c < nc; ++c) {
        const Index v = m.face_indices[c];
        const Vec3 e1 = normalize_or(m.positions[m.face_indices[cnext[c]]] - m.positions[v], Vec3{1, 0, 0});
        const Vec3 e2 = normalize_or(m.positions[m.face_indices[cprev[c]]] - m.positions[v], Vec3{0, 1, 0});
        const double ang = std::acos(std::clamp(dot(e1, e2), -1.0, 1.0));
        const double w = std::max(1e-12, farea[cface[c]] * std::max(0.05, ang));
        acc[find_root(c)] += fnorm[cface[c]] * w;
    }
    for (Index c = 0; c < nc; ++c) {
        cnorm[c] = normalize_or(acc[find_root(c)], fnorm[cface[c]]);
    }
}

void subdivide_step(const Mesh& m, const UvChannel& uv_in, const SubdivideOptions& opts, bool linear_step,
                    Mesh& out, UvChannel& uv_out,
                    const std::vector<Vec3>* cnorm_in = nullptr, std::vector<Vec3>* cnorm_out = nullptr,
                    const std::vector<double>* evw_in = nullptr, std::vector<double>* evw_out = nullptr,
                    const std::map<std::uint64_t, double>* knots_in = nullptr,
                    std::map<std::uint64_t, double>* knots_out = nullptr,
                    const std::vector<double>* w_in = nullptr, std::vector<double>* w_out = nullptr) {
    const std::size_t nv = m.vertex_count(), nf = m.face_count(), nc = m.corner_count();

    std::vector<Index> corner_face(nc), corner_next(nc);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            corner_face[c] = static_cast<Index>(f);
            corner_next[c] = c + 1 == e ? b : c + 1;
        }
    }

    struct KC {
        std::uint64_t key;
        Index corner;
    };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(m.face_indices[c], m.face_indices[corner_next[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });

    struct Edge {
        Index v0, v1, f0, f1, uses;
        float sharpness;
    };
    std::vector<Edge> edges;
    edges.reserve(nc / 2 + 1);
    std::vector<Index> corner_edge(nc);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        const auto eid = static_cast<Index>(edges.size());
        const auto v0 = static_cast<Index>(kc[i].key >> 32);
        const auto v1 = static_cast<Index>(kc[i].key & 0xffffffffu);
        Edge e{v0, v1, corner_face[kc[i].corner], 0, 0, 0.0f};
        for (; j < nc && kc[j].key == kc[i].key; ++j) {
            corner_edge[kc[j].corner] = eid;
            if (e.uses == 1) e.f1 = corner_face[kc[j].corner];
            ++e.uses;
        }
        if (e.uses != 2) {
            e.sharpness = kInf;
        } else {
            e.sharpness = m.edge_crease(v0, v1);
        }
        edges.push_back(e);
        i = j;
    }
    const std::size_t ne = edges.size();

    // Build per-vertex incident edge CSR (for exact Chaikin & transitional rules).
    std::vector<Index> v_edge_off(nv + 1, 0);
    for (const Edge& e : edges) {
        ++v_edge_off[e.v0 + 1];
        ++v_edge_off[e.v1 + 1];
    }
    for (std::size_t v = 0; v < nv; ++v) v_edge_off[v + 1] += v_edge_off[v];
    std::vector<Index> v_edges(v_edge_off.back());
    {
        std::vector<Index> cur(v_edge_off.begin(), v_edge_off.end() - 1);
        for (Index e = 0; e < ne; ++e) {
            v_edges[cur[edges[e].v0]++] = e;
            v_edges[cur[edges[e].v1]++] = e;
        }
    }

    // Non-uniform chord-length knot intervals (Sederberg NURSS 1998 / Cashman 2007 / Li et al. 2020).
    const bool use_nu = opts.non_uniform && knots_in != nullptr && !knots_in->empty();
    std::vector<double> edge_d(ne, 1.0);
    if (use_nu) {
        for (std::size_t e = 0; e < ne; ++e) {
            const auto it = knots_in->find(edge_key(edges[e].v0, edges[e].v1));
            edge_d[e] = (it != knots_in->end() && it->second > 1e-9)
                            ? it->second
                            : std::max(1e-6, length(m.positions[edges[e].v1] - m.positions[edges[e].v0]));
        }
    }

    // Regularized NURSS / Virtual-Knot-Span Subdivision (Sederberg 1998 + Cashman-Dodgson-Sabin 2009):
    // 1. Edge points on bevel-to-panel boundaries use perpendicular face-span weights `dp0 / (2*(dp0+dp1))`
    //    so wide flat faces anchor edges cleanly without bowing.
    // 2. For each vertex `v`, compute `v_min_d[v]` (its shortest incident edge knot) and pull long-edge
    //    neighbors at virtual support distance `min(len, 2.0 * v_min_d[v])` so rectangular corners stay
    //    crisp & square (never clipping into octagons!) while wide flat panels stay dead-flat!
    std::vector<double> v_min_d(nv, std::numeric_limits<double>::infinity());
    if (use_nu) {
        for (std::size_t e = 0; e < ne; ++e) {
            v_min_d[edges[e].v0] = std::min(v_min_d[edges[e].v0], edge_d[e]);
            v_min_d[edges[e].v1] = std::min(v_min_d[edges[e].v1], edge_d[e]);
        }
    }

    // Symmetric geometric edge midpoints & face centroids -> zero corner notches or sliver quads!
    std::vector<Vec3> edge_mid(ne);
    for (std::size_t e = 0; e < ne; ++e) {
        edge_mid[e] = (m.positions[edges[e].v0] + m.positions[edges[e].v1]) * 0.5;
    }
    std::vector<Vec3> face_pt(nf);
    for (std::size_t f = 0; f < nf; ++f) face_pt[f] = face_centroid(m, f);

    // Optional homogeneous weight channel for Rational R^4 Catmull-Clark (mirrors position stencils identically).
    const bool use_w = (w_in != nullptr && w_out != nullptr && w_in->size() == nv);
    std::vector<double> face_w;
    if (use_w) {
        w_out->assign(nv + ne + nf, 1.0);
        face_w.assign(nf, 1.0);
        for (std::size_t f = 0; f < nf; ++f) {
            const auto fv = m.face(f);
            double sw = 0.0;
            for (Index v : fv) sw += (*w_in)[v];
            face_w[f] = sw / static_cast<double>(fv.size());
            (*w_out)[nv + ne + f] = face_w[f];
        }
    }

    out = Mesh{};
    out.reserve(nv + ne + nf, nc, nc * 4);
    out.positions.resize(nv + ne + nf);

    // Precompute child edge sharpnesses per (edge, end 0/1) for Uniform or Chaikin.
    std::vector<std::array<float, 2>> child_edge_s(ne, {0.0f, 0.0f});
    std::vector<float> edge_sharp_vec(ne, 0.0f);
    for (std::size_t e = 0; e < ne; ++e) {
        const Edge& ed = edges[e];
        edge_sharp_vec[e] = ed.sharpness;
        if (linear_step) {
            const float keep = ed.uses == 2 ? ed.sharpness : 0.0f;  // boundary creases are topological
            child_edge_s[e] = {keep, keep};
        } else if (ed.uses != 2) {
            child_edge_s[e] = {kInf, kInf};
        } else if (!is_sharp(ed.sharpness)) {
            child_edge_s[e] = {0.0f, 0.0f};
        } else if (is_infinite(ed.sharpness) || opts.crease_method == CreaseMethod::Uniform) {
            const float d = dec_sharpness(ed.sharpness);
            child_edge_s[e] = {d, d};
        } else {
            // OpenSubdiv Chaikin sharpness subdivision at each endpoint vertex.
            const Index ends[2] = {ed.v0, ed.v1};
            for (int side = 0; side < 2; ++side) {
                const Index v = ends[side];
                double sum_other = 0.0;
                int cnt_other = 0;
                for (Index i = v_edge_off[v]; i < v_edge_off[v + 1]; ++i) {
                    const Index oe = v_edges[i];
                    if (oe == e) continue;
                    const float os = edges[oe].sharpness;
                    if (is_sharp(os)) {
                        sum_other += static_cast<double>(os);
                        ++cnt_other;
                    }
                }
                if (cnt_other == 0) {
                    child_edge_s[e][side] = dec_sharpness(ed.sharpness);
                } else {
                    const double avg_other = sum_other / static_cast<double>(cnt_other);
                    double ch_s = 0.75 * static_cast<double>(ed.sharpness) + 0.25 * avg_other - 1.0;
                    // Prevent an adjacent infinitely-sharp edge from keeping a finite semi-sharp edge sharp forever.
                    ch_s = std::min(ch_s, std::max(0.0, static_cast<double>(ed.sharpness) - 0.25));
                    child_edge_s[e][side] = ch_s > 0.0 ? static_cast<float>(std::min(ch_s, static_cast<double>(kInf))) : 0.0f;
                }
            }
        }
    }

    // Helper: perpendicular span of face `f` adjacent to edge `eid` (for NURSS edge points).
    auto face_perp_d = [&](Index f, Index eid) -> double {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        const std::size_t k = e - b;
        if (k == 4) {
            for (Index c = b; c < e; ++c) {
                if (corner_edge[c] == eid) {
                    const Index c_prev = (c == b) ? e - 1 : c - 1;
                    const Index c_next = (c + 1 == e) ? b : c + 1;
                    return 0.5 * (edge_d[corner_edge[c_prev]] + edge_d[corner_edge[c_next]]);
                }
            }
        }
        double sum = 0.0;
        int cnt = 0;
        for (Index c = b; c < e; ++c) {
            if (corner_edge[c] != eid) { sum += edge_d[corner_edge[c]]; ++cnt; }
        }
        return cnt > 0 ? sum / static_cast<double>(cnt) : edge_d[eid];
    };

    // Edge points and vertex accumulators.
    std::vector<Vec3> face_sum(nv), mid_sum(nv);
    std::vector<double> face_wsum(nv, 0.0), mid_wsum(nv, 0.0);
    std::vector<double> w_fsum(use_w ? nv : 0, 0.0), w_msum(use_w ? nv : 0, 0.0);
    std::vector<std::uint32_t> face_cnt(nv, 0), edge_cnt(nv, 0);
    for (std::size_t e = 0; e < ne; ++e) {
        const Edge& ed = edges[e];
        const Vec3 p0 = m.positions[ed.v0], p1 = m.positions[ed.v1];
        const Vec3 mid = edge_mid[e];
        const double w_mid = use_w ? 0.5 * ((*w_in)[ed.v0] + (*w_in)[ed.v1]) : 1.0;
        if (linear_step || ed.uses != 2 || ed.sharpness >= 1.0f) {
            out.positions[nv + e] = mid;
            if (use_w) (*w_out)[nv + e] = w_mid;
        } else {
            Vec3 e_smooth;
            double ew_smooth = w_mid;
            if (use_nu) {
                const double dp0 = face_perp_d(ed.f0, static_cast<Index>(e));
                const double dp1 = face_perp_d(ed.f1, static_cast<Index>(e));
                const double denom = 2.0 * (dp0 + dp1);
                const double wf0 = denom > 1e-12 ? dp0 / denom : 0.25;
                const double wf1 = denom > 1e-12 ? dp1 / denom : 0.25;
                const double lim = 1.5 * std::min(dp0, dp1);
                auto clamp_fp = [&](Vec3 fp) {
                    const Vec3 d = fp - mid;
                    const double len = length(d);
                    return (len > lim && lim > 1e-9) ? (mid + d * (lim / len)) : fp;
                };
                e_smooth = mid * 0.5 + clamp_fp(face_pt[ed.f0]) * wf0 + clamp_fp(face_pt[ed.f1]) * wf1;
                if (use_w) ew_smooth = w_mid * 0.5 + face_w[ed.f0] * wf0 + face_w[ed.f1] * wf1;
            } else if (opts.tri_subdiv == TriSubdivRule::Smooth &&
                       (m.face(ed.f0).size() == 3 || m.face(ed.f1).size() == 3)) {
                constexpr double kTriFaceW = 0.470;  // OpenSubdiv CATMARK_SMOOTH_TRI_EDGE_WEIGHT
                const double f0w = m.face(ed.f0).size() == 3 ? kTriFaceW : 0.25;
                const double f1w = m.face(ed.f1).size() == 3 ? kTriFaceW : 0.25;
                const double fw = 0.5 * (f0w + f1w);
                const double vw = 0.5 * (1.0 - 2.0 * fw);
                e_smooth = (p0 + p1) * vw + (face_pt[ed.f0] + face_pt[ed.f1]) * fw;
                if (use_w) ew_smooth = ((*w_in)[ed.v0] + (*w_in)[ed.v1]) * vw + (face_w[ed.f0] + face_w[ed.f1]) * fw;
            } else {
                e_smooth = (p0 + p1 + face_pt[ed.f0] + face_pt[ed.f1]) * 0.25;
                if (use_w) ew_smooth = ((*w_in)[ed.v0] + (*w_in)[ed.v1] + face_w[ed.f0] + face_w[ed.f1]) * 0.25;
            }
            if (is_sharp(ed.sharpness)) {
                const double t = static_cast<double>(ed.sharpness);  // in (0, 1)
                out.positions[nv + e] = e_smooth * (1.0 - t) + mid * t;
                if (use_w) (*w_out)[nv + e] = ew_smooth * (1.0 - t) + w_mid * t;
            } else {
                out.positions[nv + e] = e_smooth;
                if (use_w) (*w_out)[nv + e] = ew_smooth;
            }
        }
        const Vec3 ref_e = use_nu ? out.positions[nv + e] : mid;
        const double ew = use_nu ? std::max(1e-6, edge_d[e]) : 1.0;
        auto pull_pt = [&](Index v, Vec3 q) -> Vec3 {
            if (!use_nu || !std::isfinite(v_min_d[v])) return q;
            const Vec3 pv = m.positions[v];
            const Vec3 d = q - pv;
            const double len = length(d);
            const double lim = 1.5 * v_min_d[v];
            return (len > lim && lim > 1e-9) ? (pv + d * (lim / len)) : q;
        };
        mid_sum[ed.v0] += pull_pt(ed.v0, ref_e) * ew;
        mid_sum[ed.v1] += pull_pt(ed.v1, ref_e) * ew;
        mid_wsum[ed.v0] += ew;
        mid_wsum[ed.v1] += ew;
        if (use_w) {
            const double rw = use_nu ? (*w_out)[nv + e] : w_mid;
            w_msum[ed.v0] += rw * ew;
            w_msum[ed.v1] += rw * ew;
        }
        ++edge_cnt[ed.v0];
        ++edge_cnt[ed.v1];
    }
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            const Index v = m.face_indices[c];
            double fw = 1.0;
            Vec3 fp = face_pt[f];
            if (use_nu) {
                const Index c_prev = (c == b) ? e - 1 : c - 1;
                fw = std::max(1e-12, edge_d[corner_edge[c]] * edge_d[corner_edge[c_prev]]);
                if (std::isfinite(v_min_d[v])) {
                    const Vec3 pv = m.positions[v];
                    const Vec3 d = fp - pv;
                    const double len = length(d);
                    const double lim = 1.5 * v_min_d[v];
                    if (len > lim && lim > 1e-9) fp = pv + d * (lim / len);
                }
            }
            face_sum[v] += fp * fw;
            face_wsum[v] += fw;
            if (use_w) w_fsum[v] += face_w[f] * fw;
            ++face_cnt[v];
        }
    }

    // Vertex points with full semi-sharp crease & corner transitional rules.
    for (std::size_t v = 0; v < nv; ++v) {
        const Vec3 p = m.positions[v];
        if (linear_step || edge_cnt[v] == 0 || face_cnt[v] == 0) {
            out.positions[v] = p;
            if (use_w) (*w_out)[v] = (*w_in)[v];
            const float vs = m.vertex_crease(static_cast<Index>(v));
            if (linear_step && is_sharp(vs)) out.vertex_creases.push_back({static_cast<Index>(v), vs});
            continue;
        }

        float vs_parent = m.vertex_crease(static_cast<Index>(v));
        // Inspect incident sharp edges (parent and child).
        std::uint32_t parent_sharp = 0, child_sharp = 0, boundary_edges = 0;
        Index sharp_pair[2] = {0, 0};
        double parent_sharp_sum = 0.0;
        double decaying_sum = 0.0;
        int decaying_cnt = 0;
        for (Index i = v_edge_off[v]; i < v_edge_off[v + 1]; ++i) {
            const Index eid = v_edges[i];
            const Edge& ed = edges[eid];
            if (ed.uses != 2) ++boundary_edges;
            const int side = (ed.v0 == v) ? 0 : 1;
            const float ps = ed.sharpness;
            const float cs = (ed.uses != 2) ? kInf : child_edge_s[eid][side];
            if (is_sharp(ps)) {
                if (parent_sharp < 2) sharp_pair[parent_sharp] = eid;
                ++parent_sharp;
                parent_sharp_sum += static_cast<double>(ps);
                if (!is_sharp(cs)) {
                    decaying_sum += static_cast<double>(ps);
                    ++decaying_cnt;
                }
            }
            if (is_sharp(cs)) ++child_sharp;
        }

        // Open boundary corner pin (EdgeAndCorner rule): a boundary vertex with 1 incident face.
        if (opts.boundary == BoundaryRule::EdgeAndCorner && boundary_edges >= 2 && face_cnt[v] == 1) {
            vs_parent = kInf;
        }
        const float vs_child = dec_sharpness(vs_parent);

        auto rule_of = [](float vs, std::uint32_t k) -> VRule {
            if (is_sharp(vs) || k >= 3) return VRule::Corner;
            if (k == 2) return VRule::Crease;
            return VRule::Smooth;
        };
        const VRule prule = rule_of(vs_parent, parent_sharp);
        const VRule crule = rule_of(vs_child, child_sharp);

        const double n = static_cast<double>(edge_cnt[v]);
        const Vec3 F = face_sum[v] / (use_nu ? std::max(1e-12, face_wsum[v]) : static_cast<double>(face_cnt[v]));
        const Vec3 R = mid_sum[v] / (use_nu ? std::max(1e-12, mid_wsum[v]) : n);
        Vec3 v_smooth;
        if (opts.tuned_ev && edge_cnt[v] != 4) {
            // Augsdorfer-Dodgson-Sabin (CGF 2006) curvature-tuned extraordinary vertex weights:
            // reduces Gaussian curvature spikes at valence-3 box corners and valence-5/6+ poles.
            double alpha = 0.0, beta = 0.0, gamma = 0.0;
            if (edge_cnt[v] == 3) {
                alpha = 0.145; beta = 0.610; gamma = 0.245;
            } else if (edge_cnt[v] == 5) {
                alpha = 0.365; beta = 0.435; gamma = 0.200;
            } else {
                gamma = 1.0 / (n + 0.5);
                beta = 2.25 / (n + 0.5);
                alpha = std::max(0.0, 1.0 - beta - gamma);
            }
            v_smooth = F * gamma + R * beta + p * alpha;
        } else {
            v_smooth = (F + 2.0 * R + (n - 3.0) * p) / n;
        }
        // Kosinka-Sabin-Dodgson (CGF 2014) dart-vertex crease termination:
        // smoothly relax pinch when a single semi-sharp crease terminates at an interior dart vertex.
        if (opts.tuned_ev && parent_sharp == 1 && !is_sharp(vs_parent)) {
            const Edge& de = edges[sharp_pair[0]];
            const Vec3 d_other = m.positions[de.v0 == v ? de.v1 : de.v0];
            const Vec3 p_dart = (p * 3.0 + d_other) * 0.25;
            const double wd = 0.22 * std::clamp(static_cast<double>(de.sharpness), 0.0, 1.0);
            v_smooth = v_smooth * (1.0 - wd) + p_dart * wd;
        }

        auto eval_rule = [&](VRule r) -> Vec3 {
            if (r == VRule::Corner) return p;
            if (r == VRule::Crease && parent_sharp == 2) {
                const Index id0 = sharp_pair[0], id1 = sharp_pair[1];
                if (use_nu) {
                    const double d0 = edge_d[id0], d1 = edge_d[id1];
                    const double den = 2.0 * (d0 + d1);
                    const double w0 = den > 1e-12 ? d0 / den : 0.25;
                    const double w1 = den > 1e-12 ? d1 / den : 0.25;
                    return edge_mid[id0] * w0 + p * 0.5 + edge_mid[id1] * w1;
                }
                const Edge& e0 = edges[id0];
                const Edge& e1 = edges[id1];
                const Vec3 o0 = m.positions[e0.v0 == v ? e0.v1 : e0.v0];
                const Vec3 o1 = m.positions[e1.v0 == v ? e1.v1 : e1.v0];
                return (6.0 * p + o0 + o1) / 8.0;
            }
            // Crease rule when child has 2 surviving sharp edges out of >= 3 parent sharp edges.
            if (r == VRule::Crease) {
                Vec3 sum{};
                int found = 0;
                for (Index i = v_edge_off[v]; i < v_edge_off[v + 1]; ++i) {
                    const Index eid = v_edges[i];
                    const Edge& ed = edges[eid];
                    const int side = (ed.v0 == v) ? 0 : 1;
                    const float cs = (ed.uses != 2) ? kInf : child_edge_s[eid][side];
                    if (is_sharp(cs)) {
                        sum += m.positions[ed.v0 == v ? ed.v1 : ed.v0];
                        ++found;
                    }
                }
                if (found == 2) return (6.0 * p + sum) / 8.0;
                return p;
            }
            return v_smooth;
        };

        const Vec3 p_pos = eval_rule(prule);
        if (prule == crule) {
            out.positions[v] = p_pos;
        } else {
            const Vec3 c_pos = eval_rule(crule);
            double w = 0.0;
            if (is_sharp(vs_parent) && !is_sharp(vs_child)) {
                w = static_cast<double>(vs_parent);
            } else if (decaying_cnt > 0) {
                w = decaying_sum / static_cast<double>(decaying_cnt);
            } else if (parent_sharp > 0) {
                w = parent_sharp_sum / static_cast<double>(parent_sharp);
            }
            w = std::clamp(w, 0.0, 1.0);
            out.positions[v] = c_pos * (1.0 - w) + p_pos * w;
        }

        if (use_w) {
            const double wp = (*w_in)[v];
            const double wf = w_fsum[v] / (use_nu ? std::max(1e-12, face_wsum[v]) : static_cast<double>(face_cnt[v]));
            const double wr = w_msum[v] / (use_nu ? std::max(1e-12, mid_wsum[v]) : n);
            double w_smooth = (wf + 2.0 * wr + (n - 3.0) * wp) / n;
            auto eval_w = [&](VRule r) -> double {
                if (r == VRule::Corner) return wp;
                if (r == VRule::Crease && parent_sharp == 2) {
                    const Edge& e0 = edges[sharp_pair[0]];
                    const Edge& e1 = edges[sharp_pair[1]];
                    const double wo0 = (*w_in)[e0.v0 == v ? e0.v1 : e0.v0];
                    const double wo1 = (*w_in)[e1.v0 == v ? e1.v1 : e1.v0];
                    return (6.0 * wp + wo0 + wo1) / 8.0;
                }
                return w_smooth;
            };
            const double pw = eval_w(prule);
            if (prule == crule) {
                (*w_out)[v] = pw;
            } else {
                const double cw = eval_w(crule);
                double b = 0.0;
                if (is_sharp(vs_parent) && !is_sharp(vs_child)) b = static_cast<double>(vs_parent);
                else if (decaying_cnt > 0) b = decaying_sum / static_cast<double>(decaying_cnt);
                else if (parent_sharp > 0) b = parent_sharp_sum / static_cast<double>(parent_sharp);
                b = std::clamp(b, 0.0, 1.0);
                (*w_out)[v] = cw * (1.0 - b) + pw * b;
            }
        }

        // Propagate non-topological vertex sharpness.
        const float orig_vs = m.vertex_crease(static_cast<Index>(v));
        const float next_vs = dec_sharpness(orig_vs);
        if (is_sharp(next_vs)) out.vertex_creases.push_back({static_cast<Index>(v), next_vs});
    }

    for (std::size_t f = 0; f < nf; ++f) out.positions[nv + ne + f] = face_pt[f];

    // Emit child faces: each k-gon -> k quads.
    const auto ebase = static_cast<Index>(nv);
    const auto fbase = static_cast<Index>(nv + ne);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            const Index prev = c == b ? e - 1 : c - 1;
            out.add_face({m.face_indices[c], ebase + corner_edge[c], fbase + static_cast<Index>(f),
                          ebase + corner_edge[prev]});
        }
    }

    // Emit propagated interior edge creases.
    for (std::size_t e = 0; e < ne; ++e) {
        const Edge& ed = edges[e];
        if (ed.uses != 2) continue;  // boundary creases are implicit from topology
        const Index mid_v = ebase + static_cast<Index>(e);
        if (is_sharp(child_edge_s[e][0])) out.edge_creases.push_back({edge_key(ed.v0, mid_v), child_edge_s[e][0]});
        if (is_sharp(child_edge_s[e][1])) out.edge_creases.push_back({edge_key(ed.v1, mid_v), child_edge_s[e][1]});
    }
    out.normalize_creases();

    // Propagate NURSS chord-length knot intervals to child half-edges & face spokes.
    if (use_nu && knots_out) {
        knots_out->clear();
        for (std::size_t e = 0; e < ne; ++e) {
            const Edge& ed = edges[e];
            const Index mid_v = ebase + static_cast<Index>(e);
            const double half_d = 0.5 * edge_d[e];
            (*knots_out)[edge_key(ed.v0, mid_v)] = half_d;
            (*knots_out)[edge_key(ed.v1, mid_v)] = half_d;
        }
        for (std::size_t f = 0; f < nf; ++f) {
            const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
            const Index fv = fbase + static_cast<Index>(f);
            const std::size_t k = e - b;
            for (Index c = b; c < e; ++c) {
                const Index eid = corner_edge[c];
                const Index mid_v = ebase + eid;
                double spoke_d = 0.5 * edge_d[eid];
                if (k == 4) {
                    const Index cp = (c == b) ? e - 1 : c - 1;
                    const Index cn = (c + 1 == e) ? b : c + 1;
                    spoke_d = 0.25 * (edge_d[corner_edge[cp]] + edge_d[corner_edge[cn]]);
                } else {
                    double s = 0.0;
                    for (Index j = b; j < e; ++j) s += edge_d[corner_edge[j]];
                    spoke_d = 0.5 * s / static_cast<double>(k);
                }
                (*knots_out)[edge_key(mid_v, fv)] = spoke_d;
            }
        }
    }

    if (!uv_in.corner_uv.empty()) {
        uv_out = subdivide_uv_step(m, uv_in, corner_face, corner_next, corner_edge, edge_sharp_vec, ne, opts.uv_rule,
                                   linear_step);
    }

    // Subdivide EV blend weights (Zhou-Boonstra-Kosinka 2023 Variant A).
    if (evw_in && evw_out && evw_in->size() == nv) {
        evw_out->assign(nv + ne + nf, 0.0);
        std::vector<double> f_evw(nf, 0.0);
        for (std::size_t f = 0; f < nf; ++f) {
            const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
            double s = 0.0;
            for (Index c = b; c < e; ++c) s += (*evw_in)[m.face_indices[c]];
            f_evw[f] = s / static_cast<double>(e - b);
            (*evw_out)[nv + ne + f] = f_evw[f];
        }
        std::vector<double> e_evw_sum(nv, 0.0), f_evw_sum(nv, 0.0);
        for (std::size_t e = 0; e < ne; ++e) {
            const Edge& ed = edges[e];
            const double mid = 0.5 * ((*evw_in)[ed.v0] + (*evw_in)[ed.v1]);
            if (linear_step || ed.uses != 2) {
                (*evw_out)[nv + e] = mid;
            } else {
                (*evw_out)[nv + e] = 0.25 * ((*evw_in)[ed.v0] + (*evw_in)[ed.v1] + f_evw[ed.f0] + f_evw[ed.f1]);
            }
            e_evw_sum[ed.v0] += mid;
            e_evw_sum[ed.v1] += mid;
        }
        for (Index c = 0; c < nc; ++c) f_evw_sum[m.face_indices[c]] += f_evw[corner_face[c]];
        for (std::size_t v = 0; v < nv; ++v) {
            if (linear_step || edge_cnt[v] == 0 || face_cnt[v] == 0) {
                (*evw_out)[v] = (*evw_in)[v];
            } else {
                const double dn = static_cast<double>(edge_cnt[v]);
                const double fv = f_evw_sum[v] / static_cast<double>(face_cnt[v]);
                const double rv = e_evw_sum[v] / dn;
                (*evw_out)[v] = std::clamp((fv + 2.0 * rv + (dn - 3.0) * (*evw_in)[v]) / dn, 0.0, 1.0);
            }
        }
    }

    // Subdivide crease-split per-corner normal field (Alexa-Boubekeur 2008 + Zhou-Boonstra-Kosinka 2023).
    if (cnorm_in && cnorm_out && cnorm_in->size() == nc) {
        std::vector<Vec3> fn_pt(nf);
        for (std::size_t f = 0; f < nf; ++f) {
            const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
            Vec3 s{};
            for (Index c = b; c < e; ++c) s += (*cnorm_in)[c];
            fn_pt[f] = normalize_or(s, Vec3{0, 0, 1});
        }
        // Build edge-corner lookup so opposite sides of a sharp crease stay decoupled while smooth edges blend.
        std::vector<Index> e_c0(ne, ~Index{0}), e_c1(ne, ~Index{0});
        for (Index c = 0; c < nc; ++c) {
            const Index eid = corner_edge[c];
            if (e_c0[eid] == ~Index{0}) e_c0[eid] = c;
            else if (e_c1[eid] == ~Index{0}) e_c1[eid] = c;
        }
        // Compute per-corner outgoing edge normal (split on hard crease sharpness >= 1.0, blended on 0 < s < 1).
        std::vector<Vec3> c_edge_n(nc);
        for (Index c = 0; c < nc; ++c) {
            const Index eid = corner_edge[c];
            const Edge& ed = edges[eid];
            const Vec3 n_a = (*cnorm_in)[c], n_b = (*cnorm_in)[corner_next[c]];
            const Vec3 n_split = normalize_or(n_a + n_b, n_a);
            if (linear_step || ed.uses != 2 || ed.sharpness >= 1.0f) {
                c_edge_n[c] = n_split;
            } else {
                const Index other_c = (e_c0[eid] == c) ? e_c1[eid] : e_c0[eid];
                Vec3 n_smooth = n_a + n_b + fn_pt[ed.f0] + fn_pt[ed.f1];
                if (other_c != ~Index{0}) {
                    const Vec3 oa = (*cnorm_in)[other_c], ob = (*cnorm_in)[corner_next[other_c]];
                    if (dot(oa, n_a) > 0.0 && dot(ob, n_b) > 0.0) {
                        n_smooth = (n_a + n_b + oa + ob) * 0.5 + fn_pt[ed.f0] + fn_pt[ed.f1];
                    }
                }
                n_smooth = normalize_or(n_smooth, n_split);
                if (is_sharp(ed.sharpness)) {
                    const double t = static_cast<double>(ed.sharpness);
                    c_edge_n[c] = normalize_or(n_smooth * (1.0 - t) + n_split * t, n_split);
                } else {
                    c_edge_n[c] = n_smooth;
                }
            }
        }
        // Group corners around each vertex into smooth sectors separated by sharp edges (sharpness >= 1.0).
        std::vector<Index> parent(nc);
        for (Index c = 0; c < nc; ++c) parent[c] = c;
        auto find_r = [&](Index x) {
            while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        };
        auto unite = [&](Index a, Index b) { a = find_r(a); b = find_r(b); if (a != b) parent[b] = a; };
        for (std::size_t e = 0; e < ne; ++e) {
            const Edge& ed = edges[e];
            if (ed.uses != 2 || ed.sharpness >= 1.0f || e_c0[e] == ~Index{0} || e_c1[e] == ~Index{0}) continue;
            const Index a = e_c0[e], b = e_c1[e];
            const Index an = corner_next[a], bn = corner_next[b];
            if (m.face_indices[a] == m.face_indices[bn]) unite(a, bn);
            if (m.face_indices[an] == m.face_indices[b]) unite(an, b);
        }
        std::vector<Vec3> sec_p(nc), sec_r(nc), sec_f(nc);
        std::vector<std::uint32_t> sec_cnt(nc, 0);
        for (Index c = 0; c < nc; ++c) {
            const Index r = find_r(c);
            sec_p[r] += (*cnorm_in)[c];
            sec_r[r] += (c_edge_n[c] + (*cnorm_in)[c]) * 0.5;
            sec_f[r] += fn_pt[corner_face[c]];
            ++sec_cnt[r];
        }
        std::vector<Vec3> c_vert_n(nc);
        for (Index c = 0; c < nc; ++c) {
            if (linear_step) {
                c_vert_n[c] = (*cnorm_in)[c];
                continue;
            }
            const Index r = find_r(c);
            const double k = static_cast<double>(std::max<std::uint32_t>(1, sec_cnt[r]));
            const Vec3 P_n = sec_p[r] / k;
            const Vec3 R_n = sec_r[r] / k;
            const Vec3 F_n = sec_f[r] / k;
            const Vec3 avg = (k >= 3.0) ? ((F_n + R_n * 2.0 + P_n * (k - 3.0)) / k) : ((P_n * 2.0 + R_n + F_n) * 0.25);
            c_vert_n[c] = normalize_or(avg, (*cnorm_in)[c]);
        }

        cnorm_out->clear();
        cnorm_out->reserve(out.corner_count());
        for (std::size_t f = 0; f < nf; ++f) {
            const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
            for (Index c = b; c < e; ++c) {
                const Index prev = c == b ? e - 1 : c - 1;
                cnorm_out->push_back(c_vert_n[c]);
                cnorm_out->push_back(c_edge_n[c]);
                cnorm_out->push_back(fn_pt[f]);
                cnorm_out->push_back(c_edge_n[prev]);
            }
        }
    }
}

// Evaluate exact Halstead-Kass-DeRose / OpenSubdiv limit positions and analytical
// limit tangent normals on an all-quad (or post-subdivision) mesh.
void evaluate_limit(Mesh& m, const SubdivideOptions& opts, std::vector<Vec3>* corner_normals_out) {
    const std::size_t nv = m.vertex_count(), nf = m.face_count(), nc = m.corner_count();
    if (nv == 0 || nf == 0) return;

    constexpr Index none = ~Index{0};
    std::vector<Index> corner_face(nc), next(nc), prev(nc);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            corner_face[c] = static_cast<Index>(f);
            next[c] = c + 1 == e ? b : c + 1;
            prev[c] = c == b ? e - 1 : c - 1;
        }
    }

    // Build half-edge twin map + per-edge sharpness.
    struct KC {
        std::uint64_t key;
        Index corner;
    };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(m.face_indices[c], m.face_indices[next[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });

    std::vector<Index> twin(nc, none);
    std::vector<float> corner_edge_sharp(nc, 0.0f);
    std::vector<std::uint8_t> corner_edge_boundary(nc, 0);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        while (j < nc && kc[j].key == kc[i].key) ++j;
        const std::size_t uses = j - i;
        const auto v0 = static_cast<Index>(kc[i].key >> 32);
        const auto v1 = static_cast<Index>(kc[i].key & 0xffffffffu);
        if (uses == 2 && m.face_indices[kc[i].corner] != m.face_indices[kc[i + 1].corner]) {
            const Index a = kc[i].corner, b = kc[i + 1].corner;
            twin[a] = b;
            twin[b] = a;
            const float s = m.edge_crease(v0, v1);
            corner_edge_sharp[a] = corner_edge_sharp[b] = s;
        } else {
            for (std::size_t k = i; k < j; ++k) {
                corner_edge_sharp[kc[k].corner] = kInf;
                corner_edge_boundary[kc[k].corner] = 1;
            }
        }
        i = j;
    }

    // Group corners by vertex.
    std::vector<Index> vc_off(nv + 1, 0);
    for (Index c = 0; c < nc; ++c) ++vc_off[m.face_indices[c] + 1];
    for (std::size_t v = 0; v < nv; ++v) vc_off[v + 1] += vc_off[v];
    std::vector<Index> vc(nc);
    {
        std::vector<Index> cur(vc_off.begin(), vc_off.end() - 1);
        for (Index c = 0; c < nc; ++c) vc[cur[m.face_indices[c]]++] = c;
    }

    // Face normals (for orienting cross products and fallback).
    std::vector<Vec3> f_area_vec(nf);
    for (std::size_t f = 0; f < nf; ++f) f_area_vec[f] = face_area_vector(m, f);

    const std::vector<Vec3> orig_pos = m.positions;
    if (corner_normals_out) corner_normals_out->assign(nc, Vec3{0, 0, 1});

    for (std::size_t v = 0; v < nv; ++v) {
        const Index cb = vc_off[v], ce = vc_off[v + 1];
        const std::size_t nfaces = ce - cb;
        if (nfaces == 0) continue;

        // Check whether all incident faces are quads and try to walk a clean manifold fan.
        bool all_quads = true;
        for (Index i = cb; i < ce; ++i) {
            if (m.face(corner_face[vc[i]]).size() != 4) {
                all_quads = false;
                break;
            }
        }

        // Collect unique incident edges & sharpness from outgoing/incoming corners.
        // A corner c in face f has outgoing edge c -> next[c] and incoming edge prev[c] -> c.
        // Build ordered fan(s) of corners around v by walking across non-boundary twins:
        // From corner c (outgoing e_out = c, incoming e_in = prev[c]), the CCW-next face across e_in is twin[prev[c]]?
        // Wait: in CCW face (v, e_out_v, opp_v, e_in_v), prev[c] is directed (e_in_v -> v),
        // so twin[prev[c]] is directed (v -> e_in_v), which is a corner AT v whose outgoing edge is e_in!
        // Walking c -> twin[prev[c]] visits incident faces in CCW order around v!
        std::vector<Index> fan;
        fan.reserve(nfaces);
        bool is_closed_fan = false;
        bool is_single_open_fan = false;
        if (all_quads) {
            // Find a boundary start corner (where twin[c] == none, i.e., outgoing edge is boundary),
            // or start at vc[cb] for a closed fan.
            Index start = none;
            int boundary_starts = 0;
            for (Index i = cb; i < ce; ++i) {
                const Index c = vc[i];
                if (twin[c] == none) {
                    start = c;
                    ++boundary_starts;
                }
            }
            if (boundary_starts == 0) {
                start = vc[cb];
                Index c = start;
                do {
                    fan.push_back(c);
                    const Index t = twin[prev[c]];
                    if (t == none || m.face_indices[t] != v) break;
                    c = t;
                } while (c != start && fan.size() <= nfaces);
                is_closed_fan = (c == start && fan.size() == nfaces);
            } else if (boundary_starts == 1) {
                // Start at the corner whose outgoing edge is the leading boundary edge, walk CCW via twin[prev[c]].
                Index c = start;
                while (c != none && fan.size() <= nfaces) {
                    fan.push_back(c);
                    const Index t = twin[prev[c]];
                    if (t == none || m.face_indices[t] != v) break;
                    c = t;
                }
                is_single_open_fan = (fan.size() == nfaces);
            }
        }

        const Vec3 P = orig_pos[v];
        float vs = m.vertex_crease(static_cast<Index>(v));
        if (opts.boundary == BoundaryRule::EdgeAndCorner && is_single_open_fan && nfaces == 1) vs = kInf;

        if (is_closed_fan && nfaces >= 3) {
            // Ordered CCW ring of edges E[i] = next[fan[i]] and opposite quad corners O[i] = next[next[fan[i]]].
            const std::size_t n = fan.size();
            std::vector<Vec3> E(n), O(n);
            std::vector<float> e_sharp(n);
            std::size_t sharp_cnt = 0;
            std::size_t sharp_idx[2] = {0, 0};
            double sharp_sum = 0.0;
            for (std::size_t i = 0; i < n; ++i) {
                const Index c = fan[i];
                E[i] = orig_pos[m.face_indices[next[c]]];
                O[i] = orig_pos[m.face_indices[next[next[c]]]];
                e_sharp[i] = corner_edge_sharp[c];
                if (is_sharp(e_sharp[i])) {
                    if (sharp_cnt < 2) sharp_idx[sharp_cnt] = i;
                    ++sharp_cnt;
                    sharp_sum += static_cast<double>(e_sharp[i]);
                }
            }

            // Smooth Halstead-Kass-DeRose limit position.
            Vec3 sumE{}, sumO{};
            for (std::size_t i = 0; i < n; ++i) {
                sumE += E[i];
                sumO += O[i];
            }
            const double dn = static_cast<double>(n);
            const Vec3 p_smooth = (P * (dn * dn) + sumE * 4.0 + sumO) / (dn * (dn + 5.0));

            if (opts.limit_snap) {
                if (is_sharp(vs) || sharp_cnt >= 3) {
                    const double w = std::clamp(
                        is_sharp(vs) ? static_cast<double>(vs) : (sharp_sum / static_cast<double>(sharp_cnt)), 0.0, 1.0);
                    m.positions[v] = p_smooth * (1.0 - w) + P * w;
                } else if (sharp_cnt == 2) {
                    const Vec3 p_crease = (P * 4.0 + E[sharp_idx[0]] + E[sharp_idx[1]]) / 6.0;
                    const double w = std::clamp(0.5 * sharp_sum, 0.0, 1.0);
                    m.positions[v] = p_smooth * (1.0 - w) + p_crease * w;
                } else {
                    m.positions[v] = p_smooth;
                }
            }

            if (corner_normals_out) {
                auto compute_smooth_normal = [&]() -> Vec3 {
                    Vec3 t1{}, t2{};
                    if (n == 4) {
                        t1 = (E[0] - E[2]) * 4.0 + (O[0] - O[1] - O[2] + O[3]);
                        t2 = (E[1] - E[3]) * 4.0 + (O[0] + O[1] - O[2] - O[3]);
                    } else {
                        const double theta = 2.0 * std::numbers::pi / dn;
                        const double cosT = std::cos(theta);
                        const double cosHalf = std::cos(0.5 * theta);
                        const double lam = (5.0 / 16.0) + (1.0 / 16.0) * (cosT + cosHalf * std::sqrt(2.0 * (9.0 + cosT)));
                        const double fscale = 1.0 / (4.0 * lam - 1.0);
                        std::vector<double> ew(n), fw(n);
                        for (std::size_t i = 0; i < n; ++i) {
                            const double ci = std::cos(static_cast<double>(i) * theta);
                            const double ci1 = std::cos(static_cast<double>(i + 1) * theta);
                            ew[i] = 4.0 * ci;
                            fw[i] = fscale * (ci + ci1);
                        }
                        for (std::size_t i = 0; i < n; ++i) {
                            t1 += E[i] * ew[i] + O[i] * fw[i];
                            const std::size_t prev_i = (i + n - 1) % n;
                            t2 += E[i] * ew[prev_i] + O[i] * fw[prev_i];
                        }
                    }
                    Vec3 avg_fn{};
                    for (Index c : fan) avg_fn += f_area_vec[corner_face[c]];
                    Vec3 nrm = normalize_or(cross(t1, t2), normalize_or(avg_fn, {0, 0, 1}));
                    if (dot(nrm, avg_fn) < 0.0) nrm = -nrm;
                    return nrm;
                };

                // Split sectors across hard interior creases (sharpness >= 1.0) or sharp corners.
                std::vector<std::size_t> hard_edges;
                for (std::size_t i = 0; i < n; ++i)
                    if (e_sharp[i] >= 1.0f) hard_edges.push_back(i);

                if (hard_edges.size() < 2 && vs < 1.0f) {
                    const Vec3 nrm = compute_smooth_normal();
                    for (Index c : fan) (*corner_normals_out)[c] = nrm;
                } else if (hard_edges.size() == 2 && vs < 1.0f) {
                    // Two sectors separated by a hard crease chain: exact Biermann crease tangent mask per sector.
                    for (int sec = 0; sec < 2; ++sec) {
                        const std::size_t lead = hard_edges[static_cast<std::size_t>(sec)];
                        const std::size_t trail = hard_edges[static_cast<std::size_t>(1 - sec)];
                        const std::size_t k = (trail + n - lead) % n;  // number of faces in this sector
                        const Vec3 t1 = (E[lead] - E[trail]) * 0.5;
                        Vec3 t2{};
                        if (k == 1) {
                            t2 = (E[lead] + E[trail]) * 3.0 - P * 6.0;
                        } else if (k == 2) {
                            const std::size_t mid = (lead + 1) % n;
                            t2 = P * (-4.0 / 6.0) + E[lead] * (-1.0 / 6.0) + E[trail] * (-1.0 / 6.0) +
                                 E[mid] * (4.0 / 6.0) + (O[lead] + O[mid]) * (1.0 / 6.0);
                        } else {
                            const double dk = static_cast<double>(k);
                            const double theta = std::numbers::pi / dk;
                            const double cosT = std::cos(theta), sinT = std::sin(theta);
                            const double denom = 1.0 / (dk * (3.0 + cosT));
                            const double R = (cosT + 1.0) / sinT;
                            t2 = P * (4.0 * R * (cosT - 1.0) * denom) +
                                 (E[lead] + E[trail]) * (-R * (1.0 + 2.0 * cosT) * denom) +
                                 O[lead] * (sinT * denom);
                            double sinI = 0.0, sinIp1 = sinT;
                            for (std::size_t i = 1; i < k; ++i) {
                                sinI = sinIp1;
                                sinIp1 = std::sin(static_cast<double>(i + 1) * theta);
                                const std::size_t idx = (lead + i) % n;
                                t2 += E[idx] * (4.0 * sinI * denom) + O[idx] * ((sinI + sinIp1) * denom);
                            }
                        }
                        Vec3 avg_fn{};
                        for (std::size_t i = 0; i < k; ++i) avg_fn += f_area_vec[corner_face[fan[(lead + i) % n]]];
                        Vec3 nrm = normalize_or(cross(t1, t2), normalize_or(avg_fn, {0, 0, 1}));
                        if (dot(nrm, avg_fn) < 0.0) nrm = -nrm;
                        for (std::size_t i = 0; i < k; ++i) (*corner_normals_out)[fan[(lead + i) % n]] = nrm;
                    }
                } else {
                    // Corner with >= 3 hard creases (or pinned corner vertex): sector area-weighted normals.
                    if (hard_edges.empty()) {
                        Vec3 avg_fn{};
                        for (Index c : fan) avg_fn += f_area_vec[corner_face[c]];
                        const Vec3 nrm = normalize_or(avg_fn, {0, 0, 1});
                        for (Index c : fan) (*corner_normals_out)[c] = nrm;
                    } else {
                        for (std::size_t h = 0; h < hard_edges.size(); ++h) {
                            const std::size_t lead = hard_edges[h];
                            const std::size_t trail = hard_edges[(h + 1) % hard_edges.size()];
                            const std::size_t k = (trail + n - lead) % n;
                            const std::size_t count = k == 0 ? n : k;
                            Vec3 avg_fn{};
                            for (std::size_t i = 0; i < count; ++i)
                                avg_fn += f_area_vec[corner_face[fan[(lead + i) % n]]];
                            const Vec3 nrm = normalize_or(avg_fn, {0, 0, 1});
                            for (std::size_t i = 0; i < count; ++i) (*corner_normals_out)[fan[(lead + i) % n]] = nrm;
                        }
                    }
                }
            }
        } else if (is_single_open_fan) {
            // Open boundary vertex: fan[0] has leading boundary edge, fan.back() has trailing boundary edge prev[fan.back()].
            const std::size_t k = fan.size();
            const Vec3 E_lead = orig_pos[m.face_indices[next[fan.front()]]];
            const Vec3 E_trail = orig_pos[m.face_indices[prev[fan.back()]]];
            if (opts.limit_snap) {
                if (is_sharp(vs) || (opts.boundary == BoundaryRule::EdgeAndCorner && k == 1)) {
                    m.positions[v] = P;
                } else {
                    m.positions[v] = (P * 4.0 + E_lead + E_trail) / 6.0;
                }
            }
            if (corner_normals_out) {
                const Vec3 t1 = (E_lead - E_trail) * 0.5;
                Vec3 t2{};
                if (k == 1) {
                    t2 = (E_lead + E_trail) * 3.0 - P * 6.0;
                } else if (k == 2) {
                    const Vec3 E1 = orig_pos[m.face_indices[next[fan[1]]]];
                    const Vec3 O0 = orig_pos[m.face_indices[next[next[fan[0]]]]];
                    const Vec3 O1 = orig_pos[m.face_indices[next[next[fan[1]]]]];
                    t2 = P * (-4.0 / 6.0) + (E_lead + E_trail) * (-1.0 / 6.0) + E1 * (4.0 / 6.0) + (O0 + O1) * (1.0 / 6.0);
                } else {
                    const double dk = static_cast<double>(k);
                    const double theta = std::numbers::pi / dk;
                    const double cosT = std::cos(theta), sinT = std::sin(theta);
                    const double denom = 1.0 / (dk * (3.0 + cosT));
                    const double R = (cosT + 1.0) / sinT;
                    const Vec3 O0 = orig_pos[m.face_indices[next[next[fan[0]]]]];
                    t2 = P * (4.0 * R * (cosT - 1.0) * denom) + (E_lead + E_trail) * (-R * (1.0 + 2.0 * cosT) * denom) +
                         O0 * (sinT * denom);
                    double sinI = 0.0, sinIp1 = sinT;
                    for (std::size_t i = 1; i < k; ++i) {
                        sinI = sinIp1;
                        sinIp1 = std::sin(static_cast<double>(i + 1) * theta);
                        const Vec3 Ei = orig_pos[m.face_indices[next[fan[i]]]];
                        const Vec3 Oi = orig_pos[m.face_indices[next[next[fan[i]]]]];
                        t2 += Ei * (4.0 * sinI * denom) + Oi * ((sinI + sinIp1) * denom);
                    }
                }
                Vec3 avg_fn{};
                for (Index c : fan) avg_fn += f_area_vec[corner_face[c]];
                Vec3 nrm = normalize_or(cross(t1, t2), normalize_or(avg_fn, {0, 0, 1}));
                if (dot(nrm, avg_fn) < 0.0) nrm = -nrm;
                for (Index c : fan) (*corner_normals_out)[c] = nrm;
            }
        } else {
            // Non-quad (levels == 0) or non-manifold fallback: leave position, area-weighted normal.
            if (corner_normals_out) {
                Vec3 avg_fn{};
                for (Index i = cb; i < ce; ++i) avg_fn += f_area_vec[corner_face[vc[i]]];
                const Vec3 nrm = normalize_or(avg_fn, {0, 0, 1});
                for (Index i = cb; i < ce; ++i) (*corner_normals_out)[vc[i]] = nrm;
            }
        }
    }
}

// Karčiauskas-Peters / Halstead-Kass-DeRose quadratic osculating G2 EV completion:
// Around every uncreased interior extraordinary vertex (valence != 4), fits the best-fit
// G2 quadratic surface z(u,v) = 0.5*(a*u^2 + 2*b*u*v + c*v^2) in the EV's exact eigen-tangent
// frame anchored by its surrounding regular C2 neighborhood, and projects the EV & its 1-ring
// smoothly onto that G2 paraboloid to eliminate geometric curvature spikes & pinching!
void apply_g2_ev_fairing(Mesh& m, const SubdivideOptions& opts) {
    const std::size_t nv = m.vertex_count(), nf = m.face_count(), nc = m.corner_count();
    if (nv == 0 || nf == 0) return;

    constexpr Index none = ~Index{0};
    std::vector<Index> cface(nc), cnext(nc), cprev(nc);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            cface[c] = static_cast<Index>(f);
            cnext[c] = c + 1 == e ? b : c + 1;
            cprev[c] = c == b ? e - 1 : c - 1;
        }
    }
    struct KC { std::uint64_t key; Index corner; };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(m.face_indices[c], m.face_indices[cnext[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });

    std::vector<Index> twin(nc, none);
    std::vector<std::uint8_t> v_locked(nv, 0);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        while (j < nc && kc[j].key == kc[i].key) ++j;
        const auto v0 = static_cast<Index>(kc[i].key >> 32);
        const auto v1 = static_cast<Index>(kc[i].key & 0xffffffffu);
        if (j - i == 2 && m.face_indices[kc[i].corner] != m.face_indices[kc[i + 1].corner]) {
            twin[kc[i].corner] = kc[i + 1].corner;
            twin[kc[i + 1].corner] = kc[i].corner;
            if (m.edge_crease(v0, v1) >= 1.0f) { v_locked[v0] = 1; v_locked[v1] = 1; }
        } else {
            v_locked[v0] = 1;
            v_locked[v1] = 1;
        }
        i = j;
    }
    for (std::size_t v = 0; v < nv; ++v)
        if (m.vertex_crease(static_cast<Index>(v)) >= 1.0f) v_locked[v] = 1;

    std::vector<Index> vc_off(nv + 1, 0);
    for (Index c = 0; c < nc; ++c) ++vc_off[m.face_indices[c] + 1];
    for (std::size_t v = 0; v < nv; ++v) vc_off[v + 1] += vc_off[v];
    std::vector<Index> vc(nc);
    {
        std::vector<Index> cur(vc_off.begin(), vc_off.end() - 1);
        for (Index c = 0; c < nc; ++c) vc[cur[m.face_indices[c]]++] = c;
    }

    const double blend = std::clamp(opts.g2_weight, 0.0, 1.0);
    for (int iter = 0; iter < 2; ++iter) {
        const std::vector<Vec3> pos = m.positions;
        std::vector<Vec3> delta_sum(nv, Vec3{0, 0, 0});
        std::vector<double> delta_w(nv, 0.0);

        for (std::size_t v = 0; v < nv; ++v) {
            if (v_locked[v]) continue;
            const Index cb = vc_off[v], ce = vc_off[v + 1];
            const std::size_t n = ce - cb;
            if (n < 3 || n == 4) continue;  // only extraordinary interior vertices!

            bool all_quads = true;
            for (Index i = cb; i < ce; ++i)
                if (m.face(cface[vc[i]]).size() != 4) { all_quads = false; break; }
            if (!all_quads) continue;

            // Walk closed CCW fan around v.
            std::vector<Index> fan;
            fan.reserve(n);
            Index start = vc[cb], c = start;
            do {
                fan.push_back(c);
                const Index t = twin[cprev[c]];
                if (t == none || m.face_indices[t] != v) break;
                c = t;
            } while (c != start && fan.size() <= n);
            if (c != start || fan.size() != n) continue;

            std::vector<Index> e_idx(n), o_idx(n);
            Vec3 sumE{}, sumO{}, avg_fn{};
            for (std::size_t i = 0; i < n; ++i) {
                e_idx[i] = m.face_indices[cnext[fan[i]]];
                o_idx[i] = m.face_indices[cnext[cnext[fan[i]]]];
                sumE += pos[e_idx[i]];
                sumO += pos[o_idx[i]];
                avg_fn += face_area_vector(m, cface[fan[i]]);
            }
            const double dn = static_cast<double>(n);
            const Vec3 P0 = (pos[v] * (dn * dn) + sumE * 4.0 + sumO) / (dn * (dn + 5.0));
            const Vec3 N = normalize_or(avg_fn, Vec3{0, 0, 1});
            const Vec3 T1 = normalize_or(cross(N, std::abs(N.z) < 0.9 ? Vec3{0, 0, 1} : Vec3{1, 0, 0}), Vec3{1, 0, 0});
            const Vec3 T2 = cross(N, T1);

            // Fit quadratic osculating paraboloid z(u,v) = 0.5*a*u^2 + b*u*v + 0.5*c*v^2 via weighted 3x3 normal equations.
            // Outer quad corners O_i carry weight 1.0 (regular C2 region); inner 1-ring E_i carry weight 0.30.
            double A[3][3] = {{1e-10, 0, 0}, {0, 1e-10, 0}, {0, 0, 1e-10}};
            double rhs[3] = {0, 0, 0};
            auto add_sample = [&](Vec3 q, double w) {
                const Vec3 d = q - P0;
                const double u = dot(d, T1), vv = dot(d, T2), z = dot(d, N);
                const double phi[3] = {0.5 * u * u, u * vv, 0.5 * vv * vv};
                for (int r = 0; r < 3; ++r) {
                    rhs[r] += w * phi[r] * z;
                    for (int k = 0; k < 3; ++k) A[r][k] += w * phi[r] * phi[k];
                }
            };
            for (std::size_t i = 0; i < n; ++i) {
                add_sample(pos[o_idx[i]], 1.0);
                add_sample(pos[e_idx[i]], 0.30);
            }
            // Solve 3x3 linear system via Cramer's rule.
            auto det3 = [](const double M[3][3]) {
                return M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
                     - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                     + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]);
            };
            const double D = det3(A);
            if (std::abs(D) < 1e-24) continue;
            double sol[3] = {0, 0, 0};
            for (int col = 0; col < 3; ++col) {
                double B[3][3];
                for (int r = 0; r < 3; ++r)
                    for (int k = 0; k < 3; ++k) B[r][k] = (k == col) ? rhs[r] : A[r][k];
                sol[col] = det3(B) / D;
            }
            const double a = sol[0], b = sol[1], cc = sol[2];

            // Pull EV toward exact limit P0 and pull uncreased 1-ring neighbors E_i along N onto the G2 paraboloid!
            delta_sum[v] += (P0 - pos[v]) * blend;
            delta_w[v] += 1.0;
            for (std::size_t i = 0; i < n; ++i) {
                const Index ei = e_idx[i];
                if (v_locked[ei]) continue;
                const Vec3 d = pos[ei] - P0;
                const double u = dot(d, T1), vv = dot(d, T2), z = dot(d, N);
                const double z_target = 0.5 * a * u * u + b * u * vv + 0.5 * cc * vv * vv;
                const double dz = std::clamp(z_target - z, -0.25 * length(d), 0.25 * length(d));
                delta_sum[ei] += N * (dz * blend);
                delta_w[ei] += 1.0;
            }
        }
        for (std::size_t v = 0; v < nv; ++v) {
            if (delta_w[v] > 0.0) m.positions[v] += delta_sum[v] / delta_w[v];
        }
    }
}

// Feature-adaptive watertight flat coarsening (Nießner et al. 2012 inspired):
// Merges strictly coplanar 2x2 child quad blocks back into single quads (and emits
// crack-free 3..5 triangle fans on flat faces bordering curved fillets -> 0 T-junctions!).
void coarsen_flat_blocks(const Mesh& parent_m, SubdivideResult& res, double angle_deg) {
    const std::size_t nf_p = parent_m.face_count();
    const std::size_t nv_c = res.mesh.vertex_count();
    if (nf_p == 0 || nv_c == 0) return;

    const double cos_tol = std::cos(std::clamp(angle_deg, 0.05, 20.0) * std::numbers::pi / 180.0);
    const bool has_uv = res.uv.size() == res.mesh.corner_count();
    const bool has_n = res.corner_normals.size() == res.mesh.corner_count();

    auto uv_eq = [&](std::size_t ca, std::size_t cb) {
        if (!has_uv) return true;
        return std::abs(res.uv[ca][0] - res.uv[cb][0]) < 1e-4f &&
               std::abs(res.uv[ca][1] - res.uv[cb][1]) < 1e-4f;
    };

    std::vector<std::uint8_t> is_flat(nf_p, 0);
    std::vector<Vec3> pnorm(nf_p, Vec3{0, 0, 1});
    // Track incident parent faces for every midpoint vertex `e_s`.
    std::vector<std::pair<Index, Index>> ev_faces(nv_c, {~Index{0}, ~Index{0}});
    std::vector<std::uint8_t> ev_uses(nv_c, 0);
    std::vector<std::pair<Index, Index>> ev_ends(nv_c, {0, 0});

    for (std::size_t pf = 0; pf < nf_p; ++pf) {
        const Index pb = parent_m.face_offsets[pf], pe = parent_m.face_offsets[pf + 1];
        const std::size_t k = pe - pb;
        // Record midpoint vertex adjacency for all parent faces so non-quads safely block edge collapse.
        for (std::size_t s = 0; s < k; ++s) {
            const std::size_t cf = static_cast<std::size_t>(pb) + s;
            const auto fv = res.mesh.face(cf);
            const auto fv_next = res.mesh.face(static_cast<std::size_t>(pb) + ((s + 1) % k));
            const Index ev = fv[1];
            if (ev_uses[ev] == 0) {
                ev_faces[ev].first = static_cast<Index>(pf);
                ev_ends[ev] = {fv[0], fv_next[0]};
            } else if (ev_uses[ev] == 1) {
                ev_faces[ev].second = static_cast<Index>(pf);
            }
            if (ev_uses[ev] < 3) ++ev_uses[ev];
        }
        if (k != 4) continue;
        Vec3 n0 = face_normal(res.mesh, pb);
        if (length(n0) < 0.5) continue;
        bool ok = true;
        Vec3 nsum = n0;
        const std::size_t c0_fv = res.mesh.face_offsets[pb] + 2;
        for (std::size_t s = 1; s < 4; ++s) {
            const Vec3 ns = face_normal(res.mesh, pb + s);
            if (dot(n0, ns) < cos_tol) { ok = false; break; }
            nsum += ns;
            if (!uv_eq(c0_fv, res.mesh.face_offsets[pb + s] + 2)) { ok = false; break; }
        }
        if (!ok) continue;
        const Vec3 nmean = normalize_or(nsum, n0);
        // Check coplanarity of corners & center and absence of interior creases.
        const Index fv_center = res.mesh.face(pb)[2];
        const Vec3 pc = res.mesh.positions[fv_center];
        double span = 0.0;
        for (std::size_t s = 0; s < 4; ++s) {
            const auto q = res.mesh.face(pb + s);
            if (res.mesh.edge_crease(q[1], fv_center) > 0.0f) { ok = false; break; }
            span = std::max(span, length(res.mesh.positions[q[0]] - pc));
        }
        if (!ok || span < 1e-9) continue;
        for (std::size_t s = 0; s < 4; ++s) {
            const auto q = res.mesh.face(pb + s);
            if (std::abs(dot(res.mesh.positions[q[0]] - pc, nmean)) > 2e-4 * span) { ok = false; break; }
            if (std::abs(dot(res.mesh.positions[q[1]] - pc, nmean)) > 2e-4 * span) { ok = false; break; }
        }
        if (ok) {
            is_flat[pf] = 1;
            pnorm[pf] = nmean;
        }
    }

    std::vector<std::uint8_t> collapse_ev(nv_c, 0);
    for (std::size_t ev = 0; ev < nv_c; ++ev) {
        if (ev_uses[ev] != 2) continue;
        const Index f0 = ev_faces[ev].first, f1 = ev_faces[ev].second;
        if (!is_flat[f0] || !is_flat[f1]) continue;
        if (dot(pnorm[f0], pnorm[f1]) < cos_tol) continue;
        const Index a = ev_ends[ev].first, b = ev_ends[ev].second;
        if (res.mesh.edge_crease(a, static_cast<Index>(ev)) > 0.0f ||
            res.mesh.edge_crease(b, static_cast<Index>(ev)) > 0.0f) continue;
        const Vec3 pa = res.mesh.positions[a], pb = res.mesh.positions[b], pe = res.mesh.positions[ev];
        const double ab = length(pb - pa);
        if (ab < 1e-12) continue;
        const Vec3 dir = (pb - pa) / ab;
        const double proj = dot(pe - pa, dir);
        const Vec3 perp = (pe - pa) - dir * proj;
        if (length(perp) > 2e-4 * ab) continue;
        collapse_ev[ev] = 1;
    }

    Mesh nm;
    nm.positions = res.mesh.positions;
    nm.edge_creases = res.mesh.edge_creases;
    nm.vertex_creases = res.mesh.vertex_creases;
    std::vector<std::array<float, 2>> nuv;
    std::vector<Vec3> nn;
    if (has_uv) nuv.reserve(res.uv.size());
    if (has_n) nn.reserve(res.corner_normals.size());

    auto copy_child = [&](std::size_t cf) {
        const Index cb = res.mesh.face_offsets[cf], ce = res.mesh.face_offsets[cf + 1];
        nm.add_face(std::span<const Index>(res.mesh.face_indices.data() + cb, ce - cb));
        if (has_uv) for (Index c = cb; c < ce; ++c) nuv.push_back(res.uv[c]);
        if (has_n) for (Index c = cb; c < ce; ++c) nn.push_back(res.corner_normals[c]);
    };

    for (std::size_t pf = 0; pf < nf_p; ++pf) {
        const Index pb = parent_m.face_offsets[pf], pe = parent_m.face_offsets[pf + 1];
        const std::size_t k = pe - pb;
        if (!is_flat[pf]) {
            for (std::size_t s = 0; s < k; ++s) copy_child(static_cast<std::size_t>(pb) + s);
            continue;
        }
        // Exact non-degenerate watertight transition table by 4-bit split mask (zero collinear triangles!).
        struct CRef { Index v; std::size_t c; };
        CRef V[4], E[4];
        bool sp[4];
        int nsp = 0;
        for (std::size_t s = 0; s < 4; ++s) {
            const std::size_t cf = static_cast<std::size_t>(pb) + s;
            const Index cb = res.mesh.face_offsets[cf];
            V[s] = {res.mesh.face_indices[cb], cb};
            E[s] = {res.mesh.face_indices[cb + 1], cb + 1};
            sp[s] = !collapse_ev[E[s].v];
            if (sp[s]) ++nsp;
        }
        auto emit_tri = [&](CRef a, CRef b, CRef c) {
            nm.add_face({a.v, b.v, c.v});
            if (has_uv) { nuv.push_back(res.uv[a.c]); nuv.push_back(res.uv[b.c]); nuv.push_back(res.uv[c.c]); }
            if (has_n) { nn.push_back(res.corner_normals[a.c]); nn.push_back(res.corner_normals[b.c]); nn.push_back(res.corner_normals[c.c]); }
        };
        auto emit_quad = [&](CRef a, CRef b, CRef c, CRef d) {
            nm.add_face({a.v, b.v, c.v, d.v});
            if (has_uv) { nuv.push_back(res.uv[a.c]); nuv.push_back(res.uv[b.c]); nuv.push_back(res.uv[c.c]); nuv.push_back(res.uv[d.c]); }
            if (has_n) { nn.push_back(res.corner_normals[a.c]); nn.push_back(res.corner_normals[b.c]); nn.push_back(res.corner_normals[c.c]); nn.push_back(res.corner_normals[d.c]); }
        };
        if (nsp == 0) {
            emit_quad(V[0], V[1], V[2], V[3]);
        } else if (nsp == 4) {
            for (std::size_t s = 0; s < 4; ++s) copy_child(static_cast<std::size_t>(pb) + s);
        } else if (nsp == 1) {
            int s = 0; while (!sp[s]) ++s;
            const int s1 = (s + 1) & 3, s2 = (s + 2) & 3, s3 = (s + 3) & 3;
            emit_tri(V[s3], V[s], E[s]);
            emit_tri(V[s3], E[s], V[s1]);
            emit_tri(V[s3], V[s1], V[s2]);
        } else if (nsp == 2 && sp[0] == sp[2]) {
            const int s = sp[0] ? 0 : 1;
            const int s1 = (s + 1) & 3, s2 = (s + 2) & 3, s3 = (s + 3) & 3;
            emit_quad(V[s], E[s], E[s2], V[s3]);
            emit_quad(E[s], V[s1], V[s2], E[s2]);
        } else if (nsp == 2) {
            int s = 0; while (!(sp[s] && sp[(s + 1) & 3])) ++s;
            const int s1 = (s + 1) & 3, s2 = (s + 2) & 3, s3 = (s + 3) & 3;
            emit_tri(V[s3], V[s], E[s]);
            emit_tri(V[s3], E[s], V[s1]);
            emit_tri(V[s3], V[s1], E[s1]);
            emit_tri(V[s3], E[s1], V[s2]);
        } else {  // nsp == 3: rotate so the single unsplit side is s3 -> s0
            int u = 0; while (sp[u]) ++u;
            const int s0 = (u + 1) & 3, s1 = (u + 2) & 3, s2 = (u + 3) & 3, s3 = u;
            emit_tri(V[s3], V[s0], E[s0]);
            emit_tri(V[s3], E[s0], E[s2]);
            emit_tri(E[s0], V[s1], E[s1]);
            emit_tri(E[s0], E[s1], E[s2]);
            emit_tri(E[s1], V[s2], E[s2]);
        }
    }

    // Compact unreferenced vertices and remap creases cleanly.
    std::vector<Index> remap(nm.positions.size(), ~Index{0});
    std::vector<Vec3> compact_pos;
    compact_pos.reserve(nm.positions.size());
    for (Index& v : nm.face_indices) {
        if (remap[v] == ~Index{0}) {
            remap[v] = static_cast<Index>(compact_pos.size());
            compact_pos.push_back(nm.positions[v]);
        }
        v = remap[v];
    }
    nm.positions = std::move(compact_pos);
    std::vector<EdgeCrease> kept_ec;
    for (const EdgeCrease& ec : nm.edge_creases) {
        const Index a = remap[static_cast<Index>(ec.key >> 32)];
        const Index b = remap[static_cast<Index>(ec.key & 0xffffffffu)];
        if (a != ~Index{0} && b != ~Index{0} && a != b) kept_ec.push_back({edge_key(a, b), ec.sharpness});
    }
    nm.edge_creases = std::move(kept_ec);
    std::vector<VertexCrease> kept_vc;
    for (const VertexCrease& vc : nm.vertex_creases) {
        if (vc.vertex < remap.size() && remap[vc.vertex] != ~Index{0})
            kept_vc.push_back({remap[vc.vertex], vc.sharpness});
    }
    nm.vertex_creases = std::move(kept_vc);
    nm.normalize_creases();

    res.mesh = std::move(nm);
    if (has_uv) res.uv = std::move(nuv);
    if (has_n) res.corner_normals = std::move(nn);
}

}  // namespace

SubdivideResult catmull_clark_full(const Mesh& m, std::span<const std::array<float, 2>> corner_uv,
                                   const SubdivideOptions& opts) {
    SubdivideResult res;
    res.mesh = m;
    res.mesh.normalize_creases();

    // 1. Fair Anti-Ringing Weighted Progressive Iterative Interpolation (Halstead-Kass-DeRose 1993 / WH-PIA 2024):
    //    iteratively pre-compensate control cage vertices so curved walls, cylinders, bores, and domes
    //    pass through their exact authored dimensions (zero radial/bore shrinkage), while automatically
    //    attenuating W-PIA on uncreased high-dihedral (>= 50 deg) bends to prevent cubic Gibbs ringing
    //    (volcano lips on flat caps) and locking flat-plane vertices so mating bases stay dead-flat.
    if (opts.interpolate_iters > 0 && (opts.levels > 0 || opts.limit_snap) && res.mesh.vertex_count() > 0) {
        const std::size_t nv0 = res.mesh.vertex_count();
        const std::vector<Vec3> target = res.mesh.positions;

        // Compute per-vertex max incident edge length, max uncreased dihedral angle (radians),
        // and any planar constraint normal from incident creased edges (keeps creased base/cap planes exact!).
        std::vector<double> max_elen(nv0, 0.0);
        std::vector<double> max_uncreased_ang(nv0, 0.0);
        std::vector<Vec3> crease_e0(nv0), crease_e1(nv0);
        std::vector<int> crease_cnt(nv0, 0);
        const std::size_t nf0 = res.mesh.face_count();
        std::vector<Vec3> fn0(nf0);
        for (std::size_t f = 0; f < nf0; ++f) fn0[f] = face_normal(res.mesh, f);

        const auto hes0 = sorted_half_edges(res.mesh);
        for_each_edge_group(std::span<const HalfEdge>(hes0), [&](std::span<const HalfEdge> g) {
            const Index a = g[0].lo(), b = g[0].hi();
            const Vec3 ev = target[b] - target[a];
            const double len = length(ev);
            max_elen[a] = std::max(max_elen[a], len);
            max_elen[b] = std::max(max_elen[b], len);
            if (g.size() == 2) {
                const float cs = res.mesh.edge_crease(a, b);
                if (cs < 1.0f) {
                    const double ang = std::acos(std::clamp(dot(fn0[g[0].face], fn0[g[1].face]), -1.0, 1.0));
                    max_uncreased_ang[a] = std::max(max_uncreased_ang[a], ang);
                    max_uncreased_ang[b] = std::max(max_uncreased_ang[b], ang);
                } else if (len > 1e-12) {
                    const Vec3 dir = ev / len;
                    if (crease_cnt[a] == 0) crease_e0[a] = dir;
                    else if (crease_cnt[a] == 1) crease_e1[a] = dir;
                    ++crease_cnt[a];
                    if (crease_cnt[b] == 0) crease_e0[b] = -dir;
                    else if (crease_cnt[b] == 1) crease_e1[b] = -dir;
                    ++crease_cnt[b];
                }
            }
        });

        // Planar crease normal (if >=2 incident creased edges lie in a plane, lock W-PIA normal to it —
        // handles both corners and collinear mid-edges on creased base/cap planes!).
        std::vector<Vec3> lock_n(nv0);
        std::vector<std::uint8_t> has_lock_n(nv0, 0);
        for (std::size_t v = 0; v < nv0; ++v) {
            if (crease_cnt[v] == 2) {
                const Vec3 cr = cross(crease_e0[v], crease_e1[v]);
                if (length(cr) > 1e-4) {
                    lock_n[v] = normalize_or(cr, {0, 0, 0});
                    has_lock_n[v] = 1;
                }
            }
        }
        // Fallback for collinear creased edges (e.g. subdivided straight flange/base rim):
        // find an incident face whose normal is perpendicular to the crease direction and has a creased interior partner.
        for (std::size_t f = 0; f < nf0; ++f) {
            const auto fv = res.mesh.face(f);
            for (Index v : fv) {
                if (has_lock_n[v] || crease_cnt[v] < 2) continue;
                if (std::abs(dot(fn0[f], crease_e0[v])) < 1e-3 && std::abs(dot(fn0[f], crease_e1[v])) < 1e-3) {
                    // Prefer the planar cap/base face (most aligned with a cardinal axis or shared by 2 creased edges).
                    int face_creased = 0;
                    for (std::size_t i = 0; i < fv.size(); ++i)
                        if (res.mesh.edge_crease(fv[i], fv[(i + 1) % fv.size()]) >= 1.0f) ++face_creased;
                    if (face_creased >= 2) {
                        lock_n[v] = fn0[f];
                        has_lock_n[v] = 1;
                    }
                }
            }
        }

        // Fair attenuation per vertex: 1.0 for smooth curved seams (<= 45 deg), smoothly fading to 0.0
        // at uncreased sharp bends (>= 70 deg) so rounded shoulders never develop Gibbs overshoot.
        constexpr double kLoRad = 45.0 * std::numbers::pi / 180.0;
        constexpr double kHiRad = 70.0 * std::numbers::pi / 180.0;
        std::vector<double> fair_w(nv0, 1.0);
        for (std::size_t v = 0; v < nv0; ++v) {
            const double a = max_uncreased_ang[v];
            if (a > kLoRad) {
                const double t = std::clamp((kHiRad - a) / (kHiRad - kLoRad), 0.0, 1.0);
                fair_w[v] = t * t * (3.0 - 2.0 * t);
            }
        }

        SubdivideOptions probe_opt = opts;
        probe_opt.interpolate_iters = 0;
        probe_opt.compute_limit_normals = false;
        probe_opt.subdiv_normals = 0;
        if (probe_opt.levels > 2) {
            probe_opt.levels = 2;
            probe_opt.limit_snap = true;
        }
        const double omega = std::clamp(opts.interpolate_weight, 0.3, 1.8);
        for (std::uint32_t it = 0; it < opts.interpolate_iters; ++it) {
            const Mesh probe = catmull_clark_full(res.mesh, {}, probe_opt).mesh;
            if (probe.vertex_count() < nv0) break;
            double max_err = 0.0;
            for (std::size_t v = 0; v < nv0; ++v) {
                if (fair_w[v] <= 1e-6) continue;
                Vec3 delta = (target[v] - probe.positions[v]) * fair_w[v];
                if (has_lock_n[v]) delta -= lock_n[v] * dot(delta, lock_n[v]);
                const double dlen = length(delta);
                max_err = std::max(max_err, dlen);
                const double cap = 0.35 * max_elen[v];
                if (cap > 1e-12 && dlen > cap) delta = delta * (cap / dlen);
                res.mesh.positions[v] += delta * omega;
            }
            if (max_err < 1e-6) break;
        }
    }

    UvChannel uv = build_uv_channel(res.mesh, corner_uv);
    std::vector<Vec3> sub_cnorm;
    std::vector<double> evw;
    const bool do_subdiv_normals = (opts.subdiv_normals > 0);
    if (do_subdiv_normals) {
        init_subdiv_normals(res.mesh, sub_cnorm, evw);
    }

    // Initialize NURSS chord-length knot intervals (clamped to [0.02*median, 40*median] for robustness).
    std::map<std::uint64_t, double> knots;
    if (opts.non_uniform && res.mesh.face_count() > 0) {
        std::vector<double> lens;
        const auto hes_k = sorted_half_edges(res.mesh);
        for_each_edge_group(std::span<const HalfEdge>(hes_k), [&](std::span<const HalfEdge> g) {
            const Index a = g[0].lo(), b = g[0].hi();
            const double len = length(res.mesh.positions[b] - res.mesh.positions[a]);
            knots[edge_key(a, b)] = len;
            if (len > 1e-9) lens.push_back(len);
        });
        if (!lens.empty()) {
            std::nth_element(lens.begin(), lens.begin() + static_cast<std::ptrdiff_t>(lens.size() / 2), lens.end());
            const double med = std::max(1e-6, lens[lens.size() / 2]);
            const double lo = 0.02 * med, hi = 40.0 * med;
            for (auto& [k, d] : knots) d = std::clamp(d, lo, hi);
        }
    }

    // Initialize homogeneous R^4 weights when rational mode or non-unit vertex_weights are present.
    const bool do_rational = (opts.rational || !res.mesh.vertex_weights.empty()) && res.mesh.vertex_count() > 0;
    std::vector<double> w_cur;
    if (do_rational) {
        w_cur.assign(res.mesh.vertex_count(), 1.0);
        for (std::size_t v = 0; v < std::min(w_cur.size(), res.mesh.vertex_weights.size()); ++v)
            w_cur[v] = std::max(1e-4, res.mesh.vertex_weights[v]);
        for (std::size_t v = 0; v < res.mesh.vertex_count(); ++v)
            res.mesh.positions[v] = res.mesh.positions[v] * w_cur[v];
    }

    Mesh prev_for_adaptive;
    for (std::uint32_t l = 0; l < opts.linear_pre_subdiv; ++l) {
        Mesh next_m;
        UvChannel next_uv;
        std::vector<Vec3> next_cn;
        std::vector<double> next_evw;
        std::map<std::uint64_t, double> next_knots;
        std::vector<double> next_w;
        subdivide_step(res.mesh, uv, opts, true, next_m, next_uv,
                       do_subdiv_normals ? &sub_cnorm : nullptr, do_subdiv_normals ? &next_cn : nullptr,
                       do_subdiv_normals ? &evw : nullptr, do_subdiv_normals ? &next_evw : nullptr,
                       opts.non_uniform ? &knots : nullptr, opts.non_uniform ? &next_knots : nullptr,
                       do_rational ? &w_cur : nullptr, do_rational ? &next_w : nullptr);
        res.mesh = std::move(next_m);
        uv = std::move(next_uv);
        if (do_subdiv_normals) { sub_cnorm = std::move(next_cn); evw = std::move(next_evw); }
        if (opts.non_uniform) knots = std::move(next_knots);
        if (do_rational) w_cur = std::move(next_w);
    }
    for (std::uint32_t l = 0; l < opts.levels; ++l) {
        if (opts.adaptive && l + 1 == opts.levels) prev_for_adaptive = res.mesh;
        Mesh next_m;
        UvChannel next_uv;
        std::vector<Vec3> next_cn;
        std::vector<double> next_evw;
        std::map<std::uint64_t, double> next_knots;
        std::vector<double> next_w;
        subdivide_step(res.mesh, uv, opts, false, next_m, next_uv,
                       do_subdiv_normals ? &sub_cnorm : nullptr, do_subdiv_normals ? &next_cn : nullptr,
                       do_subdiv_normals ? &evw : nullptr, do_subdiv_normals ? &next_evw : nullptr,
                       opts.non_uniform ? &knots : nullptr, opts.non_uniform ? &next_knots : nullptr,
                       do_rational ? &w_cur : nullptr, do_rational ? &next_w : nullptr);
        res.mesh = std::move(next_m);
        uv = std::move(next_uv);
        if (do_subdiv_normals) { sub_cnorm = std::move(next_cn); evw = std::move(next_evw); }
        if (opts.non_uniform) knots = std::move(next_knots);
        if (do_rational) w_cur = std::move(next_w);
        // Pixar hierarchical multiresolution edit hook at level (l + 1):
        if (opts.on_level && !do_rational) {
            const std::size_t prev_nv = res.mesh.vertex_count();
            const std::size_t prev_nf = res.mesh.face_count();
            opts.on_level(l + 1, res.mesh);
            if (res.mesh.vertex_count() != prev_nv || res.mesh.face_count() != prev_nf) {
                // Topology changed by hierarchical inset/extrude: reset derived per-corner channels cleanly!
                uv = UvChannel{};
                if (do_subdiv_normals) init_subdiv_normals(res.mesh, sub_cnorm, evw);
                if (opts.non_uniform) knots.clear();
            }
        }
    }

    // Project homogeneous R^4 positions back to R^3 before G2 fairing, limit & normal evaluation.
    if (do_rational && w_cur.size() == res.mesh.vertex_count()) {
        for (std::size_t v = 0; v < res.mesh.vertex_count(); ++v) {
            const double wv = std::max(1e-6, w_cur[v]);
            res.mesh.positions[v] = res.mesh.positions[v] / wv;
        }
        res.mesh.vertex_weights.clear();
    }

    // True G2 quadratic osculating EV completion before limit & normal evaluation.
    if (opts.g2_fair && opts.levels >= 1) {
        apply_g2_ev_fairing(res.mesh, opts);
    }

    const bool need_limit_normals = opts.compute_limit_normals || (opts.subdiv_normals == 1);
    if ((opts.limit_snap && !opts.non_uniform) || need_limit_normals) {
        SubdivideOptions lim_opt = opts;
        if (opts.non_uniform) lim_opt.limit_snap = false;  // preserve non-uniform panel flatness!
        evaluate_limit(res.mesh, lim_opt, need_limit_normals ? &res.corner_normals : nullptr);
    }

    // 2. Semi-Sharp Subdivision Shading (Zhou-Boonstra-Kosinka 2023):
    //    mode 1 = smooth EV-weighted blend of subdivided normal field & exact limit normal;
    //    mode 2 = full subdivided normal field (Alexa-Boubekeur 2008).
    if (opts.subdiv_normals == 2 && sub_cnorm.size() == res.mesh.corner_count()) {
        res.corner_normals = std::move(sub_cnorm);
    } else if (opts.subdiv_normals == 1 && sub_cnorm.size() == res.mesh.corner_count() &&
               res.corner_normals.size() == res.mesh.corner_count() && evw.size() == res.mesh.vertex_count()) {
        for (std::size_t c = 0; c < res.mesh.corner_count(); ++c) {
            const double w = std::clamp(evw[res.mesh.face_indices[c]], 0.0, 1.0);
            if (w <= 1e-6) continue;
            const Vec3 n_lim = res.corner_normals[c];
            const Vec3 n_sub = sub_cnorm[c];
            if (dot(n_lim, n_sub) > 0.1) {
                res.corner_normals[c] = normalize_or(n_lim * (1.0 - w) + n_sub * w, n_lim);
            }
        }
    }

    if (!uv.corner_uv.empty()) {
        res.uv.resize(uv.corner_uv.size());
        for (std::size_t c = 0; c < uv.corner_uv.size(); ++c) {
            const auto& xy = uv.coords[uv.corner_uv[c]];
            res.uv[c] = {static_cast<float>(xy[0]), static_cast<float>(xy[1])};
        }
    }

    // 3. Feature-Adaptive Watertight Flat Coarsening: collapse flat regular interior 2x2 quad blocks
    //    while keeping 100% full detail on curved fillets/domes and zero T-junctions!
    if (opts.adaptive && opts.levels >= 1 && prev_for_adaptive.face_count() > 0) {
        coarsen_flat_blocks(prev_for_adaptive, res, opts.adaptive_angle_deg);
    }
    return res;
}

Mesh catmull_clark(const Mesh& m, const SubdivideOptions& opts) {
    return catmull_clark_full(m, {}, opts).mesh;
}

Mesh catmull_clark(const Mesh& m, std::uint32_t levels) {
    SubdivideOptions opts;
    opts.levels = levels;
    return catmull_clark(m, opts);
}

}  // namespace pg
