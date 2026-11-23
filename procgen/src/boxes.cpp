// 3D axis-aligned box-intersection reporting based on:
//   Afra Zomorodian and Herbert Edelsbrunner,
//   "Fast software for box intersections",
//   International Journal of Computational Geometry & Applications, 12(1-2), 2002,
// and Inria Geogram's BSD-3 implementation (boxes_intersections.{h,cpp}).

#include "pg/boxes.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <tuple>
#include <utility>
#include <vector>

namespace pg::detail {
namespace {

// Deterministic SplitMix64 PRNG so pivot sampling is fast and 100% reproducible.
struct Rng {
    std::uint64_t s = 0x9E3779B97F4A7C15ull;

    std::uint32_t next_bounded(std::uint32_t bound) noexcept {
        std::uint64_t z = (s += 0x9E3779B97F4A7C15ull);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
        z = z ^ (z >> 31);
        return static_cast<std::uint32_t>((static_cast<__uint128_t>(static_cast<std::uint32_t>(z)) * bound) >> 32);
    }
};

// Strict total order on lower bounds using globally unique box IDs `gid` to
// break coordinate ties consistently across both self and bipartite queries.
inline bool lt_sos(double a, double b, std::uint64_t gid_a, std::uint64_t gid_b) noexcept {
    return a < b || (a == b && gid_a < gid_b);
}

struct BoxesRange {
    const Aabb* boxes = nullptr;
    std::uint64_t id_offset = 0;
    std::uint32_t* b = nullptr;
    std::uint32_t* e = nullptr;

    [[nodiscard]] bool empty() const noexcept { return b == e; }
    [[nodiscard]] std::uint32_t size() const noexcept { return static_cast<std::uint32_t>(e - b); }
    [[nodiscard]] std::uint64_t gid(std::uint32_t idx) const noexcept { return id_offset + idx; }
    [[nodiscard]] double xmin(std::uint32_t idx, int d) const noexcept { return boxes[idx].lo[d]; }
    [[nodiscard]] double xmax(std::uint32_t idx, int d) const noexcept { return boxes[idx].hi[d]; }

    template <class Pred>
    std::pair<BoxesRange, BoxesRange> split(Pred&& pred, bool precond = true) {
        if (!precond) {
            return {BoxesRange{boxes, id_offset, b, b}, BoxesRange{boxes, id_offset, b, e}};
        }
        std::uint32_t* m = std::partition(b, e, pred);
        return {BoxesRange{boxes, id_offset, b, m}, BoxesRange{boxes, id_offset, m, e}};
    }

    // Check overlap of box i (in *this) and box j (in J) along dimensions 1..d.
    [[nodiscard]] bool isect_1_to_d(std::uint32_t i, const BoxesRange& J, std::uint32_t j, int d) const noexcept {
        if (gid(i) == J.gid(j)) return false;
        const Aabb& bi = boxes[i];
        const Aabb& bj = J.boxes[j];
        if (d >= 1 && (bi.hi.y < bj.lo.y || bi.lo.y > bj.hi.y)) return false;
        if (d >= 2 && (bi.hi.z < bj.lo.z || bi.lo.z > bj.hi.z)) return false;
        return true;
    }

    // Check whether box i (seen as interval along d) contains box p's lower bound
    // under the symbolic perturbation order.
    [[nodiscard]] bool contains_point(std::uint32_t i, const BoxesRange& P, std::uint32_t p, int d) const noexcept {
        const double px = P.xmin(p, d);
        return lt_sos(xmin(i, d), px, gid(i), P.gid(p)) && xmax(i, d) >= px;
    }
};

struct BoxesCompare {
    const Aabb* boxes;
    std::uint64_t id_offset;
    int d;

    bool operator()(std::uint32_t a, std::uint32_t b) const noexcept {
        return lt_sos(boxes[a].lo[d], boxes[b].lo[d], id_offset + a, id_offset + b);
    }
};

inline void emit_pair(BoxPairFn fn, void* ctx, bool self_mode, bool swap_ip, std::uint32_t i, std::uint32_t p) {
    if (self_mode) {
        if (i < p) fn(ctx, i, p);
        else if (p < i) fn(ctx, p, i);
    } else if (swap_ip) {
        fn(ctx, p, i);
    } else {
        fn(ctx, i, p);
    }
}

void one_way_scan(BoxesRange I, BoxesRange P, int d, BoxPairFn fn, void* ctx, bool self_mode, bool swap_ip) {
    std::sort(I.b, I.e, BoxesCompare{I.boxes, I.id_offset, 0});
    std::sort(P.b, P.e, BoxesCompare{P.boxes, P.id_offset, 0});
    for (std::uint32_t* i = I.b; i != I.e; ++i) {
        const std::uint32_t ii = *i;
        const double ilo = I.xmin(ii, 0);
        const double ihi = I.xmax(ii, 0);
        const std::uint64_t igid = I.gid(ii);
        while (P.b != P.e && lt_sos(P.xmin(*P.b, 0), ilo, P.gid(*P.b), igid)) {
            ++P.b;
        }
        for (std::uint32_t* p = P.b; p != P.e && P.xmin(*p, 0) <= ihi; ++p) {
            const std::uint32_t pp = *p;
            if (P.isect_1_to_d(pp, I, ii, d)) {
                emit_pair(fn, ctx, self_mode, swap_ip, ii, pp);
            }
        }
    }
}

void modified_two_way_scan(BoxesRange I, BoxesRange P, int d, BoxPairFn fn, void* ctx, bool self_mode, bool swap_ip) {
    std::sort(I.b, I.e, BoxesCompare{I.boxes, I.id_offset, 0});
    std::sort(P.b, P.e, BoxesCompare{P.boxes, P.id_offset, 0});
    while (I.b != I.e && P.b != P.e) {
        const std::uint32_t ii0 = *I.b;
        const std::uint32_t pp0 = *P.b;
        if (lt_sos(I.xmin(ii0, 0), P.xmin(pp0, 0), I.gid(ii0), P.gid(pp0))) {
            const double ihi = I.xmax(ii0, 0);
            for (std::uint32_t* p = P.b; p != P.e && P.xmin(*p, 0) <= ihi; ++p) {
                const std::uint32_t pp = *p;
                if (P.isect_1_to_d(pp, I, ii0, d) && I.contains_point(ii0, P, pp, d)) {
                    emit_pair(fn, ctx, self_mode, swap_ip, ii0, pp);
                }
            }
            ++I.b;
        } else {
            const double phi = P.xmax(pp0, 0);
            for (std::uint32_t* i = I.b; i != I.e && I.xmin(*i, 0) <= phi; ++i) {
                const std::uint32_t ii = *i;
                if (I.isect_1_to_d(ii, P, pp0, d) && I.contains_point(ii, P, pp0, d)) {
                    emit_pair(fn, ctx, self_mode, swap_ip, ii, pp0);
                }
            }
            ++P.b;
        }
    }
}

std::uint32_t* median_of_three(const BoxesRange& R, std::uint32_t* a, std::uint32_t* b, std::uint32_t* c,
                               int dim) noexcept {
    const BoxesCompare cmp{R.boxes, R.id_offset, dim};
    if (cmp(*a, *b)) {
        if (cmp(*b, *c)) return b;
        return cmp(*a, *c) ? c : a;
    }
    if (cmp(*a, *c)) return a;
    return cmp(*b, *c) ? c : b;
}

std::uint32_t* approximate_median(const BoxesRange& R, int d, int levels, Rng& rng) noexcept {
    if (levels < 0) {
        return R.b + rng.next_bounded(R.size());
    }
    std::uint32_t* m0 = approximate_median(R, d, levels - 1, rng);
    std::uint32_t* m1 = approximate_median(R, d, levels - 1, rng);
    std::uint32_t* m2 = approximate_median(R, d, levels - 1, rng);
    return median_of_three(R, m0, m1, m2, d);
}

std::tuple<BoxesRange, double, BoxesRange> split_points(BoxesRange& P, int d, Rng& rng) {
    const double n = static_cast<double>(P.size());
    int levels = static_cast<int>(0.91 * std::log(n / 137.035999206) + 1.0);
    if (levels <= 0) levels = 1;
    std::uint32_t* m = approximate_median(P, d, levels, rng);
    const double px_m = P.xmin(*m, d);
    auto p1p2 = P.split([&P, d, px_m](std::uint32_t idx) { return P.xmin(idx, d) < px_m; });
    return {p1p2.first, px_m, p1p2.second};
}

void hybrid(BoxesRange I, BoxesRange P, int d, BoxPairFn fn, void* ctx, bool self_mode, bool swap_ip, double lo,
            double hi, Rng& rng) {
    constexpr std::uint32_t kScanningCutoff = 256;
    constexpr double kNegInf = -std::numeric_limits<double>::infinity();
    constexpr double kPosInf = std::numeric_limits<double>::infinity();

    if (I.empty() || P.empty() || lo >= hi) return;

    if (d == 0) {
        one_way_scan(I, P, d, fn, ctx, self_mode, swap_ip);
        return;
    }

    if (I.size() <= kScanningCutoff || P.size() <= kScanningCutoff) {
        modified_two_way_scan(I, P, d, fn, ctx, self_mode, swap_ip);
        return;
    }

    BoxesRange Ispan, Inonspan;
    std::tie(Ispan, Inonspan) = I.split(
        [&I, d, lo, hi](std::uint32_t idx) { return I.xmin(idx, d) < lo && I.xmax(idx, d) > hi; },
        lo != kNegInf && hi != kPosInf);

    if (!Ispan.empty()) {
        hybrid(Ispan, P, d - 1, fn, ctx, self_mode, swap_ip, kNegInf, kPosInf, rng);
        hybrid(P, Ispan, d - 1, fn, ctx, self_mode, !swap_ip, kNegInf, kPosInf, rng);
    }

    BoxesRange Pl, Pr;
    double mi = 0.0;
    std::tie(Pl, mi, Pr) = split_points(P, d, rng);

    if (Pl.empty() || Pr.empty()) {
        modified_two_way_scan(Inonspan, P, d, fn, ctx, self_mode, swap_ip);
        return;
    }

    BoxesRange Il = Inonspan.split([&Inonspan, d, mi](std::uint32_t idx) { return Inonspan.xmin(idx, d) < mi; }).first;
    hybrid(Il, Pl, d, fn, ctx, self_mode, swap_ip, lo, mi, rng);

    BoxesRange Ir =
        Inonspan.split([&Inonspan, d, mi](std::uint32_t idx) { return Inonspan.xmax(idx, d) >= mi; }).first;
    hybrid(Ir, Pr, d, fn, ctx, self_mode, swap_ip, mi, hi, rng);
}

std::vector<std::uint32_t> collect_valid_indices(std::span<const Aabb> boxes) {
    std::vector<std::uint32_t> idx;
    idx.reserve(boxes.size());
    for (std::size_t i = 0; i < boxes.size(); ++i) {
        if (boxes[i].valid()) idx.push_back(static_cast<std::uint32_t>(i));
    }
    return idx;
}

}  // namespace

void boxes_intersections_self_impl(std::span<const Aabb> boxes, BoxPairFn fn, void* ctx) {
    if (boxes.size() < 2) return;
    std::vector<std::uint32_t> idx = collect_valid_indices(boxes);
    if (idx.size() < 2) return;
    std::vector<std::uint32_t> pdx = idx;

    constexpr double kNegInf = -std::numeric_limits<double>::infinity();
    constexpr double kPosInf = std::numeric_limits<double>::infinity();
    Rng rng{};
    BoxesRange I{boxes.data(), 0, idx.data(), idx.data() + idx.size()};
    BoxesRange P{boxes.data(), 0, pdx.data(), pdx.data() + pdx.size()};
    hybrid(I, P, 2, fn, ctx, /*self_mode=*/true, /*swap_ip=*/false, kNegInf, kPosInf, rng);
}

void boxes_intersections_bipartite_impl(std::span<const Aabb> boxes_a, std::span<const Aabb> boxes_b, BoxPairFn fn,
                                        void* ctx) {
    if (boxes_a.empty() || boxes_b.empty()) return;
    std::vector<std::uint32_t> ia = collect_valid_indices(boxes_a);
    std::vector<std::uint32_t> ib = collect_valid_indices(boxes_b);
    if (ia.empty() || ib.empty()) return;

    const std::uint64_t offset_b = static_cast<std::uint64_t>(boxes_a.size());
    constexpr double kNegInf = -std::numeric_limits<double>::infinity();
    constexpr double kPosInf = std::numeric_limits<double>::infinity();
    Rng rng{};

    // Pass 1: boxes_a as intervals stabbing lower bounds of boxes_b in dim 2.
    {
        std::vector<std::uint32_t> a_work = ia;
        std::vector<std::uint32_t> b_work = ib;
        BoxesRange I{boxes_a.data(), 0, a_work.data(), a_work.data() + a_work.size()};
        BoxesRange P{boxes_b.data(), offset_b, b_work.data(), b_work.data() + b_work.size()};
        hybrid(I, P, 2, fn, ctx, /*self_mode=*/false, /*swap_ip=*/false, kNegInf, kPosInf, rng);
    }
    // Pass 2: boxes_b as intervals stabbing lower bounds of boxes_a in dim 2.
    {
        BoxesRange I{boxes_b.data(), offset_b, ib.data(), ib.data() + ib.size()};
        BoxesRange P{boxes_a.data(), 0, ia.data(), ia.data() + ia.size()};
        hybrid(I, P, 2, fn, ctx, /*self_mode=*/false, /*swap_ip=*/true, kNegInf, kPosInf, rng);
    }
}

}  // namespace pg::detail
