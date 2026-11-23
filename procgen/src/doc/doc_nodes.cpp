// Mesh-building nodes of the geometry-graph format, ported from the reference
// Swift evaluator (GeometryNodePrimitives, GeometryExtrusion, GeometryPolygon,
// GeometryProfileSolid, GeometryNodeSpline, GeometrySurfaceBand,
// GeometryBoxEditing). Vertex order and face order are kept identical so that
// counts and bounds can be compared one to one.
#include "doc_internal.hpp"
#include "doc_expr.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numbers>
#include <set>

namespace pg::doc::detail {
namespace {

struct P2 {
    double x = 0, y = 0;
    friend auto operator<=>(const P2&, const P2&) = default;
};
P2 operator-(P2 a, P2 b) { return {a.x - b.x, a.y - b.y}; }
P2 operator+(P2 a, P2 b) { return {a.x + b.x, a.y + b.y}; }
P2 operator*(P2 a, double s) { return {a.x * s, a.y * s}; }
double cross2(P2 a, P2 b) { return a.x * b.y - a.y * b.x; }
double dot2(P2 a, P2 b) { return a.x * b.x + a.y * b.y; }
double len2sq(P2 a) { return dot2(a, a); }
P2 norm2(P2 a) { const double l = std::sqrt(len2sq(a)); return {a.x / l, a.y / l}; }

using Tri = std::array<Index, 3>;

void add_tri(Proto& p, Index a, Index b, Index c) { p.mesh.add_face({a, b, c}); }
void add_quad(Proto& p, Index a, Index b, Index c, Index d) { p.mesh.add_face({a, b, c, d}); }
ProtoPtr share(Proto&& p) { return std::make_shared<const Proto>(std::move(p)); }

Vec3 rotate_about(Vec3 v, Vec3 axis, double angle) {  // Rodrigues, unit axis
    const double c = std::cos(angle), s = std::sin(angle);
    return v * c + cross(axis, v) * s + axis * (dot(axis, v) * (1.0 - c));
}
Vec3 rotate_from_to(Vec3 u, Vec3 a, Vec3 b) {  // shortest arc taking unit a to unit b
    const Vec3 k = cross(a, b);
    const double s = length(k), c = dot(a, b);
    if (s < 1e-12) return u;
    const Vec3 axis = k / s;
    return u * c + cross(axis, u) * s + axis * (dot(axis, u) * (1.0 - c));
}

// ---- profiles ---------------------------------------------------------------

std::vector<P2> profile_points(const Options& o, const json::Value* list = nullptr) {
    const std::vector<json::Value>& entries = list ? list->array : o.list("profile", 3, 96);
    if (list && (!list->is_array() || entries.size() < 3 || entries.size() > 96)) fail("profile must contain 3…96 entries");
    std::vector<P2> out;
    out.reserve(entries.size());
    for (const json::Value& e : entries) {
        if (!e.is_array() || e.array.size() != 2) fail("Expected XY profile points");
        out.push_back({o.number(e.array[0]), o.number(e.array[1])});
    }
    return out;
}

// Counter-clockwise boundary order and an ear-clipped triangulation.
void profile_topology(const std::vector<P2>& polygon, std::vector<int>& border, std::vector<Tri>& caps) {
    const int n = static_cast<int>(polygon.size());
    const double tolerance = 1e-12;
    auto turn = [&](int a, int b, int c) { return cross2(polygon[b] - polygon[a], polygon[c] - polygon[a]); };
    for (int i = 0; i < n; ++i) {
        const int next = (i + 1) % n;
        if (!(len2sq(polygon[i] - polygon[next]) > tolerance)) fail("Extrusion has repeated profile points");
        for (int j = i + 2; j < n; ++j) {
            if ((j + 1) % n == i) continue;
            const int other = (j + 1) % n;
            const P2 a = polygon[i], b = polygon[next], c = polygon[j], d = polygon[other];
            const double lx = std::max(std::min(a.x, b.x), std::min(c.x, d.x)), rx = std::min(std::max(a.x, b.x), std::max(c.x, d.x));
            const double ly = std::max(std::min(a.y, b.y), std::min(c.y, d.y)), ry = std::min(std::max(a.y, b.y), std::max(c.y, d.y));
            const double across_a = turn(i, next, j) * turn(i, next, other);
            const double across_b = turn(j, other, i) * turn(j, other, next);
            if (lx <= rx && ly <= ry && across_a <= 0 && across_b <= 0) fail("Extrusion profile crosses itself");
        }
    }
    double area = 0;
    for (int i = 0; i < n; ++i) area += cross2(polygon[i], polygon[(i + 1) % n]);
    if (!(std::abs(area) > tolerance)) fail("Extrusion profile has no area");
    border.resize(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) border[static_cast<std::size_t>(i)] = area > 0 ? i : n - 1 - i;
    std::vector<int> remaining = border;
    caps.clear();
    while (remaining.size() > 3) {
        int ear = -1;
        const int m = static_cast<int>(remaining.size());
        for (int i = 0; i < m; ++i) {
            const int a = remaining[static_cast<std::size_t>((i + m - 1) % m)], b = remaining[static_cast<std::size_t>(i)];
            const int c = remaining[static_cast<std::size_t>((i + 1) % m)];
            if (!(turn(a, b, c) > tolerance)) continue;
            bool blocked = false;
            for (int p : remaining) {
                if (p == a || p == b || p == c) continue;
                if (turn(a, b, p) >= -tolerance && turn(b, c, p) >= -tolerance && turn(c, a, p) >= -tolerance) { blocked = true; break; }
            }
            if (blocked) continue;
            caps.push_back({static_cast<Index>(a), static_cast<Index>(b), static_cast<Index>(c)});
            ear = i;
            break;
        }
        if (ear < 0) fail("Could not triangulate the extrusion profile");
        remaining.erase(remaining.begin() + ear);
    }
    caps.push_back({static_cast<Index>(remaining[0]), static_cast<Index>(remaining[1]), static_cast<Index>(remaining[2])});
}

// ---- planar polygons with holes (bounded ear clipping) -----------------------

constexpr std::size_t kMaxCorners = 1024;

std::vector<Tri> triangulate_projected(const std::vector<std::vector<Index>>& loops, const std::vector<P2>& pts) {
    const double eps = 1e-12;
    auto P = [&](Index i) { return pts[i]; };
    auto turn = [](P2 a, P2 b, P2 p) { return cross2(b - a, p - a); };
    auto area = [&](const std::vector<Index>& loop) {
        double sum = 0;
        for (std::size_t i = 0; i < loop.size(); ++i) sum += cross2(P(loop[i]), P(loop[(i + 1) % loop.size()]));
        return sum;
    };
    auto contains = [&](P2 p, const std::vector<Index>& loop) {
        bool inside = false;
        for (std::size_t i = 0; i < loop.size(); ++i) {
            const P2 a = P(loop[i]), b = P(loop[(i + 1) % loop.size()]);
            if ((a.y > p.y) != (b.y > p.y) && p.x < a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)) inside = !inside;
        }
        return inside;
    };
    auto sign = [&](double x) { return x > eps ? 1 : (x < -eps ? -1 : 0); };
    auto intersects = [&](P2 a, P2 b, P2 c, P2 d) {
        const double lx = std::max(std::min(a.x, b.x), std::min(c.x, d.x)), rx = std::min(std::max(a.x, b.x), std::max(c.x, d.x));
        const double ly = std::max(std::min(a.y, b.y), std::min(c.y, d.y)), ry = std::min(std::max(a.y, b.y), std::max(c.y, d.y));
        if (!(lx <= rx + eps && ly <= ry + eps)) return false;
        return sign(turn(a, b, c)) * sign(turn(a, b, d)) <= 0 && sign(turn(c, d, a)) * sign(turn(c, d, b)) <= 0;
    };

    std::vector<std::vector<Index>> ordered;
    for (std::size_t li = 0; li < loops.size(); ++li) {
        const double signed_area = area(loops[li]);
        if (!(std::abs(signed_area) > eps)) fail("Polygon loop has no area");
        std::vector<Index> loop = loops[li];
        if ((signed_area > 0) != (li == 0)) std::reverse(loop.begin(), loop.end());
        ordered.push_back(std::move(loop));
    }
    using Edge = std::pair<Index, Index>;
    std::vector<Edge> boundary;
    for (const auto& loop : ordered) {
        const std::size_t k = loop.size();
        for (std::size_t i = 0; i < k; ++i) {
            const P2 p = P(loop[(i + k - 1) % k]), q = P(loop[i]), r = P(loop[(i + 1) % k]);
            if (std::abs(turn(p, q, r)) <= eps && dot2(p - q, r - q) > eps) fail("Polygon boundary doubles back");
            boundary.emplace_back(loop[i], loop[(i + 1) % k]);
        }
    }
    for (std::size_t i = 0; i < boundary.size(); ++i) {
        const auto [a, b] = boundary[i];
        if (!(len2sq(P(a) - P(b)) > eps * eps)) fail("Polygon has coincident consecutive vertices");
        for (std::size_t j = i + 1; j < boundary.size(); ++j) {
            const auto [c, d] = boundary[j];
            if (a == c || a == d || b == c || b == d) continue;
            if (intersects(P(a), P(b), P(c), P(d))) fail("Polygon loops cross or touch");
        }
    }
    const std::vector<Index>& outer = ordered[0];
    const std::vector<std::vector<Index>> holes(ordered.begin() + 1, ordered.end());
    for (std::size_t i = 0; i < holes.size(); ++i) {
        const P2 p = P(holes[i][0]);
        bool nested = false;
        for (std::size_t j = 0; j < holes.size(); ++j) nested = nested || (j != i && contains(p, holes[j]));
        if (!contains(p, outer) || nested) fail("Polygon holes must lie inside the outer loop and cannot be nested");
    }

    std::vector<Index> contour = outer;
    std::vector<Edge> bridges;
    for (const auto& hole : holes) {
        int best_outer = -1, best_hole = -1;
        double best_len = std::numeric_limits<double>::infinity();
        for (std::size_t j = 0; j < hole.size(); ++j) {
            const Index a = hole[j];
            const P2 p = P(a);
            for (std::size_t i = 0; i < contour.size(); ++i) {
                const Index b = contour[i];
                const P2 q = P(b);
                const P2 middle = (p + q) * 0.5;
                const double len = len2sq(p - q);
                if (!(len < best_len) || !contains(middle, outer)) continue;
                bool in_hole = false;
                for (const auto& h : holes) in_hole = in_hole || contains(middle, h);
                if (in_hole) continue;
                bool blocked = false;
                auto blocks = [&](Edge e) {
                    const P2 r = P(e.first), s = P(e.second);
                    if (a == e.first || a == e.second || b == e.first || b == e.second)
                        return std::abs(turn(p, q, r)) <= eps && std::abs(turn(p, q, s)) <= eps;
                    return intersects(p, q, r, s);
                };
                for (const Edge& e : boundary) if (blocks(e)) { blocked = true; break; }
                if (!blocked) for (const Edge& e : bridges) if (blocks(e)) { blocked = true; break; }
                if (!blocked) { best_outer = static_cast<int>(i); best_hole = static_cast<int>(j); best_len = len; }
            }
        }
        if (best_outer < 0) fail("Could not connect polygon hole to its outer boundary");
        std::vector<Index> cycle(hole.begin() + best_hole, hole.end());
        cycle.insert(cycle.end(), hole.begin(), hole.begin() + best_hole);
        const Index a = contour[static_cast<std::size_t>(best_outer)], b = cycle[0];
        bridges.emplace_back(a, b);
        cycle.push_back(b);
        cycle.push_back(a);
        contour.insert(contour.begin() + best_outer + 1, cycle.begin(), cycle.end());
    }

    std::vector<Tri> triangles;
    while (contour.size() > 3) {
        int ear = -1;
        const int m = static_cast<int>(contour.size());
        for (int i = 0; i < m; ++i) {
            const Index a = contour[static_cast<std::size_t>((i + m - 1) % m)], b = contour[static_cast<std::size_t>(i)];
            const Index c = contour[static_cast<std::size_t>((i + 1) % m)];
            const P2 p = P(a), q = P(b), r = P(c);
            if (!(turn(p, q, r) > eps)) continue;
            bool blocked = false;
            for (Index v : contour) {
                if (v == a || v == b || v == c) continue;
                const P2 x = P(v);
                if (turn(p, q, x) >= -eps && turn(q, r, x) >= -eps && turn(r, p, x) >= -eps) { blocked = true; break; }
            }
            if (blocked) continue;
            triangles.push_back({a, b, c});
            ear = i;
            break;
        }
        if (ear < 0) fail("Could not triangulate polygon");
        contour.erase(contour.begin() + ear);
    }
    triangles.push_back({contour[0], contour[1], contour[2]});
    double target = 0, covered = 0;
    for (const auto& loop : ordered) target += area(loop);
    for (const Tri& t : triangles) {
        const double s = turn(P(t[0]), P(t[1]), P(t[2]));
        if (!(s > eps)) fail("Polygon triangulation produced a degenerate face");
        covered += s;
    }
    if (!(std::abs(target - covered) <= std::max(target * 1e-8, eps))) fail("Polygon triangulation does not match its area");
    return triangles;
}

std::vector<Tri> triangulate_polygon(const std::vector<std::vector<Index>>& loops, const std::vector<Vec3>& positions) {
    std::size_t count = 0;
    std::set<Index> unique;
    for (const auto& l : loops) {
        if (l.size() < 3) fail("Polygon needs distinct vertices and at most " + std::to_string(kMaxCorners) + " corners");
        count += l.size();
        unique.insert(l.begin(), l.end());
    }
    if (loops.empty() || count > kMaxCorners || unique.size() != count) fail("Polygon needs distinct vertices and at most " + std::to_string(kMaxCorners) + " corners");
    const std::vector<Index>& outer = loops[0];
    const Vec3 origin = positions[outer[0]];
    std::vector<Vec3> rel(positions.size());
    double extent = 0;
    for (Index i : unique) {
        rel[i] = positions[i] - origin;
        extent = std::max(extent, length(rel[i]));
    }
    if (!(extent > 0)) fail("Polygon boundary has no area");
    Vec3 normal{};
    for (std::size_t i = 0; i < outer.size(); ++i) normal += cross(rel[outer[i]], rel[outer[(i + 1) % outer.size()]]);
    if (!(length(normal) > extent * extent * 1e-12)) fail("Polygon boundary has no area");
    normal = normal / length(normal);
    double magnitude = 0;
    for (Index i : unique) magnitude = std::max({magnitude, std::abs(positions[i].x), std::abs(positions[i].y), std::abs(positions[i].z)});
    const double flat = std::max(extent * 2e-6, magnitude * 16.0 * static_cast<double>(std::numeric_limits<float>::epsilon()));
    for (Index i : unique)
        if (!(std::abs(dot(rel[i], normal)) <= flat)) fail("Polygon vertices must be coplanar");
    const Vec3 reference = std::abs(normal.z) > 0.9 ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
    const Vec3 u = normalize_or(cross(reference, normal), {1, 0, 0});
    const Vec3 v = cross(normal, u);
    std::vector<P2> xy(positions.size());
    for (Index i : unique) xy[i] = {dot(rel[i], u) / extent, dot(rel[i], v) / extent};
    return triangulate_projected(loops, xy);
}

// ---- primitives --------------------------------------------------------------

Proto make_grid(Vec3 size, int nx, int ny, int u, int v, double sign) {
    const int ex = nx - 1, ey = ny - 1;
    const double dx = ex == 0 ? 0 : size[u] / ex, dy = ey == 0 ? 0 : size[v] / ey;
    const double xs = ex / 2.0, ys = ey / 2.0;
    Proto p;
    for (int x = 0; x < nx; ++x)
        for (int y = 0; y < ny; ++y) {
            double c[3] = {0, 0, 0};
            c[u] = (x - xs) * dx * sign;
            c[v] = (y - ys) * dy;
            p.mesh.add_vertex({c[0], c[1], c[2]});
        }
    const auto row = static_cast<Index>(ny);
    for (int x = 0; x < ex; ++x)
        for (int y = 0; y < ey; ++y) {
            const auto i = static_cast<Index>(x * ny + y);
            add_quad(p, i, i + row, i + row + 1, i + 1);
        }
    if (ex == 0 || ey == 0)
        for (Index i = 0; i + 1 < p.mesh.vertex_count(); ++i) p.edges.push_back({i, i + 1});
    return p;
}

Proto make_cuboid(Vec3 size, const int n[3]) {
    const int e[3] = {n[0] - 1, n[1] - 1, n[2] - 1};
    const Vec3 delta{size.x / e[0], size.y / e[1], size.z / e[2]};
    const Vec3 minimum = size * -0.5;
    Proto p;
    for (int z = 0; z < n[2]; ++z)
        for (int y = 0; y < n[1]; ++y) {
            const bool full_row = z == 0 || z == e[2] || y == 0 || y == e[1];
            const int step = full_row ? 1 : e[0];
            for (int x = 0; x <= e[0]; x += step) p.mesh.add_vertex(minimum + Vec3{delta.x * x, delta.y * y, delta.z * z});
        }
    const int section = n[0] * n[1] - (n[0] - 2) * (n[1] - 2);
    auto vertex = [&](const int q[3]) -> Index {
        if (q[2] == 0) return static_cast<Index>(q[1] * n[0] + q[0]);
        const int layer = n[0] * n[1] + (q[2] - 1) * section;
        if (q[2] == e[2] || q[1] == 0) return static_cast<Index>(layer + q[1] * n[0] + q[0]);
        const int row = layer + n[0] + (q[1] - 1) * 2;
        if (q[1] == e[1]) return static_cast<Index>(row + q[0]);
        return static_cast<Index>(row + (q[0] == 0 ? 0 : 1));
    };
    // Face order matches Blender: bottom, front, top, back, left, right.
    const int faces[6][4] = {{2, 0, 0, 1}, {1, 0, 0, 2}, {2, 1, 0, 1}, {1, 1, 0, 2}, {0, 0, 1, 2}, {0, 1, 1, 2}};
    for (const auto& f : faces) {
        const int axis = f[0], side = f[1], u = f[2], v = f[3];
        const bool reversed = axis == 1 ? side == 1 : side == 0;
        for (int j = 0; j < e[v]; ++j)
            for (int i = 0; i < e[u]; ++i) {
                int a[3] = {0, 0, 0};
                a[axis] = side * e[axis];
                a[u] = i;
                a[v] = j;
                int b[3] = {a[0], a[1], a[2]}, c[3] = {a[0], a[1], a[2]}, d[3] = {a[0], a[1], a[2]};
                b[u] += 1;
                c[u] += 1;
                c[v] += 1;
                d[v] += 1;
                if (reversed) add_quad(p, vertex(a), vertex(d), vertex(c), vertex(b));
                else add_quad(p, vertex(a), vertex(b), vertex(c), vertex(d));
            }
    }
    return p;
}

// Cap a planar convex ring of `n` vertices (CCW when seen from outside) using:
// "ngon" (single face), "inset" (concentric holding quad ring + inner ngon), or
// "quad" (Coons-interpolated all-quad grid for even n >= 4, zero high-valence poles).
void emit_ring_cap_3d(Mesh& m, const std::vector<Index>& rim, const std::string& cap_mode, double inset_f,
                      bool flip_winding) {
    const std::size_t n = rim.size();
    if (n < 3) return;
    if (cap_mode == "ngon") {
        std::vector<Index> poly = rim;
        if (flip_winding) std::reverse(poly.begin(), poly.end());
        m.add_face(poly);
        return;
    }
    if (cap_mode == "inset") {
        Vec3 c{};
        for (Index v : rim) c += m.positions[v];
        c = c / static_cast<double>(n);
        std::vector<Index> inner(n);
        for (std::size_t i = 0; i < n; ++i) {
            inner[i] = m.add_vertex(m.positions[rim[i]] * (1.0 - inset_f) + c * inset_f);
        }
        for (std::size_t i = 0; i < n; ++i) {
            const std::size_t j = (i + 1) % n;
            if (flip_winding) m.add_face({rim[j], rim[i], inner[i], inner[j]});
            else m.add_face({rim[i], rim[j], inner[j], inner[i]});
        }
        std::vector<Index> core = inner;
        if (flip_winding) std::reverse(core.begin(), core.end());
        m.add_face(core);
        return;
    }
    // "quad": Coons-interpolated (a+1) x (b+1) all-quad grid with perimeter 2*(a+b) == n.
    const int half = static_cast<int>(n / 2);
    const int b = half / 2;
    const int a = half - b;
    const int W = a + 1, H = b + 1;
    std::vector<Index> grid(static_cast<std::size_t>(W) * static_cast<std::size_t>(H), ~Index{0});
    auto at = [&](int gx, int gy) -> Index& {
        return grid[static_cast<std::size_t>(gy) * static_cast<std::size_t>(W) + static_cast<std::size_t>(gx)];
    };
    std::size_t k = 0;
    for (int x = 0; x < a; ++x) at(x, 0) = rim[k++];
    for (int y = 0; y < b; ++y) at(a, y) = rim[k++];
    for (int x = a; x > 0; --x) at(x, b) = rim[k++];
    for (int y = b; y > 0; --y) at(0, y) = rim[k++];
    for (int gy = 1; gy < b; ++gy) {
        const double v = static_cast<double>(gy) / static_cast<double>(b);
        const Vec3 p_left = m.positions[at(0, gy)], p_right = m.positions[at(a, gy)];
        const Vec3 p_00 = m.positions[at(0, 0)], p_a0 = m.positions[at(a, 0)];
        const Vec3 p_0b = m.positions[at(0, b)], p_ab = m.positions[at(a, b)];
        for (int gx = 1; gx < a; ++gx) {
            const double u = static_cast<double>(gx) / static_cast<double>(a);
            const Vec3 p_bot = m.positions[at(gx, 0)], p_top = m.positions[at(gx, b)];
            const Vec3 lin_u = p_left * (1.0 - u) + p_right * u;
            const Vec3 lin_v = p_bot * (1.0 - v) + p_top * v;
            const Vec3 bilin = (p_00 * (1.0 - u) + p_a0 * u) * (1.0 - v) + (p_0b * (1.0 - u) + p_ab * u) * v;
            at(gx, gy) = m.add_vertex(lin_u + lin_v - bilin);
        }
    }
    for (int gy = 0; gy < b; ++gy) {
        for (int gx = 0; gx < a; ++gx) {
            const Index i0 = at(gx, gy), i1 = at(gx + 1, gy), i2 = at(gx + 1, gy + 1), i3 = at(gx, gy + 1);
            if (flip_winding) m.add_face({i0, i3, i2, i1});
            else m.add_face({i0, i1, i2, i3});
        }
    }
}

Proto make_tube(const std::vector<Vec3>& path, double radius, int sides, const std::string& cap_mode = "fan",
                double cap_inset = 0.15) {
    if (cap_mode == "quad" && (sides < 4 || (sides % 2) != 0))
        fail("sweep cap quad requires an even sides >= 4");
    std::vector<Vec3> directions;
    for (std::size_t i = 1; i < path.size(); ++i) {
        const Vec3 d = path[i] - path[i - 1];
        if (!(dot(d, d) > 1e-10)) fail("Sweep has repeated points");
        directions.push_back(d / length(d));
    }
    Proto p;
    Vec3 previous{};
    std::vector<Vec3> ring_u, ring_v;
    for (std::size_t i = 0; i < path.size(); ++i) {
        const Vec3 combined = directions[i == 0 ? 0 : i - 1] + directions[std::min(i, directions.size() - 1)];
        if (!(dot(combined, combined) > 1e-10)) fail("Sweep reverses direction at point " + std::to_string(i));
        const Vec3 tangent = combined / length(combined);
        const Vec3 projected = previous - tangent * dot(tangent, previous);
        const Vec3 fallback = std::abs(tangent.z) < 0.95 ? Vec3{0, 0, 1} : Vec3{0, 1, 0};
        const Vec3 basis = dot(projected, projected) > 1e-10 ? projected : cross(tangent, fallback);
        const Vec3 u = basis / length(basis);
        const Vec3 v = cross(tangent, u);
        if (i == 0 || i + 1 == path.size()) {
            ring_u.push_back(u);
            ring_v.push_back(v);
        }
        for (int k = 0; k < sides; ++k) {
            const double a = k * 2.0 * std::numbers::pi / sides;
            p.mesh.add_vertex(path[i] + (u * std::cos(a) + v * std::sin(a)) * radius);
        }
        previous = u;
    }
    const auto stride = static_cast<Index>(sides);
    for (std::size_t i = 0; i + 1 < path.size(); ++i)
        for (int j = 0; j < sides; ++j) {
            const auto a = static_cast<Index>(i * static_cast<std::size_t>(sides) + static_cast<std::size_t>(j));
            const auto b = static_cast<Index>(i * static_cast<std::size_t>(sides) + static_cast<std::size_t>((j + 1) % sides));
            add_quad(p, a, b, b + stride, a + stride);
        }
    const std::size_t wall_faces = p.mesh.face_count();
    const auto top = static_cast<Index>((path.size() - 1) * static_cast<std::size_t>(sides));
    if (cap_mode == "fan") {
        for (Index i = 1; i + 1 < stride; ++i) {
            add_tri(p, 0, i + 1, i);
            add_tri(p, top, top + i, top + i + 1);
        }
    } else {
        std::vector<Index> bot_rim(stride), top_rim(stride);
        for (Index i = 0; i < stride; ++i) {
            bot_rim[i] = i;
            top_rim[i] = top + i;
        }
        emit_ring_cap_3d(p.mesh, bot_rim, cap_mode, cap_inset, true);
        emit_ring_cap_3d(p.mesh, top_rim, cap_mode, cap_inset, false);
    }
    // Chart: round the tube by its girth, along it by arc length.
    const double girth = 2.0 * std::numbers::pi * radius;
    std::vector<std::array<double, 2>> chart(p.mesh.vertex_count(), {0.0, 0.0});
    double along = 0;
    for (std::size_t i = 0; i < path.size(); ++i) {
        if (i) along += length(path[i] - path[i - 1]);
        for (int k = 0; k < sides; ++k) chart[i * static_cast<std::size_t>(sides) + static_cast<std::size_t>(k)] = {girth * k / sides, along};
    }
    assign_chart(p, chart, girth, 0);
    // The end caps cannot use (girth, length): give them the section's own plane.
    for (std::size_t f = wall_faces; f < p.mesh.face_count(); ++f) {
        const auto fv = p.mesh.face(f);
        const bool is_top = (cap_mode == "fan") ? (fv[0] >= top) : (dot(face_centroid(p.mesh, f) - path.back(), face_centroid(p.mesh, f) - path.back()) < dot(face_centroid(p.mesh, f) - path.front(), face_centroid(p.mesh, f) - path.front()));
        const Vec3 origin = is_top ? path.back() : path.front();
        const Vec3 bu = is_top ? ring_u.back() : ring_u.front();
        const Vec3 bv = is_top ? ring_v.back() : ring_v.front();
        for (std::size_t i = 0; i < fv.size(); ++i) {
            if (cap_mode == "fan") {
                const double a = static_cast<double>(fv[i] % static_cast<Index>(sides)) * 2.0 * std::numbers::pi / sides;
                p.uv[p.mesh.face_offsets[f] + i] = {static_cast<float>(radius + radius * std::cos(a)), static_cast<float>(radius + radius * std::sin(a))};
            } else {
                const Vec3 d = p.mesh.positions[fv[i]] - origin;
                p.uv[p.mesh.face_offsets[f] + i] = {static_cast<float>(radius + dot(d, bu)), static_cast<float>(radius + dot(d, bv))};
            }
        }
    }
    return p;
}

Proto make_surface(const Options& o) {
    Proto p;
    for (const json::Value& v : o.list("vertices", 3, 16384)) p.mesh.add_vertex(o.vector(v));
    const std::vector<Vec3>& pos = p.mesh.positions;
    auto indices = [&](const std::vector<json::Value>& values) {
        if (values.size() < 3 || values.size() > kMaxCorners) fail("A face loop needs 3…" + std::to_string(kMaxCorners) + " vertex indices");
        std::vector<Index> result;
        for (const json::Value& v : values) {
            const float f = o.number(v);
            if (!(f >= 0 && f < static_cast<float>(pos.size()) && std::round(f) == f)) fail("Face index out of bounds");
            result.push_back(static_cast<Index>(f));
        }
        if (std::set<Index>(result.begin(), result.end()).size() != result.size()) fail("Face repeats a vertex index");
        return result;
    };
    std::size_t polygon_budget = 0;
    for (const json::Value& face : o.list("faces", 1, 24000)) {
        if (!face.is_array() || face.array.empty()) fail("Expected face indices or a list of face loops");
        const json::Value& first = face.array[0];
        std::vector<std::vector<Index>> loops;
        // A vertex index can itself be an arithmetic expression.
        const bool expression = first.is_array() && !first.array.empty() && first.array[0].is_string() &&
                                !first.array[0].string.starts_with("$");
        if (!expression && first.is_array()) {
            for (const json::Value& loop : face.array) {
                if (!loop.is_array()) fail("Expected a face loop");
                loops.push_back(indices(loop.array));
            }
        } else {
            loops.push_back(indices(face.array));
        }
        const std::vector<Index>& c = loops[0];
        if (loops.size() > 1 || c.size() > 4) {
            std::size_t count = 0;
            for (const auto& l : loops) count += l.size();
            if (count > kMaxCorners) fail("Polygon exceeds corner limit");
            polygon_budget += count * count * (loops.size() + 1);
            if (polygon_budget > 4'000'000) fail("Surface polygon tessellation exceeds its work budget");
            for (const Tri& t : triangulate_polygon(loops, pos)) add_tri(p, t[0], t[1], t[2]);
            continue;
        }
        if (c.size() == 3) { add_tri(p, c[0], c[1], c[2]); continue; }
        const Vec3 pa = pos[c[0]], pb = pos[c[1]], pc = pos[c[2]], pd = pos[c[3]];
        const Vec3 first_n = cross(pb - pa, pc - pa), second_n = cross(pc - pa, pd - pa);
        const Vec3 normal = first_n + second_n;
        const Vec3 alt_first = cross(pb - pa, pd - pa), alt_second = cross(pc - pb, pd - pb);
        // Use the interior diagonal when the default split reverses one triangle.
        if (std::min(dot(first_n, normal), dot(second_n, normal)) <= 0 &&
            std::min(dot(alt_first, normal), dot(alt_second, normal)) > 0) {
            add_tri(p, c[0], c[1], c[3]);
            add_tri(p, c[1], c[2], c[3]);
        } else {
            add_quad(p, c[0], c[1], c[2], c[3]);
        }
    }
    return p;
}

// ---- curves ---------------------------------------------------------------------

std::vector<double> arc_lengths(const Curve& c) {
    std::vector<double> lengths{0.0};
    for (std::size_t i = 0; i < c.segments(); ++i) {
        const double len = length(c.points[(i + 1) % c.points.size()] - c.points[i]);
        const double total = lengths[i] + len;
        if (!(len > 1e-5 && std::isfinite(total) && total > lengths[i]))
            fail("Spline has coincident points or cannot represent the segment length");
        lengths.push_back(total);
    }
    return lengths;
}

Vec3 transported(Vec3 u, Vec3 a, Vec3 b) {
    if (!(dot(a, b) > -0.99999)) fail("Profile sweep has an ambiguous frame at a tangent reversal");
    const Vec3 rotated = rotate_from_to(u, a, b);
    const Vec3 flat = rotated - b * dot(rotated, b);
    return flat / length(flat);
}

void sweep_frames(const Curve& curve, Vec3 up, std::vector<Vec3>& us, std::vector<Vec3>& vs) {
    const std::vector<double> lengths = arc_lengths(curve);
    const std::size_t n = curve.points.size(), segs = curve.segments();
    std::vector<Vec3> seg(segs);
    for (std::size_t i = 0; i < segs; ++i) {
        const Vec3 d = curve.points[(i + 1) % n] - curve.points[i];
        seg[i] = d / length(d);
    }
    std::vector<Vec3> tangents(n);
    for (std::size_t i = 0; i < n; ++i) {
        const std::size_t prev = curve.closed ? (i + segs - 1) % segs : (i == 0 ? 0 : i - 1);
        const std::size_t next = std::min(i, segs - 1);
        const Vec3 bis = seg[prev] + seg[next];
        if (!(dot(bis, bis) > 1e-10)) fail("Profile sweep reverses direction at point " + std::to_string(i));
        tangents[i] = bis / length(bis);
    }
    const Vec3 first = tangents[0];
    const Vec3 seed = std::abs(dot(first, up)) < 0.999 ? up : (std::abs(first.x) < 0.9 ? Vec3{1, 0, 0} : Vec3{0, 1, 0});
    std::vector<Vec3> axes(n);
    axes[0] = normalize_or(cross(seed, first), {1, 0, 0});
    for (std::size_t i = 1; i < n; ++i) axes[i] = transported(axes[i - 1], tangents[i - 1], tangents[i]);
    double correction = 0;
    if (curve.closed) {
        const Vec3 last = transported(axes[n - 1], tangents[n - 1], first);
        correction = std::atan2(dot(cross(last, axes[0]), first), dot(last, axes[0]));
    }
    us.resize(n);
    vs.resize(n);
    for (std::size_t i = 0; i < n; ++i) {
        const double angle = correction * (lengths[i] / lengths[segs]);
        us[i] = rotate_about(axes[i], tangents[i], angle);
        vs[i] = cross(tangents[i], us[i]);
    }
}

// ---- box editing ------------------------------------------------------------------

constexpr int kBoxFaces[6][4] = {{0, 4, 6, 2}, {1, 3, 7, 5}, {0, 1, 5, 4}, {2, 6, 7, 3}, {0, 2, 3, 1}, {4, 5, 7, 6}};

std::array<Vec3, 8> box_corners(Vec3 lo, Vec3 hi) {
    std::array<Vec3, 8> c;
    for (int i = 0; i < 8; ++i) c[static_cast<std::size_t>(i)] = {(i & 1) ? hi.x : lo.x, (i & 2) ? hi.y : lo.y, (i & 4) ? hi.z : lo.z};
    return c;
}

const Part& editing_box(const Geometry& value, Vec3& lo, Vec3& hi) {
    if (value.parts.size() != 1 || !value.points.empty() || !value.curves.empty()) fail("Expected one box solid");
    const Part& part = value.parts[0];
    const Mesh& m = part.proto->mesh;
    const Aabb box = bounds(m);
    lo = box.lo;
    hi = box.hi;
    bool ok = m.vertex_count() == 8 && m.face_count() == 6 && part.proto->edges.empty() && lo.x < hi.x && lo.y < hi.y && lo.z < hi.z;
    if (ok) {
        const auto corners = box_corners(lo, hi);
        std::set<std::array<double, 3>> want, have;
        for (const Vec3& c : corners) want.insert({c.x, c.y, c.z});
        for (const Vec3& p : m.positions) have.insert({p.x, p.y, p.z});
        ok = want == have;
        for (std::size_t f = 0; ok && f < 6; ++f) {
            const auto fv = m.face(f);
            if (fv.size() != 4) { ok = false; break; }
            // Each face must lie in one bounding plane.
            bool planar = false;
            for (int axis = 0; axis < 3 && !planar; ++axis)
                for (double side : {lo[axis], hi[axis]}) {
                    bool all = true;
                    for (Index v : fv) all = all && m.positions[v][axis] == side;
                    planar = planar || all;
                }
            ok = planar;
        }
    }
    if (!ok) fail("Expected an unsubdivided box solid");
    return part;
}

}  // namespace

// ---- public builders -----------------------------------------------------------

ProtoPtr make_primitive(std::string_view type, const Options& o) {
    if (type == "line") {
        const int count = o.integer("count", 10, 1, 10000);
        const Vec3 start = o.vector("start", {}), step = o.vector("step", {0, 0, 1});
        Proto p;
        for (int i = 0; i < count; ++i) p.mesh.add_vertex(start + step * static_cast<double>(i));
        for (Index i = 0; i + 1 < static_cast<Index>(count); ++i) p.edges.push_back({i, i + 1});
        return share(std::move(p));
    }
    if (type == "grid" || type == "cube") {
        const bool grid = type == "grid";
        const Vec3 size = o.vector("size", {1, 1, 1});
        const Vec3 div = o.vector("divisions", grid ? Vec3{3, 3, 1} : Vec3{2, 2, 2});
        int n[3];
        for (int a = 0; a < 3; ++a) {
            if (!(size[a] >= 0 && size[a] <= 10000 && div[a] >= 1 && div[a] <= 512 && std::round(div[a]) == div[a]))
                fail("Invalid primitive size or divisions");
            n[a] = static_cast<int>(div[a]);
        }
        if (grid && n[2] != 1) fail("Invalid primitive size or divisions");
        std::vector<int> axes;
        for (int a = 0; a < 3; ++a)
            if (n[a] > 1) axes.push_back(a);
        if (axes.size() == 3) return share(make_cuboid(size, n));
        if (axes.empty()) {
            Proto p;
            p.mesh.add_vertex({});
            return share(std::move(p));
        }
        // Blender's YZ cube fallback rotates its grid around Y.
        const bool two = axes.size() == 2;
        const int u = two && axes[0] == 1 ? 2 : axes[0];
        const int v = two && u == 2 ? 1 : (two ? axes[1] : (u + 1) % 3);
        return share(make_grid(size, n[u], two ? n[v] : 1, u, v, two && u == 2 ? -1.0 : 1.0));
    }
    if (type == "cylinder") {
        const double radius = o.scalar("radius", 1, 0.0001f, 10000), depth = o.scalar("depth", 2, 0.0001f, 10000);
        const int sides = o.integer("sides", 32, 3, 512);
        const int rings = o.integer("rings", 1, 1, 64);
        const double inset_f = o.scalar("capInset", 0.15f, 0.02f, 0.45f);
        std::string cap_mode = "fan";
        if (const json::Value* cv = o.get("cap")) {
            if (!cv->is_string()) fail("cylinder cap must be fan, ngon, inset or quad");
            cap_mode = cv->string;
            if (cap_mode != "fan" && cap_mode != "ngon" && cap_mode != "inset" && cap_mode != "quad")
                fail("cylinder cap must be fan, ngon, inset or quad");
        }
        if (cap_mode == "quad" && (sides < 4 || (sides % 2) != 0))
            fail("cylinder cap quad requires an even sides >= 4");

        Proto p;
        for (int ring = 0; ring <= rings; ++ring) {
            const double z = -depth / 2.0 + depth * (static_cast<double>(ring) / static_cast<double>(rings));
            for (int i = 0; i < sides; ++i) {
                const double a = 2.0 * std::numbers::pi * i / sides;
                p.mesh.add_vertex({radius * std::cos(a), radius * std::sin(a), z});
            }
        }
        const auto n = static_cast<Index>(sides);
        for (int ring = 0; ring < rings; ++ring) {
            const auto r0 = static_cast<Index>(ring * sides);
            const auto r1 = r0 + n;
            for (Index i = 0; i < n; ++i) {
                const Index j = (i + 1) % n;
                add_quad(p, r0 + i, r0 + j, r1 + j, r1 + i);
            }
        }

        const Index top_base = static_cast<Index>(rings * sides);
        if (cap_mode == "fan") {
            for (Index i = 1; i + 1 < n; ++i) {
                add_tri(p, 0, i + 1, i);
                add_tri(p, top_base, top_base + i, top_base + i + 1);
            }
        } else if (cap_mode == "ngon") {
            std::vector<Index> bot(n), top(n);
            for (Index i = 0; i < n; ++i) {
                bot[i] = (n - i) % n;
                top[i] = top_base + i;
            }
            p.mesh.add_face(bot);
            p.mesh.add_face(top);
        } else if (cap_mode == "inset") {
            // Concentric holding ring on both caps + clean inner n-gon (holds cylindrical rims under SDS).
            const double r_in = radius * (1.0 - inset_f);
            const Index b_in = static_cast<Index>(p.mesh.vertex_count());
            for (int i = 0; i < sides; ++i) {
                const double a = 2.0 * std::numbers::pi * i / sides;
                p.mesh.add_vertex({r_in * std::cos(a), r_in * std::sin(a), -depth / 2.0});
            }
            const Index t_in = static_cast<Index>(p.mesh.vertex_count());
            for (int i = 0; i < sides; ++i) {
                const double a = 2.0 * std::numbers::pi * i / sides;
                p.mesh.add_vertex({r_in * std::cos(a), r_in * std::sin(a), depth / 2.0});
            }
            std::vector<Index> bot_core(n), top_core(n);
            for (Index i = 0; i < n; ++i) {
                const Index j = (i + 1) % n;
                add_quad(p, j, i, b_in + i, b_in + j);
                add_quad(p, top_base + i, top_base + j, t_in + j, t_in + i);
                bot_core[i] = b_in + ((n - i) % n);
                top_core[i] = t_in + i;
            }
            p.mesh.add_face(bot_core);
            p.mesh.add_face(top_core);
        } else {
            // All-quad grid-fill cap (zero triangles, zero high-valence poles):
            // grid (a + 1) x (b + 1) with perimeter 2*(a + b) == sides.
            const int half = sides / 2;
            const int b = half / 2;
            const int a = half - b;
            auto emit_quad_cap = [&](Index rim_base, double z, bool flip_winding) {
                // Map perimeter index k in [0, sides) to grid coordinate (gx, gy):
                // bottom edge (0..a, 0), right edge (a, 0..b), top edge (a..0, b), left edge (0, b..0).
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

                // Interior vertices placed by Coons patch interpolation of the exact circular boundary!
                for (int gy = 1; gy < b; ++gy) {
                    const double v = static_cast<double>(gy) / static_cast<double>(b);
                    const Vec3 p_left = p.mesh.positions[at(0, gy)];
                    const Vec3 p_right = p.mesh.positions[at(a, gy)];
                    const Vec3 p_00 = p.mesh.positions[at(0, 0)], p_a0 = p.mesh.positions[at(a, 0)];
                    const Vec3 p_0b = p.mesh.positions[at(0, b)], p_ab = p.mesh.positions[at(a, b)];
                    for (int gx = 1; gx < a; ++gx) {
                        const double u = static_cast<double>(gx) / static_cast<double>(a);
                        const Vec3 p_bot = p.mesh.positions[at(gx, 0)];
                        const Vec3 p_top = p.mesh.positions[at(gx, b)];
                        const Vec3 lin_u = p_left * (1.0 - u) + p_right * u;
                        const Vec3 lin_v = p_bot * (1.0 - v) + p_top * v;
                        const Vec3 bilin = (p_00 * (1.0 - u) + p_a0 * u) * (1.0 - v) + (p_0b * (1.0 - u) + p_ab * u) * v;
                        Vec3 q = lin_u + lin_v - bilin;
                        q.z = z;
                        at(gx, gy) = p.mesh.add_vertex(q);
                    }
                }
                for (int gy = 0; gy < b; ++gy) {
                    for (int gx = 0; gx < a; ++gx) {
                        const Index i0 = at(gx, gy), i1 = at(gx + 1, gy), i2 = at(gx + 1, gy + 1), i3 = at(gx, gy + 1);
                        if (flip_winding) add_quad(p, i0, i3, i2, i1);
                        else add_quad(p, i0, i1, i2, i3);
                    }
                }
            };
            emit_quad_cap(0, -depth / 2.0, true);
            emit_quad_cap(top_base, depth / 2.0, false);
        }
        return share(std::move(p));
    }
    if (type == "surface") return share(make_surface(o));
    if (type == "sweep") {
        std::vector<Vec3> path;
        for (const json::Value& v : o.list("path", 2, 4096)) path.push_back(o.vector(v));
        const double radius = o.scalar("radius", 0.05f, 0.0001f, 1000);
        const int sides = o.integer("sides", 8, 3, 64);
        const double cap_inset = o.scalar("capInset", 0.15f, 0.02f, 0.45f);
        std::string cap_mode = "fan";
        if (const json::Value* cv = o.get("cap")) {
            if (!cv->is_string()) fail("sweep cap must be fan, ngon, inset or quad");
            cap_mode = cv->string;
            if (cap_mode != "fan" && cap_mode != "ngon" && cap_mode != "inset" && cap_mode != "quad")
                fail("sweep cap must be fan, ngon, inset or quad");
        }
        return share(make_tube(path, radius, sides, cap_mode, cap_inset));
    }
    fail("Expected a geometry primitive");
}

ProtoPtr make_extrusion(const Options& o) {
    const double depth = o.scalar("depth", 1, 0.0001f, 10000);
    const std::vector<P2> polygon = profile_points(o);
    const double cap_inset = o.scalar("capInset", 0.15f, 0.02f, 0.45f);
    std::string cap_mode = "tri";
    if (const json::Value* cv = o.get("cap")) {
        if (!cv->is_string()) fail("extrude cap must be tri, ngon, inset or quad");
        cap_mode = cv->string;
        if (cap_mode != "tri" && cap_mode != "ngon" && cap_mode != "inset" && cap_mode != "quad")
            fail("extrude cap must be tri, ngon, inset or quad");
    }

    // Optional open through-bores (`holes: [[[x,y], ...], ...]`).
    if (const json::Value* hv = o.get("holes")) {
        if (!hv->is_array() || hv->array.size() > 16) fail("extrude holes must contain 0..16 entries");
        if (!hv->array.empty()) {
            std::vector<std::vector<P2>> loops;
            {
                std::vector<int> b;
                std::vector<Tri> c;
                profile_topology(polygon, b, c);
                std::vector<P2> out_loop;
                out_loop.reserve(b.size());
                for (int idx : b) out_loop.push_back(polygon[static_cast<std::size_t>(idx)]);
                loops.push_back(std::move(out_loop));
            }
            for (const json::Value& h : hv->array) {
                const std::vector<P2> hpoly = profile_points(o, &h);
                std::vector<int> b;
                std::vector<Tri> c;
                profile_topology(hpoly, b, c);
                std::reverse(b.begin(), b.end());  // holes wind CW opposite to CCW outer loop
                std::vector<P2> hloop;
                hloop.reserve(b.size());
                for (int idx : b) hloop.push_back(hpoly[static_cast<std::size_t>(idx)]);
                loops.push_back(std::move(hloop));
            }
            std::size_t total_pts = 0;
            for (const auto& l : loops) total_pts += l.size();
            if (total_pts > kMaxCorners) fail("extrude with holes exceeds polygon corner budget");

            // Fast all-quad annular path: 1 hole with same vertex count as outer + cap quad/inset.
            if (loops.size() == 2 && loops[0].size() == loops[1].size() && (cap_mode == "quad" || cap_mode == "inset")) {
                const std::size_t n = loops[0].size();
                // Reverse CW hole to CCW for 1-to-1 radial bridge alignment.
                std::vector<P2> inner_ccw(loops[1].rbegin(), loops[1].rend());
                std::size_t best_shift = 0;
                double best_cost = std::numeric_limits<double>::infinity();
                for (std::size_t sh = 0; sh < n; ++sh) {
                    double cost = 0.0;
                    for (std::size_t i = 0; i < n; ++i) {
                        const P2 d = loops[0][i] - inner_ccw[(i + sh) % n];
                        cost += d.x * d.x + d.y * d.y;
                    }
                    if (cost < best_cost) { best_cost = cost; best_shift = sh; }
                }
                Proto p;
                // Ring 0: outer z=0, Ring 1: outer z=depth, Ring 2: inner z=0, Ring 3: inner z=depth.
                for (std::size_t i = 0; i < n; ++i) p.mesh.add_vertex({loops[0][i].x, loops[0][i].y, 0.0});
                for (std::size_t i = 0; i < n; ++i) p.mesh.add_vertex({loops[0][i].x, loops[0][i].y, depth});
                for (std::size_t i = 0; i < n; ++i) {
                    const P2 q = inner_ccw[(i + best_shift) % n];
                    p.mesh.add_vertex({q.x, q.y, 0.0});
                }
                for (std::size_t i = 0; i < n; ++i) {
                    const P2 q = inner_ccw[(i + best_shift) % n];
                    p.mesh.add_vertex({q.x, q.y, depth});
                }
                const auto N = static_cast<Index>(n);
                for (std::size_t i = 0; i < n; ++i) {
                    const Index a = static_cast<Index>(i);
                    const Index b = static_cast<Index>((i + 1) % n);
                    add_quad(p, a, b, b + N, a + N);                      // outer wall (+out)
                    add_quad(p, b + 2 * N, a + 2 * N, a + 3 * N, b + 3 * N);  // inner bore wall (-in)
                    add_quad(p, a + N, b + N, b + 3 * N, a + 3 * N);      // top annular cap (+z)
                    add_quad(p, b, a, a + 2 * N, b + 2 * N);              // bottom annular cap (-z)
                }
                return share(std::move(p));
            }

            // General 1..16 holes: validate + triangulate bottom/top caps with clean quad outer & bore walls.
            std::vector<std::vector<Index>> loop_ids;
            std::vector<Vec3> bot_pts, top_pts;
            bot_pts.reserve(total_pts);
            top_pts.reserve(total_pts);
            Index cursor = 0;
            for (const auto& l : loops) {
                std::vector<Index> ids(l.size());
                for (std::size_t i = 0; i < l.size(); ++i) {
                    ids[i] = cursor + static_cast<Index>(i);
                    bot_pts.push_back({l[i].x, l[i].y, 0.0});
                    top_pts.push_back({l[i].x, l[i].y, depth});
                }
                cursor += static_cast<Index>(l.size());
                loop_ids.push_back(std::move(ids));
            }
            const std::vector<Tri> caps_tri = triangulate_polygon(loop_ids, bot_pts);
            const auto stride = static_cast<Index>(total_pts);
            Proto p;
            for (const Vec3& v : bot_pts) p.mesh.add_vertex(v);
            for (const Vec3& v : top_pts) p.mesh.add_vertex(v);
            for (const Tri& t : caps_tri) add_tri(p, t[2], t[1], t[0]);
            for (const Tri& t : caps_tri) add_tri(p, t[0] + stride, t[1] + stride, t[2] + stride);
            for (const auto& ids : loop_ids) {
                for (std::size_t i = 0; i < ids.size(); ++i) {
                    const Index a = ids[i], b = ids[(i + 1) % ids.size()];
                    add_quad(p, a, b, b + stride, a + stride);
                }
            }
            return share(std::move(p));
        }
    }

    std::vector<int> border;
    std::vector<Tri> caps;
    profile_topology(polygon, border, caps);
    if (cap_mode == "quad" && (border.size() < 4 || (border.size() % 2) != 0))
        fail("extrude cap quad requires an even profile vertex count >= 4");
    const auto offset = static_cast<Index>(polygon.size());
    Proto p;
    for (const P2& q : polygon) p.mesh.add_vertex({q.x, q.y, 0});
    for (const P2& q : polygon) p.mesh.add_vertex({q.x, q.y, depth});
    if (cap_mode == "tri") {
        for (const Tri& t : caps) add_tri(p, t[2], t[1], t[0]);
        for (const Tri& t : caps) add_tri(p, t[0] + offset, t[1] + offset, t[2] + offset);
    } else {
        std::vector<Index> bot_rim(border.size()), top_rim(border.size());
        for (std::size_t i = 0; i < border.size(); ++i) {
            bot_rim[i] = static_cast<Index>(border[i]);
            top_rim[i] = static_cast<Index>(border[i]) + offset;
        }
        emit_ring_cap_3d(p.mesh, bot_rim, cap_mode, cap_inset, true);
        emit_ring_cap_3d(p.mesh, top_rim, cap_mode, cap_inset, false);
    }
    const std::size_t wall_start = p.mesh.face_count();
    for (std::size_t i = 0; i < border.size(); ++i) {
        const auto a = static_cast<Index>(border[i]), b = static_cast<Index>(border[(i + 1) % border.size()]);
        add_quad(p, a, b, b + offset, a + offset);
    }
    // chart "perimeter": the sides unroll to u = arc length round the profile, v = height along the extrusion (metres);
    // the caps keep the profile plane. Opt-in, so documents that rely on the box projection are unchanged.
    const json::Value* chart = o.get("chart");
    if (chart && chart->is_string()) {
        if (chart->string != "perimeter") fail("extrude chart must be perimeter");
        std::vector<double> s(border.size() + 1, 0.0);
        for (std::size_t i = 0; i < border.size(); ++i) {
            const P2 q0 = polygon[static_cast<std::size_t>(border[i])], q1 = polygon[static_cast<std::size_t>(border[(i + 1) % border.size()])];
            s[i + 1] = s[i] + std::hypot(q1.x - q0.x, q1.y - q0.y);
        }
        p.uv.reserve(p.mesh.corner_count());
        for (std::size_t f = 0; f < wall_start; ++f) {
            for (Index v : p.mesh.face(f)) {
                const Vec3 pos = p.mesh.positions[v];
                p.uv.push_back({static_cast<float>(pos.x), static_cast<float>(pos.y)});
            }
        }
        for (std::size_t i = 0; i < border.size(); ++i) {
            const auto u0 = static_cast<float>(s[i]), u1 = static_cast<float>(s[i + 1]), h = static_cast<float>(depth);
            p.uv.push_back({u0, 0.0f});
            p.uv.push_back({u1, 0.0f});
            p.uv.push_back({u1, h});
            p.uv.push_back({u0, h});
        }
        if (p.uv.size() != p.mesh.corner_count()) p.uv.clear();
    }
    return share(std::move(p));
}

namespace {

std::vector<Vec3> resample_closed_ring_3d(const std::vector<Vec3>& ring, std::size_t target) {
    const std::size_t n = ring.size();
    if (n == target) return ring;
    std::vector<double> cum(n + 1, 0.0);
    for (std::size_t i = 0; i < n; ++i) cum[i + 1] = cum[i] + length(ring[(i + 1) % n] - ring[i]);
    const double total = cum.back();
    if (!(total > 1e-12)) fail("loft section has zero perimeter");
    std::vector<Vec3> out;
    out.reserve(target);
    std::size_t seg = 0;
    for (std::size_t k = 0; k < target; ++k) {
        const double d = total * static_cast<double>(k) / static_cast<double>(target);
        while (seg + 1 < n && cum[seg + 1] < d) ++seg;
        const double span = cum[seg + 1] - cum[seg];
        const double t = span > 1e-12 ? (d - cum[seg]) / span : 0.0;
        const Vec3 a = ring[seg], b = ring[(seg + 1) % n];
        out.push_back(a + (b - a) * t);
    }
    return out;
}

Vec3 ring_center_3d(const std::vector<Vec3>& r) {
    Vec3 c{0, 0, 0};
    for (const Vec3& p : r) c = c + p;
    return c / static_cast<double>(r.size());
}

Vec3 ring_area_normal_3d(const std::vector<Vec3>& r) {
    const Vec3 c = ring_center_3d(r);
    Vec3 n{0, 0, 0};
    for (std::size_t i = 0; i < r.size(); ++i)
        n = n + cross(r[i] - c, r[(i + 1) % r.size()] - c);
    return n;
}

}  // namespace

ProtoPtr make_section_loft(const Options& o) {
    const auto& sec_list = o.list("sections", 2, 128);
    const bool closed = o.integer("closed", 0, 0, 1) != 0;
    if (closed && sec_list.size() < 3) fail("closed loft needs at least 3 sections");
    const int req_segs = o.integer("segments", 0, 0, 512);
    const int rings_per_span = o.integer("rings", 1, 1, 16);
    const bool smooth_interp = o.integer("smooth", 0, 0, 1) != 0;
    const double cap_inset = o.scalar("capInset", 0.15f, 0.02f, 0.45f);
    std::string cap_mode = "quad";
    if (const json::Value* cv = o.get("cap")) {
        if (!cv->is_string()) fail("loft cap must be quad, inset, tri, ngon or open");
        cap_mode = cv->string;
        if (cap_mode != "quad" && cap_mode != "inset" && cap_mode != "tri" && cap_mode != "ngon" && cap_mode != "open")
            fail("loft cap must be quad, inset, tri, ngon or open");
    }

    std::vector<std::vector<Vec3>> raw_sections;
    raw_sections.reserve(sec_list.size());
    std::size_t max_n = 0;
    bool all_same = true;
    for (const json::Value& sv : sec_list) {
        std::vector<Vec3> ring;
        if (sv.is_array()) {
            if (sv.array.size() < 3 || sv.array.size() > 512) fail("loft section must have 3..512 points");
            ring.reserve(sv.array.size());
            for (const json::Value& pv : sv.array) ring.push_back(o.vector(pv));
        } else if (sv.is_object()) {
            const Options so{&sv, o.controls, o.point};
            const std::vector<P2> prof = profile_points(so);
            const double z = so.scalar("z", 0.0f, -1e6f, 1e6f);
            const double sc = so.scalar("scale", 1.0f, 1e-4f, 1e4f);
            const double twist = so.scalar("twist", 0.0f, -1000.0f, 1000.0f);
            const Vec3 tr = so.vector("t", {0, 0, 0});
            const double ct = std::cos(twist), st = std::sin(twist);
            ring.reserve(prof.size());
            for (const P2& q : prof) {
                const double x = (q.x * ct - q.y * st) * sc + tr.x;
                const double y = (q.x * st + q.y * ct) * sc + tr.y;
                ring.push_back({x, y, z + tr.z});
            }
        } else {
            fail("loft section must be an array of [x,y,z] points or {profile, z, scale, t, twist}");
        }
        if (!raw_sections.empty() && ring.size() != raw_sections[0].size()) all_same = false;
        max_n = std::max(max_n, ring.size());
        raw_sections.push_back(std::move(ring));
    }

    std::size_t segs = req_segs > 0 ? static_cast<std::size_t>(req_segs) : (all_same ? raw_sections[0].size() : max_n);
    if (!closed && cap_mode == "quad" && (segs < 4 || (segs % 2) != 0)) {
        if (req_segs == 0) segs = std::max<std::size_t>(4, segs + (segs % 2));
        else fail("loft cap quad requires an even segment count >= 4");
    }

    // Resample every section to `segs` and align winding + starting index to prevent twist/flip.
    std::vector<std::vector<Vec3>> key_rings;
    key_rings.reserve(raw_sections.size());
    for (const auto& rs : raw_sections) key_rings.push_back(resample_closed_ring_3d(rs, segs));

    // Orient first ring so its area normal aligns with the direction from ring 0 to ring 1.
    const Vec3 axis0 = ring_center_3d(key_rings[1]) - ring_center_3d(key_rings[0]);
    if (dot(ring_area_normal_3d(key_rings[0]), axis0) < 0.0) {
        std::reverse(key_rings[0].begin(), key_rings[0].end());
    }
    for (std::size_t k = 1; k < key_rings.size(); ++k) {
        const auto& prev = key_rings[k - 1];
        auto& cur = key_rings[k];
        auto best_align = [&](const std::vector<Vec3>& cand) {
            std::size_t best_sh = 0;
            double best_cost = std::numeric_limits<double>::infinity();
            for (std::size_t sh = 0; sh < segs; ++sh) {
                double cost = 0.0;
                for (std::size_t i = 0; i < segs; ++i) {
                    const Vec3 d = cand[(i + sh) % segs] - prev[i];
                    cost += dot(d, d);
                }
                if (cost < best_cost) { best_cost = cost; best_sh = sh; }
            }
            return std::pair{best_cost, best_sh};
        };
        std::vector<Vec3> rev(cur.rbegin(), cur.rend());
        const auto [c_fwd, sh_fwd] = best_align(cur);
        const auto [c_rev, sh_rev] = best_align(rev);
        const auto& chosen = (c_rev < c_fwd) ? rev : cur;
        const std::size_t sh = (c_rev < c_fwd) ? sh_rev : sh_fwd;
        std::vector<Vec3> aligned(segs);
        for (std::size_t i = 0; i < segs; ++i) aligned[i] = chosen[(i + sh) % segs];
        cur = std::move(aligned);
    }

    // Optional intermediate rings per span (linear or Catmull-Rom smooth).
    std::vector<std::vector<Vec3>> rings;
    const std::size_t nk = key_rings.size();
    const std::size_t n_spans = closed ? nk : nk - 1;
    auto key_at = [&](int idx) -> const std::vector<Vec3>& {
        if (closed) return key_rings[static_cast<std::size_t>((idx % static_cast<int>(nk) + static_cast<int>(nk)) % static_cast<int>(nk))];
        return key_rings[static_cast<std::size_t>(std::clamp(idx, 0, static_cast<int>(nk) - 1))];
    };
    for (std::size_t sp = 0; sp < n_spans; ++sp) {
        const auto& r0 = key_at(static_cast<int>(sp) - 1);
        const auto& r1 = key_at(static_cast<int>(sp));
        const auto& r2 = key_at(static_cast<int>(sp) + 1);
        const auto& r3 = key_at(static_cast<int>(sp) + 2);
        for (int sub = 0; sub < rings_per_span; ++sub) {
            const double t = static_cast<double>(sub) / static_cast<double>(rings_per_span);
            if (sub == 0) {
                rings.push_back(r1);
            } else if (!smooth_interp) {
                std::vector<Vec3> mid(segs);
                for (std::size_t i = 0; i < segs; ++i) mid[i] = r1[i] + (r2[i] - r1[i]) * t;
                rings.push_back(std::move(mid));
            } else {
                const double t2 = t * t, t3 = t2 * t;
                std::vector<Vec3> mid(segs);
                for (std::size_t i = 0; i < segs; ++i) {
                    mid[i] = (r1[i] * 2.0 + (r2[i] - r0[i]) * t
                              + (r0[i] * 2.0 - r1[i] * 5.0 + r2[i] * 4.0 - r3[i]) * t2
                              + (r0[i] * -1.0 + r1[i] * 3.0 - r2[i] * 3.0 + r3[i]) * t3) * 0.5;
                }
                rings.push_back(std::move(mid));
            }
        }
    }
    if (!closed) rings.push_back(key_rings.back());

    const std::size_t nr = rings.size();
    Proto p;
    for (const auto& r : rings)
        for (const Vec3& q : r) p.mesh.add_vertex(q);
    const auto S = static_cast<Index>(segs);
    const std::size_t wall_spans = closed ? nr : nr - 1;
    for (std::size_t k = 0; k < wall_spans; ++k) {
        const Index a_base = static_cast<Index>(k) * S;
        const Index b_base = static_cast<Index>((k + 1) % nr) * S;
        for (std::size_t i = 0; i < segs; ++i) {
            const Index i0 = static_cast<Index>(i), i1 = static_cast<Index>((i + 1) % segs);
            add_quad(p, a_base + i0, a_base + i1, b_base + i1, b_base + i0);
        }
    }
    if (!closed && cap_mode != "open") {
        std::vector<Index> bot_rim(segs), top_rim(segs);
        const Index top_base = static_cast<Index>(nr - 1) * S;
        for (std::size_t i = 0; i < segs; ++i) {
            bot_rim[i] = static_cast<Index>(i);
            top_rim[i] = top_base + static_cast<Index>(i);
        }
        const std::string emode = (cap_mode == "tri") ? "fan" : cap_mode;
        emit_ring_cap_3d(p.mesh, bot_rim, emode, cap_inset, true);
        emit_ring_cap_3d(p.mesh, top_rim, emode, cap_inset, false);
    }
    // Check signed volume on closed solids and flip if inside out.
    if (closed || cap_mode != "open") {
        double vol = 0.0;
        const Vec3 origin = ring_center_3d(rings[0]);
        for (std::size_t f = 0; f < p.mesh.face_count(); ++f) {
            const auto fv = p.mesh.face(f);
            const Vec3 p0 = p.mesh.positions[fv[0]] - origin;
            for (std::size_t i = 1; i + 1 < fv.size(); ++i)
                vol += dot(p0, cross(p.mesh.positions[fv[i]] - origin, p.mesh.positions[fv[i + 1]] - origin));
        }
        if (vol < 0.0) {
            for (std::size_t f = 0; f < p.mesh.face_count(); ++f) {
                const std::size_t beg = p.mesh.face_offsets[f], end = p.mesh.face_offsets[f + 1];
                std::reverse(p.mesh.face_indices.begin() + static_cast<std::ptrdiff_t>(beg),
                             p.mesh.face_indices.begin() + static_cast<std::ptrdiff_t>(end));
            }
        }
    }
    return share(std::move(p));
}

ProtoPtr make_profile_solid(const Options& o) {
    const double depth = o.scalar("depth", 1, 0.0001f, 10000);
    const double bevel = o.scalar("bevel", 0, 0, 100);
    if (!(bevel * 2 < depth)) fail("Rim bevel exceeds half the solid depth");
    const std::vector<P2> outer = profile_points(o);
    std::vector<std::vector<P2>> polygons{outer};
    if (const json::Value* holes = o.get("holes")) {
        if (!holes->is_array() || holes->array.size() > 16) fail("holes must contain 0…16 entries");
        for (const json::Value& h : holes->array) polygons.push_back(profile_points(o, &h));
    }
    std::vector<std::vector<P2>> loops;
    std::size_t count = 0;
    for (std::size_t i = 0; i < polygons.size(); ++i) {
        std::vector<int> border;
        std::vector<Tri> caps;
        profile_topology(polygons[i], border, caps);
        if (i != 0) std::reverse(border.begin(), border.end());
        std::vector<P2> loop;
        for (int b : border) loop.push_back(polygons[i][static_cast<std::size_t>(b)]);
        count += loop.size();
        loops.push_back(std::move(loop));
    }
    if (count > kMaxCorners) fail("Profile solid exceeds polygon corner budget");
    bool has_selection = false;
    std::set<std::pair<P2, P2>> selected;  // unordered endpoint pairs, stored sorted
    auto edge = [](P2 a, P2 b) { return a < b ? std::pair{a, b} : std::pair{b, a}; };
    if (o.get("rimEdges")) {
        has_selection = true;
        std::set<int> seen;
        for (const json::Value& v : o.list("rimEdges", 1, outer.size())) {
            const float f = o.number(v);
            if (!(f >= 0 && f < static_cast<float>(outer.size()) && std::round(f) == f)) fail("Rim edge index is outside the outer profile");
            const int i = static_cast<int>(f);
            if (!seen.insert(i).second) fail("Duplicate rim edge index");
            selected.insert(edge(outer[static_cast<std::size_t>(i)], outer[(static_cast<std::size_t>(i) + 1) % outer.size()]));
        }
    }
    auto inset = [&](const std::vector<P2>& loop, const std::vector<double>& widths) {
        std::vector<P2> out(loop.size());
        const std::size_t k = loop.size();
        for (std::size_t i = 0; i < k; ++i) {
            const P2 point = loop[i];
            const P2 a = norm2(point - loop[(i + k - 1) % k]), b = norm2(loop[(i + 1) % k] - point);
            const P2 na{-a.y, a.x}, nb{-b.y, b.x};
            const double pw = widths[(i + k - 1) % k], nw = widths[i];
            if (pw == 0 && nw == 0) { out[i] = point; continue; }
            const double d = dot2(na, nb);
            if (pw == nw) {
                if (!(1 + d > 0.01)) fail("Rim bevel meets a narrow profile corner");
                out[i] = point + (na + nb) * (pw / (1 + d));
                continue;
            }
            // Very shallow transitions would give a long miter: taper to zero there.
            if (d > 0.98) { out[i] = point; continue; }
            const double den = 1 - d * d;
            if (!(den > 0.01)) fail("Rim bevel meets a narrow profile corner");
            out[i] = point + na * ((pw - d * nw) / den) + nb * ((nw - d * pw) / den);
        }
        return out;
    };
    std::vector<std::vector<P2>> rim = loops;
    if (bevel > 0) {
        for (std::size_t li = 0; li < loops.size(); ++li) {
            const auto& loop = loops[li];
            std::vector<double> widths(loop.size());
            for (std::size_t i = 0; i < loop.size(); ++i) {
                if (!has_selection) { widths[i] = bevel; continue; }
                widths[i] = li == 0 && selected.count(edge(loop[i], loop[(i + 1) % loop.size()])) ? bevel : 0;
            }
            rim[li] = inset(loop, widths);
        }
        for (std::size_t li = 0; li < loops.size(); ++li)
            for (std::size_t i = 0; i < loops[li].size(); ++i) {
                const std::size_t j = (i + 1) % loops[li].size();
                if (!(dot2(loops[li][j] - loops[li][i], rim[li][j] - rim[li][i]) > 0)) fail("Rim bevel reverses a profile edge");
            }
    }
    auto ring = [&](const std::vector<std::vector<P2>>& rings, double z) {
        std::vector<Vec3> out;
        for (const auto& r : rings)
            for (const P2& q : r) out.push_back({q.x, q.y, z});
        return out;
    };
    std::vector<std::vector<Index>> indices;
    Index start = 0;
    for (const auto& loop : loops) {
        std::vector<Index> ids(loop.size());
        for (std::size_t i = 0; i < loop.size(); ++i) ids[i] = start + static_cast<Index>(i);
        start += static_cast<Index>(loop.size());
        indices.push_back(std::move(ids));
    }
    (void)triangulate_polygon(indices, ring(loops, 0));  // validates the stock outline
    const std::vector<Vec3> bottom = ring(rim, 0), upper = ring(rim, depth);
    const std::vector<Tri> bottom_caps = triangulate_polygon(indices, bottom), upper_caps = triangulate_polygon(indices, upper);
    std::vector<std::vector<Vec3>> rings{bottom};
    if (bevel > 0) {
        rings.push_back(ring(loops, bevel));
        rings.push_back(ring(loops, depth - bevel));
    }
    rings.push_back(upper);
    Proto p;
    for (const auto& r : rings)
        for (const Vec3& v : r) p.mesh.add_vertex(v);
    const auto stride = static_cast<Index>(count);
    const Index top = static_cast<Index>(rings.size() - 1) * stride;
    for (const Tri& t : bottom_caps) add_tri(p, t[2], t[1], t[0]);
    for (const Tri& t : upper_caps) add_tri(p, t[0] + top, t[1] + top, t[2] + top);
    for (std::size_t layer = 0; layer + 1 < rings.size(); ++layer) {
        const Index base = static_cast<Index>(layer) * stride;
        for (const auto& loop : indices)
            for (std::size_t i = 0; i < loop.size(); ++i) {
                const Index a = loop[i] + base, b = loop[(i + 1) % loop.size()] + base;
                add_quad(p, a, b, b + stride, a + stride);
            }
    }
    return share(std::move(p));
}

ProtoPtr make_surface_band(const std::vector<Vec3>& alignment, const Options& o) {
    bool increasing = alignment.size() >= 2 && alignment.size() <= 4096;
    for (std::size_t i = 1; increasing && i < alignment.size(); ++i) increasing = alignment[i - 1].x < alignment[i].x;
    if (!increasing) fail("Surface band requires 2…4096 samples with increasing X");
    const double width = o.scalar("width", 1, 0.0001f, 10000), depth = o.scalar("depth", 1, 0.0001f, 10000);
    const double offset = o.scalar("offset", 0, -1e6f, 1e6f), elevation = o.scalar("elevation", 0, -1e6f, 1e6f);
    Proto p;
    const double sides[4][2] = {{-0.5, 0}, {0.5, 0}, {-0.5, depth}, {0.5, depth}};
    for (const auto& s : sides)
        for (const Vec3& q : alignment) p.mesh.add_vertex({q.x, (q.y + offset) + width * s[0], (q.z + elevation) - s[1]});
    const auto n = static_cast<Index>(alignment.size());
    add_quad(p, 0, n, 3 * n, 2 * n);
    add_quad(p, n - 1, 3 * n - 1, 4 * n - 1, 2 * n - 1);
    for (Index i = 0; i + 1 < n; ++i) {
        const Index j = i + 1;
        add_quad(p, i, j, n + j, n + i);
        add_quad(p, i, 2 * n + i, 2 * n + j, j);
        add_quad(p, n + i, n + j, 3 * n + j, 3 * n + i);
        add_quad(p, 2 * n + i, 3 * n + i, 3 * n + j, 2 * n + j);
    }
    return share(std::move(p));
}

Curve make_spline(const Options& o) {
    Curve original;
    for (const json::Value& v : o.list("points", 2, 4096)) original.points.push_back(o.vector(v));
    original.closed = o.integer("closed", 0, 0, 1) != 0;
    const int resolution = o.integer("resolution", 12, 1, 128);
    if (original.closed && original.points.size() < 3) fail("Closed spline needs at least three points");
    (void)arc_lengths(original);
    const json::Value* mode = o.get("interpolation");
    const std::string interpolation = mode && mode->is_string() ? mode->string : (mode ? "?" : "catmullRom");
    if (interpolation == "polyline") return original;
    if (interpolation != "catmullRom") fail("Interpolation must be polyline or catmullRom");
    const std::vector<Vec3>& a = original.points;
    const auto count = static_cast<long>(a.size());
    if (original.segments() * static_cast<std::size_t>(resolution) + (original.closed ? 0 : 1) > 4096) fail("Spline exceeds sample budget");
    auto anchor = [&](long i) {
        if (original.closed) return a[static_cast<std::size_t>(((i % count) + count) % count)];
        return a[static_cast<std::size_t>(std::clamp(i, 0L, count - 1))];
    };
    Curve out;
    out.closed = original.closed;
    for (long segment = 0; segment < static_cast<long>(original.segments()); ++segment) {
        const Vec3 p0 = anchor(segment - 1), p1 = anchor(segment), p2 = anchor(segment + 1), p3 = anchor(segment + 2);
        const Vec3 cubic = (p1 - p2) * 3.0 + (p3 - p0);
        const Vec3 quadratic = (p0 * 2.0 - p1 * 5.0) + (p2 * 4.0 - p3);
        for (int step = 0; step < resolution; ++step) {
            const double t = static_cast<double>(step) / resolution;
            const Vec3 slope = (cubic * t + quadratic) * t + (p2 - p0);
            out.points.push_back(p1 + slope * (t * 0.5));
        }
    }
    if (!out.closed) out.points.push_back(a.back());
    (void)arc_lengths(out);
    return out;
}

Curve resample_curve(const Curve& c, int count) {
    if (c.closed && count < 3) fail("Closed spline needs at least three resampled points");
    const std::vector<double> lengths = arc_lengths(c);
    const std::size_t segs = c.segments();
    const double total = lengths[segs];
    Curve out;
    out.closed = c.closed;
    std::size_t segment = 0;
    for (int i = 0; i < count; ++i) {
        const double distance = total * (static_cast<double>(i) / (c.closed ? count : count - 1));
        while (segment + 1 < segs && lengths[segment + 1] < distance) ++segment;
        const double weight = (distance - lengths[segment]) / (lengths[segment + 1] - lengths[segment]);
        const Vec3 end = c.points[(segment + 1) % c.points.size()];
        out.points.push_back(c.points[segment] + (end - c.points[segment]) * weight);
    }
    if (!c.closed) out.points.back() = c.points.back();
    (void)arc_lengths(out);
    return out;
}

void assign_chart(Proto& p, const std::vector<std::array<double, 2>>& vertex_uv, double wrap_u, double wrap_v, const std::vector<char>* free_u) {
    const Mesh& m = p.mesh;
    p.uv.assign(m.corner_count(), {0.0f, 0.0f});
    if (vertex_uv.size() != m.vertex_count()) { p.uv.clear(); return; }
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        const std::size_t off = m.face_offsets[f];
        // Unwrap toward the corner furthest round, so a face across the seam stays in one piece and
        // the chart runs from 0 to exactly once round.
        double ref_u = -1e300, ref_v = -1e300, sum_u = 0;
        std::size_t owned = 0;
        for (std::size_t i = 0; i < fv.size(); ++i) {
            if (!(free_u && (*free_u)[fv[i]])) ref_u = std::max(ref_u, vertex_uv[fv[i]][0]);
            ref_v = std::max(ref_v, vertex_uv[fv[i]][1]);
        }
        std::vector<double> us(fv.size()), vs(fv.size());
        for (std::size_t i = 0; i < fv.size(); ++i) {
            double u = vertex_uv[fv[i]][0], v = vertex_uv[fv[i]][1];
            if (!(free_u && (*free_u)[fv[i]])) {
                if (wrap_u > 0)
                    while (ref_u - u > wrap_u * 0.5) u += wrap_u;
                sum_u += u;
                ++owned;
            }
            if (wrap_v > 0)
                while (ref_v - v > wrap_v * 0.5) v += wrap_v;
            us[i] = u;
            vs[i] = v;
        }
        for (std::size_t i = 0; i < fv.size(); ++i) {
            const bool is_free = free_u && (*free_u)[fv[i]];
            p.uv[off + i] = {static_cast<float>(is_free && owned ? sum_u / static_cast<double>(owned) : us[i]), static_cast<float>(vs[i])};
        }
    }
}

ProtoPtr curve_proto(const Curve& c) {
    Proto p;
    for (const Vec3& v : c.points) p.mesh.add_vertex(v);
    const auto n = static_cast<Index>(c.points.size());
    for (Index i = 0; i < static_cast<Index>(c.segments()); ++i) p.edges.push_back({i, (i + 1) % n});
    return share(std::move(p));
}

namespace {
std::vector<P2> structural_section(const Options& o, const std::string& kind) {
    const double w = o.scalar("width", 0.16f, 0.0005f, 1000.0f);
    const double h = o.scalar("height", static_cast<float>(w), 0.0005f, 1000.0f);
    const double tf = o.scalar("flange", static_cast<float>(0.12 * std::min(w, h)), 0.0002f, 500.0f);
    const double tw = o.scalar("web", static_cast<float>(tf), 0.0002f, 500.0f);
    const double hw = 0.5 * w, hh = 0.5 * h;
    if (kind == "rect") {
        return {{-hw, -hh}, {hw, -hh}, {hw, hh}, {-hw, hh}};
    }
    if (kind == "ibeam") {
        if (!(tw < w - 1e-6 && 2.0 * tf < h - 1e-6)) fail("ibeam requires web < width and 2 * flange < height");
        const double htw = 0.5 * tw, iy = hh - tf;
        return {{-hw, -hh}, {hw, -hh}, {hw, -iy}, {htw, -iy}, {htw, iy}, {hw, iy},
                {hw, hh},   {-hw, hh}, {-hw, iy}, {-htw, iy}, {-htw, -iy}, {-hw, -iy}};
    }
    if (kind == "channel") {
        if (!(tw < w - 1e-6 && 2.0 * tf < h - 1e-6)) fail("channel requires web < width and 2 * flange < height");
        const double ix = -hw + tw, iy = hh - tf;
        return {{-hw, -hh}, {hw, -hh}, {hw, -iy}, {ix, -iy}, {ix, iy}, {hw, iy}, {hw, hh}, {-hw, hh}};
    }
    if (kind == "angle") {
        if (!(tf < w - 1e-6 && tf < h - 1e-6)) fail("angle requires flange < width and flange < height");
        return {{-hw, -hh}, {hw, -hh}, {hw, -hh + tf}, {-hw + tf, -hh + tf}, {-hw + tf, hh}, {-hw, hh}};
    }
    if (kind == "tee") {
        if (!(tw < w - 1e-6 && tf < h - 1e-6)) fail("tee requires web < width and flange < height");
        const double htw = 0.5 * tw, iy = hh - tf;
        return {{-htw, -hh}, {htw, -hh}, {htw, iy}, {hw, iy}, {hw, hh}, {-hw, hh}, {-hw, iy}, {-htw, iy}};
    }
    if (kind == "cruciform") {
        if (!(tw < w - 1e-6 && tw < h - 1e-6)) fail("cruciform requires web < width and web < height");
        const double htw = 0.5 * tw;
        return {{-htw, -hh}, {htw, -hh}, {htw, -htw}, {hw, -htw}, {hw, htw}, {htw, htw},
                {htw, hh},   {-htw, hh}, {-htw, htw}, {-hw, htw}, {-hw, -htw}, {-htw, -htw}};
    }
    fail("section must be rect, ibeam, channel, angle, tee or cruciform");
}

std::vector<ProtoPtr> sweep_section(const std::vector<Curve>& curves, const std::vector<P2>& profile, bool capped, Vec3 up_raw,
                                    const Options* mod = nullptr);
}  // namespace

std::vector<ProtoPtr> make_profile_sweep(const std::vector<Curve>& curves, const Options& o) {
    std::vector<P2> profile;
    if (const json::Value* sec = o.get("section"); sec && !o.get("profile")) {
        if (!sec->is_string()) fail("section must be a section name");
        profile = structural_section(o, sec->string);
    } else {
        profile = profile_points(o);
    }
    return sweep_section(curves, profile, o.integer("caps", 1, 0, 1) != 0, o.vector("up", {0, 0, 1}), &o);
}

std::vector<ProtoPtr> make_tube_sweep(const std::vector<Curve>& curves, double radius, int sides, Vec3 up) {
    std::vector<P2> circle;
    for (int k = 0; k < sides; ++k) {
        const double a = k * 2.0 * std::numbers::pi / sides;
        circle.push_back({radius * std::cos(a), radius * std::sin(a)});
    }
    return sweep_section(curves, circle, true, up, nullptr);
}

std::vector<ProtoPtr> make_tube_sweep_options(const std::vector<Curve>& curves, const Options& o) {
    const int sides = o.integer("sides", 8, 3, 64);
    const Vec3 up = o.vector("up", {0, 0, 1});
    // Unit circle modulated per station by radius (and optional twist).
    std::vector<P2> unit_circle;
    unit_circle.reserve(static_cast<std::size_t>(sides));
    for (int k = 0; k < sides; ++k) {
        const double a = k * 2.0 * std::numbers::pi / sides;
        unit_circle.push_back({std::cos(a), std::sin(a)});
    }
    // If radius does not depend on station fields and no twist/cap is given, match make_tube_sweep bit-for-bit.
    if (!o.get("twist") && !o.get("cap")) {
        const json::Value* rv = o.get("radius");
        if (!rv || rv->is_number()) {
            const double radius = o.scalar("radius", 0.05f, 0.0001f, 1000.0f);
            return make_tube_sweep(curves, radius, sides, up);
        }
    }
    return sweep_section(curves, unit_circle, true, up, &o);
}

namespace {
std::vector<ProtoPtr> sweep_section(const std::vector<Curve>& curves, const std::vector<P2>& profile, bool capped, Vec3 up_raw,
                                    const Options* mod) {
    std::vector<int> border;
    std::vector<Tri> caps;
    profile_topology(profile, border, caps);
    if (!(dot(up_raw, up_raw) > 1e-10)) fail("Profile sweep up vector must be nonzero");
    const Vec3 up = up_raw / length(up_raw);
    const json::Value* scale_v = mod ? mod->get("scale") : nullptr;
    const json::Value* radius_v = mod ? mod->get("radius") : nullptr;
    const json::Value* twist_v = mod ? mod->get("twist") : nullptr;
    std::string cap_mode = "fan";
    double cap_inset = 0.15;
    if (mod) {
        cap_inset = mod->scalar("capInset", 0.15f, 0.02f, 0.45f);
        if (const json::Value* cv = mod->get("cap")) {
            if (!cv->is_string()) fail("tube/profileSweep cap must be fan, ngon, inset or quad");
            cap_mode = cv->string;
            if (cap_mode != "fan" && cap_mode != "ngon" && cap_mode != "inset" && cap_mode != "quad")
                fail("tube/profileSweep cap must be fan, ngon, inset or quad");
        }
    }
    if (cap_mode == "quad" && (border.size() < 4 || (border.size() % 2) != 0))
        fail("tube/profileSweep cap quad requires an even profile vertex count >= 4");
    const Controls* ctrls = mod ? mod->controls : nullptr;
    CompiledExpr radius_expr;
    CompiledExpr sx_expr, sy_expr, twist_expr;
    bool scale_pair = false;
    if (radius_v) {
        radius_expr = CompiledExpr(radius_v, ctrls, 0.05f);
    } else if (scale_v) {
        if (scale_v->is_array()) {
            if (scale_v->array.size() != 2) fail("Profile sweep scale array must be [sx, sy]");
            sx_expr = CompiledExpr(&scale_v->array[0], ctrls, 1.0f);
            sy_expr = CompiledExpr(&scale_v->array[1], ctrls, 1.0f);
            scale_pair = true;
        } else {
            sx_expr = CompiledExpr(scale_v, ctrls, 1.0f);
        }
    }
    if (twist_v) twist_expr = CompiledExpr(twist_v, ctrls, 0.0f);
    std::vector<ProtoPtr> out;
    for (const Curve& curve : curves) {
        std::vector<Vec3> us, vs;
        sweep_frames(curve, up, us, vs);
        const std::vector<double> lengths = arc_lengths(curve);
        const double total_len = std::max(lengths.back(), 1e-12);
        Proto p;
        double sum_sx = 0.0, sum_sy = 0.0;
        double sx_start = 1.0, sy_start = 1.0, sx_end = 1.0, sy_end = 1.0;
        for (std::size_t i = 0; i < curve.points.size(); ++i) {
            double sx = 1.0, sy = 1.0, tw = 0.0;
            if (scale_v || radius_v || twist_v) {
                Point pt;
                pt.index = static_cast<int>(i);
                pt.position = curve.points[i];
                pt.parametric = true;
                pt.u = static_cast<float>(lengths[i] / total_len);
                if (radius_v) {
                    const float rv = radius_expr.eval(&pt);
                    if (!(rv >= 0.0001f && rv <= 1000.0f)) fail("Invalid radius");
                    sx = sy = rv;
                } else if (scale_v) {
                    if (scale_pair) {
                        sx = sx_expr.eval(&pt);
                        sy = sy_expr.eval(&pt);
                    } else {
                        sx = sy = sx_expr.eval(&pt);
                    }
                    if (!(sx >= 1e-4 && sy >= 1e-4 && sx <= 1000.0 && sy <= 1000.0))
                        fail("Profile sweep scale must lie in [0.0001, 1000]");
                }
                if (twist_v) {
                    const float tv = twist_expr.eval(&pt);
                    if (!(tv >= -1000.0f && tv <= 1000.0f)) fail("Invalid twist");
                    tw = tv;
                }
            }
            sum_sx += sx;
            sum_sy += sy;
            if (i == 0) { sx_start = sx; sy_start = sy; }
            sx_end = sx;
            sy_end = sy;
            const double ct = std::cos(tw), st = std::sin(tw);
            const Vec3 ui = tw != 0.0 ? us[i] * ct + vs[i] * st : us[i];
            const Vec3 vi = tw != 0.0 ? vs[i] * ct - us[i] * st : vs[i];
            for (const P2& q : profile) p.mesh.add_vertex(curve.points[i] + ui * (q.x * sx) + vi * (q.y * sy));
        }
        const double mean_sx = sum_sx / static_cast<double>(curve.points.size());
        const double mean_sy = sum_sy / static_cast<double>(curve.points.size());
        const auto width = static_cast<Index>(profile.size());
        for (std::size_t i = 0; i < curve.segments(); ++i) {
            const Index here = static_cast<Index>(i) * width, next = static_cast<Index>((i + 1) % curve.points.size()) * width;
            for (std::size_t j = 0; j < border.size(); ++j) {
                const auto a = static_cast<Index>(border[j]), b = static_cast<Index>(border[(j + 1) % border.size()]);
                add_quad(p, here + a, here + b, next + b, next + a);
            }
        }
        const std::size_t wall_faces = p.mesh.face_count();
        std::size_t start_cap_end = wall_faces;
        if (capped && !curve.closed) {
            const Index end = static_cast<Index>(curve.points.size() - 1) * width;
            if (cap_mode == "fan") {
                for (const Tri& t : caps) add_tri(p, t[2], t[1], t[0]);
                start_cap_end = p.mesh.face_count();
                for (const Tri& t : caps) add_tri(p, t[0] + end, t[1] + end, t[2] + end);
            } else {
                std::vector<Index> bot_rim(border.size()), top_rim(border.size());
                for (std::size_t j = 0; j < border.size(); ++j) {
                    bot_rim[j] = static_cast<Index>(border[j]);
                    top_rim[j] = static_cast<Index>(border[j]) + end;
                }
                emit_ring_cap_3d(p.mesh, bot_rim, cap_mode, cap_inset, true);
                start_cap_end = p.mesh.face_count();
                emit_ring_cap_3d(p.mesh, top_rim, cap_mode, cap_inset, false);
            }
        }
        // Chart: round the section by its perimeter, along the path by arc length.
        std::vector<double> round(profile.size(), 0.0);
        double perimeter = 0;
        for (std::size_t j = 0; j < border.size(); ++j) {
            const auto a = static_cast<std::size_t>(border[j]), b2 = static_cast<std::size_t>(border[(j + 1) % border.size()]);
            round[a] = perimeter;
            perimeter += std::hypot((profile[b2].x - profile[a].x) * mean_sx, (profile[b2].y - profile[a].y) * mean_sy);
        }
        std::vector<std::array<double, 2>> chart(p.mesh.vertex_count(), {0.0, 0.0});
        double along = 0;
        for (std::size_t i = 0; i < curve.points.size(); ++i) {
            if (i) along += length(curve.points[i] - curve.points[i - 1]);
            for (std::size_t j = 0; j < profile.size(); ++j) chart[i * profile.size() + j] = {round[j], along};
        }
        const double loop = curve.closed ? along + length(curve.points.front() - curve.points.back()) : 0.0;
        assign_chart(p, chart, perimeter, loop);
        if (capped && !curve.closed) {  // end caps: the section's own plane, from its lower corner
            double min_x = 1e300, min_y = 1e300;
            for (const P2& q : profile) { min_x = std::min(min_x, q.x); min_y = std::min(min_y, q.y); }
            for (std::size_t f = wall_faces; f < p.mesh.face_count(); ++f) {
                const bool is_end = f >= start_cap_end;
                const double csx = is_end ? sx_end : sx_start, csy = is_end ? sy_end : sy_start;
                const Vec3 orig = is_end ? curve.points.back() : curve.points.front();
                const Vec3 bu = is_end ? us.back() : us.front(), bv = is_end ? vs.back() : vs.front();
                const auto fv = p.mesh.face(f);
                for (std::size_t i = 0; i < fv.size(); ++i) {
                    if (cap_mode == "fan") {
                        const P2& q = profile[fv[i] % width];
                        p.uv[p.mesh.face_offsets[f] + i] = {static_cast<float>((q.x - min_x) * csx), static_cast<float>((q.y - min_y) * csy)};
                    } else {
                        const Vec3 d = p.mesh.positions[fv[i]] - orig;
                        p.uv[p.mesh.face_offsets[f] + i] = {static_cast<float>(dot(d, bu) - min_x * csx), static_cast<float>(dot(d, bv) - min_y * csy)};
                    }
                }
            }
        }
        out.push_back(share(std::move(p)));
    }
    return out;
}
}  // namespace

Geometry box_boolean(const Geometry& stock, const Geometry& cutter, std::string_view operation) {
    Vec3 lo, hi;
    const Part& part = editing_box(stock, lo, hi);
    const Part& tool = editing_box(cutter, lo, hi);
    auto world_bounds = [](const Part& solid, Vec3& a, Vec3& b) {
        for (int axis = 0; axis < 3; ++axis) {
            const Vec3 col = solid.transform.column(axis);
            const double largest = std::max({std::abs(col.x), std::abs(col.y), std::abs(col.z)});
            int significant = 0;
            for (int k = 0; k < 3; ++k) significant += std::abs(col[k]) > largest * 1e-6 ? 1 : 0;
            if (!(largest > 0) || significant != 1) fail("Box Boolean requires axis-aligned operands");
        }
        Aabb box;
        // Compare in float like the reference, so planes authored to coincide do.
        for (const Vec3& p : solid.proto->mesh.positions) {
            const Vec3 w = solid.transform.point(p);
            box.expand({static_cast<double>(static_cast<float>(w.x)), static_cast<double>(static_cast<float>(w.y)),
                        static_cast<double>(static_cast<float>(w.z))});
        }
        a = box.lo;
        b = box.hi;
    };
    Vec3 a, b, c, d;
    world_bounds(part, a, b);
    world_bounds(tool, c, d);
    if (operation == "union") {
        int touching = 0;
        double least = std::numeric_limits<double>::infinity();
        for (int k = 0; k < 3; ++k) {
            const double overlap = std::min(b[k], d[k]) - std::max(a[k], c[k]);
            touching += overlap == 0 ? 1 : 0;
            least = std::min(least, overlap);
        }
        if (least >= 0 && touching > 1) fail("Boxes touching only at an edge or vertex cannot form a manifold union");
    }
    std::vector<double> axes[3];
    for (int k = 0; k < 3; ++k) {
        std::set<double> values{a[k], b[k], c[k], d[k]};
        axes[k].assign(values.begin(), values.end());
    }
    const int limits[3] = {static_cast<int>(axes[0].size()) - 1, static_cast<int>(axes[1].size()) - 1, static_cast<int>(axes[2].size()) - 1};
    std::set<std::array<int, 3>> occupied;
    std::vector<std::array<int, 3>> cells;
    for (int x = 0; x < limits[0]; ++x)
        for (int y = 0; y < limits[1]; ++y)
            for (int z = 0; z < limits[2]; ++z) {
                const Vec3 low{axes[0][static_cast<std::size_t>(x)], axes[1][static_cast<std::size_t>(y)], axes[2][static_cast<std::size_t>(z)]};
                const Vec3 high{axes[0][static_cast<std::size_t>(x) + 1], axes[1][static_cast<std::size_t>(y) + 1], axes[2][static_cast<std::size_t>(z) + 1]};
                auto inside = [&](Vec3 mn, Vec3 mx) {
                    return low.x >= mn.x && high.x <= mx.x && low.y >= mn.y && high.y <= mx.y && low.z >= mn.z && high.z <= mx.z;
                };
                const bool in_stock = inside(a, b), in_tool = inside(c, d);
                const bool keep = operation == "difference" ? in_stock && !in_tool : (operation == "union" ? in_stock || in_tool : in_stock && in_tool);
                if (keep) {
                    occupied.insert({x, y, z});
                    cells.push_back({x, y, z});
                }
            }
    Geometry out;
    if (cells.empty()) return out;
    Proto p;
    std::map<std::array<int, 3>, Index> ids;
    for (const auto& cell : cells)
        for (int face = 0; face < 6; ++face) {
            std::array<int, 3> neighbour = cell;
            neighbour[static_cast<std::size_t>(face / 2)] += face % 2 == 0 ? -1 : 1;
            if (occupied.count(neighbour)) continue;
            Index q[4];
            for (int k = 0; k < 4; ++k) {
                const int corner = kBoxFaces[face][k];
                const std::array<int, 3> key{cell[0] + (corner & 1), cell[1] + ((corner >> 1) & 1), cell[2] + ((corner >> 2) & 1)};
                auto [it, fresh] = ids.try_emplace(key, 0);
                if (fresh)
                    it->second = p.mesh.add_vertex({axes[0][static_cast<std::size_t>(key[0])], axes[1][static_cast<std::size_t>(key[1])],
                                                    axes[2][static_cast<std::size_t>(key[2])]});
                q[k] = it->second;
            }
            add_quad(p, q[0], q[1], q[2], q[3]);
        }
    Part edited = part;
    edited.proto = share(std::move(p));
    edited.transform = Mat4::identity();
    out.parts.push_back(std::move(edited));
    return out;
}

Geometry box_bevel(const Geometry& input, double width) {
    Vec3 lo, hi;
    Part part = editing_box(input, lo, hi);
    if (!(2 * width < std::min({hi.x - lo.x, hi.y - lo.y, hi.z - lo.z}))) fail("Bevel exceeds half the shortest box side");
    const auto corners = box_corners(lo, hi);
    Proto p;
    for (int i = 0; i < 8; ++i)
        for (int axis = 0; axis < 3; ++axis) {
            double v[3] = {corners[static_cast<std::size_t>(i)].x, corners[static_cast<std::size_t>(i)].y, corners[static_cast<std::size_t>(i)].z};
            for (int other = 0; other < 3; ++other)
                if (other != axis) v[other] += (i & (1 << other)) == 0 ? width : -width;
            p.mesh.add_vertex({v[0], v[1], v[2]});
        }
    auto index = [](int corner, int axis) { return static_cast<Index>(corner * 3 + axis); };
    const Vec3 center = (lo + hi) * 0.5;
    auto oriented = [&](std::vector<Index> face) {
        const Vec3 a = p.mesh.positions[face[0]], b = p.mesh.positions[face[1]], c = p.mesh.positions[face[2]];
        if (dot(cross(b - a, c - a), a - center) < 0) std::reverse(face.begin(), face.end());
        return face;
    };
    for (int face = 0; face < 6; ++face) {
        std::vector<Index> ids;
        for (int k = 0; k < 4; ++k) ids.push_back(index(kBoxFaces[face][k], face / 2));
        p.mesh.add_face(oriented(std::move(ids)));
    }
    // Triangles are stored after all quads in the reference; order only matters
    // for face ids, so keep quads first, then corners.
    std::vector<std::vector<Index>> tris;
    for (int corner = 0; corner < 8; ++corner) {
        tris.push_back(oriented({index(corner, 0), index(corner, 1), index(corner, 2)}));
        for (int axis = 0; axis < 3; ++axis) {
            if (corner & (1 << axis)) continue;
            const int neighbour = corner | (1 << axis);
            const int a = (axis + 1) % 3, b = (axis + 2) % 3;
            p.mesh.add_face(oriented({index(corner, a), index(neighbour, a), index(neighbour, b), index(corner, b)}));
        }
    }
    for (auto& t : tris) p.mesh.add_face(std::move(t));
    part.proto = share(std::move(p));
    Geometry out;
    out.parts.push_back(std::move(part));
    return out;
}

}  // namespace pg::doc::detail
