#pragma once

#include <algorithm>
#include <cmath>
#include <limits>

namespace pg {

// Double precision throughout: procedural chains accumulate error, and every
// quality metric (volume, watertightness, intersection) is computed on these.
struct Vec3 {
    double x = 0.0, y = 0.0, z = 0.0;

    [[nodiscard]] constexpr double operator[](int i) const noexcept { return i == 0 ? x : (i == 1 ? y : z); }
    friend constexpr bool operator==(const Vec3&, const Vec3&) = default;
};

constexpr Vec3 operator+(Vec3 a, Vec3 b) noexcept { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
constexpr Vec3 operator-(Vec3 a, Vec3 b) noexcept { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
constexpr Vec3 operator-(Vec3 a) noexcept { return {-a.x, -a.y, -a.z}; }
constexpr Vec3 operator*(Vec3 a, double s) noexcept { return {a.x * s, a.y * s, a.z * s}; }
constexpr Vec3 operator*(double s, Vec3 a) noexcept { return a * s; }
constexpr Vec3 operator/(Vec3 a, double s) noexcept { return {a.x / s, a.y / s, a.z / s}; }
constexpr Vec3& operator+=(Vec3& a, Vec3 b) noexcept { a = a + b; return a; }
constexpr Vec3& operator-=(Vec3& a, Vec3 b) noexcept { a = a - b; return a; }

constexpr double dot(Vec3 a, Vec3 b) noexcept { return a.x * b.x + a.y * b.y + a.z * b.z; }
constexpr Vec3 cross(Vec3 a, Vec3 b) noexcept {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline double length(Vec3 a) noexcept { return std::sqrt(dot(a, a)); }
inline Vec3 normalize_or(Vec3 a, Vec3 fallback) noexcept {
    const double l = length(a);
    return l > 0.0 ? a / l : fallback;
}
constexpr Vec3 vmin(Vec3 a, Vec3 b) noexcept { return {std::min(a.x, b.x), std::min(a.y, b.y), std::min(a.z, b.z)}; }
constexpr Vec3 vmax(Vec3 a, Vec3 b) noexcept { return {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)}; }

struct Aabb {
    static constexpr double inf = std::numeric_limits<double>::infinity();
    Vec3 lo{inf, inf, inf};
    Vec3 hi{-inf, -inf, -inf};

    constexpr void expand(Vec3 p) noexcept { lo = vmin(lo, p); hi = vmax(hi, p); }
    [[nodiscard]] constexpr bool valid() const noexcept { return lo.x <= hi.x && lo.y <= hi.y && lo.z <= hi.z; }
    [[nodiscard]] constexpr Vec3 extent() const noexcept { return valid() ? hi - lo : Vec3{}; }
    [[nodiscard]] constexpr Vec3 center() const noexcept { return valid() ? (lo + hi) * 0.5 : Vec3{}; }
    [[nodiscard]] double diagonal() const noexcept { return length(extent()); }
};

}  // namespace pg
