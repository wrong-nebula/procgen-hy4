// Zomorodian-Edelsbrunner 3D box intersection and exact/coplanar self-intersection tests.
#include "testing.hpp"

#include "pg/boxes.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

using namespace pg;

namespace {

bool boxes_overlap_closed(const Aabb& a, const Aabb& b) {
    if (!a.valid() || !b.valid()) return false;
    return a.lo.x <= b.hi.x && a.hi.x >= b.lo.x &&
           a.lo.y <= b.hi.y && a.hi.y >= b.lo.y &&
           a.lo.z <= b.hi.z && a.hi.z >= b.lo.z;
}

std::vector<std::pair<std::uint32_t, std::uint32_t>> oracle_self(std::span<const Aabb> boxes) {
    std::vector<std::pair<std::uint32_t, std::uint32_t>> out;
    for (std::uint32_t i = 0; i < boxes.size(); ++i) {
        for (std::uint32_t j = i + 1; j < boxes.size(); ++j) {
            if (boxes_overlap_closed(boxes[i], boxes[j])) out.emplace_back(i, j);
        }
    }
    return out;
}

std::vector<std::pair<std::uint32_t, std::uint32_t>> oracle_bipartite(std::span<const Aabb> a,
                                                                       std::span<const Aabb> b) {
    std::vector<std::pair<std::uint32_t, std::uint32_t>> out;
    for (std::uint32_t i = 0; i < a.size(); ++i) {
        for (std::uint32_t j = 0; j < b.size(); ++j) {
            if (boxes_overlap_closed(a[i], b[j])) out.emplace_back(i, j);
        }
    }
    return out;
}

struct TestRng {
    std::uint64_t s = 0xCAFEBABE12345678ull;
    double u() {
        s = s * 6364136223846793005ull + 1442695040888963407ull;
        return static_cast<double>(s >> 11) * 0x1.0p-53;
    }
};

}  // namespace

TEST(boxes_self_matches_bruteforce_oracle) {
    // Empty & singleton
    {
        std::vector<Aabb> empty;
        std::size_t hits = 0;
        boxes_intersections(empty, [&](std::uint32_t, std::uint32_t) { ++hits; });
        CHECK_EQ(hits, std::size_t(0));

        Aabb b;
        b.expand({0, 0, 0});
        b.expand({1, 1, 1});
        std::vector<Aabb> single{b};
        boxes_intersections(single, [&](std::uint32_t, std::uint32_t) { ++hits; });
        CHECK_EQ(hits, std::size_t(0));
    }

    // Degenerate: identical boxes, zero-thickness planar/point boxes, invalid Aabbs,
    // and >1200 boxes to exercise full hybrid segment-tree recursion + Ispan + cutoffs.
    TestRng rng;
    std::vector<Aabb> boxes;
    boxes.reserve(1300);
    for (int i = 0; i < 1200; ++i) {
        const Vec3 c{rng.u() * 10.0 - 5.0, rng.u() * 10.0 - 5.0, rng.u() * 10.0 - 5.0};
        const Vec3 r{rng.u() * 0.4, rng.u() * 0.4, (i % 11 == 0) ? 0.0 : rng.u() * 0.4};
        Aabb box;
        box.expand(c - r);
        box.expand(c + r);
        boxes.push_back(box);
    }
    // Exact duplicates, corner-touching boxes, point boxes, and invalid boxes.
    for (int i = 0; i < 25; ++i) boxes.push_back(boxes[static_cast<std::size_t>(i * 7)]);
    for (int i = 0; i < 15; ++i) {
        Aabb pt;
        pt.expand({1.0, 2.0, 3.0});
        boxes.push_back(pt);
    }
    boxes.push_back(Aabb{});  // invalid (+inf..-inf)

    std::vector<std::pair<std::uint32_t, std::uint32_t>> got;
    boxes_intersections(boxes, [&](std::uint32_t i, std::uint32_t j) {
        CHECK(i < j);
        got.emplace_back(i, j);
    });
    std::sort(got.begin(), got.end());
    const auto expected = oracle_self(boxes);
    CHECK_EQ(got.size(), expected.size());
    CHECK(got == expected);
}

TEST(boxes_bipartite_matches_bruteforce_oracle) {
    TestRng rng;
    std::vector<Aabb> a, b;
    for (int i = 0; i < 700; ++i) {
        const Vec3 c{rng.u() * 8.0, rng.u() * 8.0, rng.u() * 8.0};
        const Vec3 r{rng.u() * 0.5, rng.u() * 0.5, rng.u() * 0.5};
        Aabb box;
        box.expand(c - r);
        box.expand(c + r);
        a.push_back(box);
    }
    for (int i = 0; i < 650; ++i) {
        const Vec3 c{rng.u() * 8.0, rng.u() * 8.0, rng.u() * 8.0};
        const Vec3 r{rng.u() * 0.5, (i % 9 == 0) ? 0.0 : rng.u() * 0.5, rng.u() * 0.5};
        Aabb box;
        box.expand(c - r);
        box.expand(c + r);
        b.push_back(box);
    }
    // Critical tie-breaking stress: identical box at identical local index 0 in BOTH arrays!
    b[0] = a[0];
    b[17] = a[17];
    a.push_back(Aabb{});
    b.push_back(Aabb{});

    std::vector<std::pair<std::uint32_t, std::uint32_t>> got;
    boxes_intersections(a, b, [&](std::uint32_t ia, std::uint32_t ib) { got.emplace_back(ia, ib); });
    std::sort(got.begin(), got.end());
    const auto expected = oracle_bipartite(a, b);
    CHECK_EQ(got.size(), expected.size());
    CHECK(got == expected);
}

TEST(self_intersections_exact_and_coplanar_modes) {
    // 1. Sub-tolerance microscopic proper crossing (1e-14 penetration inside a 10m scene):
    // tolerance_rel = 1e-12 ignores it as touching, whereas tolerance_rel = 0.0 (exact PCK) catches it.
    {
        Mesh m;
        // Far bounding triangle so scene diagonal ~ 10.0 (tol = 1e-12 * 1000 = 1e-9).
        m.add_vertex({10, 0, 0});
        m.add_vertex({10, 1, 0});
        m.add_vertex({10, 0, 1});
        m.add_face({0, 1, 2});

        // Triangle A in z = 0 plane around (0.2, 0.2, 0)
        m.add_vertex({0, 0, 0});
        m.add_vertex({1, 0, 0});
        m.add_vertex({0, 1, 0});
        m.add_face({3, 4, 5});

        // Triangle B dipping just 1e-13 below z = 0 at (0.25, 0.25)
        m.add_vertex({0.25, 0.25, -1e-13});
        m.add_vertex({0.25, 0.25, 1.0});
        m.add_vertex({0.45, 0.25, 1.0});
        m.add_face({6, 7, 8});

        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 1e-12}).intersecting_pairs, std::size_t(0));
        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 0.0, .exact_predicates = true}).intersecting_pairs,
                 std::size_t(1));
    }

    // 2. Coplanar overlapping triangles: ignored by default, caught with include_coplanar = true.
    {
        Mesh m;
        m.add_vertex({0, 0, 0});
        m.add_vertex({2, 0, 0});
        m.add_vertex({0, 2, 0});
        m.add_face({0, 1, 2});

        m.add_vertex({0.5, 0.5, 0});
        m.add_vertex({2.5, 0.5, 0});
        m.add_vertex({0.5, 2.5, 0});
        m.add_face({3, 4, 5});

        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 0.0, .include_coplanar = false}).intersecting_pairs,
                 std::size_t(0));
        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 0.0, .include_coplanar = true}).intersecting_pairs,
                 std::size_t(1));
    }

    // 3. Coplanar triangles touching ONLY along a shared line (disjoint 2D open interiors):
    // must NOT be flagged even with include_coplanar = true.
    {
        Mesh m;
        m.add_vertex({0, 0, 0});
        m.add_vertex({1, 0, 0});
        m.add_vertex({0, 1, 0});
        m.add_face({0, 1, 2});

        // Shares segment from (0.8, 0.2, 0) to (0.2, 0.8, 0) on hypotenuse x + y = 1, on the opposite side.
        m.add_vertex({0.8, 0.2, 0});
        m.add_vertex({1.5, 1.5, 0});
        m.add_vertex({0.2, 0.8, 0});
        m.add_face({3, 4, 5});

        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 0.0, .include_coplanar = true}).intersecting_pairs,
                 std::size_t(0));
    }

    // 4. Touching closed boxes: 0 proper crossings even with tolerance_rel = 0.0,
    // >0 coplanar overlaps when include_coplanar = true.
    {
        Mesh a = make_box({1, 1, 1}, 1);
        Mesh b = make_box({1, 1, 1}, 1);
        translate(b, {1.0, 0, 0});
        const Mesh m = merge(a, b);
        CHECK_EQ(find_self_intersections(m, {.tolerance_rel = 0.0, .include_coplanar = false}).intersecting_pairs,
                 std::size_t(0));
        CHECK(find_self_intersections(m, {.tolerance_rel = 0.0, .include_coplanar = true}).intersecting_pairs > 0);
    }
}
