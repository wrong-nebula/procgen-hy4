#pragma once
// Search structures measured against pg::ClosestIndex and not kept in the library
// (docs/experiments/closest-point-index.md). Each answers exactly as the scan does, with the same
// skip rule as src/closest.cpp, so the comparison is between structures and not between shortcuts:
//
//   WideBvh     the library's tree walked by a copy of its traversal, optionally starting from a
//               seed triangle (the last hit, or one looked up in a grid of seeds)
//   ClassicBvh  the same tree with one double-precision box per node: a visit has to load both
//               children to decide on them
//   Grid        a uniform grid of triangle lists searched in growing shells of cells
//   SeedGrid    a coarse grid holding, per cell, a triangle near it: a starting bound for WideBvh
#include "pg/closest.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

namespace cand {

using pg::Aabb;
using pg::ClosestIndex;
using pg::Vec3;

constexpr std::uint32_t kNone = 0xFFFFFFFFu;
constexpr double kInfinity = std::numeric_limits<double>::infinity();

struct Found {
    double d = 0.0;  // before a hit: the largest distance accepted
    std::uint32_t order = kNone, slot = kNone;
    Vec3 q{};
    Found() = default;
    explicit Found(double limit) noexcept : d(limit) {}
    [[nodiscard]] bool hit() const noexcept { return slot != kNone; }
    void offer(double distance, std::uint32_t triangle_order, std::uint32_t triangle_slot, Vec3 point) noexcept {
        if (distance < d || (distance == d && triangle_order < order)) {
            d = distance;
            order = triangle_order;
            slot = triangle_slot;
            q = point;
        }
    }
};

inline double hit_limit(double max_distance) noexcept { return std::min(max_distance, std::nextafter(1e300, 0.0)); }

inline void test(const ClosestIndex::Triangle& t, std::uint32_t slot, Vec3 p, double metric, Found& best) noexcept {
    const Vec3 q = pg::closest_on_triangle(p, t.a, t.b, t.c);
    best.offer(pg::length(q - p) * metric, t.order, slot, q);
}

// kappa of a triangle and the skip rule: the constants of src/closest.cpp.
inline double kappa_of(const ClosestIndex::Triangle& t) noexcept {
    const Vec3 n = pg::cross(t.b - t.a, t.c - t.a);
    const double edge2 = std::max({pg::dot(t.b - t.a, t.b - t.a), pg::dot(t.c - t.a, t.c - t.a), pg::dot(t.c - t.b, t.c - t.b)});
    const double area2 = pg::dot(n, n);
    return area2 >= 0x1p-30 * edge2 * edge2 ? edge2 / area2 * (1.0 + 0x1p-20) : kInfinity;
}

struct Bound {
    Vec3 p;
    double g, diagonal, rounding, metric;
    Bound(const Aabb& box, Vec3 point, double m) : p(point), metric(m) {
        const double fx = std::max(std::abs(p.x - box.lo.x), std::abs(box.hi.x - p.x));
        const double fy = std::max(std::abs(p.y - box.lo.y), std::abs(box.hi.y - p.y));
        const double fz = std::max(std::abs(p.z - box.lo.z), std::abs(box.hi.z - p.z));
        g = 0x1p-40 * (1.0 + 0x1p-20) * (fx * fx + fy * fy + fz * fz);
        diagonal = box.diagonal() * (1.0 + 0x1p-20);
        rounding = 0x1p-48 * std::max({std::abs(box.lo.x), std::abs(box.lo.y), std::abs(box.lo.z), std::abs(box.hi.x), std::abs(box.hi.y), std::abs(box.hi.z)});
    }
    [[nodiscard]] double lower(double lx, double ly, double lz, double hx, double hy, double hz, double kappa) const noexcept {
        const double x = g * kappa;
        if (!(x < 0.25)) return -kInfinity;
        const double dx = std::max(std::max(lx - p.x, p.x - hx), 0.0);
        const double dy = std::max(std::max(ly - p.y, p.y - hy), 0.0);
        const double dz = std::max(std::max(lz - p.z, p.z - hz), 0.0);
        return (std::sqrt(dx * dx + dy * dy + dz * dz) * (1.0 - 0x1p-40) - (x * diagonal + rounding)) * metric;
    }
};

// --- the library's tree, walked here so that a seed can be given ---------------------------------
struct WideBvh {
    const ClosestIndex* index = nullptr;
    double root_kappa = 0.0;
    explicit WideBvh(const ClosestIndex& ix) : index(&ix) {
        for (const ClosestIndex::Triangle& t : ix.triangles()) root_kappa = std::max(root_kappa, kappa_of(t));
    }
    // `seed`: a triangle slot tested before anything else (any slot is valid; a near one prunes more).
    bool closest(Vec3 p, double max_distance, Found& best, double metric = 1.0, std::uint32_t seed = kNone) const noexcept {
        const auto tris = index->triangles();
        const auto nodes = index->nodes();
        best = Found{hit_limit(max_distance)};
        if (tris.empty()) return false;
        if (nodes.empty()) {
            for (std::uint32_t i = 0; i < tris.size(); ++i) test(tris[i], i, p, metric, best);
            return best.hit();
        }
        if (seed != kNone) test(tris[seed], seed, p, metric, best);
        const Bound bound(index->bounds(), p, metric);
        const Aabb box = index->bounds();
        if (bound.lower(box.lo.x, box.lo.y, box.lo.z, box.hi.x, box.hi.y, box.hi.z, root_kappa) > best.d) return best.hit();
        const auto lower_child = [&](const ClosestIndex::Node& n, int s) noexcept {
            return bound.lower(static_cast<double>(n.lo[s][0]), static_cast<double>(n.lo[s][1]), static_cast<double>(n.lo[s][2]),
                               static_cast<double>(n.hi[s][0]), static_cast<double>(n.hi[s][1]), static_cast<double>(n.hi[s][2]),
                               static_cast<double>(n.kappa[s]));
        };
        struct Entry {
            double bound;
            std::uint32_t ref;
        };
        Entry stack[64];
        int depth = 0;
        std::uint32_t at = index->root();
        for (;;) {
            bool descend = false;
            if (at & ClosestIndex::kLeaf) {
                const std::uint32_t first = at & 0x0FFFFFFFu, last = first + ((at >> 28) & 7u) + 1;
                for (std::uint32_t i = first; i < last; ++i) test(tris[i], i, p, metric, best);
            } else {
                const ClosestIndex::Node& n = nodes[at];
                const double b0 = lower_child(n, 0), b1 = lower_child(n, 1);
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
            while (depth > 0 && stack[depth - 1].bound > best.d) --depth;
            if (depth == 0) break;
            at = stack[--depth].ref;
        }
        return best.hit();
    }
};

// --- one box per node, in double precision --------------------------------------------------------
struct ClassicBvh {
    struct Node {
        double lo[3], hi[3];
        float kappa;
        std::uint32_t left, right;  // left & kLeaf: a leaf reference in the library's encoding
    };
    const ClosestIndex* index = nullptr;
    std::vector<Node> nodes;

    explicit ClassicBvh(const ClosestIndex& ix) : index(&ix) {
        if (ix.nodes().empty()) return;
        double root_kappa = 0.0;
        for (const ClosestIndex::Triangle& t : ix.triangles()) root_kappa = std::max(root_kappa, kappa_of(t));
        const Aabb b = ix.bounds();
        nodes.reserve(2 * ix.nodes().size() + 1);
        const float inf = std::numeric_limits<float>::infinity();
        convert(ix.root(), b.lo.x, b.lo.y, b.lo.z, b.hi.x, b.hi.y, b.hi.z, root_kappa < 3e38 ? std::nextafter(static_cast<float>(root_kappa), inf) : inf);
    }
    std::uint32_t convert(std::uint32_t ref, double lx, double ly, double lz, double hx, double hy, double hz, float kappa) {
        const auto at = static_cast<std::uint32_t>(nodes.size());
        nodes.push_back({{lx, ly, lz}, {hx, hy, hz}, kappa, ref, 0});
        if (ref & ClosestIndex::kLeaf) return at;
        const ClosestIndex::Node w = index->nodes()[ref];
        std::uint32_t child[2];
        for (int s = 0; s < 2; ++s)
            child[s] = convert(w.child[s], static_cast<double>(w.lo[s][0]), static_cast<double>(w.lo[s][1]), static_cast<double>(w.lo[s][2]),
                               static_cast<double>(w.hi[s][0]), static_cast<double>(w.hi[s][1]), static_cast<double>(w.hi[s][2]), w.kappa[s]);
        nodes[at].left = child[0];
        nodes[at].right = child[1];
        return at;
    }
    [[nodiscard]] std::size_t memory_bytes() const noexcept { return nodes.size() * sizeof(Node); }

    bool closest(Vec3 p, double max_distance, Found& best, double metric = 1.0) const noexcept {
        const auto tris = index->triangles();
        best = Found{hit_limit(max_distance)};
        if (tris.empty()) return false;
        if (nodes.empty()) {
            for (std::uint32_t i = 0; i < tris.size(); ++i) test(tris[i], i, p, metric, best);
            return best.hit();
        }
        const Bound bound(index->bounds(), p, metric);
        const auto lower = [&](const Node& n) noexcept {
            return bound.lower(n.lo[0], n.lo[1], n.lo[2], n.hi[0], n.hi[1], n.hi[2], static_cast<double>(n.kappa));
        };
        if (lower(nodes[0]) > best.d) return false;
        struct Entry {
            double bound;
            std::uint32_t at;
        };
        Entry stack[64];
        int depth = 0;
        std::uint32_t at = 0;
        for (;;) {
            bool descend = false;
            const Node& n = nodes[at];
            if (n.left & ClosestIndex::kLeaf) {
                const std::uint32_t first = n.left & 0x0FFFFFFFu, last = first + ((n.left >> 28) & 7u) + 1;
                for (std::uint32_t i = first; i < last; ++i) test(tris[i], i, p, metric, best);
            } else {
                const double b0 = lower(nodes[n.left]), b1 = lower(nodes[n.right]);
                const bool v0 = !(b0 > best.d), v1 = !(b1 > best.d);
                if (v0 && v1) {
                    const bool right_first = b1 < b0;
                    stack[depth++] = {right_first ? b0 : b1, right_first ? n.left : n.right};
                    at = right_first ? n.right : n.left;
                    descend = true;
                } else if (v0 || v1) {
                    at = v0 ? n.left : n.right;
                    descend = true;
                }
            }
            if (descend) continue;
            while (depth > 0 && stack[depth - 1].bound > best.d) --depth;
            if (depth == 0) break;
            at = stack[--depth].at;
        }
        return best.hit();
    }
};

// --- uniform grid ---------------------------------------------------------------------------------
struct Grid {
    const ClosestIndex* index = nullptr;
    Aabb box;
    int n[3] = {1, 1, 1};
    double cell[3] = {1, 1, 1};
    double kappa = 0.0;                    // of the worst triangle that has one
    std::vector<std::uint32_t> offsets;    // per cell, into `slots`
    std::vector<std::uint32_t> slots;      // triangle slots, cell by cell
    std::vector<std::uint32_t> always;     // slivers: tested on every query
    mutable std::vector<std::uint32_t> seen;  // per triangle: the query that last tested it (not thread safe; a benchmark)
    mutable std::uint32_t query = 0;

    // `density`: cells per triangle, over the whole box.
    Grid(const ClosestIndex& ix, double density) : index(&ix), box(ix.bounds()) {
        const auto tris = ix.triangles();
        if (tris.empty()) return;
        const Vec3 e = box.extent();
        const double longest = std::max({e.x, e.y, e.z, 1e-300});
        double volume = 1.0;
        for (int a = 0; a < 3; ++a) volume *= std::max(e[a], longest * 1e-3);
        const double size = std::cbrt(volume / std::max(1.0, density * static_cast<double>(tris.size())));
        for (int a = 0; a < 3; ++a) {
            n[a] = std::clamp(static_cast<int>(std::ceil(e[a] / size)), 1, 1024);
            cell[a] = e[a] > 0 ? e[a] / n[a] : 1.0;
        }
        const std::size_t cells = static_cast<std::size_t>(n[0]) * n[1] * n[2];
        std::vector<std::uint32_t> counts(cells + 1, 0);
        const auto range = [&](const ClosestIndex::Triangle& t, int a, int& lo, int& hi) {
            const double l = std::min({t.a[a], t.b[a], t.c[a]}), h = std::max({t.a[a], t.b[a], t.c[a]});
            lo = coordinate(l, a);
            hi = coordinate(h, a);
        };
        for (int pass = 0; pass < 2; ++pass) {
            for (std::uint32_t i = 0; i < tris.size(); ++i) {
                const double k = kappa_of(tris[i]);
                if (!(k < kInfinity)) {
                    if (pass == 0) always.push_back(i);
                    continue;
                }
                kappa = std::max(kappa, k);
                int lo[3], hi[3];
                for (int a = 0; a < 3; ++a) range(tris[i], a, lo[a], hi[a]);
                for (int z = lo[2]; z <= hi[2]; ++z)
                    for (int y = lo[1]; y <= hi[1]; ++y)
                        for (int x = lo[0]; x <= hi[0]; ++x) {
                            const std::size_t c = at(x, y, z);
                            if (pass == 0) ++counts[c + 1];
                            else slots[offsets[c] + counts[c]++] = i;
                        }
            }
            if (pass == 0) {
                offsets.assign(cells + 1, 0);
                for (std::size_t c = 0; c < cells; ++c) offsets[c + 1] = offsets[c] + counts[c + 1];
                slots.resize(offsets[cells]);
                std::fill(counts.begin(), counts.end(), 0u);
            }
        }
        seen.assign(tris.size(), kNone);
    }
    [[nodiscard]] std::size_t at(int x, int y, int z) const noexcept { return (static_cast<std::size_t>(z) * n[1] + y) * n[0] + x; }
    [[nodiscard]] int coordinate(double v, int a) const noexcept {
        return std::clamp(static_cast<int>(std::floor((v - box.lo[a]) / cell[a])), 0, n[a] - 1);
    }
    [[nodiscard]] std::size_t memory_bytes() const noexcept { return (offsets.size() + slots.size() + seen.size()) * sizeof(std::uint32_t); }

    bool closest(Vec3 p, double max_distance, Found& best, double metric = 1.0) const noexcept {
        const auto tris = index->triangles();
        best = Found{hit_limit(max_distance)};
        if (tris.empty()) return false;
        const Bound bound(box, p, metric);
        if (!(bound.g * kappa < 0.25)) {  // nothing can be skipped safely from here
            for (std::uint32_t i = 0; i < tris.size(); ++i) test(tris[i], i, p, metric, best);
            return best.hit();
        }
        for (const std::uint32_t i : always) test(tris[i], i, p, metric, best);
        if (bound.lower(box.lo.x, box.lo.y, box.lo.z, box.hi.x, box.hi.y, box.hi.z, kappa) > best.d) return best.hit();
        const std::uint32_t stamp = ++query;
        const int c0[3] = {coordinate(p.x, 0), coordinate(p.y, 1), coordinate(p.z, 2)};
        const int reach = std::max({c0[0], n[0] - 1 - c0[0], c0[1], n[1] - 1 - c0[1], c0[2], n[2] - 1 - c0[2]});
        const auto visit = [&](int x, int y, int z) noexcept {
            const double lx = box.lo.x + x * cell[0], ly = box.lo.y + y * cell[1], lz = box.lo.z + z * cell[2];
            // A cell on the far side of the grid takes the box's own edge: the rounding of lo + n * cell must not cut a triangle off.
            const double hx = x + 1 == n[0] ? box.hi.x : lx + cell[0], hy = y + 1 == n[1] ? box.hi.y : ly + cell[1], hz = z + 1 == n[2] ? box.hi.z : lz + cell[2];
            const std::size_t c = at(x, y, z);
            if (offsets[c] == offsets[c + 1]) return;
            const double pad = 1e-9 * bound.diagonal;  // the cell a triangle is filed under is decided in rounded arithmetic
            if (bound.lower(lx - pad, ly - pad, lz - pad, hx + pad, hy + pad, hz + pad, kappa) > best.d) return;
            for (std::uint32_t k = offsets[c]; k < offsets[c + 1]; ++k) {
                const std::uint32_t i = slots[k];
                if (seen[i] == stamp) continue;
                seen[i] = stamp;
                test(tris[i], i, p, metric, best);
            }
        };
        for (int r = 0; r <= reach; ++r) {
            if (r > 0) {
                // No cell of this shell or a later one is nearer than the nearest face of the block inside it.
                double nearest = kInfinity;
                for (int a = 0; a < 3; ++a) {
                    if (c0[a] + r <= n[a] - 1) nearest = std::min(nearest, box.lo[a] + (c0[a] + r) * cell[a] - p[a]);
                    if (c0[a] - r >= 0) nearest = std::min(nearest, p[a] - (box.lo[a] + (c0[a] - r + 1) * cell[a]));
                }
                const double pad = 1e-9 * bound.diagonal;
                if ((std::max(nearest - pad, 0.0) * (1.0 - 0x1p-40) - (bound.g * kappa * bound.diagonal + bound.rounding)) * metric > best.d) break;
            }
            const int x0 = std::max(c0[0] - r, 0), x1 = std::min(c0[0] + r, n[0] - 1);
            const int y0 = std::max(c0[1] - r, 0), y1 = std::min(c0[1] + r, n[1] - 1);
            const int z0 = std::max(c0[2] - r, 0), z1 = std::min(c0[2] + r, n[2] - 1);
            for (int y = y0; y <= y1; ++y)
                for (int x = x0; x <= x1; ++x) {
                    if (std::abs(x - c0[0]) == r || std::abs(y - c0[1]) == r) {
                        for (int z = z0; z <= z1; ++z) visit(x, y, z);
                    } else {
                        if (c0[2] - r >= 0) visit(x, y, c0[2] - r);
                        if (r > 0 && c0[2] + r <= n[2] - 1) visit(x, y, c0[2] + r);
                    }
                }
        }
        return best.hit();
    }
};

// --- a grid of starting triangles for WideBvh ("grid-accelerated query initialisation") -------------
struct SeedGrid {
    Aabb box;
    int n = 1;
    double cell[3] = {1, 1, 1};
    std::vector<std::uint32_t> seeds;  // per cell: the slot of the triangle nearest its centre

    // The grid covers the bounds grown by `margin` on every side (queries come from around the surface).
    SeedGrid(const WideBvh& tree, int resolution, double margin) : n(std::max(resolution, 1)) {
        const Aabb b = tree.index->bounds();
        box.expand(b.lo - Vec3{margin, margin, margin});
        box.expand(b.hi + Vec3{margin, margin, margin});
        const Vec3 e = box.extent();
        for (int a = 0; a < 3; ++a) cell[a] = e[a] > 0 ? e[a] / n : 1.0;
        seeds.resize(static_cast<std::size_t>(n) * n * n, kNone);
        std::uint32_t last = kNone;
        for (int z = 0; z < n; ++z)
            for (int y = 0; y < n; ++y)
                for (int x = 0; x < n; ++x) {
                    const Vec3 centre{box.lo.x + (x + 0.5) * cell[0], box.lo.y + (y + 0.5) * cell[1], box.lo.z + (z + 0.5) * cell[2]};
                    Found f;
                    if (tree.closest(centre, kInfinity, f, 1.0, last)) last = f.slot;
                    seeds[(static_cast<std::size_t>(z) * n + y) * n + x] = last;
                }
    }
    [[nodiscard]] std::uint32_t seed(Vec3 p) const noexcept {
        const auto c = [&](double v, int a) { return std::clamp(static_cast<int>(std::floor((v - box.lo[a]) / cell[a])), 0, n - 1); };
        return seeds[(static_cast<std::size_t>(c(p.z, 2)) * n + c(p.y, 1)) * n + c(p.x, 0)];
    }
    [[nodiscard]] std::size_t memory_bytes() const noexcept { return seeds.size() * sizeof(std::uint32_t); }
};

}  // namespace cand
