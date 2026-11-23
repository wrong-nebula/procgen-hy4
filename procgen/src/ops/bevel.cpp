#include "pg/ops/bevel.hpp"

#include "pg/cleanup.hpp"
#include "pg/topology.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <numbers>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace pg {
namespace {

struct UnionFind {
    std::vector<Index> parent;
    explicit UnionFind(std::size_t n) : parent(n) { std::iota(parent.begin(), parent.end(), Index{0}); }
    Index find(Index x) noexcept {
        while (parent[x] != x) x = parent[x] = parent[parent[x]];
        return x;
    }
    void unite(Index a, Index b) noexcept {
        a = find(a);
        b = find(b);
        if (a != b) parent[std::max(a, b)] = std::min(a, b);
    }
};

// Evaluate a point along the bevel profile from A to B with corner/ridge apex Q.
// t in (0, 1). profile in [0, 1]: 0 = linear chord, 0.5 = circular-like rational
// arc, 1.0 = piecewise linear through exact apex Q (support-loop hold).
Vec3 eval_profile(Vec3 A, Vec3 Q, Vec3 B, double t, double profile) {
    if (profile >= 1.0 - 1e-6) {
        return t <= 0.5 ? (A + (Q - A) * (2.0 * t)) : (Q + (B - Q) * (2.0 * (t - 0.5)));
    }
    if (profile <= 1e-6) {
        return A + (B - A) * t;
    }
    // Rational quadratic Bezier with control point Q and weight w_b.
    // w_b = cos(half_angle) gives an exact circular arc at profile = 0.5.
    const Vec3 da = normalize_or(A - Q, Vec3{});
    const Vec3 db = normalize_or(B - Q, Vec3{});
    const double cos_ang = std::clamp(dot(da, db), -0.999, 0.999);
    const double half_sin = std::sqrt(std::max(0.0, 0.5 * (1.0 - cos_ang)));  // sin(angle/2) = cos(dihedral/2)
    // Map profile in (0, 1) -> weight: profile=0.5 => w_circ = half_sin (exact circular arc for 90° -> 1/sqrt(2)).
    const double w_circ = std::max(0.1, half_sin);
    const double wb = profile <= 0.5 ? (w_circ * (profile * 2.0))
                                     : (w_circ + (12.0 - w_circ) * std::pow((profile - 0.5) * 2.0, 2.0));
    const double u = 1.0 - t;
    const double b0 = u * u;
    const double b1 = 2.0 * u * t * wb;
    const double b2 = t * t;
    const double denom = b0 + b1 + b2;
    return (A * b0 + Q * b1 + B * b2) / denom;
}

}  // namespace

Mesh bevel(const Mesh& m, const BevelParams& params) {
    if (!(params.width > 0.0)) throw std::invalid_argument("bevel: width must be > 0");
    const std::uint32_t segs = std::max<std::uint32_t>(1, params.segments);
    const double prof = std::clamp(params.profile, 0.0, 1.0);
    const std::size_t nv = m.vertex_count(), nf = m.face_count(), nc = m.corner_count();
    constexpr Index none = ~Index{0};

    std::vector<Index> corner_face(nc), next(nc), prev(nc);
    std::vector<Index> vertex_corner(nv, none);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = m.face_offsets[f], e = m.face_offsets[f + 1];
        for (Index c = b; c < e; ++c) {
            corner_face[c] = static_cast<Index>(f);
            next[c] = c + 1 == e ? b : c + 1;
            prev[c] = c == b ? e - 1 : c - 1;
            if (vertex_corner[m.face_indices[c]] == none) vertex_corner[m.face_indices[c]] = c;
        }
    }

    // Twins: corner c owns half-edge (v_c -> v_next). Closed manifold required.
    struct KC { std::uint64_t key; Index corner; };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(m.face_indices[c], m.face_indices[next[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });
    std::vector<Index> twin(nc), edge_of(nc);
    std::vector<std::uint64_t> edge_key_of;
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        while (j < nc && kc[j].key == kc[i].key) ++j;
        const Index a = kc[i].corner;
        if (j - i != 2 || m.face_indices[a] == m.face_indices[kc[i + 1].corner])
            throw std::invalid_argument("bevel: input must be a closed, consistently oriented manifold");
        const Index b = kc[i + 1].corner;
        twin[a] = b;
        twin[b] = a;
        edge_of[a] = edge_of[b] = static_cast<Index>(edge_key_of.size());
        edge_key_of.push_back(kc[i].key);
        i = j;
    }
    const std::size_t ne = edge_key_of.size();

    std::vector<Vec3> fn(nf);
    for (std::size_t f = 0; f < nf; ++f) fn[f] = face_normal(m, f);
    const double cos_limit = std::cos(params.min_angle_deg * std::numbers::pi / 180.0);
    std::vector<std::uint8_t> beveled(ne, 0);
    for (Index c = 0; c < nc; ++c) {
        if (c >= twin[c]) continue;
        const Index e = edge_of[c];
        const Vec3 fn0 = fn[corner_face[c]], fn1 = fn[corner_face[twin[c]]];
        bool ok = dot(fn0, fn1) <= cos_limit;
        if (ok && params.edge_filter) {
            const Vec3 p0 = m.positions[m.face_indices[c]], p1 = m.positions[m.face_indices[next[c]]];
            ok = params.edge_filter(e, p0, p1, fn0, fn1);
        }
        beveled[e] = ok ? 1 : 0;
    }
    if (std::none_of(beveled.begin(), beveled.end(), [](std::uint8_t b) { return b != 0; })) return m;

    // Count beveled edges incident to each vertex.
    std::vector<Index> vertex_bevel_deg(nv, 0);
    for (Index c = 0; c < nc; ++c)
        if (c < twin[c] && beveled[edge_of[c]]) {
            ++vertex_bevel_deg[m.face_indices[c]];
            ++vertex_bevel_deg[m.face_indices[next[c]]];
        }

    auto slot = [&](Index e, Index v) -> Index {
        return 2 * e + (v == static_cast<Index>(edge_key_of[e] >> 32) ? 0 : 1);
    };
    UnionFind uf(2 * ne);
    std::vector<std::uint8_t> split_cap_corner(nc, 0);
    for (Index c = 0; c < nc; ++c) {
        const Index ein = edge_of[prev[c]], eout = edge_of[c], v = m.face_indices[c];
        if (!beveled[ein] && !beveled[eout]) {
            if (vertex_bevel_deg[v] == 1) {
                const Index prev_face_ein = edge_of[prev[twin[prev[c]]]];
                if (beveled[prev_face_ein]) split_cap_corner[c] = 1;
                else uf.unite(slot(ein, v), slot(eout, v));
            } else {
                uf.unite(slot(ein, v), slot(eout, v));
            }
        }
    }

    struct CornerFrame { Vec3 p, n, a, b, m_out, m_in; double len_out, len_in; };
    auto frame = [&](Index c) {
        CornerFrame fr;
        fr.p = m.positions[m.face_indices[c]];
        fr.n = fn[corner_face[c]];
        const Vec3 to_next = m.positions[m.face_indices[next[c]]] - fr.p;
        const Vec3 to_prev = m.positions[m.face_indices[prev[c]]] - fr.p;
        fr.len_out = length(to_next);
        fr.len_in = length(to_prev);
        fr.a = normalize_or(to_next, Vec3{});
        fr.b = normalize_or(to_prev, Vec3{});
        fr.m_out = normalize_or(cross(fr.n, fr.a), Vec3{});
        fr.m_in = normalize_or(-cross(fr.n, fr.b), Vec3{});
        return fr;
    };

    // Optional overlap clamping: bound bevel width to at most 45% of the tightest
    // face span touching a beveled edge so faces cannot flip or self-intersect.
    double w = params.width;
    if (params.clamp_overlap) {
        double max_w = w;
        for (Index c = 0; c < nc; ++c) {
            const Index ein = edge_of[prev[c]], eout = edge_of[c];
            if (!beveled[ein] && !beveled[eout]) continue;
            const CornerFrame fr = frame(c);
            const double sin_c = length(cross(fr.a, fr.b));
            if (beveled[ein] && beveled[eout]) {
                const double s = std::max(0.1, sin_c);
                max_w = std::min(max_w, 0.45 * std::min(fr.len_in, fr.len_out) * s);
            } else if (beveled[ein]) {
                max_w = std::min(max_w, 0.45 * fr.len_out * std::max(0.1, sin_c));
            } else {
                max_w = std::min(max_w, 0.45 * fr.len_in * std::max(0.1, sin_c));
            }
        }
        w = std::max(1e-7, max_w);
    }

    auto inset = [&](Vec3 n, Vec3 m1, Vec3 m2) -> Vec3 {
        const double det = dot(m1, cross(m2, n));
        if (std::abs(det) < 1e-9) return m1 * w;
        return (cross(m2, n) * w + cross(n, m1) * w) / det;
    };

    struct Request { Index group; Vec3 slide; Vec3 n; Vec3 m_bev; };
    std::vector<Request> requests;
    for (Index c = 0; c < nc; ++c) {
        const Index ein = edge_of[prev[c]], eout = edge_of[c], v = m.face_indices[c];
        if (beveled[ein] == beveled[eout]) continue;
        const CornerFrame fr = frame(c);
        const double s = length(cross(fr.a, fr.b));
        const Vec3 along = beveled[ein] ? fr.a : fr.b;
        const double len = beveled[ein] ? fr.len_out : fr.len_in;
        const double dist = s > 1e-12 ? std::min(w / s, 0.5 * len) : 0.5 * len;
        requests.push_back({uf.find(beveled[ein] ? slot(eout, v) : slot(ein, v)), along * dist, fr.n,
                            beveled[ein] ? fr.m_in : fr.m_out});
    }
    std::sort(requests.begin(), requests.end(), [](const Request& x, const Request& y) { return x.group < y.group; });
    std::vector<Vec3> group_offset(2 * ne);
    for (std::size_t i = 0; i < requests.size();) {
        std::size_t j = i;
        Vec3 sum{};
        while (j < requests.size() && requests[j].group == requests[i].group) sum += requests[j++].slide;
        const std::size_t k = j - i;
        if (k == 2 && dot(requests[i].n, requests[i + 1].n) > 1.0 - 1e-9)
            group_offset[requests[i].group] = inset(requests[i].n, requests[i].m_bev, requests[i + 1].m_bev);
        else
            group_offset[requests[i].group] = sum / static_cast<double>(k);
        i = j;
    }

    Mesh out;
    out.reserve(nv + nc * segs, nf + ne * segs + nv * segs, nc * (3 + segs));
    std::vector<Index> group_vertex(2 * ne, none);
    auto group_id = [&](Index s, Index v) {
        s = uf.find(s);
        if (group_vertex[s] == none) group_vertex[s] = out.add_vertex(m.positions[v] + group_offset[s]);
        return group_vertex[s];
    };
    std::vector<Index> corner_out(nc);
    for (Index c = 0; c < nc; ++c) {
        const Index ein = edge_of[prev[c]], eout = edge_of[c], v = m.face_indices[c];
        if (beveled[ein] && beveled[eout]) {
            const CornerFrame fr = frame(c);
            corner_out[c] = out.add_vertex(fr.p + inset(fr.n, fr.m_out, fr.m_in));
        } else if (beveled[ein]) {
            corner_out[c] = group_id(slot(eout, v), v);
        } else {
            corner_out[c] = group_id(slot(ein, v), v);
        }
    }

    // Shared multi-segment profile chains between pairs of shrunk vertices (va, vb)
    // straddling a beveled edge end at original vertex v.
    // Keyed by canonical (min(va, vb), max(va, vb)) so adjacent beveled edges
    // (e.g. around a cylinder rim) and corner patches share exact interior vertices.
    struct ChainKey {
        Index lo, hi;
        auto operator<=>(const ChainKey&) const = default;
    };
    std::map<ChainKey, std::vector<Index>> profile_chains;  // interior vertices from lo -> hi (size == segs - 1)

    // Get or build the ordered vertex chain [va, m_1, ..., m_{segs-1}, vb] along a bevel end.
    // `apex` is the original sharp ridge point for this end of the beveled edge.
    auto get_chain = [&](Index va, Index vb, Vec3 apex) -> std::vector<Index> {
        std::vector<Index> chain;
        chain.reserve(segs + 1);
        chain.push_back(va);
        if (segs > 1 && va != vb) {
            const bool rev = va > vb;
            const ChainKey key{rev ? vb : va, rev ? va : vb};
            auto [it, fresh] = profile_chains.try_emplace(key);
            if (fresh) {
                const Vec3 A = out.positions[key.lo], B = out.positions[key.hi];
                it->second.reserve(segs - 1);
                for (std::uint32_t s = 1; s < segs; ++s) {
                    const double t = static_cast<double>(s) / static_cast<double>(segs);
                    it->second.push_back(out.add_vertex(eval_profile(A, apex, B, t, prof)));
                }
            }
            if (rev) {
                for (std::size_t i = it->second.size(); i-- > 0;) chain.push_back(it->second[i]);
            } else {
                for (Index id : it->second) chain.push_back(id);
            }
        }
        chain.push_back(vb);
        return chain;
    };

    // Exact ridge apex for one end of beveled half-edge c (at vertex v_end, heading along edge_dir).
    auto edge_end_apex = [&](Index v_end, Index v_other, Index va, Index vb) -> Vec3 {
        const Vec3 P = m.positions[v_end];
        if (vertex_bevel_deg[v_end] <= 2) return P;
        const Vec3 dir = normalize_or(m.positions[v_other] - P, Vec3{});
        const Vec3 mid = (out.positions[va] + out.positions[vb]) * 0.5;
        const double proj = std::max(0.0, dot(mid - P, dir));
        return P + dir * proj;
    };

    // 1. Shrunk original faces.
    std::vector<Index> poly;
    for (std::size_t f = 0; f < nf; ++f) {
        poly.clear();
        for (Index c = m.face_offsets[f]; c < m.face_offsets[f + 1]; ++c) {
            if (split_cap_corner[c]) {
                const Index ein = edge_of[prev[c]], eout = edge_of[c], v = m.face_indices[c];
                const Index va = group_id(slot(ein, v), v);
                const Index vb = group_id(slot(eout, v), v);
                if (segs == 1) {
                    poly.push_back(va);
                    poly.push_back(vb);
                } else {
                    const auto ch = get_chain(va, vb, m.positions[v]);
                    for (Index id : ch) poly.push_back(id);
                }
            } else {
                poly.push_back(corner_out[c]);
            }
        }
        out.add_face(poly);
    }

    // 2. Beveled edge strips (1 quad per segment).
    const bool tag_ridge = params.crease_sharpness > 0.0f && segs >= 2;
    for (Index c = 0; c < nc; ++c) {
        if (c > twin[c] || !beveled[edge_of[c]]) continue;
        const Index t = twin[c];
        const Index v0 = m.face_indices[c], v1 = m.face_indices[next[c]];
        const Index a0 = corner_out[c], b0 = corner_out[next[t]];
        const Index a1 = corner_out[next[c]], b1 = corner_out[t];
        if (segs == 1) {
            out.add_face({a1, a0, b0, b1});
        } else {
            const Vec3 q0 = edge_end_apex(v0, v1, a0, b0);
            const Vec3 q1 = edge_end_apex(v1, v0, a1, b1);
            const auto ch0 = get_chain(a0, b0, q0);
            const auto ch1 = get_chain(a1, b1, q1);
            for (std::uint32_t s = 0; s < segs; ++s) {
                out.add_face({ch1[s], ch0[s], ch0[s + 1], ch1[s + 1]});
            }
            if (tag_ridge && (segs % 2 == 0)) {
                const std::uint32_t mid = segs / 2;
                out.edge_creases.push_back({edge_key(ch0[mid], ch1[mid]), params.crease_sharpness});
            }
        }
    }

    // 3. Corner polygons / quad corner patches (walking the fan around each multi-bevel vertex).
    for (std::size_t v = 0; v < nv; ++v) {
        if (vertex_bevel_deg[v] <= 1) continue;
        const Index start = vertex_corner[v];
        if (start == none) continue;

        // Collect corner vertices around the fan, together with the edge between consecutive corner vertices.
        struct FanStep {
            Index v_out;
            Index outgoing_corner;  // half-edge whose origin is v along the boundary of the corner fan
        };
        std::vector<FanStep> steps;
        bool touches_bevel = false;
        Index g = start;
        do {
            touches_bevel |= beveled[edge_of[g]] != 0;
            if (steps.empty() || steps.back().v_out != corner_out[g]) {
                steps.push_back({corner_out[g], g});
            }
            g = twin[prev[g]];
        } while (g != start);
        while (steps.size() > 1 && steps.front().v_out == steps.back().v_out) steps.pop_back();
        if (!touches_bevel || steps.size() < 3) continue;

        if (segs == 1) {
            poly.clear();
            for (const FanStep& st : steps) poly.push_back(st.v_out);
            out.add_face(poly);
            continue;
        }

        // Build the outer perimeter chains for each side of the corner polygon.
        // Between steps[k].v_out and steps[k+1].v_out lies either a beveled edge strip end
        // (already registered in profile_chains!) or an unbeveled edge sector.
        const std::size_t d = steps.size();
        std::vector<std::vector<Index>> side_chains(d);
        Vec3 corner_centroid{};
        for (std::size_t k = 0; k < d; ++k) corner_centroid += out.positions[steps[k].v_out];
        corner_centroid = corner_centroid / static_cast<double>(d);

        for (std::size_t k = 0; k < d; ++k) {
            const Index va = steps[k].v_out;
            const Index vb = steps[(k + 1) % d].v_out;
            const ChainKey key{std::min(va, vb), std::max(va, vb)};
            auto it = profile_chains.find(key);
            if (it != profile_chains.end()) {
                side_chains[k] = get_chain(va, vb, m.positions[v]);
            } else {
                // Unbeveled gap between two shrunk corners: subdivide linearly so perimeter rings stay uniform.
                side_chains[k] = get_chain(va, vb, (out.positions[va] + out.positions[vb]) * 0.5);
            }
        }

        // Compute smooth corner apex position (exact original vertex P at profile == 1.0,
        // spherical/superelliptic dome point for profile < 1.0).
        const Vec3 P = m.positions[v];
        Vec3 apex_pos = P;
        if (prof < 1.0 - 1e-6) {
            const double alpha = prof <= 1e-6 ? 0.0 : std::pow(prof, 1.4);
            apex_pos = corner_centroid + (P - corner_centroid) * alpha;
        }

        if (segs == 2) {
            // Textbook quad corner patch: d pure quads meeting at a single apex vertex!
            // On a box corner with profile == 1.0, this gives 3 planar w x w holding quads!
            const Index v_center = out.add_vertex(apex_pos);
            for (std::size_t k = 0; k < d; ++k) {
                const Index v_corner = side_chains[k][0];
                const Index m_next = side_chains[k][1];
                const Index m_prev = side_chains[(k + d - 1) % d][1];
                out.add_face({v_corner, m_next, v_center, m_prev});
                if (tag_ridge) {
                    out.edge_creases.push_back({edge_key(m_next, v_center), params.crease_sharpness});
                }
            }
        } else {
            // General multi-segment concentric quad rings closing cleanly onto the spherical corner dome.
            std::vector<Index> ring;
            ring.reserve(d * segs);
            for (std::size_t k = 0; k < d; ++k) {
                for (std::uint32_t s = 0; s < segs; ++s) ring.push_back(side_chains[k][s]);
            }
            const std::uint32_t nrings = std::max<std::uint32_t>(2, (segs + 1) / 2);
            const Vec3 dome_lift = (P - apex_pos) * (0.35 * prof);
            for (std::uint32_t r = 1; r < nrings; ++r) {
                const double u = static_cast<double>(r) / static_cast<double>(nrings);
                std::vector<Index> next_ring(ring.size());
                for (std::size_t i = 0; i < ring.size(); ++i) {
                    const Vec3 p_outer = out.positions[ring[i]];
                    const Vec3 q = p_outer * (1.0 - u) + apex_pos * u + dome_lift * (u * (1.0 - u));
                    next_ring[i] = out.add_vertex(q);
                }
                for (std::size_t i = 0; i < ring.size(); ++i) {
                    const std::size_t j = (i + 1) % ring.size();
                    out.add_face({ring[i], ring[j], next_ring[j], next_ring[i]});
                }
                ring = std::move(next_ring);
            }
            const Index v_center = out.add_vertex(apex_pos);
            const std::size_t m_ring = ring.size();
            if (m_ring % 2 == 0) {
                for (std::size_t i = 0; i < m_ring; i += 2) {
                    out.add_face({ring[i], ring[(i + 1) % m_ring], ring[(i + 2) % m_ring], v_center});
                }
            } else {
                for (std::size_t i = 0; i < m_ring; ++i) {
                    out.add_face({ring[i], ring[(i + 1) % m_ring], v_center});
                }
            }
        }
    }

    if (!out.edge_creases.empty()) out.normalize_creases();
    remove_collapsed_faces(out);
    compact_vertices(out);
    return out;
}

}  // namespace pg
