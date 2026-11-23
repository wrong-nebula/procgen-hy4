#pragma once

#include "pg/math.hpp"

#include <cstdint>
#include <span>

namespace pg {

namespace detail {
using BoxPairFn = void (*)(void* ctx, std::uint32_t a, std::uint32_t b);
void boxes_intersections_self_impl(std::span<const Aabb> boxes, BoxPairFn fn, void* ctx);
void boxes_intersections_bipartite_impl(std::span<const Aabb> boxes_a, std::span<const Aabb> boxes_b,
                                        BoxPairFn fn, void* ctx);
}  // namespace detail

// Reports all intersecting pairs (i, j) with i < j in `boxes` using the
// Zomorodian & Edelsbrunner (2002) hybrid segment-tree / scanning algorithm.
// Closed-box intersection: boxes touching on boundaries or corners are reported.
// Invalid boxes (!box.valid()) are ignored.
template <class Fn>
void boxes_intersections(std::span<const Aabb> boxes, Fn&& callback) {
    auto thunk = [](void* ctx, std::uint32_t i, std::uint32_t j) {
        (*static_cast<std::remove_reference_t<Fn>*>(ctx))(i, j);
    };
    detail::boxes_intersections_self_impl(boxes, thunk, &callback);
}

// Reports all intersecting pairs (i, j) with i in [0, boxes_a.size()) and
// j in [0, boxes_b.size()). Each intersecting pair is reported exactly once.
// Invalid boxes (!box.valid()) are ignored.
template <class Fn>
void boxes_intersections(std::span<const Aabb> boxes_a, std::span<const Aabb> boxes_b, Fn&& callback) {
    auto thunk = [](void* ctx, std::uint32_t i, std::uint32_t j) {
        (*static_cast<std::remove_reference_t<Fn>*>(ctx))(i, j);
    };
    detail::boxes_intersections_bipartite_impl(boxes_a, boxes_b, thunk, &callback);
}

}  // namespace pg
