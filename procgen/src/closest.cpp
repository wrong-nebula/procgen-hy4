// Closest point on a mesh: the reference scan, and a bounding-volume tree that gives the same answer.
//
// Why the tree is exact, not approximately right
// ----------------------------------------------
// Both paths run the same code on a triangle (`closest_on_triangle`, then length(q - p) * metric) and
// keep the candidate with the smaller distance, the lower scan number on a tie. They can only differ
// if the tree skips a triangle the scan would have kept, so a subtree is skipped only when every
// triangle in it is PROVABLY further than the best so far, in the arithmetic actually performed:
//
//   computed distance >= (distance from p to the subtree's box) - stray
//
// where `stray` is how far the computed nearest point q can lie outside the box of its triangle.
// On a vertex or an edge q is a vertex or a + (b - a) * t with t in [0, 1]: a few units in the last
// place of the coordinates. Inside the triangle q = a + ab * v + ac * w with v and w quotients of
// differences of products of dot products, which cancel: their error grows as
//   eps * kappa * D^2,   kappa = (longest edge)^2 / |ab x ac|^2 = 1 / height^2,
// D the distance from p to the triangle's vertices. So each child box carries the largest kappa
// below it, and with x = C * eps * kappa * D^2 (C = 4096, against about 200 from a crude bound and
// under 1 measured: tests/test_closest.cpp):
//   - x < 1/4: stray <= x * (mesh diagonal) + rounding, and the subtree is skipped when its box is
//     further than the best by more than that;
//   - otherwise nothing is promised and the subtree is always visited. Slivers thinner than 1 in
//     32768 are given kappa = infinity. They cost a visit on every query; they never cost exactness.
// Boxes are stored in single precision rounded outwards, the comparison is strict (an equally close
// triangle may come earlier in scan order), and box distances are shrunk by 2^-40 to cover their own
// rounding. Non-finite coordinates, or a mesh of a few triangles, get no tree at all.

#include "pg/closest.hpp"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <limits>

namespace pg {

namespace {

constexpr double kInfinity = std::numeric_limits<double>::infinity();
constexpr double kNoHit = 1e300;                   // the scan starts from here: nothing at or beyond it is a hit
constexpr std::uint32_t kNone = 0xFFFFFFFFu;
constexpr std::uint32_t kFirstMask = 0x0FFFFFFFu;  // leaf: first triangle; three bits above hold count - 1
constexpr int kCountShift = 28;
constexpr int kBins = 16;
constexpr int kSahDepth = 32;  // below this depth splits are by count, which bounds the tree's depth
constexpr int kStack = 64;     // >= kSahDepth + log2(2^28 triangles)

constexpr double kStrayScale = 0x1p-40;         // C * eps = 2^12 * 2^-52
constexpr double kProvable = 0.25;              // the stray bound holds while C * eps * kappa * D^2 is below this
constexpr double kShrink = 1.0 - 0x1p-40;       // covers the rounding of a box distance and of a length
constexpr double kPad = 1.0 + 0x1p-20;          // covers the rounding of the bound's own inputs
constexpr double kSliver = 0x1p-30;             // |ab x ac|^2 below this * edge^4: height under edge / 32768
constexpr double kRounding = 0x1p-48;           // of the largest coordinate: q on a vertex or an edge

// The largest distance that can be a hit: the scan accepts d < 1e300 and then d <= max_distance.
inline double hit_limit(double max_distance) noexcept { return std::min(max_distance, std::nextafter(kNoHit, 0.0)); }

// The best candidate so far. Until there is one, `d` is the largest distance still accepted.
struct Best {
    double d;
    std::uint32_t order = kNone, slot = kNone;
    Vec3 q{};
    explicit Best(double limit) noexcept : d(limit) {}
    void offer(double distance, std::uint32_t triangle_order, std::uint32_t triangle_slot, Vec3 point) noexcept {
        if (distance < d || (distance == d && triangle_order < order)) {
            d = distance;
            order = triangle_order;
            slot = triangle_slot;
            q = point;
        }
    }
};

float round_down(double v) noexcept {
    if (v > static_cast<double>(FLT_MAX)) return FLT_MAX;
    if (v < -static_cast<double>(FLT_MAX)) return -std::numeric_limits<float>::infinity();
    const float f = static_cast<float>(v);
    return static_cast<double>(f) > v ? std::nextafter(f, -std::numeric_limits<float>::infinity()) : f;
}
float round_up(double v) noexcept {
    if (v > static_cast<double>(FLT_MAX)) return std::numeric_limits<float>::infinity();
    if (v < -static_cast<double>(FLT_MAX)) return -FLT_MAX;
    const float f = static_cast<float>(v);
    return static_cast<double>(f) < v ? std::nextafter(f, std::numeric_limits<float>::infinity()) : f;
}

// What the builder sorts: a triangle's box and which triangle it is, 28 bytes moved in place so
// every pass runs through memory in order. Single precision is enough to choose where to split; the
// boxes kept in the tree are taken from the triangles themselves.
struct Prim {
    float lo[3], hi[3];
    std::uint32_t id;
};
float coarse(double v) noexcept { return static_cast<float>(std::clamp(v, -1e30, 1e30)); }

struct Extent {
    float lo[3] = {FLT_MAX, FLT_MAX, FLT_MAX}, hi[3] = {-FLT_MAX, -FLT_MAX, -FLT_MAX};
    void add(const float* l, const float* h) noexcept {
        for (int a = 0; a < 3; ++a) {
            lo[a] = std::min(lo[a], l[a]);
            hi[a] = std::max(hi[a], h[a]);
        }
    }
    void add(const Extent& e) noexcept { add(e.lo, e.hi); }
    [[nodiscard]] double half_area() const noexcept {
        if (lo[0] > hi[0]) return 0.0;
        const double x = static_cast<double>(hi[0]) - static_cast<double>(lo[0]), y = static_cast<double>(hi[1]) - static_cast<double>(lo[1]),
                     z = static_cast<double>(hi[2]) - static_cast<double>(lo[2]);
        return x * y + y * z + z * x;
    }
};
struct Bin {
    Extent box, centres;
    std::uint32_t count = 0;
};
// Twice the centre of a triangle's box: what triangles are sorted by.
inline float centre2(const Prim& p, int axis) noexcept { return p.lo[axis] + p.hi[axis]; }
inline int bin_of(float c, float lo, float scale) noexcept { return std::min(kBins - 1, static_cast<int>((c - lo) * scale)); }

struct Subtree {
    std::uint32_t ref;
    Aabb box;
    double kappa;
};

struct Builder {
    const std::vector<ClosestIndex::Triangle>& source;
    const std::vector<double>& kappas;
    std::vector<Prim>& prims;
    std::vector<ClosestIndex::Node>& nodes;
    ClosestIndex::Options options;
    std::uint32_t leaf_size;

    // Fills `bins` across `axis` and returns the cost of the cheapest of the kBins - 1 cuts between
    // them, or infinity when every cut leaves one side empty.
    double best_cut(std::uint32_t first, std::uint32_t count, const Extent& centres, int axis, Bin* bins, int& cut) const {
        const float lo = centres.lo[axis], extent = centres.hi[axis] - lo;
        const float scale = extent > 0 ? static_cast<float>(kBins) / extent : 0.0f;
        if (!(scale > 0) || !std::isfinite(scale)) return kInfinity;
        for (std::uint32_t i = first; i < first + count; ++i) {
            const Prim& p = prims[i];
            const float c[3] = {centre2(p, 0), centre2(p, 1), centre2(p, 2)};
            Bin& bin = bins[bin_of(c[axis], lo, scale)];
            bin.box.add(p.lo, p.hi);
            bin.centres.add(c, c);
            ++bin.count;
        }
        double right_cost[kBins];
        Extent acc;
        std::uint32_t n = 0;
        for (int k = kBins - 1; k > 0; --k) {
            acc.add(bins[k].box);
            n += bins[k].count;
            right_cost[k] = n ? acc.half_area() * n : kInfinity;
        }
        double best = kInfinity;
        acc = Extent{};
        n = 0;
        for (int k = 0; k + 1 < kBins; ++k) {
            acc.add(bins[k].box);
            n += bins[k].count;
            if (!n) continue;
            const double cost = acc.half_area() * n + right_cost[k + 1];
            if (cost < best) {
                best = cost;
                cut = k;
            }
        }
        return best;
    }

    // `centres`: the bounds of the centres of the triangles in [first, first + count).
    Subtree build(std::uint32_t first, std::uint32_t count, const Extent& centres, int depth) {
        if (count <= leaf_size) {
            Aabb box;
            double kappa = 0.0;
            for (std::uint32_t i = first; i < first + count; ++i) {
                const ClosestIndex::Triangle& t = source[prims[i].id];
                box.expand(t.a);
                box.expand(t.b);
                box.expand(t.c);
                kappa = std::max(kappa, kappas[prims[i].id]);
            }
            return {ClosestIndex::kLeaf | (count - 1) << kCountShift | first, box, kappa};
        }
        const float spread[3] = {centres.hi[0] - centres.lo[0], centres.hi[1] - centres.lo[1], centres.hi[2] - centres.lo[2]};
        const int longest = spread[0] >= spread[1] && spread[0] >= spread[2] ? 0 : spread[1] >= spread[2] ? 1 : 2;
        const auto begin = prims.begin() + first;
        std::uint32_t mid = first;
        Extent sides[2];
        bool have_sides = false;
        if (options.split == ClosestIndex::Options::Split::SurfaceArea && depth < kSahDepth) {
            Bin bins[kBins];
            int axis = -1, cut = 0;
            double cost = kInfinity;
            for (int a = 0; a < 3; ++a) {
                if (!options.all_axes && a != longest) continue;
                Bin trial[kBins];
                int c = 0;
                const double k = best_cut(first, count, centres, a, trial, c);
                if (k < cost) {
                    cost = k;
                    axis = a;
                    cut = c;
                    std::copy(trial, trial + kBins, bins);
                }
            }
            if (axis >= 0) {
                const float lo = centres.lo[axis], scale = static_cast<float>(kBins) / (centres.hi[axis] - lo);
                const auto split = std::partition(begin, begin + count, [&](const Prim& p) { return bin_of(centre2(p, axis), lo, scale) <= cut; });
                mid = first + static_cast<std::uint32_t>(split - begin);
                for (int k = 0; k < kBins; ++k) sides[k <= cut ? 0 : 1].add(bins[k].centres);
                have_sides = true;
            }
        }
        if (mid == first || mid == first + count) {  // by count: no cut found, or the tree is getting deep
            mid = first + count / 2;
            if (spread[longest] > 0) {
                std::nth_element(begin, begin + count / 2, begin + count, [&](const Prim& l, const Prim& r) {
                    const float cl = centre2(l, longest), cr = centre2(r, longest);
                    return cl < cr || (cl == cr && l.id < r.id);
                });
            }
            have_sides = false;
        }
        if (!have_sides) {
            sides[0] = sides[1] = Extent{};
            for (std::uint32_t i = first; i < first + count; ++i) {
                const float c[3] = {centre2(prims[i], 0), centre2(prims[i], 1), centre2(prims[i], 2)};
                sides[i < mid ? 0 : 1].add(c, c);
            }
        }

        const auto index = static_cast<std::uint32_t>(nodes.size());
        nodes.emplace_back();  // parents before children: the top of the tree stays together in memory
        const Subtree side[2] = {build(first, mid - first, sides[0], depth + 1), build(mid, first + count - mid, sides[1], depth + 1)};
        ClosestIndex::Node& node = nodes[index];
        Aabb box;
        for (int s = 0; s < 2; ++s) {
            for (int a = 0; a < 3; ++a) {
                node.lo[s][a] = round_down(side[s].box.lo[a]);
                node.hi[s][a] = round_up(side[s].box.hi[a]);
            }
            node.child[s] = side[s].ref;
            node.kappa[s] = round_up(side[s].kappa);
            box.lo = vmin(box.lo, side[s].box.lo);
            box.hi = vmax(box.hi, side[s].box.hi);
        }
        return {index, box, std::max(side[0].kappa, side[1].kappa)};
    }
};

}  // namespace

bool closest_point_scan(const Mesh& m, Vec3 p, double max_distance, ClosestHit& hit, double metric) noexcept {
    double best = kNoHit;
    std::uint32_t order = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i, ++order) {
            const Vec3 a = m.positions[fv[0]], b = m.positions[fv[i]], c = m.positions[fv[i + 1]];
            const Vec3 q = closest_on_triangle(p, a, b, c);
            const double d = length(q - p) * metric;
            if (d < best) {
                const Vec3 n = cross(b - a, c - a);
                const double nl = length(n);
                if (!(nl > 1e-20)) continue;
                best = d;
                hit = {q, n / nl, d, static_cast<std::uint32_t>(f), order};
            }
        }
    }
    return best < kNoHit && best <= max_distance;
}

ClosestIndex::ClosestIndex(const Mesh& m, Options options) {
    std::vector<Triangle> source;
    source.reserve(m.corner_count() >= 2 * m.face_count() ? m.corner_count() - 2 * m.face_count() : 0);
    std::vector<Prim> prims;
    std::vector<double> kappas;
    prims.reserve(source.capacity());
    kappas.reserve(source.capacity());
    Extent centres;
    bool finite = true;
    double largest = 0.0;
    std::uint32_t order = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i, ++order) {
            const Vec3 a = m.positions[fv[0]], b = m.positions[fv[i]], c = m.positions[fv[i + 1]];
            const Vec3 n = cross(b - a, c - a);
            const double nl = length(n);
            if (!(nl > 1e-20)) continue;  // the scan never keeps these
            const Vec3 lo = vmin(a, vmin(b, c)), hi = vmax(a, vmax(b, c));
            const Prim prim{{coarse(lo.x), coarse(lo.y), coarse(lo.z)}, {coarse(hi.x), coarse(hi.y), coarse(hi.z)}, static_cast<std::uint32_t>(source.size())};
            const float centre[3] = {centre2(prim, 0), centre2(prim, 1), centre2(prim, 2)};
            centres.add(centre, centre);
            prims.push_back(prim);
            const double edge2 = std::max({dot(b - a, b - a), dot(c - a, c - a), dot(c - b, c - b)}), area2 = dot(n, n);
            kappas.push_back(area2 >= kSliver * edge2 * edge2 ? edge2 / area2 * kPad : kInfinity);
            source.push_back({a, b, c, order, static_cast<std::uint32_t>(f)});
            bounds_.lo = vmin(bounds_.lo, lo);
            bounds_.hi = vmax(bounds_.hi, hi);
            // A sum is finite only if every term is (and an overflowing sum of finite terms is refused too).
            finite = finite && std::isfinite(a.x + a.y + a.z + b.x + b.y + b.z + c.x + c.y + c.z);
            largest = std::max({largest, std::abs(lo.x), std::abs(lo.y), std::abs(lo.z), std::abs(hi.x), std::abs(hi.y), std::abs(hi.z)});
        }
    }
    const auto leaf_size = static_cast<std::uint32_t>(std::clamp(options.leaf_size, 1, 8));
    if (source.size() <= leaf_size || !finite || source.size() > kFirstMask) {
        triangles_ = std::move(source);  // too few to be worth a tree, or nothing can be promised: test them all
        return;
    }
    nodes_.reserve(source.size() / std::max<std::uint32_t>(1, leaf_size / 2) + 1);
    Builder builder{source, kappas, prims, nodes_, options, leaf_size};
    const Subtree all = builder.build(0, static_cast<std::uint32_t>(prims.size()), centres, 0);
    root_ = all.ref;
    root_kappa_ = all.kappa;
    diagonal_ = bounds_.diagonal() * kPad;
    rounding_ = largest * kRounding;
    triangles_.reserve(source.size());
    for (const Prim& p : prims) triangles_.push_back(source[p.id]);
}

bool ClosestIndex::closest(Vec3 p, double max_distance, ClosestHit& hit, double metric) const noexcept {
    if (triangles_.empty()) return false;
    Best best(hit_limit(max_distance));
    const auto test = [&](std::uint32_t slot) noexcept {
        const Triangle& t = triangles_[slot];
        const Vec3 q = closest_on_triangle(p, t.a, t.b, t.c);
        const double d = length(q - p) * metric;
        best.offer(d, t.order, slot, q);
    };
    if (nodes_.empty() || !(metric > 0.0) || !(metric <= DBL_MAX)) {
        for (std::uint32_t i = 0; i < triangles_.size(); ++i) test(i);
    } else {
        // D^2 for every triangle at once: the far corner of the bounds.
        const double fx = std::max(std::abs(p.x - bounds_.lo.x), std::abs(bounds_.hi.x - p.x));
        const double fy = std::max(std::abs(p.y - bounds_.lo.y), std::abs(bounds_.hi.y - p.y));
        const double fz = std::max(std::abs(p.z - bounds_.lo.z), std::abs(bounds_.hi.z - p.z));
        const double g = kStrayScale * kPad * (fx * fx + fy * fy + fz * fz);
        // A value no computed distance in the box can be below, in the caller's units.
        const auto lower = [&](double lx, double ly, double lz, double hx, double hy, double hz, double kappa) noexcept {
            const double x = g * kappa;
            if (!(x < kProvable)) return -kInfinity;
            const double dx = std::max(std::max(lx - p.x, p.x - hx), 0.0);
            const double dy = std::max(std::max(ly - p.y, p.y - hy), 0.0);
            const double dz = std::max(std::max(lz - p.z, p.z - hz), 0.0);
            return (std::sqrt(dx * dx + dy * dy + dz * dz) * kShrink - (x * diagonal_ + rounding_)) * metric;
        };
        const auto lower_child = [&](const Node& n, int s) noexcept {
            return lower(static_cast<double>(n.lo[s][0]), static_cast<double>(n.lo[s][1]), static_cast<double>(n.lo[s][2]),
                         static_cast<double>(n.hi[s][0]), static_cast<double>(n.hi[s][1]), static_cast<double>(n.hi[s][2]),
                         static_cast<double>(n.kappa[s]));
        };
        if (lower(bounds_.lo.x, bounds_.lo.y, bounds_.lo.z, bounds_.hi.x, bounds_.hi.y, bounds_.hi.z, root_kappa_) > best.d) return false;

        struct Entry {
            double bound;
            std::uint32_t ref;
        };
        Entry stack[kStack];
        int depth = 0;
        std::uint32_t at = root_;
        for (;;) {
            bool descend = false;
            if (at & kLeaf) {
                const std::uint32_t first = at & kFirstMask, last = first + ((at >> kCountShift) & 7u) + 1;
                for (std::uint32_t i = first; i < last; ++i) test(i);
            } else {
                const Node& n = nodes_[at];
                const double b0 = lower_child(n, 0), b1 = lower_child(n, 1);
                // Strict: a subtree exactly as far as the best may hold an earlier triangle.
                const bool v0 = !(b0 > best.d), v1 = !(b1 > best.d);
                if (v0 && v1) {
                    const int near = b1 < b0 ? 1 : 0;
                    stack[depth++] = {near ? b0 : b1, n.child[1 - near]};
                    at = n.child[near];
                    descend = true;
                } else if (v0 || v1) {
                    at = n.child[v0 ? 0 : 1];
                    descend = true;
                }
            }
            if (descend) continue;
            while (depth > 0 && stack[depth - 1].bound >= best.d) --depth;
            if (depth == 0) break;
            at = stack[--depth].ref;
        }
    }
    if (best.slot == kNone) return false;
    const Triangle& t = triangles_[best.slot];
    const Vec3 n = cross(t.b - t.a, t.c - t.a);
    const double nl = length(n);
    hit = {best.q, n / nl, best.d, t.face, t.order};
    return true;
}

namespace {

// Conservative slab entry parameter on [t_min, t_max]; returns +infinity when the ray misses the box.
// Outward float rounding in the BVH plus a few-ULP pad ensures slab arithmetic never prunes a triangle
// accepted by `ray_triangle_intersect`, including parallel rays (`dir[a] == 0`) grazing box faces.
[[nodiscard, gnu::always_inline]] inline double slab_entry(
    double lx, double ly, double lz, double hx, double hy, double hz,
    Vec3 origin, Vec3 dir, double t_min, double t_max) noexcept {
    const double lo[3] = {lx, ly, lz}, hi[3] = {hx, hy, hz};
    double t0 = t_min, t1 = t_max;
    for (int a = 0; a < 3; ++a) {
        const double d = dir[a], o = origin[a];
        if (d == 0.0) {
            if (o < lo[a] || o > hi[a]) return kInfinity;
        } else {
            double ta = (lo[a] - o) / d;
            double tb = (hi[a] - o) / d;
            if (ta > tb) std::swap(ta, tb);
            const double pad = (std::abs(ta) + std::abs(tb)) * 0x1p-38 + 1e-13;
            t0 = std::max(t0, ta - pad);
            t1 = std::min(t1, tb + pad);
            if (t0 > t1) return kInfinity;
        }
    }
    return t0;
}

}  // namespace

bool raycast_scan(const Mesh& m, Vec3 origin, Vec3 dir, double t_min, double t_max, RaycastHit& hit,
                  bool cull_backfaces) noexcept {
    if (!(t_min <= t_max) || !std::isfinite(origin.x + origin.y + origin.z + dir.x + dir.y + dir.z)) return false;
    const double limit = hit_limit(t_max);
    if (!(t_min <= limit)) return false;
    double best_t = limit;
    bool found = false;
    std::uint32_t order = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f) {
        const auto fv = m.face(f);
        for (std::size_t i = 1; i + 1 < fv.size(); ++i, ++order) {
            const Vec3 a = m.positions[fv[0]], b = m.positions[fv[i]], c = m.positions[fv[i + 1]];
            const Vec3 n = cross(b - a, c - a);
            const double nl = length(n);
            if (!(nl > 1e-20)) continue;
            double t = 0.0, u = 0.0, v = 0.0;
            if (!ray_triangle_intersect(origin, dir, a, b, c, t_min, best_t, cull_backfaces, t, u, v)) continue;
            if (!found || t < best_t) {
                found = true;
                best_t = t;
                const Vec3 unit_n = n / nl;
                hit = {a + (b - a) * u + (c - a) * v, unit_n, t, u, v,
                       static_cast<std::uint32_t>(f), order, dot(dir, unit_n) < 0.0};
            }
        }
    }
    return found;
}

bool ClosestIndex::raycast(Vec3 origin, Vec3 dir, double t_min, double t_max, RaycastHit& hit,
                           bool cull_backfaces) const noexcept {
    if (triangles_.empty() || !(t_min <= t_max) ||
        !std::isfinite(origin.x + origin.y + origin.z + dir.x + dir.y + dir.z)) {
        return false;
    }
    const double limit = hit_limit(t_max);
    if (!(t_min <= limit)) return false;

    double best_t = limit, best_u = 0.0, best_v = 0.0;
    std::uint32_t best_order = kNone, best_slot = kNone;

    const auto test = [&](std::uint32_t slot) noexcept {
        const Triangle& tri = triangles_[slot];
        double t = 0.0, u = 0.0, v = 0.0;
        if (!ray_triangle_intersect(origin, dir, tri.a, tri.b, tri.c, t_min, best_t, cull_backfaces, t, u, v)) return;
        if (best_slot == kNone || t < best_t || (t == best_t && tri.order < best_order)) {
            best_t = t;
            best_u = u;
            best_v = v;
            best_order = tri.order;
            best_slot = slot;
        }
    };

    if (nodes_.empty()) {
        for (std::uint32_t i = 0; i < triangles_.size(); ++i) test(i);
    } else {
        if (slab_entry(bounds_.lo.x, bounds_.lo.y, bounds_.lo.z, bounds_.hi.x, bounds_.hi.y, bounds_.hi.z,
                       origin, dir, t_min, best_t) > best_t) {
            return false;
        }
        struct Entry {
            double t_near;
            std::uint32_t ref;
        };
        Entry stack[kStack];
        int depth = 0;
        std::uint32_t at = root_;
        for (;;) {
            bool descend = false;
            if (at & kLeaf) {
                const std::uint32_t first = at & kFirstMask, last = first + ((at >> kCountShift) & 7u) + 1;
                for (std::uint32_t i = first; i < last; ++i) test(i);
            } else {
                const Node& n = nodes_[at];
                const double b0 = slab_entry(n.lo[0][0], n.lo[0][1], n.lo[0][2], n.hi[0][0], n.hi[0][1], n.hi[0][2],
                                             origin, dir, t_min, best_t);
                const double b1 = slab_entry(n.lo[1][0], n.lo[1][1], n.lo[1][2], n.hi[1][0], n.hi[1][1], n.hi[1][2],
                                             origin, dir, t_min, best_t);
                const bool v0 = !(b0 > best_t), v1 = !(b1 > best_t);
                if (v0 && v1) {
                    const int near = b1 < b0 ? 1 : 0;
                    stack[depth++] = {near ? b0 : b1, n.child[1 - near]};
                    at = n.child[near];
                    descend = true;
                } else if (v0 || v1) {
                    at = n.child[v0 ? 0 : 1];
                    descend = true;
                }
            }
            if (descend) continue;
            while (depth > 0 && stack[depth - 1].t_near > best_t) --depth;
            if (depth == 0) break;
            at = stack[--depth].ref;
        }
    }

    if (best_slot == kNone) return false;
    const Triangle& tri = triangles_[best_slot];
    const Vec3 n = cross(tri.b - tri.a, tri.c - tri.a);
    const Vec3 unit_n = n / length(n);
    hit = {tri.a + (tri.b - tri.a) * best_u + (tri.c - tri.a) * best_v, unit_n,
           best_t, best_u, best_v, tri.face, tri.order, dot(dir, unit_n) < 0.0};
    return true;
}

bool ClosestIndex::any_hit(Vec3 origin, Vec3 dir, double t_min, double t_max, bool cull_backfaces) const noexcept {
    if (triangles_.empty() || !(t_min <= t_max) ||
        !std::isfinite(origin.x + origin.y + origin.z + dir.x + dir.y + dir.z)) {
        return false;
    }
    const double limit = hit_limit(t_max);
    if (!(t_min <= limit)) return false;

    const auto hit_tri = [&](std::uint32_t slot) noexcept {
        const Triangle& tri = triangles_[slot];
        double t = 0.0, u = 0.0, v = 0.0;
        return ray_triangle_intersect(origin, dir, tri.a, tri.b, tri.c, t_min, limit, cull_backfaces, t, u, v);
    };
    if (nodes_.empty()) {
        for (std::uint32_t i = 0; i < triangles_.size(); ++i)
            if (hit_tri(i)) return true;
        return false;
    }
    if (slab_entry(bounds_.lo.x, bounds_.lo.y, bounds_.lo.z, bounds_.hi.x, bounds_.hi.y, bounds_.hi.z,
                   origin, dir, t_min, limit) > limit) {
        return false;
    }
    std::uint32_t stack[kStack];
    int depth = 0;
    stack[depth++] = root_;
    while (depth > 0) {
        const std::uint32_t at = stack[--depth];
        if (at & kLeaf) {
            const std::uint32_t first = at & kFirstMask, last = first + ((at >> kCountShift) & 7u) + 1;
            for (std::uint32_t i = first; i < last; ++i)
                if (hit_tri(i)) return true;
        } else {
            const Node& n = nodes_[at];
            for (int s = 0; s < 2; ++s) {
                if (!(slab_entry(n.lo[s][0], n.lo[s][1], n.lo[s][2], n.hi[s][0], n.hi[s][1], n.hi[s][2],
                                 origin, dir, t_min, limit) > limit)) {
                    stack[depth++] = n.child[s];
                }
            }
        }
    }
    return false;
}

}  // namespace pg
