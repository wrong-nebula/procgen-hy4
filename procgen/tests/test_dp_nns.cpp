#include "testing.hpp"

#include "pg/dp_nns.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <random>
#include <thread>
#include <vector>

using namespace pg;

namespace {

double dist2(Vec3 a, Vec3 b) {
    const double dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return dx * dx + dy * dy + dz * dz;
}

DpNnsHit oracle_1nn(std::span<const Vec3> pts, Vec3 q) {
    DpNnsHit best;
    best.distance_sq = std::numeric_limits<double>::infinity();
    for (std::size_t i = 0; i < pts.size(); ++i) {
        const double d2 = dist2(pts[i], q);
        if (d2 < best.distance_sq || (d2 == best.distance_sq && static_cast<Index>(i) < best.index)) {
            best.distance_sq = d2;
            best.index = static_cast<Index>(i);
        }
    }
    return best;
}

std::vector<double> oracle_knn_dists(std::span<const Vec3> pts, Vec3 q, std::size_t k) {
    std::vector<double> d2(pts.size());
    for (std::size_t i = 0; i < pts.size(); ++i) d2[i] = dist2(pts[i], q);
    const std::size_t m = std::min(k, d2.size());
    std::partial_sort(d2.begin(), d2.begin() + static_cast<std::ptrdiff_t>(m), d2.end());
    d2.resize(m);
    return d2;
}

}  // namespace

TEST(dp_nns_empty_single_and_nonfinite) {
    DpNnsIndex empty_idx;
    CHECK(empty_idx.empty());
    CHECK_EQ(empty_idx.query({1.0, 2.0, 3.0}).index, kInvalidIndex);
    CHECK(empty_idx.query_knn({0.0, 0.0, 0.0}, 5).empty());

    const std::vector<Vec3> one = {{3.5, -1.25, 7.0}};
    DpNnsIndex single_idx(one);
    CHECK_EQ(single_idx.size(), std::size_t{1});
    const DpNnsHit h = single_idx.query({0.0, 0.0, 0.0});
    CHECK_EQ(h.index, Index{0});
    CHECK_NEAR(h.distance_sq, dist2(one[0], {0.0, 0.0, 0.0}), 1e-12);

    const std::vector<Vec3> bad = {{0.0, 0.0, 0.0}, {std::numeric_limits<double>::quiet_NaN(), 1.0, 2.0}};
    CHECK_THROWS(DpNnsIndex(bad));
}

TEST(dp_nns_collinear_coplanar_and_duplicates_match_oracle) {
    std::mt19937_64 rng(42);
    std::uniform_real_distribution<double> qdist(-6.0, 6.0);

    // 1. Strictly collinear 3D line (120 points)
    std::vector<Vec3> line_pts;
    for (int i = 0; i < 120; ++i) {
        const double t = -3.0 + 0.05 * i;
        line_pts.push_back({1.2 + 0.6 * t, -0.7 + 1.1 * t, 0.3 - 0.4 * t});
    }
    DpNnsIndex line_idx(line_pts);
    for (int i = 0; i < 200; ++i) {
        const Vec3 q{qdist(rng), qdist(rng), qdist(rng)};
        const DpNnsHit got = line_idx.query(q);
        const DpNnsHit ref = oracle_1nn(line_pts, q);
        CHECK_NEAR(got.distance_sq, ref.distance_sq, 1e-11);
    }

    // 2. Strictly coplanar tilted 2D grid + cocircular quads + exact duplicates
    std::vector<Vec3> plane_pts;
    const Vec3 u = normalize_or(Vec3{1.0, 0.4, -0.3}, Vec3{1.0, 0.0, 0.0});
    const Vec3 v = normalize_or(cross(u, Vec3{0.2, 1.0, 0.5}), Vec3{0.0, 1.0, 0.0});
    for (int ix = -8; ix <= 8; ++ix) {
        for (int iy = -8; iy <= 8; ++iy) {
            const Vec3 p = Vec3{0.5, -0.2, 1.1} + u * (0.25 * ix) + v * (0.25 * iy);
            plane_pts.push_back(p);
            if ((ix + iy) % 11 == 0) plane_pts.push_back(p);  // exact duplicate
        }
    }
    DpNnsIndex plane_idx(plane_pts);
    for (int i = 0; i < 300; ++i) {
        const Vec3 q{qdist(rng), qdist(rng), qdist(rng)};
        const DpNnsHit got = plane_idx.query(q);
        const DpNnsHit ref = oracle_1nn(plane_pts, q);
        CHECK_NEAR(got.distance_sq, ref.distance_sq, 1e-11);

        const auto knn = plane_idx.query_knn(q, 6);
        const auto ref_k = oracle_knn_dists(plane_pts, q, 6);
        CHECK_EQ(knn.size(), ref_k.size());
        for (std::size_t j = 0; j < knn.size(); ++j) {
            CHECK_NEAR(knn[j].distance_sq, ref_k[j], 1e-11);
        }
    }
}

TEST(dp_nns_manifold_surface_and_far_volumetric_queries_exact) {
    // Subdivided hard-surface/organic mesh (~1,538 vertices on a 2-manifold surface)
    Mesh sphere = catmull_clark(make_box({2.0, 1.4, 1.0}, 2), 4);
    DpNnsOptions opts;
    opts.parallel = true;
    DpNnsIndex idx(sphere.positions, opts);
    CHECK_EQ(idx.size(), sphere.positions.size());
    CHECK(idx.stats().directed_edges > sphere.positions.size());

    std::mt19937_64 rng(20261118);
    std::uniform_real_distribution<double> near_d(-2.5, 2.5);
    std::uniform_real_distribution<double> far_d(-50.0, 50.0);

    std::vector<Vec3> queries;
    queries.reserve(600);
    for (int i = 0; i < 400; ++i) queries.push_back({near_d(rng), near_d(rng), near_d(rng)});
    for (int i = 0; i < 200; ++i) queries.push_back({far_d(rng), far_d(rng), far_d(rng)});

    // Single, batch (Morton), k-NN, and concurrent reader threads must all match brute-force 100%.
    const auto batch = idx.query_batch(queries, true);
    CHECK_EQ(batch.size(), queries.size());

    std::size_t total_visited = 0;
    for (std::size_t i = 0; i < queries.size(); ++i) {
        const DpNnsHit ref = oracle_1nn(sphere.positions, queries[i]);
        const DpNnsHit single = idx.query(queries[i]);
        CHECK_NEAR(single.distance_sq, ref.distance_sq, 1e-11);
        CHECK_NEAR(batch[i].distance_sq, ref.distance_sq, 1e-11);
        total_visited += single.visited;

        if (i % 20 == 0) {
            const auto knn = idx.query_knn(queries[i], 8);
            const auto ref_k = oracle_knn_dists(sphere.positions, queries[i], 8);
            CHECK_EQ(knn.size(), ref_k.size());
            for (std::size_t j = 0; j < knn.size(); ++j) {
                CHECK_NEAR(knn[j].distance_sq, ref_k[j], 1e-11);
            }
        }
    }
    // Grid-accelerated walk visits only a small fraction of the 1,538 vertices on average.
    const double avg_visited = static_cast<double>(total_visited) / static_cast<double>(queries.size());
    CHECK(avg_visited < 180.0);

    // Thread-safe concurrent reads + integration helpers (`find_close_pairs_dpnns`, `sample_unsigned_distance_field`)
    std::vector<double> thread_d2(queries.size(), -1.0);
    std::vector<std::thread> workers;
    for (std::size_t t = 0; t < 4; ++t) {
        workers.emplace_back([&, t] {
            for (std::size_t i = t; i < queries.size(); i += 4) {
                thread_d2[i] = idx.query(queries[i]).distance_sq;
            }
        });
    }
    for (auto& w : workers) w.join();
    const auto udf = sample_unsigned_distance_field(sphere.positions, queries, opts);
    for (std::size_t i = 0; i < queries.size(); ++i) {
        CHECK_NEAR(thread_d2[i], batch[i].distance_sq, 1e-12);
        CHECK_NEAR(udf[i] * udf[i], batch[i].distance_sq, 1e-10);
    }

    // Close-pair integration check against O(N^2) oracle on 250 points
    std::vector<Vec3> subset(sphere.positions.begin(), sphere.positions.begin() + 250);
    const double rad = 0.22;
    const auto got_pairs = find_close_pairs_dpnns(subset, rad, opts);
    std::vector<std::pair<Index, Index>> ref_pairs;
    for (std::size_t i = 0; i < subset.size(); ++i) {
        for (std::size_t j = i + 1; j < subset.size(); ++j) {
            if (dist2(subset[i], subset[j]) <= rad * rad) {
                ref_pairs.emplace_back(static_cast<Index>(i), static_cast<Index>(j));
            }
        }
    }
    CHECK_EQ(got_pairs.size(), ref_pairs.size());
    CHECK(got_pairs == ref_pairs);
}
