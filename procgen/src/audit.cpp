#include "pg/audit.hpp"

#include "pg/closest.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <unordered_map>
#include <utility>

namespace pg::doc {
namespace {

struct UnionFind {
    std::vector<std::size_t> parent;
    explicit UnionFind(std::size_t n) : parent(n) { std::iota(parent.begin(), parent.end(), std::size_t{0}); }
    std::size_t find(std::size_t a) {
        while (parent[a] != a) a = parent[a] = parent[parent[a]];
        return a;
    }
    void join(std::size_t a, std::size_t b) {
        a = find(a);
        b = find(b);
        if (a != b) parent[std::max(a, b)] = std::min(a, b);
    }
};

// World -> prototype space for an affine transform (rows of the inverse 3x3, then the translation).
struct Inverse {
    double m[3][3]{};
    Vec3 t{};
    [[nodiscard]] Vec3 apply(Vec3 w) const noexcept {
        const Vec3 d = w - t;
        return {m[0][0] * d.x + m[0][1] * d.y + m[0][2] * d.z, m[1][0] * d.x + m[1][1] * d.y + m[1][2] * d.z,
                m[2][0] * d.x + m[2][1] * d.y + m[2][2] * d.z};
    }
};

Inverse invert(const Mat4& a) {
    double b[3][3];
    for (int r = 0; r < 3; ++r)
        for (int c = 0; c < 3; ++c) b[r][c] = a.c[c][r];
    const double det = b[0][0] * (b[1][1] * b[2][2] - b[1][2] * b[2][1]) - b[0][1] * (b[1][0] * b[2][2] - b[1][2] * b[2][0]) +
                       b[0][2] * (b[1][0] * b[2][1] - b[1][1] * b[2][0]);
    Inverse inv;
    inv.t = a.column(3);
    if (std::abs(det) < 1e-300) return inv;
    const double k = 1.0 / det;
    inv.m[0][0] = (b[1][1] * b[2][2] - b[1][2] * b[2][1]) * k;
    inv.m[0][1] = (b[0][2] * b[2][1] - b[0][1] * b[2][2]) * k;
    inv.m[0][2] = (b[0][1] * b[1][2] - b[0][2] * b[1][1]) * k;
    inv.m[1][0] = (b[1][2] * b[2][0] - b[1][0] * b[2][2]) * k;
    inv.m[1][1] = (b[0][0] * b[2][2] - b[0][2] * b[2][0]) * k;
    inv.m[1][2] = (b[0][2] * b[1][0] - b[0][0] * b[1][2]) * k;
    inv.m[2][0] = (b[1][0] * b[2][1] - b[1][1] * b[2][0]) * k;
    inv.m[2][1] = (b[0][1] * b[2][0] - b[0][0] * b[2][1]) * k;
    inv.m[2][2] = (b[0][0] * b[1][1] - b[0][1] * b[1][0]) * k;
    return inv;
}

// One solid island of a prototype, in prototype space.
struct LocalIsland {
    Mesh mesh;
    std::unique_ptr<ClosestIndex> index;
    std::vector<Vec3> samples;
    Aabb bounds;
    std::size_t triangles = 0;
    bool closed = false;
    std::string provenance_source;                           // pre-bake authoring node ID when preserved via face_sources
    std::vector<std::array<Index, 3>> fan;                   // ClosestIndex triangle order -> corners
    std::vector<Vec3> vertex_normal;                         // angle-weighted sum of incident face normals
    std::unordered_map<std::uint64_t, Vec3> edge_normal;     // sum of the two adjacent face normals
};

std::uint64_t edge_key(Index a, Index b) noexcept {
    return (static_cast<std::uint64_t>(std::min(a, b)) << 32) | std::max(a, b);
}

// Angle-weighted pseudonormals (Baerentzen & Aanaes 2005) over the exact fan triangulation ClosestIndex uses.
void build_pseudonormals(LocalIsland& isl) {
    const Mesh& m = isl.mesh;
    isl.vertex_normal.assign(m.vertex_count(), Vec3{});
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
            const std::array<Index, 3> t{fv[0], fv[i], fv[i + 1]};
            isl.fan.push_back(t);
            const Vec3 n = cross(m.positions[t[1]] - m.positions[t[0]], m.positions[t[2]] - m.positions[t[0]]);
            const double nl = length(n);
            if (!(nl > 1e-20)) continue;
            const Vec3 u = n / nl;
            for (int k = 0; k < 3; ++k) {
                const Index a = t[static_cast<std::size_t>(k)], b = t[static_cast<std::size_t>((k + 1) % 3)],
                            c = t[static_cast<std::size_t>((k + 2) % 3)];
                const Vec3 e1 = m.positions[b] - m.positions[a], e2 = m.positions[c] - m.positions[a];
                const double angle = std::atan2(length(cross(e1, e2)), dot(e1, e2));
                isl.vertex_normal[a] += u * angle;
                isl.edge_normal[edge_key(a, b)] += u;
            }
        }
    }
}

Vec3 feature_normal(const LocalIsland& isl, const ClosestHit& hit) {
    if (hit.triangle >= isl.fan.size()) return hit.normal;
    const std::array<Index, 3>& t = isl.fan[hit.triangle];
    const Vec3 a = isl.mesh.positions[t[0]], b = isl.mesh.positions[t[1]], c = isl.mesh.positions[t[2]];
    const Vec3 v0 = b - a, v1 = c - a, v2 = hit.point - a;
    const double d00 = dot(v0, v0), d01 = dot(v0, v1), d11 = dot(v1, v1), d20 = dot(v2, v0), d21 = dot(v2, v1);
    const double den = d00 * d11 - d01 * d01;
    if (!(std::abs(den) > 0)) return hit.normal;
    const double w1 = (d11 * d20 - d01 * d21) / den, w2 = (d00 * d21 - d01 * d20) / den, w0 = 1.0 - w1 - w2;
    const double eps = 1e-9;
    const bool z0 = w0 <= eps, z1 = w1 <= eps, z2 = w2 <= eps;
    auto edge = [&](Index p, Index q) {
        const auto it = isl.edge_normal.find(edge_key(p, q));
        return it == isl.edge_normal.end() ? hit.normal : it->second;
    };
    if (z1 && z2) return isl.vertex_normal[t[0]];
    if (z0 && z2) return isl.vertex_normal[t[1]];
    if (z0 && z1) return isl.vertex_normal[t[2]];
    if (z0) return edge(t[1], t[2]);
    if (z1) return edge(t[0], t[2]);
    if (z2) return edge(t[0], t[1]);
    return hit.normal;
}

struct KeyHash {
    std::size_t operator()(const std::array<std::int64_t, 3>& k) const noexcept {
        std::uint64_t h = 1469598103934665603ull;
        for (std::int64_t v : k) h = (h ^ static_cast<std::uint64_t>(v)) * 1099511628211ull;
        return static_cast<std::size_t>(h);
    }
};

std::vector<LocalIsland> split_islands(const Proto& proto, const ContactOptions& o) {
    const Mesh& m = proto.mesh;
    const bool has_fsrc = proto.face_sources.size() == m.face_count();
    // Weld by position (a seam in the UV chart duplicates vertices without splitting the solid).
    std::unordered_map<std::array<std::int64_t, 3>, Index, KeyHash> canon;
    std::vector<Index> id(m.vertex_count());
    std::vector<Vec3> pos;
    for (std::size_t v = 0; v < m.vertex_count(); ++v) {
        const Vec3 p = m.positions[v];
        const std::array<std::int64_t, 3> key{std::llround(p.x * 1e6), std::llround(p.y * 1e6), std::llround(p.z * 1e6)};
        const auto [it, fresh] = canon.try_emplace(key, static_cast<Index>(pos.size()));
        if (fresh) pos.push_back(p);
        id[v] = it->second;
    }
    UnionFind uf(pos.size());
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto face = m.face(f);
        for (std::size_t k = 1; k < face.size(); ++k) uf.join(id[face[0]], id[face[k]]);
    }
    std::map<std::size_t, std::size_t> slot;  // root -> island
    std::vector<LocalIsland> out;
    std::vector<std::vector<std::size_t>> faces;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto face = m.face(f);
        if (face.size() < 3) continue;
        const std::size_t root = uf.find(id[face[0]]);
        const auto [it, fresh] = slot.try_emplace(root, out.size());
        if (fresh) {
            out.emplace_back();
            faces.emplace_back();
        }
        faces[it->second].push_back(f);
    }
    for (std::size_t i = 0; i < out.size(); ++i) {
        LocalIsland& isl = out[i];
        std::unordered_map<Index, Index> local;
        std::map<std::pair<Index, Index>, int> edge_uses;
        std::map<std::string, std::size_t> src_counts;
        std::vector<Index> corners;
        for (std::size_t f : faces[i]) {
            const auto face = m.face(f);
            if (has_fsrc && proto.face_sources[f] && !proto.face_sources[f]->empty()) {
                src_counts[*proto.face_sources[f]] += face.size() >= 3 ? face.size() - 2 : 1;
            }
            corners.clear();
            for (std::size_t k = 0; k < face.size(); ++k) {
                const Index c = id[face[k]];
                const auto [it, fresh] = local.try_emplace(c, static_cast<Index>(isl.mesh.positions.size()));
                if (fresh) {
                    isl.mesh.add_vertex(pos[c]);
                    isl.bounds.expand(pos[c]);
                }
                corners.push_back(it->second);
                const Index d = id[face[(k + 1) % face.size()]];
                ++edge_uses[{std::min(c, d), std::max(c, d)}];
            }
            isl.mesh.add_face(std::span<const Index>(corners));
            isl.triangles += face.size() - 2;
        }
        isl.closed = std::all_of(edge_uses.begin(), edge_uses.end(), [](const auto& e) { return e.second == 2; });
        if (!src_counts.empty()) {
            isl.provenance_source = std::max_element(src_counts.begin(), src_counts.end(),
                                                     [](const auto& x, const auto& y) { return x.second < y.second; })->first;
        }
        // Samples: the vertices, then points along each edge.
        isl.samples = isl.mesh.positions;
        double total = 0.0;
        for (const auto& [e, uses] : edge_uses) total += length(pos[e.second] - pos[e.first]);
        const double step = std::max(o.edge_step, total / static_cast<double>(std::max<std::size_t>(o.max_samples, 1)));
        for (const auto& [e, uses] : edge_uses) {
            const Vec3 a = pos[e.first], b = pos[e.second];
            const int n = static_cast<int>(std::floor(length(b - a) / step));
            for (int k = 1; k <= n; ++k) isl.samples.push_back(a + (b - a) * (static_cast<double>(k) / (n + 1)));
        }
        isl.index = std::make_unique<ClosestIndex>(isl.mesh);
        if (isl.closed) build_pseudonormals(isl);
    }
    return out;
}

struct Placed {
    const LocalIsland* island = nullptr;
    const Part* part = nullptr;
    Inverse inverse;
    std::size_t report = 0;  // index into ContactReport::islands
};

Aabb world_bounds(const Aabb& local, const Mat4& m) {
    Aabb out;
    for (int k = 0; k < 8; ++k)
        out.expand(m.point({k & 1 ? local.hi.x : local.lo.x, k & 2 ? local.hi.y : local.lo.y, k & 4 ? local.hi.z : local.lo.z}));
    return out;
}

bool inside_box(Vec3 p, const Aabb& b, double pad) {
    return p.x >= b.lo.x - pad && p.x <= b.hi.x + pad && p.y >= b.lo.y - pad && p.y <= b.hi.y + pad && p.z >= b.lo.z - pad &&
           p.z <= b.hi.z + pad;
}

// Does any sample of `a` lie within the tolerance of `b`'s surface, or inside `b`?
bool touches(const Placed& a, const Aabb& b_world, const Placed& b, double tol) {
    const double reach = b.island->bounds.diagonal() + 1.0;
    for (const Vec3& s : a.island->samples) {
        const Vec3 w = a.part->transform.point(s);
        if (!inside_box(w, b_world, tol)) continue;
        const Vec3 l = b.inverse.apply(w);
        ClosestHit hit;
        if (!b.island->index->closest(l, reach, hit)) continue;
        if (length(b.part->transform.point(hit.point) - w) <= tol) return true;
        if (b.island->closed && dot(l - hit.point, feature_normal(*b.island, hit)) < 0.0) return true;
    }
    return false;
}

double aabb_distance(const Aabb& a, const Aabb& b) noexcept {
    const double dx = std::max({0.0, a.lo.x - b.hi.x, b.lo.x - a.hi.x});
    const double dy = std::max({0.0, a.lo.y - b.hi.y, b.lo.y - a.hi.y});
    const double dz = std::max({0.0, a.lo.z - b.hi.z, b.lo.z - a.hi.z});
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

}  // namespace

ContactReport audit_contacts(const Geometry& g, const ContactOptions& o) {
    ContactReport report;
    std::map<const Proto*, std::vector<LocalIsland>> cache;
    std::vector<Placed> placed;
    std::vector<Aabb> boxes;
    for (std::size_t p = 0; p < g.parts.size(); ++p) {
        const Part& part = g.parts[p];
        if (!part.proto || part.proto->mesh.face_count() == 0) continue;
        auto it = cache.find(part.proto.get());
        if (it == cache.end()) it = cache.emplace(part.proto.get(), split_islands(*part.proto, o)).first;
        const Inverse inv = invert(part.transform);
        for (const LocalIsland& isl : it->second) {
            ContactIsland ci;
            ci.part = p;
            ci.source = !isl.provenance_source.empty() ? isl.provenance_source : (part.source ? *part.source : std::string());
            ci.material = part.material;
            ci.bounds = world_bounds(isl.bounds, part.transform);
            ci.triangles = isl.triangles;
            placed.push_back({&isl, &part, inv, report.islands.size()});
            boxes.push_back(ci.bounds);
            report.islands.push_back(std::move(ci));
        }
    }
    const std::size_t n = placed.size();
    UnionFind uf(n);
    std::vector<std::vector<std::size_t>> adj(o.check_dangling ? n : 0);
    std::vector<std::size_t> order(n);
    std::iota(order.begin(), order.end(), std::size_t{0});
    std::sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) { return boxes[a].lo.x < boxes[b].lo.x; });
    const double tol = o.tolerance;
    for (std::size_t i = 0; i < n; ++i) {
        const std::size_t a = order[i];
        for (std::size_t j = i + 1; j < n; ++j) {
            const std::size_t b = order[j];
            if (boxes[b].lo.x > boxes[a].hi.x + tol) break;
            if (boxes[b].lo.y > boxes[a].hi.y + tol || boxes[a].lo.y > boxes[b].hi.y + tol) continue;
            if (boxes[b].lo.z > boxes[a].hi.z + tol || boxes[a].lo.z > boxes[b].hi.z + tol) continue;
            if (!o.check_dangling && uf.find(a) == uf.find(b)) continue;
            ++report.pairs_tested;
            if (touches(placed[a], boxes[b], placed[b], tol) || touches(placed[b], boxes[a], placed[a], tol)) {
                uf.join(a, b);
                if (o.check_dangling) {
                    adj[a].push_back(b);
                    adj[b].push_back(a);
                }
            }
        }
    }
    std::map<std::size_t, int> component;
    for (std::size_t i = 0; i < n; ++i) {
        const auto [it, fresh] = component.try_emplace(uf.find(i), report.components);
        if (fresh) {
            ++report.components;
            report.component_triangles.push_back(0);
        }
        report.islands[i].component = it->second;
        report.component_triangles[static_cast<std::size_t>(it->second)] += report.islands[i].triangles;
    }
    if (report.components > 0)
        report.main_component = static_cast<int>(std::max_element(report.component_triangles.begin(), report.component_triangles.end()) -
                                                 report.component_triangles.begin());

    // Measure exact minimum gap and attachment vector for up to 24 smallest detached components (matching write_detached).
    if (o.compute_gaps && report.components > 1 && report.main_component >= 0) {
        std::vector<int> det_comps;
        det_comps.reserve(static_cast<std::size_t>(report.components - 1));
        for (int c = 0; c < report.components; ++c)
            if (c != report.main_component) det_comps.push_back(c);
        std::sort(det_comps.begin(), det_comps.end(), [&](int x, int y) {
            return report.component_triangles[static_cast<std::size_t>(x)] < report.component_triangles[static_cast<std::size_t>(y)];
        });
        if (det_comps.size() > 24) det_comps.resize(24);

        std::vector<std::vector<std::size_t>> by_comp(static_cast<std::size_t>(report.components));
        for (std::size_t j = 0; j < n; ++j) by_comp[static_cast<std::size_t>(report.islands[j].component)].push_back(j);
        const auto& main_indices = by_comp[static_cast<std::size_t>(report.main_component)];

        for (int c : det_comps) {
            double best_gap = std::numeric_limits<double>::infinity();
            Vec3 best_delta{};
            std::string best_main_src;
            for (std::size_t i : by_comp[static_cast<std::size_t>(c)]) {
                for (std::size_t j : main_indices) {
                    if (aabb_distance(boxes[i], boxes[j]) >= best_gap) continue;
                    const double scale_j = std::max(1e-9, std::cbrt(std::abs(placed[j].part->transform.det3())));
                    const double max_local = best_gap < 1e9 ? (best_gap / scale_j) * 1.25 + 0.05 : placed[j].island->bounds.diagonal() + 1000.0;
                    const std::size_t n_samp = placed[i].island->samples.size();
                    const std::size_t stride = n_samp > 64 ? n_samp / 64 : 1;
                    for (std::size_t sk = 0; sk < n_samp; sk += stride) {
                        const Vec3 w = placed[i].part->transform.point(placed[i].island->samples[sk]);
                        const Vec3 l = placed[j].inverse.apply(w);
                        ClosestHit hit;
                        if (!placed[j].island->index->closest(l, max_local, hit)) continue;
                        const Vec3 mw = placed[j].part->transform.point(hit.point);
                        const double d = length(mw - w);
                        if (d < best_gap) {
                            best_gap = d;
                            best_delta = mw - w;
                            best_main_src = report.islands[j].source;
                        }
                    }
                }
            }
            if (std::isfinite(best_gap)) {
                report.detached_gaps[c] = ComponentGap{best_gap, best_delta, best_main_src};
            }
        }
    }

    if (o.check_dangling && report.main_component >= 0) {
        double ground_z = std::numeric_limits<double>::infinity();
        for (std::size_t i = 0; i < n; ++i) {
            if (report.islands[i].component == report.main_component) {
                ground_z = std::min(ground_z, boxes[i].lo.z);
            }
        }
        // Flag elevated hanging shoes & overhanging brackets:
        //  (a) degree-1 hanging foot suspended from a single upward neighbor, OR
        //  (b) multi-neighbor bracket/strut whose neighbors touch ONLY its upper 55% while its lower foot hangs in empty air.
        for (std::size_t i = 0; i < n; ++i) {
            if (report.islands[i].component != report.main_component || adj[i].empty()) continue;
            const Aabb& bi = boxes[i];
            const double hi = bi.hi.z - bi.lo.z;
            if (bi.lo.z <= ground_z + 0.25 || hi < 0.06) continue;

            bool suspended_only_from_above = false;
            if (adj[i].size() == 1) {
                const Aabb& bnb = boxes[adj[i][0]];
                suspended_only_from_above =
                    (bnb.hi.z > bi.hi.z + 0.10 && bnb.lo.z >= bi.lo.z - 0.06) ||
                    (bnb.lo.z > bi.lo.z + 0.42 * hi && bnb.hi.z >= bi.hi.z - 0.02);
            } else {
                suspended_only_from_above = true;
                bool any_above = false;
                for (std::size_t nb : adj[i]) {
                    const Aabb& bnb = boxes[nb];
                    if (bnb.lo.z <= bi.lo.z + 0.42 * hi) {
                        suspended_only_from_above = false;
                        break;
                    }
                    if (bnb.hi.z >= bi.hi.z - 0.02) any_above = true;
                }
                suspended_only_from_above = suspended_only_from_above && any_above;
            }
            if (!suspended_only_from_above) continue;

            // Fire downward -Z ray from the island's bottom centre into all other islands whose XY bounds cover it.
            const Vec3 probe_w{(bi.lo.x + bi.hi.x) * 0.5, (bi.lo.y + bi.hi.y) * 0.5, bi.lo.z - 0.002};
            bool anything_below = false;
            for (std::size_t j = 0; j < n; ++j) {
                if (j == i) continue;
                const Aabb& bj = boxes[j];
                if (bj.lo.z >= bi.lo.z || probe_w.x < bj.lo.x - tol || probe_w.x > bj.hi.x + tol ||
                    probe_w.y < bj.lo.y - tol || probe_w.y > bj.hi.y + tol) {
                    continue;
                }
                const Vec3 o_local = placed[j].inverse.apply(probe_w);
                const Vec3 d_local = placed[j].inverse.apply(probe_w + Vec3{0.0, 0.0, -1.0}) - o_local;
                const double dl = length(d_local);
                if (dl > 1e-12 && placed[j].island->index->any_hit(o_local, d_local / dl, 0.0, 1e6)) {
                    anything_below = true;
                    break;
                }
            }
            if (!anything_below) {
                report.dangling.push_back(i);
            }
        }
    }
    return report;
}

}  // namespace pg::doc
