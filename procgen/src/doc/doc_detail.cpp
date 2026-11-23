// Surface details: sampling, and the generators that place them (docs/DETAILING.md).
//
//   detailStamp  [geometry, points]   one detail at each point, projected onto the nearest surface
//                shape disc|ring|box|polygon|cross, profile dome|flat|groove|cone|ramp, radius, inner,
//                size [x, y], corner, sides, width, height, fillet, slot, tangent [x, y, z], angle, reach
//   fastener     [geometry, points or curves]   heads at the points, or along the curves at `pitch` or `count`
//                head rivet|hex|square|cap|stud|slotted|cross, radius, height, washer, turn aligned|random,
//                seed, slot, clearance, reach.   rivetLine is fastener with head rivet along curves.
//   detailStroke [geometry, curves]   profile groove|bead|band|lap|mark, width, depth or height, dash [on, off],
//                step, slot, clearance, reach.   detailSeam is the groove.
//   detailTicks  [geometry, curves]   graduations square to the curve: pitch or count, length, major, majorLength,
//                width, depth or slot, side centre|left|right
//   detailText   [geometry, points]   single-stroke figures and capitals: text, size, direction, align, profile
//                bead|band|groove|mark, height, width, spacing, slot
//   pointField   []   points in a plane: layout grid|hex, spacing, size [x, y] or radius (and inner)
//   outline      []   one curve: shape circle|arc|rect|polygon, radius, size, corner, sides, start, sweep, segments
//   courses      []   joints of coursed work in a rectangle: size [x, y], course, block, bond, minimum
//
// A head never sits on a joint: a fastener closer to a groove, lap or bead of the same face than
// `clearance` (edge to edge; default one head radius) is left out, whichever of the two is authored
// first, so a row that crosses a plate joint is interrupted there. Heads that would overlap another
// head are left out too (rows meeting at a corner). Flat bands and paint marks carry heads, and so
// does a lapped plate: only the lap's edge counts as the joint.
//
// A raised detail stands on what earlier nodes left under it: its height is added to the raised
// height already at its place (a bead on a door leaf, a screw on a bezel, a rivet on a strap).
//
// A detail belongs to the prototype it lands on, so every instance of that prototype shows it.
#include "doc_internal.hpp"

#include "pg/closest.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <map>
#include <optional>
#include <unordered_map>

namespace pg::doc {

namespace {

// How far along its normal a detail still counts as lying on the surface (curved surfaces leave
// its tangent plane), and the radius of a sphere holding its footprint.
struct Reach {
    double lift, radius;
    Vec3 centre;
};
constexpr double kPi = 3.14159265358979323846;
Reach reach_of(const Detail& d) {
    switch (d.shape) {
        case Detail::Shape::Segment: {
            const double len = length(d.end - d.origin);
            const double lift = std::max(0.5 * d.size_a, 0.05 * len);
            return {lift, 0.5 * len + d.size_a + lift, (d.origin + d.end) * 0.5};
        }
        case Detail::Shape::Box: {
            const double lift = std::max(0.5 * std::min(d.size_a, d.size_b), 0.1 * std::max(d.size_a, d.size_b));
            return {lift, std::hypot(d.size_a, d.size_b) + lift, d.origin};
        }
        case Detail::Shape::Polygon: {
            const double circum = d.size_a / std::cos(kPi / std::max(3.0, d.size_b));
            return {0.5 * d.size_a, circum + 0.5 * d.size_a, d.origin};
        }
        case Detail::Shape::Cross: return {0.5 * d.size_a, std::hypot(d.size_a, d.size_b) + 0.5 * d.size_a, d.origin};
        default: return {0.5 * d.size_a, 1.5 * d.size_a, d.origin};
    }
}

double box_distance(double x, double y, double half_x, double half_y, double corner) {
    const double c = std::clamp(corner, 0.0, std::min(half_x, half_y));
    const double qx = std::abs(x) - (half_x - c), qy = std::abs(y) - (half_y - c);
    return (qx > 0 || qy > 0 ? std::hypot(std::max(qx, 0.0), std::max(qy, 0.0)) : std::max(qx, qy)) - c;
}

// 0 outside the footprint, 1 where the detail is at its full height.
double cover_of(const Detail& d, Vec3 p, Vec3 n) {
    if (dot(n, d.normal) < 0.3) return 0;  // on another face
    const Vec3 r = p - d.origin;
    const double tolerance = reach_of(d).lift;
    double sd = 0;        // signed distance to the outline in the footprint plane, negative inside
    double inradius = 0;  // how far inside the deepest point is
    double across = 0;    // -1 on one side of the footprint, +1 on the other (Ramp)
    if (d.shape == Detail::Shape::Segment) {
        const Vec3 axis = d.end - d.origin;
        const double len2 = dot(axis, axis);
        const double t = len2 > 0 ? std::clamp(dot(r, axis) / len2, 0.0, 1.0) : 0.0;
        const Vec3 off = r - axis * t;
        const double lift = dot(off, d.normal);
        if (std::abs(lift) > tolerance) return 0;
        const Vec3 flat = off - d.normal * lift;
        sd = length(flat) - d.size_a;
        inradius = d.size_a;
        if (d.profile == Detail::Profile::Ramp && len2 > 0) across = dot(flat, cross(d.normal, axis)) / (std::sqrt(len2) * std::max(d.size_a, 1e-12));
    } else {
        if (std::abs(dot(r, d.normal)) > tolerance) return 0;
        const Vec3 bitangent = cross(d.normal, d.tangent);
        const double x = dot(r, d.tangent), y = dot(r, bitangent), radial = std::hypot(x, y);
        switch (d.shape) {
            case Detail::Shape::Ring:
                sd = std::max(radial - d.size_a, d.size_b - radial);
                inradius = 0.5 * (d.size_a - d.size_b);
                across = y / std::max(d.size_a, 1e-12);
                break;
            case Detail::Shape::Box:
                sd = box_distance(x, y, d.size_a, d.size_b, d.size_c);
                inradius = std::min(d.size_a, d.size_b);
                across = y / std::max(d.size_b, 1e-12);
                break;
            case Detail::Shape::Polygon: {  // a flat faces the tangent
                const double sector = 2 * kPi / std::max(3.0, std::round(d.size_b));
                const double angle = std::atan2(y, x);
                sd = radial * std::cos(angle - sector * std::round(angle / sector)) - d.size_a;
                inradius = d.size_a;
                across = y / std::max(d.size_a, 1e-12);
                break;
            }
            case Detail::Shape::Cross:
                sd = std::min(box_distance(x, y, d.size_a, d.size_b, 0), box_distance(x, y, d.size_b, d.size_a, 0));
                inradius = d.size_b;
                across = y / std::max(d.size_a, 1e-12);
                break;
            default:
                sd = radial - d.size_a;
                inradius = d.size_a;
                across = y / std::max(d.size_a, 1e-12);
        }
    }
    if (sd >= 0) return 0;
    const double k = std::clamp(-sd / std::max(inradius, 1e-12), 0.0, 1.0);  // 0 on the outline, 1 at the deepest point
    const double edge = std::clamp(-sd / std::max(d.fillet, 1e-12), 0.0, 1.0);
    const double rounded = edge * edge * (3.0 - 2.0 * edge);
    switch (d.profile) {
        case Detail::Profile::Dome: return std::sqrt(k * (2.0 - k));
        case Detail::Profile::Groove: return 1.0 - (1.0 - k) * (1.0 - k);
        case Detail::Profile::Cone: return k;
        case Detail::Profile::Ramp: return std::clamp(0.5 * (across + 1.0), 0.0, 1.0) * rounded;
        default: return rounded;
    }
}

// Details layer in list order: heights of raised details take the highest, sunk ones the deepest,
// and the two add (a rivet on the floor of a recess); the last detail covering the point with a
// material of its own shows it.
struct Accumulator {
    double raised = 0, sunk = 0;
    std::uint16_t material = 0xFFFF;
    void add(const Detail& d, Vec3 p, Vec3 n) {
        const double cover = cover_of(d, p, n);
        if (!(cover > 0)) return;
        const double h = d.height * cover;
        raised = std::max(raised, h);
        sunk = std::min(sunk, h);
        if (d.material != 0xFFFF && cover >= 0.5) material = d.material;
    }
    [[nodiscard]] DetailSample result() const { return {raised + sunk, material}; }
};

std::uint64_t cell_key(std::int64_t x, std::int64_t y, std::int64_t z) {
    return (static_cast<std::uint64_t>(x) * 0x9E3779B97F4A7C15ull) ^ (static_cast<std::uint64_t>(y) * 0xC2B2AE3D27D4EB4Full) ^
           (static_cast<std::uint64_t>(z) * 0x165667B19E3779F9ull);
}

}  // namespace

DetailSample sample_details(std::span<const Detail> details, Vec3 p, Vec3 n) {
    Accumulator acc;
    for (const Detail& d : details) acc.add(d, p, n);
    return acc.result();
}

DetailIndex::DetailIndex(std::span<const Detail> details) : details_(details.begin(), details.end()) {
    if (details_.empty()) return;
    // Cells sized to the typical detail, so a line of rivets puts a few in each; the rare large
    // detail (a recessed plate among rivets) goes on a short list tested for every sample.
    std::vector<double> radii;
    radii.reserve(details_.size());
    for (const Detail& d : details_) radii.push_back(reach_of(d).radius);
    std::vector<double> sorted = radii;
    std::nth_element(sorted.begin(), sorted.begin() + static_cast<std::ptrdiff_t>(sorted.size() / 2), sorted.end());
    cell_ = std::max(4.0 * sorted[sorted.size() / 2], 1e-9);
    for (std::uint32_t i = 0; i < details_.size(); ++i) {
        const Reach r = reach_of(details_[i]);
        const auto lo = [&](double c) { return static_cast<std::int64_t>(std::floor((c - r.radius) / cell_)); };
        const auto hi = [&](double c) { return static_cast<std::int64_t>(std::floor((c + r.radius) / cell_)); };
        const std::int64_t x0 = lo(r.centre.x), x1 = hi(r.centre.x), y0 = lo(r.centre.y), y1 = hi(r.centre.y), z0 = lo(r.centre.z), z1 = hi(r.centre.z);
        if ((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > 64) {
            everywhere_.push_back(i);
            continue;
        }
        for (std::int64_t x = x0; x <= x1; ++x)
            for (std::int64_t y = y0; y <= y1; ++y)
                for (std::int64_t z = z0; z <= z1; ++z) cells_[cell_key(x, y, z)].push_back(i);
    }
}

DetailSample DetailIndex::sample(Vec3 p, Vec3 n) const {
    if (details_.empty()) return {};
    const auto cell = [&](double c) { return static_cast<std::int64_t>(std::floor(c / cell_)); };
    const auto found = cells_.find(cell_key(cell(p.x), cell(p.y), cell(p.z)));
    const std::vector<std::uint32_t>* near = found == cells_.end() ? nullptr : &found->second;
    // Visit in list order (both lists are ascending) so layering matches sample_details. Hash
    // collisions only add candidates; cover_of rejects them.
    Accumulator acc;
    std::size_t a = 0, b = 0;
    const std::size_t na = near ? near->size() : 0, nb = everywhere_.size();
    while (a < na || b < nb) {
        const bool take_near = b >= nb || (a < na && (*near)[a] < everywhere_[b]);
        acc.add(details_[take_near ? (*near)[a++] : everywhere_[b++]], p, n);
    }
    return acc.result();
}

namespace detail {
namespace {

Mat4 inverse_affine(const Mat4& m) {
    const Vec3 a = m.column(0), b = m.column(1), c = m.column(2), t = m.column(3);
    const double det = dot(a, cross(b, c));
    if (!(std::abs(det) > 1e-18)) fail("Cannot place details on a part with a singular transform");
    const Vec3 r0 = cross(b, c) / det, r1 = cross(c, a) / det, r2 = cross(a, b) / det;   // rows of the inverse
    Mat4 inv = Mat4::identity();
    const Vec3 rows[3] = {r0, r1, r2};
    for (int row = 0; row < 3; ++row) {
        inv.c[0][row] = rows[row].x;
        inv.c[1][row] = rows[row].y;
        inv.c[2][row] = rows[row].z;
        inv.c[3][row] = -dot(rows[row], t);
    }
    return inv;
}

std::atomic<ProjectionSearch> g_search{ProjectionSearch::Auto};
std::atomic<ProjectionProfile*> g_profile{nullptr};

// An index costs as much to build as 25 to 50 scans of the same mesh (more for larger ones) and
// gains nothing below a few dozen triangles (docs/experiments/closest-point-index.md). It is built
// at about half that: a part that is asked 16 times is usually a row with hundreds more to come.
constexpr std::size_t kScansBeforeIndex = 16;
constexpr std::size_t kIndexMinTriangles = 32;

// The nearest point of one prototype's mesh: a scan of its triangles for the first few points, an
// index built once after that. Both give the same answer, so the switch is invisible.
struct Search {
    const Proto* proto = nullptr;
    const Mesh* mesh = nullptr;
    std::size_t triangles = 0, scans = 0;
    std::shared_ptr<const ClosestIndex> index;

    bool closest(Vec3 p, double limit, ClosestHit& hit, double metric) {
        using Clock = std::chrono::steady_clock;
        const auto seconds = [](Clock::time_point from) { return std::chrono::duration<double>(Clock::now() - from).count(); };
        ProjectionProfile* const profile = g_profile.load(std::memory_order_relaxed);
        if (profile) ++profile->queries;
        const ProjectionSearch mode = g_search.load(std::memory_order_relaxed);
        if (mode == ProjectionSearch::Scan) {
            ++scans;
            if (!profile) return closest_point_scan(*mesh, p, limit, hit, metric);
            const auto start = Clock::now();
            const bool found = closest_point_scan(*mesh, p, limit, hit, metric);
            profile->scan_seconds += seconds(start);
            ++profile->scanned;
            return found;
        }
        if (!index && proto && proto->closest_index && !profile) {
            index = proto->closest_index;
        }
        if (!index) {
            if (mode == ProjectionSearch::Auto && (triangles < kIndexMinTriangles || scans < kScansBeforeIndex)) {
                ++scans;
                if (!profile) return closest_point_scan(*mesh, p, limit, hit, metric);
                const auto start = Clock::now();
                const bool found = closest_point_scan(*mesh, p, limit, hit, metric);
                profile->scan_seconds += seconds(start);
                ++profile->scanned;
                return found;
            }
            const auto start = profile ? Clock::now() : Clock::time_point{};
            index = std::make_shared<const ClosestIndex>(*mesh);
            if (proto && !profile) proto->closest_index = index;
            if (profile) {
                profile->build_seconds += seconds(start);
                ++profile->builds;
                profile->built_triangles += index->triangle_count();
            }
        }
        if (!profile) return index->closest(p, limit, hit, metric);
        const auto start = Clock::now();
        const bool found = index->closest(p, limit, hit, metric);
        profile->index_seconds += seconds(start);
        return found;
    }
};

// The parts of a geometry that can carry details, with what is needed to project onto them.
struct Target {
    std::size_t part;
    Mat4 inverse;
    double scale;   // prototype units -> metres
    Aabb box;
    std::shared_ptr<Search> search;  // shared by the parts that place the same prototype
};
struct Hit {
    std::size_t target = 0;
    Vec3 point{}, normal{};   // prototype space
    double distance = 1e300;  // metres
};

std::vector<Target> targets_of(const Geometry& g) {
    std::vector<Target> out;
    std::map<const Proto*, std::shared_ptr<Search>> searches;
    for (std::size_t i = 0; i < g.parts.size(); ++i) {
        const Part& part = g.parts[i];
        if (part.proto->mesh.face_count() == 0) continue;
        std::shared_ptr<Search>& search = searches[part.proto.get()];
        if (!search) {
            search = std::make_shared<Search>();
            search->proto = part.proto.get();
            search->mesh = &part.proto->mesh;
            search->triangles = part.proto->triangle_count();
        }
        out.push_back({i, inverse_affine(part.transform), std::cbrt(std::abs(part.transform.det3())), bounds(part.proto->mesh), search});
    }
    if (out.empty()) fail("The first input has no surface to put details on");
    return out;
}

// The nearest surface point over all the parts: the first part, and within it the first triangle,
// that attains the smallest distance. False when that is further than `reach`.
bool project(const std::vector<Target>& targets, Vec3 world, double reach, Hit& best) {
    best = Hit{};
    for (std::size_t ti = 0; ti < targets.size(); ++ti) {
        const Target& t = targets[ti];
        const Vec3 p = t.inverse.point(world);
        const double margin = std::min(reach, best.distance) / t.scale;
        if (p.x < t.box.lo.x - margin || p.y < t.box.lo.y - margin || p.z < t.box.lo.z - margin || p.x > t.box.hi.x + margin ||
            p.y > t.box.hi.y + margin || p.z > t.box.hi.z + margin)
            continue;
        ClosestHit hit;
        if (t.search->closest(p, std::min(reach, best.distance), hit, t.scale) && hit.distance < best.distance)
            best = {ti, hit.point, hit.normal, hit.distance};
    }
    return best.distance <= reach;
}

Vec3 perpendicular(Vec3 n, Vec3 preferred) {
    Vec3 t = preferred - n * dot(n, preferred);
    if (!(dot(t, t) > 1e-12)) {
        const Vec3 fallback = std::abs(n.z) < 0.9 ? Vec3{0, 0, 1} : Vec3{1, 0, 0};
        t = fallback - n * dot(n, fallback);
    }
    return t / length(t);
}

// Points along a polyline, `spacing` apart and centred on its length (rivets keep their edge
// distance; a line shorter than the spacing gets one in the middle).
std::vector<Vec3> spaced(const Curve& c, double spacing) {
    std::vector<Vec3> pts = c.points;
    if (c.closed && !pts.empty()) pts.push_back(pts.front());
    double total = 0;
    for (std::size_t i = 1; i < pts.size(); ++i) total += length(pts[i] - pts[i - 1]);
    std::vector<Vec3> out;
    if (!(total > 0) || !(spacing > 0)) return out;
    const std::size_t count = c.closed ? std::max<std::size_t>(1, static_cast<std::size_t>(std::llround(total / spacing)))
                                       : std::max<std::size_t>(1, static_cast<std::size_t>(std::floor(total / spacing + 1e-9)));
    const double step = c.closed ? total / static_cast<double>(count) : spacing;
    const double first = c.closed ? 0.0 : (total - step * static_cast<double>(count)) * 0.5 + step * 0.5;
    std::size_t seg = 1;
    double walked = 0;
    for (std::size_t k = 0; k < count; ++k) {
        const double at = std::min(first + step * static_cast<double>(k), total);
        while (seg + 1 < pts.size() && walked + length(pts[seg] - pts[seg - 1]) < at) {
            walked += length(pts[seg] - pts[seg - 1]);
            ++seg;
        }
        const double len = length(pts[seg] - pts[seg - 1]);
        out.push_back(pts[seg - 1] + (pts[seg] - pts[seg - 1]) * (len > 0 ? std::clamp((at - walked) / len, 0.0, 1.0) : 0.0));
    }
    return out;
}

// The polyline with every span cut into pieces no longer than `step` (corners are kept).
std::vector<Vec3> subdivided(const Curve& c, double step) {
    std::vector<Vec3> pts = c.points;
    if (c.closed && !pts.empty()) pts.push_back(pts.front());
    std::vector<Vec3> out;
    for (std::size_t i = 0; i + 1 < pts.size(); ++i) {
        const double len = length(pts[i + 1] - pts[i]);
        const int pieces = std::max(1, static_cast<int>(std::ceil(len / step - 1e-9)));
        for (int k = 0; k < pieces; ++k) out.push_back(pts[i] + (pts[i + 1] - pts[i]) * (static_cast<double>(k) / pieces));
    }
    if (!pts.empty()) out.push_back(pts.back());
    return out;
}

double segment_distance(const Detail& seam, Vec3 p) {
    const Vec3 axis = seam.end - seam.origin;
    const double len2 = dot(axis, axis);
    const double t = len2 > 0 ? std::clamp(dot(p - seam.origin, axis) / len2, 0.0, 1.0) : 0.0;
    return length(p - (seam.origin + axis * t));
}
// Grooves and beads are joints a head must not sit on; so is the edge of a lapped plate, but not the
// lap itself, which is where its rivets go. Flat bands and paint marks carry heads. `clearance` is
// the gap between the head's edge and the joint's edge, in prototype units.
bool head_on_seam(const Detail& head, const Detail& seam, double clearance) {
    if (seam.shape != Detail::Shape::Segment || seam.profile == Detail::Profile::Flat || seam.height == 0 || dot(head.normal, seam.normal) <= 0.3) return false;
    if (seam.profile == Detail::Profile::Ramp) {
        const Vec3 axis = seam.end - seam.origin;
        const double len = length(axis);
        if (!(len > 0)) return false;
        const Vec3 r = head.origin - seam.origin;
        const double along = dot(r, axis) / len, beyond = std::max({0.0, -along, along - len});
        const double across = dot(r, cross(seam.normal, axis)) / len;   // the step is on the +across side
        return std::hypot(across - seam.size_a, beyond) < head.extent + clearance;
    }
    return segment_distance(seam, head.origin) < head.extent + seam.size_a + clearance;
}
bool heads_overlap(const Detail& a, const Detail& b) {
    return b.fastener && dot(a.normal, b.normal) > 0.3 && length(a.origin - b.origin) < a.extent + b.extent;
}

std::uint16_t slot_option(const Options& o, std::string_view key = "slot") {
    const json::Value* slot = o.get(key);
    if (!slot) return 0xFFFF;
    if (!slot->is_string()) fail(std::string(key) + " must be a material slot name");
    return slot_id(slot->string);
}
std::string word_option(const Options& o, std::string_view key, std::string_view fallback) {
    const json::Value* v = o.get(key);
    if (!v) return std::string(fallback);
    if (!v->is_string()) fail(std::string(key) + " must be a word");
    return v->string;
}

// A polyline addressed by arc length.
struct Walk {
    std::vector<Vec3> pts;
    std::vector<double> at;  // arc length at each point
    double total = 0;
    explicit Walk(const Curve& c) : pts(c.points) {
        if (c.closed && !pts.empty()) pts.push_back(pts.front());
        at.reserve(pts.size());
        for (std::size_t i = 0; i < pts.size(); ++i) {
            if (i) total += length(pts[i] - pts[i - 1]);
            at.push_back(total);
        }
    }
    [[nodiscard]] std::size_t span(double s) const {  // index i with at[i] <= s <= at[i + 1]
        const auto it = std::upper_bound(at.begin(), at.end(), s);
        const auto i = static_cast<std::size_t>(std::max<std::ptrdiff_t>(0, (it - at.begin()) - 1));
        return std::min(i, pts.size() >= 2 ? pts.size() - 2 : 0);
    }
    [[nodiscard]] Vec3 point(double s) const {
        if (pts.size() < 2) return pts.empty() ? Vec3{} : pts[0];
        const std::size_t i = span(s);
        const double len = at[i + 1] - at[i];
        return pts[i] + (pts[i + 1] - pts[i]) * (len > 0 ? std::clamp((s - at[i]) / len, 0.0, 1.0) : 0.0);
    }
    [[nodiscard]] Vec3 direction(double s) const {
        if (pts.size() < 2) return {1, 0, 0};
        std::size_t i = span(s);
        while (i + 2 < pts.size() && !(at[i + 1] > at[i])) ++i;
        const Vec3 d = pts[i + 1] - pts[i];
        return length(d) > 0 ? d / length(d) : Vec3{1, 0, 0};
    }
    // The stretch from s0 to s1 as its own polyline, keeping the corners between.
    [[nodiscard]] std::vector<Vec3> piece(double s0, double s1) const {
        std::vector<Vec3> out{point(s0)};
        for (std::size_t i = 0; i < pts.size(); ++i)
            if (at[i] > s0 + 1e-12 && at[i] < s1 - 1e-12) out.push_back(pts[i]);
        out.push_back(point(s1));
        return out;
    }
};

// Stations along a curve: `count` of them evenly (a closed curve is divided round, an open one keeps
// half a spacing at each end), or as many as fit at `pitch`, centred.
std::vector<double> stations(const Walk& w, bool closed, double pitch, int count) {
    std::vector<double> out;
    if (!(w.total > 0)) return out;
    std::size_t n = count > 0 ? static_cast<std::size_t>(count)
                              : closed ? std::max<std::size_t>(1, static_cast<std::size_t>(std::llround(w.total / pitch)))
                                       : std::max<std::size_t>(1, static_cast<std::size_t>(std::floor(w.total / pitch + 1e-9)));
    const double step = count > 0 || closed ? w.total / static_cast<double>(n) : pitch;
    const double first = closed ? 0.0 : (w.total - step * static_cast<double>(n)) * 0.5 + step * 0.5;
    for (std::size_t k = 0; k < n; ++k) out.push_back(std::min(first + step * static_cast<double>(k), w.total));
    return out;
}

// Single-stroke capitals and figures on a 4 x 6 grid: polylines separated by '|'.
const char* glyph_strokes(char c) {
    switch (c) {
        case '0': case 'O': return "1,0 3,0 4,1 4,5 3,6 1,6 0,5 0,1 1,0";
        case '1': return "0,5 1,6 1,0|0,0 2,0";
        case '2': return "0,5 1,6 3,6 4,5 4,4 0,0 4,0";
        case '3': return "0,5 1,6 3,6 4,5 4,4 3,3 2,3|3,3 4,2 4,1 3,0 1,0 0,1";
        case '4': return "3,0 3,6 0,2 4,2";
        case '5': return "4,6 0,6 0,3.5 3,3.5 4,2.5 4,1 3,0 1,0 0,1";
        case '6': return "4,5 3,6 1,6 0,5 0,1 1,0 3,0 4,1 4,2.5 3,3.5 0,3.5";
        case '7': return "0,6 4,6 1.5,0";
        case '8': return "1,3 0,4 0,5 1,6 3,6 4,5 4,4 3,3 1,3 0,2 0,1 1,0 3,0 4,1 4,2 3,3";
        case '9': return "0,1 1,0 3,0 4,1 4,5 3,6 1,6 0,5 0,3.5 1,2.5 4,2.5";
        case 'A': return "0,0 0,4 2,6 4,4 4,0|0,2.5 4,2.5";
        case 'B': return "0,0 0,6 3,6 4,5 4,4 3,3 0,3|3,3 4,2 4,1 3,0 0,0";
        case 'C': return "4,5 3,6 1,6 0,5 0,1 1,0 3,0 4,1";
        case 'D': return "0,0 0,6 3,6 4,5 4,1 3,0 0,0";
        case 'E': return "4,6 0,6 0,0 4,0|0,3 3,3";
        case 'F': return "4,6 0,6 0,0|0,3 3,3";
        case 'G': return "4,5 3,6 1,6 0,5 0,1 1,0 3,0 4,1 4,3 2,3";
        case 'H': return "0,0 0,6|4,0 4,6|0,3 4,3";
        case 'I': return "1,0 1,6|0,0 2,0|0,6 2,6";
        case 'J': return "4,6 4,1 3,0 1,0 0,1";
        case 'K': return "0,0 0,6|4,6 0,3 4,0";
        case 'L': return "0,6 0,0 4,0";
        case 'M': return "0,0 0,6 2,3 4,6 4,0";
        case 'N': return "0,0 0,6 4,0 4,6";
        case 'P': return "0,0 0,6 3,6 4,5 4,4 3,3 0,3";
        case 'Q': return "1,0 3,0 4,1 4,5 3,6 1,6 0,5 0,1 1,0|2.5,1.5 4,0";
        case 'R': return "0,0 0,6 3,6 4,5 4,4 3,3 0,3|2,3 4,0";
        case 'S': return "4,5 3,6 1,6 0,5 0,4 1,3 3,3 4,2 4,1 3,0 1,0 0,1";
        case 'T': return "0,6 4,6|2,6 2,0";
        case 'U': return "0,6 0,1 1,0 3,0 4,1 4,6";
        case 'V': return "0,6 2,0 4,6";
        case 'W': return "0,6 1,0 2,3 3,0 4,6";
        case 'X': return "0,0 4,6|0,6 4,0";
        case 'Y': return "0,6 2,3 4,6|2,3 2,0";
        case 'Z': return "0,6 4,6 0,0 4,0";
        case '.': return "0,0 0.5,0 0.5,0.5 0,0.5 0,0";
        case ':': return "0,1 0.5,1 0.5,1.5 0,1.5 0,1|0,4 0.5,4 0.5,4.5 0,4.5 0,4";
        case '-': return "0,3 3,3";
        case '/': return "0,0 4,6";
        case ' ': return "";
        default: return nullptr;
    }
}
std::vector<std::vector<std::array<double, 2>>> glyph(char c, double& width) {
    const char* text = glyph_strokes(c);
    if (!text) fail(std::string("detailText cannot draw the character '") + c + "' (figures, capitals, . : - / and spaces only)");
    std::vector<std::vector<std::array<double, 2>>> strokes(1);
    width = c == ' ' ? 2.4 : 0;
    const char* at = text;
    while (*at) {
        if (*at == '|') { strokes.emplace_back(); ++at; continue; }
        if (*at == ' ') { ++at; continue; }
        char* end = nullptr;
        const double x = std::strtod(at, &end);
        const double y = std::strtod(end + 1, &end);  // skips the comma
        at = end;
        strokes.back().push_back({x, y});
        width = std::max(width, x);
    }
    if (strokes.size() == 1 && strokes[0].empty()) strokes.clear();
    return strokes;
}

}  // namespace

Detail transformed_detail(const Detail& d, const Mat4& m) {
    const double s = std::cbrt(std::abs(m.det3()));
    Detail out = d;
    out.origin = m.point(d.origin);
    out.end = m.point(d.end);
    const Vec3 n = m.direction(d.normal), t = m.direction(d.tangent);
    out.normal = n / length(n);
    out.tangent = perpendicular(out.normal, t);
    out.size_a = d.size_a * s;
    if (d.shape != Detail::Shape::Polygon) out.size_b = d.size_b * s;  // a polygon keeps its number of sides
    out.size_c = d.size_c * s;
    out.height = d.height * s;
    out.fillet = d.fillet * s;
    out.extent = d.extent * s;
    return out;
}

namespace {

// Guides: points and curves in the XY plane, centred on the origin, to be placed with a transform.
bool evaluate_guide(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs, Geometry& out) {
    if (type != "pointField" && type != "outline" && type != "courses") return false;
    if (!inputs.empty()) fail(std::string(type) + " takes no inputs");
    if (type == "pointField") {
        const double spacing = o.scalar("spacing", 0.1f, 0.0001f, 1000);
        const std::string layout_name = word_option(o, "layout", "grid");
        if (layout_name != "grid" && layout_name != "hex") fail("layout must be grid or hex");
        const bool hex = layout_name == "hex", disc = o.get("radius") != nullptr;
        double half_x = 0, half_y = 0, radius = 0, inner = 0;
        if (disc) {
            radius = o.scalar("radius", 1, 0.0001f, 10000);
            inner = o.scalar("inner", 0, 0, 10000);
            if (!(inner < radius)) fail("inner must be smaller than radius");
            half_x = half_y = radius;
        } else {
            const std::vector<json::Value>& size = o.list("size", 2, 2);
            half_x = o.number(size[0]) * 0.5;
            half_y = o.number(size[1]) * 0.5;
            if (!(half_x >= 0 && half_y >= 0)) fail("size must not be negative");
        }
        const double row_pitch = hex ? spacing * std::sqrt(3.0) * 0.5 : spacing;
        const auto rows = static_cast<long long>(std::floor(half_y / row_pitch + 1e-6));
        for (long long j = -rows; j <= rows; ++j) {
            const double shift = hex && (j & 1) ? 0.5 * spacing : 0.0;   // odd rows sit in the gaps of even ones
            const auto cols = static_cast<long long>(std::floor((half_x + shift) / spacing + 1e-6));
            for (long long i = -cols; i <= cols; ++i) {
                const double x = static_cast<double>(i) * spacing + shift, y = static_cast<double>(j) * row_pitch;
                if (x > half_x + 1e-6 * spacing || x < -half_x - 1e-6 * spacing) continue;
                if (disc) {
                    const double radial = std::hypot(x, y);
                    if (radial > radius * (1 + 1e-9) || radial < inner * (1 - 1e-9)) continue;
                }
                out.points.push_back({x, y, 0});
                if (out.points.size() > 200000) fail("Geometry instance, point, or triangle budget exceeded");
            }
        }
        if (out.points.empty()) fail("pointField holds no points: the region is smaller than the spacing allows");
        return true;
    }
    if (type == "outline") {  // one curve: circle, arc, rect (with rounded corners) or regular polygon
        const std::string shape = word_option(o, "shape", "circle");
        Curve c;
        if (shape == "circle" || shape == "arc" || shape == "polygon") {
            const double radius = o.scalar("radius", 0.5f, 0.0001f, 10000);
            const bool arc = shape == "arc";
            const double start = o.scalar("start", 0, -1000, 1000), sweep = arc ? o.scalar("sweep", static_cast<float>(kPi), -1000, 1000) : 2 * kPi;
            const int segments = shape == "polygon" ? o.integer("sides", 6, 3, 512) : o.integer("segments", 48, 1, 4096);
            for (int i = 0; i < segments + (arc ? 1 : 0); ++i) {
                const double a = start + sweep * i / segments;
                c.points.push_back({radius * std::cos(a), radius * std::sin(a), 0});
            }
            c.closed = !arc;
        } else if (shape == "rect") {
            const std::vector<json::Value>& size = o.list("size", 2, 2);
            const double hx = o.number(size[0]) * 0.5, hy = o.number(size[1]) * 0.5;
            if (!(hx > 0 && hy > 0)) fail("An outline needs a positive size [x, y]");
            const double corner = std::min<double>(o.scalar("corner", 0, 0, 10000), std::min(hx, hy));
            const int steps = corner > 0 ? o.integer("cornerSegments", 6, 1, 64) : 0;
            const double cx[4] = {hx - corner, -(hx - corner), -(hx - corner), hx - corner}, cy[4] = {hy - corner, hy - corner, -(hy - corner), -(hy - corner)};
            for (int q = 0; q < 4; ++q)
                for (int i = 0; i <= steps; ++i) {
                    const double a = (q + (steps ? static_cast<double>(i) / steps : 0.0)) * (kPi / 2);
                    c.points.push_back({cx[q] + corner * std::cos(a), cy[q] + corner * std::sin(a), 0});
                }
            c.closed = true;
        } else {
            fail("An outline's shape must be circle, arc, rect or polygon");
        }
        out.curves.push_back(std::move(c));
        return true;
    }
    // courses: the joints of coursed masonry (or planking, or sheeting) filling a rectangle: bed
    // joints between courses and perpends within each course, staggered from course to course.
    const std::vector<json::Value>& size = o.list("size", 2, 2);
    const double width = o.number(size[0]), height = o.number(size[1]);
    if (!(width > 0 && height > 0)) fail("courses need a positive size [x, y]");
    const double course = o.scalar("course", 0.3f, 0.001f, 10000), block = o.scalar("block", 0.6f, 0.001f, 10000);
    const double bond = o.scalar("bond", 0.5f, 0, 1), minimum = o.scalar("minimum", 0.3f, 0, 0.5f);
    const auto rows = std::max<long long>(1, std::llround(height / course)), blocks = std::max<long long>(1, std::llround(width / block));
    if (rows * (blocks + 1) > 60000) fail("Geometry instance, point, or triangle budget exceeded");
    const double h = height / static_cast<double>(rows), len = width / static_cast<double>(blocks);
    for (long long j = 1; j < rows; ++j) out.curves.push_back(Curve{{{-width / 2, -height / 2 + h * static_cast<double>(j), 0}, {width / 2, -height / 2 + h * static_cast<double>(j), 0}}, false});
    for (long long j = 0; j < rows; ++j) {
        const double offset = (j & 1) ? bond * len : 0.0, y0 = -height / 2 + h * static_cast<double>(j);
        for (long long i = 0; i <= blocks; ++i) {
            const double x = -width / 2 + offset + len * static_cast<double>(i);
            if (x < -width / 2 + minimum * len || x > width / 2 - minimum * len) continue;  // no slivers at the ends of a course
            out.curves.push_back(Curve{{{x, y0, 0}, {x, y0 + h, 0}}, false});
        }
    }
    return true;
}

// Items filed by cell, so that "what could touch a head here" is one lookup instead of a scan.
// An item is filed under every cell its box touches; the box must already be grown by the reach
// of whatever will ask, and the question is then put only to the cell the asking point lies in.
struct Buckets {
    double cell = 1;
    std::unordered_map<std::uint64_t, std::vector<std::uint32_t>> cells;
    std::vector<std::uint32_t> everywhere;  // items too large to file: always asked
    [[nodiscard]] std::int64_t at(double v) const { return static_cast<std::int64_t>(std::floor(v / cell)); }
    void add(std::uint32_t item, Vec3 lo, Vec3 hi) {
        const std::int64_t x0 = at(lo.x), x1 = at(hi.x), y0 = at(lo.y), y1 = at(hi.y), z0 = at(lo.z), z1 = at(hi.z);
        if ((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > 64) {
            everywhere.push_back(item);
            return;
        }
        for (std::int64_t x = x0; x <= x1; ++x)
            for (std::int64_t y = y0; y <= y1; ++y)
                for (std::int64_t z = z0; z <= z1; ++z) cells[cell_key(x, y, z)].push_back(item);
    }
    template <class Test>
    [[nodiscard]] bool any(Vec3 p, Test&& test) const {
        for (const std::uint32_t item : everywhere)
            if (test(item)) return true;
        const auto found = cells.find(cell_key(at(p.x), at(p.y), at(p.z)));
        if (found != cells.end())
            for (const std::uint32_t item : found->second)
                if (test(item)) return true;
        return false;
    }
};

// What a new head on one prototype must keep clear of: the joints and heads already on it, and
// the heads this node has placed so far. Built for the first head's size; a part that shows the
// same prototype at another scale asks every item instead (the answer is the same either way).
struct Clearance {
    double scale = 0, extent = 0, margin = 0;  // of the heads the buckets were built for (prototype units)
    Buckets joints, heads, fresh;
    void build(const Proto& proto, double scale_, double extent_, double margin_) {
        scale = scale_;
        extent = extent_;
        margin = margin_;
        joints.cell = heads.cell = fresh.cell = std::max(4.0 * (extent + margin), 1e-9);
        for (std::uint32_t k = 0; k < proto.details.size(); ++k) {
            const Detail& d = proto.details[k];
            if (d.fastener) {
                const Vec3 r{d.extent + extent, d.extent + extent, d.extent + extent};
                heads.add(k, d.origin - r, d.origin + r);
            }
            if (d.shape == Detail::Shape::Segment && d.profile != Detail::Profile::Flat && d.height != 0) {
                const double grow = 2.0 * d.size_a + 2.0 * (extent + margin);
                const Vec3 r{grow, grow, grow};
                joints.add(k, vmin(d.origin, d.end) - r, vmax(d.origin, d.end) + r);
            }
        }
    }
    void placed(std::uint32_t first_part, Vec3 origin, double own_extent) {
        const Vec3 r{own_extent + extent, own_extent + extent, own_extent + extent};
        fresh.add(first_part, origin - r, origin + r);
    }
};

// Where generated details go: lands world points on the surface and collects details per prototype.
struct Sink {
    const Geometry& surface;
    const std::vector<Target>& targets;
    double reach;
    std::map<const Proto*, std::vector<Detail>> added;
    std::map<const Proto*, double> scale_of;  // prototype units -> metres
    std::size_t count = 0;
    std::map<const Proto*, DetailIndex> sampler;  // the details earlier nodes left on each prototype
    std::map<const Proto*, Clearance> clearance;

    Hit land(Vec3 world) {
        Hit hit;
        if (!project(targets, world, reach, hit))
            fail("Detail " + std::to_string(count) + " is further than `reach` (" + std::to_string(reach) + " m) from the surface");
        ++count;
        return hit;
    }
    [[nodiscard]] const Target& target(const Hit& hit) const { return targets[hit.target]; }
    [[nodiscard]] const Part& part(const Hit& hit) const { return surface.parts[targets[hit.target].part]; }
    [[nodiscard]] const Proto* proto(const Hit& hit) const { return part(hit).proto.get(); }
    [[nodiscard]] Vec3 world(const Hit& hit) const { return part(hit).transform.point(hit.point); }
    [[nodiscard]] Vec3 world_normal(const Hit& hit) const {
        const Vec3 n = part(hit).transform.direction(hit.normal);
        return n / length(n);
    }
    void add(const Hit& hit, const Detail& d) {
        added[proto(hit)].push_back(d);
        scale_of[proto(hit)] = target(hit).scale;
    }
    // A raised detail stands on what earlier nodes left under it (a bead on a door leaf, a head on
    // a band): the raised height already at that point, in prototype units.
    [[nodiscard]] double base(const Hit& hit, Vec3 point) {
        const Proto* p = proto(hit);
        if (p->details.empty()) return 0.0;
        auto it = sampler.find(p);
        if (it == sampler.end()) it = sampler.emplace(p, DetailIndex(p->details)).first;
        return std::max(0.0, it->second.sample(point, hit.normal).height);
    }
    // Whether a head (`probe`: origin, normal, extent) may go here: clear of the joints and heads
    // already on the prototype and of the heads placed by this node (`fresh`).
    [[nodiscard]] bool head_clear(const Hit& hit, const Detail& probe, double margin, const std::vector<Detail>& fresh) {
        const Proto* p = proto(hit);
        const double scale = target(hit).scale;
        auto it = clearance.find(p);
        if (it == clearance.end()) {
            it = clearance.emplace(p, Clearance{}).first;
            it->second.build(*p, scale, probe.extent, margin);
        }
        const Clearance& c = it->second;
        if (scale != c.scale || probe.extent != c.extent || margin != c.margin) {  // another scale of the same prototype: ask everything
            for (const Detail& other : p->details)
                if (head_on_seam(probe, other, margin) || heads_overlap(probe, other)) return false;
            for (const Detail& other : fresh)
                if (heads_overlap(probe, other)) return false;
            return true;
        }
        return !c.joints.any(probe.origin, [&](std::uint32_t k) { return head_on_seam(probe, p->details[k], margin); }) &&
               !c.heads.any(probe.origin, [&](std::uint32_t k) { return heads_overlap(probe, p->details[k]); }) &&
               !c.fresh.any(probe.origin, [&](std::uint32_t k) { return heads_overlap(probe, fresh[k]); });
    }
    // A stroke through world points: one segment between consecutive landings on the same part.
    // `like` carries shape, profile and material; sizes are given in metres.
    void stroke(const std::vector<Vec3>& points, Detail like, double half_width, double height, double fillet) {
        Hit previous;
        bool have = false;
        for (const Vec3& p : points) {
            const Hit hit = land(p);
            if (have && previous.target == hit.target && length(hit.point - previous.point) > 1e-9) {
                const double scale = target(hit).scale;
                Detail d = like;
                d.shape = Detail::Shape::Segment;
                d.origin = previous.point;
                d.end = hit.point;
                const Vec3 n = previous.normal + hit.normal;
                d.normal = length(n) > 1e-9 ? n / length(n) : hit.normal;
                d.tangent = perpendicular(d.normal, hit.point - previous.point);
                d.size_a = half_width / scale;
                d.height = height / scale + (height > 0 ? base(hit, (previous.point + hit.point) * 0.5) : 0.0);
                d.fillet = fillet / scale;
                add(hit, d);
            }
            previous = hit;
            have = true;
        }
    }
};

// What a fastener head is made of. Sizes in metres; `turn` is the angle of its drive or its flats.
struct Head {
    std::string kind = "rivet";
    double radius = 0.012, height = 0.005, washer = 0;
    std::uint16_t slot = 0xFFFF;
};
void head_details(const Head& h, std::vector<Detail>& out) {
    auto part = [&](Detail::Shape shape, Detail::Profile profile, double a, double b, double height, double fillet) {
        Detail d;
        d.shape = shape;
        d.profile = profile;
        d.material = h.slot;
        d.size_a = a;
        d.size_b = b;
        d.height = height;
        d.fillet = fillet;
        out.push_back(d);
    };
    const double r = h.radius, t = h.height;
    using S = Detail::Shape;
    using P = Detail::Profile;
    if (h.washer > 0) part(S::Disc, P::Flat, h.washer, 0, 0.25 * t, 0.15 * r);
    if (h.kind == "rivet") part(S::Disc, P::Dome, r, 0, t, 0);
    else if (h.kind == "hex") part(S::Polygon, P::Flat, r, 6, t, 0.18 * r);
    else if (h.kind == "square") part(S::Polygon, P::Flat, r, 4, t, 0.18 * r);
    else if (h.kind == "cap") { part(S::Polygon, P::Flat, r, 6, 0.45 * t, 0.15 * r); part(S::Disc, P::Dome, 0.8 * r, 0, t, 0); }
    else if (h.kind == "stud") part(S::Polygon, P::Cone, r, 4, t, 0);
    else if (h.kind == "slotted") { part(S::Disc, P::Dome, r, 0, t, 0); part(S::Box, P::Flat, 0.85 * r, 0.13 * r, -0.5 * t, 0.06 * r); }
    else if (h.kind == "cross") { part(S::Disc, P::Dome, r, 0, t, 0); part(S::Cross, P::Flat, 0.6 * r, 0.12 * r, -0.5 * t, 0.06 * r); }
    else fail("head must be rivet, hex, square, cap, stud, slotted or cross");
}

}  // namespace

bool evaluate_detail(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs, Geometry& out) {
    if (evaluate_guide(type, o, inputs, out)) return true;
    const bool stamp = type == "detailStamp", fasten = type == "fastener" || type == "rivetLine";
    const bool stroke = type == "detailSeam" || type == "detailStroke", ticks = type == "detailTicks", text = type == "detailText";
    if (!stamp && !fasten && !stroke && !ticks && !text) return false;
    if (inputs.size() != 2) fail("Expected the surface and the points or curve to detail it with");
    const Geometry& surface = *inputs[0];
    const Geometry& guide = *inputs[1];
    const std::vector<Target> targets = targets_of(surface);
    Sink sink{surface, targets, o.scalar("reach", 0.25f, 0.0001f, 100), {}, {}, 0, {}, {}};
    const std::uint16_t slot = slot_option(o);
    double seam_clearance = -1.0;  // metres; < 0 = each head's own radius
    bool joint = false;            // this node lays strokes that heads must keep clear of

    if (stroke) {
        if (guide.curves.empty()) fail("Second input must supply a curve");
        const bool seam = type == "detailSeam";
        const std::string profile = seam ? "groove" : word_option(o, "profile", "groove");
        Detail like;
        like.material = slot;
        double height = 0;
        if (profile == "groove") { like.profile = Detail::Profile::Groove; height = -static_cast<double>(o.scalar(seam || !o.get("height") ? "depth" : "height", 0.003f, 0.00001f, 1)); }
        else if (profile == "bead") { like.profile = Detail::Profile::Dome; height = o.scalar("height", 0.004f, 0.00001f, 1); }
        else if (profile == "band") { like.profile = Detail::Profile::Flat; height = o.scalar("height", 0.003f, 0.00001f, 1); }
        else if (profile == "lap") { like.profile = Detail::Profile::Ramp; height = o.scalar("height", 0.004f, 0.00001f, 1); }
        else if (profile == "mark") {
            like.profile = Detail::Profile::Flat;
            if (slot == 0xFFFF) fail("A mark has no height: give it a slot to show");
        } else fail("A stroke's profile must be groove, bead, band, lap or mark");
        joint = like.profile != Detail::Profile::Flat;
        const double half = o.scalar("width", 0.006f, 0.0001f, 10) * 0.5;
        const double fillet = o.scalar("fillet", static_cast<float>(std::min(0.002, half * 0.5)), 0.00001f, 1);
        const double step = o.scalar("step", 0.05f, 0.001f, 10);
        seam_clearance = o.get("clearance") ? o.scalar("clearance", 0, 0, 10) : -1.0;
        const json::Value* dash = o.get("dash");  // [on, off] metres: a stitched or broken line
        double on = 0, off = 0;
        if (dash) {
            const std::vector<json::Value>& d = o.list("dash", 2, 2);
            on = o.number(d[0]);
            off = o.number(d[1]);
            if (!(on > 0 && off > 0)) fail("dash needs positive [on, off] lengths");
        }
        for (const Curve& c : guide.curves) {
            if (!dash) {
                sink.stroke(subdivided(c, step), like, half, height, fillet);
                continue;
            }
            const Walk w(c);
            const auto dashes = static_cast<long long>(std::ceil(w.total / (on + off) - 1e-6));  // lengths arrive as 32-bit floats
            for (long long k = 0; k < dashes; ++k) {
                const double s = static_cast<double>(k) * (on + off);
                Curve piece;
                piece.points = w.piece(s, std::min(s + on, w.total));
                sink.stroke(subdivided(piece, step), like, half, height, fillet);
                if (sink.count > 400000) fail("Geometry instance, point, or triangle budget exceeded");
            }
        }
    } else if (ticks) {
        // Graduations: short strokes square to the curve, every `major`-th one longer.
        if (guide.curves.empty()) fail("Second input must supply a curve to graduate");
        const double pitch = o.scalar("pitch", 0.01f, 0.0001f, 100);
        const int count = o.integer("count", 0, 0, 100000), major = o.integer("major", 5, 0, 100000);
        const double minor_length = o.scalar("length", 0.01f, 0.0001f, 10), major_length = o.scalar("majorLength", static_cast<float>(minor_length * 1.8), 0.0001f, 10);
        const double half = o.scalar("width", 0.0015f, 0.00005f, 1) * 0.5;
        const std::string side = word_option(o, "side", "centre");
        if (side != "centre" && side != "left" && side != "right") fail("side must be centre, left or right");
        Detail like;
        like.material = slot;
        const bool mark = o.get("depth") == nullptr && slot != 0xFFFF;  // paint or inlay unless a depth is given
        like.profile = mark ? Detail::Profile::Flat : Detail::Profile::Groove;
        const double height = mark ? 0.0 : -static_cast<double>(o.scalar("depth", 0.001f, 0.00001f, 1));
        for (const Curve& c : guide.curves) {
            const Walk w(c);
            const std::vector<double> at = stations(w, c.closed, pitch, count);
            for (std::size_t k = 0; k < at.size(); ++k) {
                const Hit hit = sink.land(w.point(at[k]));
                const Vec3 n = sink.world_normal(hit), centre = sink.world(hit);
                const Vec3 across = perpendicular(n, cross(n, w.direction(at[k])));
                const double len = major > 0 && k % static_cast<std::size_t>(major) == 0 ? major_length : minor_length;
                const double lo = side == "centre" ? -0.5 * len : side == "left" ? 0.0 : -len, hi = lo + len;
                const Target& t = sink.target(hit);
                Detail d = like;
                d.shape = Detail::Shape::Segment;
                d.origin = t.inverse.point(centre + across * lo);
                d.end = t.inverse.point(centre + across * hi);
                d.normal = hit.normal;
                d.tangent = perpendicular(hit.normal, d.end - d.origin);
                d.size_a = half / t.scale;
                d.height = height / t.scale;
                d.fillet = 0.5 * half / t.scale;
                sink.add(hit, d);
            }
        }
    } else if (text) {
        // Single-stroke lettering from each anchor point, reading along `direction` and wrapped onto the surface.
        const json::Value* words = o.get("text");
        if (!words || !words->is_string() || words->string.empty()) fail("detailText needs a text");
        std::vector<Vec3> anchors = guide.points;
        if (anchors.empty()) fail("Second input supplies no anchor point for the text");
        const double size = o.scalar("size", 0.1f, 0.001f, 100), unit = size / 6.0;
        const double half = o.scalar("width", static_cast<float>(size * 0.12), 0.0001f, 10) * 0.5;
        const double gap = o.scalar("spacing", 1.4f, 0, 20);
        const std::string profile = word_option(o, "profile", "bead"), align = word_option(o, "align", "centre");
        if (align != "centre" && align != "left" && align != "right") fail("align must be centre, left or right");
        Detail like;
        like.material = slot;
        double height = 0;
        if (profile == "bead") { like.profile = Detail::Profile::Dome; height = o.scalar("height", 0.003f, 0.00001f, 1); }
        else if (profile == "band") { like.profile = Detail::Profile::Flat; height = o.scalar("height", 0.003f, 0.00001f, 1); }
        else if (profile == "groove") { like.profile = Detail::Profile::Groove; height = -static_cast<double>(o.scalar("depth", 0.002f, 0.00001f, 1)); }
        else if (profile == "mark") {
            like.profile = Detail::Profile::Flat;
            if (slot == 0xFFFF) fail("Painted text has no height: give it a slot to show");
        } else fail("Text profile must be bead, band, groove or mark");
        const Vec3 direction = o.vector("direction", {1, 0, 0});
        struct Placed { std::vector<std::vector<std::array<double, 2>>> strokes; double x; };
        std::vector<Placed> line;
        double cursor = 0;
        for (const char raw : words->string) {
            const char c = raw >= 'a' && raw <= 'z' ? static_cast<char>(raw - 32) : raw;
            double width = 0;
            line.push_back({glyph(c, width), cursor});
            cursor += width + gap;
        }
        const double total = std::max(0.0, cursor - gap), shift = align == "centre" ? -0.5 * total : align == "right" ? -total : 0.0;
        const double lift = std::min(sink.reach * 0.5, 0.02);
        for (const Vec3& anchor : anchors) {
            const Hit hit = sink.land(anchor);
            const Vec3 n = sink.world_normal(hit), origin = sink.world(hit);
            const Vec3 right = perpendicular(n, direction), up = cross(n, right);
            for (const Placed& g : line)
                for (const auto& polyline : g.strokes) {
                    Curve c;
                    for (const auto& q : polyline) c.points.push_back(origin + right * ((g.x + shift + q[0]) * unit) + up * ((q[1] - 3.0) * unit) + n * lift);
                    sink.stroke(subdivided(c, std::max(size * 0.25, 0.002)), like, half, height, std::min(0.002, half * 0.5));
                }
        }
    } else if (fasten) {
        Head head;
        head.kind = type == "rivetLine" ? "rivet" : word_option(o, "head", "rivet");
        head.radius = o.scalar("radius", 0.012f, 0.0001f, 100);
        head.height = o.scalar("height", static_cast<float>(head.radius * 0.45), 0.00001f, 1);
        head.washer = o.scalar("washer", 0, 0, 100);
        head.slot = slot;
        if (head.washer > 0 && !(head.washer > head.radius)) fail("A washer must be wider than the head it sits under");
        std::vector<Detail> parts;
        head_details(head, parts);
        const bool polygon = head.kind == "hex" || head.kind == "square" || head.kind == "cap" || head.kind == "stud";
        const double extent = std::max(head.washer, polygon ? head.radius / std::cos(kPi / (head.kind == "hex" || head.kind == "cap" ? 6.0 : 4.0)) : head.radius);
        const double clearance = o.scalar("clearance", static_cast<float>(head.radius), 0, 10);
        const std::string turn = word_option(o, "turn", head.kind == "rivet" ? "aligned" : "random");
        if (turn != "aligned" && turn != "random") fail("turn must be aligned or random");
        const int seed = o.integer("seed", 0, -1'000'000, 1'000'000);
        const Vec3 along = o.vector("tangent", {1, 0, 0});
        // Where: along the curves at a pitch or a count; without either, at the guide's points.
        const bool rows = o.get("pitch") != nullptr || o.get("count") != nullptr || type == "rivetLine";
        std::vector<Vec3> points;
        if (rows) {
            if (guide.curves.empty()) fail(type == "rivetLine" ? "Second input must supply a curve for the rivet line" : "pitch and count need a curve as the second input");
            const double pitch = o.scalar("pitch", 0.08f, 0.001f, 100);
            const int count = o.integer("count", 0, 0, 100000);
            for (const Curve& c : guide.curves) {
                if (count == 0) {  // the row keeps its edge distance: half a pitch from each end
                    const std::vector<Vec3> placed = spaced(c, pitch);
                    points.insert(points.end(), placed.begin(), placed.end());
                } else {
                    const Walk w(c);
                    for (const double s : stations(w, c.closed, pitch, count)) points.push_back(w.point(s));
                }
            }
        } else {
            points = guide.points;
            if (points.empty())
                for (const Curve& c : guide.curves) points.insert(points.end(), c.points.begin(), c.points.end());
        }
        if (points.empty()) fail("Second input supplies nowhere to put a fastener");
        if (points.size() > 200000) fail("Geometry instance, point, or triangle budget exceeded");
        for (std::size_t i = 0; i < points.size(); ++i) {
            const Hit hit = sink.land(points[i]);
            const Target& t = sink.target(hit);
            const Proto* proto = sink.proto(hit);
            Detail probe;  // the head as the clearance rules see it
            probe.origin = hit.point;
            probe.normal = hit.normal;
            probe.extent = extent / t.scale;
            std::vector<Detail>& fresh = sink.added[proto];
            if (!sink.head_clear(hit, probe, clearance / t.scale, fresh)) continue;
            sink.clearance.at(proto).placed(static_cast<std::uint32_t>(fresh.size()), probe.origin, probe.extent);
            // A head stands on what is under it (a band, a boss), and is clocked like a real one.
            const double base = sink.base(hit, hit.point);
            std::uint32_t hash = static_cast<std::uint32_t>(i) * 2654435761u + static_cast<std::uint32_t>(seed) * 40503u + 12345u;
            hash ^= hash >> 15;
            hash *= 2246822519u;
            hash ^= hash >> 13;
            const double angle = turn == "random" ? (hash & 0xFFFFu) / 65536.0 * kPi : 0.0;
            const Vec3 t0 = perpendicular(hit.normal, t.inverse.direction(along)), t1 = cross(hit.normal, t0);
            for (Detail d : parts) {
                d.origin = hit.point;
                d.normal = hit.normal;
                d.tangent = t0 * std::cos(angle) + t1 * std::sin(angle);
                d.size_a /= t.scale;
                if (d.shape != Detail::Shape::Polygon) d.size_b /= t.scale;
                d.height = d.height / t.scale + (d.height > 0 ? base : 0.0);
                d.fillet /= t.scale;
                d.fastener = true;
                d.extent = extent / t.scale;
                fresh.push_back(d);
            }
            sink.scale_of[proto] = t.scale;
        }
    } else {  // detailStamp
        std::vector<Vec3> points = guide.points;
        if (points.empty())
            for (const Curve& c : guide.curves) points.insert(points.end(), c.points.begin(), c.points.end());
        if (points.empty()) fail("Second input supplies no points to stamp");
        if (points.size() > 200000) fail("Geometry instance, point, or triangle budget exceeded");
        Detail like;
        like.material = slot;
        const std::string shape = word_option(o, "shape", "disc"), profile = word_option(o, "profile", "dome");
        if (shape == "disc") like.shape = Detail::Shape::Disc;
        else if (shape == "ring") like.shape = Detail::Shape::Ring;
        else if (shape == "box") like.shape = Detail::Shape::Box;
        else if (shape == "polygon") like.shape = Detail::Shape::Polygon;
        else if (shape == "cross") like.shape = Detail::Shape::Cross;
        else fail("shape must be disc, ring or box (or polygon, cross)");
        if (profile == "dome") like.profile = Detail::Profile::Dome;
        else if (profile == "flat") like.profile = Detail::Profile::Flat;
        else if (profile == "groove") like.profile = Detail::Profile::Groove;
        else if (profile == "cone") like.profile = Detail::Profile::Cone;
        else if (profile == "ramp") like.profile = Detail::Profile::Ramp;
        else fail("profile must be dome or flat (or groove, cone, ramp)");
        double a = o.scalar("radius", 0.012f, 0.0001f, 100), b = 0, c = 0;
        bool scale_b = true;
        if (like.shape == Detail::Shape::Ring) {
            b = o.scalar("inner", static_cast<float>(a * 0.7), 0, 100);
            if (!(b < a)) fail("A ring's inner radius must be smaller than its radius");
        } else if (like.shape == Detail::Shape::Box) {
            const std::vector<json::Value>& size = o.list("size", 2, 2);  // [along the tangent, across]
            a = o.number(size[0]) * 0.5;
            b = o.number(size[1]) * 0.5;
            if (!(a > 0 && b > 0)) fail("A box detail needs a positive size [x, y]");
            c = o.scalar("corner", 0, 0, 100);
        } else if (like.shape == Detail::Shape::Polygon) {
            b = o.integer("sides", 6, 3, 64);  // radius is half the distance across flats
            scale_b = false;
        } else if (like.shape == Detail::Shape::Cross) {
            b = o.scalar("width", static_cast<float>(a * 0.3), 0.00001f, 100) * 0.5;
            if (!(b < a)) fail("A cross's arm width must be smaller than its arm length");
        }
        const double height = o.scalar("height", 0.004f, -1, 1), fillet = o.scalar("fillet", 0.002f, 0.00001f, 1);
        if (height == 0 && slot == 0xFFFF) fail("A detail needs a height (positive is raised, negative is sunk) or a slot (a flat mark)");
        const Vec3 along = o.vector("tangent", {1, 0, 0});
        const bool turned = o.get("angle") != nullptr;  // radians about the normal; may use @index, @x, @y, @z
        for (std::size_t i = 0; i < points.size(); ++i) {
            const Hit hit = sink.land(points[i]);
            const Target& t = sink.target(hit);
            Detail d = like;
            d.origin = hit.point;
            d.normal = hit.normal;
            const Vec3 t0 = perpendicular(hit.normal, t.inverse.direction(along));
            d.tangent = t0;
            if (turned) {
                const Point at{static_cast<int>(i), points[i], 0};
                const double angle = Options{o.fields, o.controls, &at}.scalar("angle", 0, -1000, 1000);
                d.tangent = t0 * std::cos(angle) + cross(hit.normal, t0) * std::sin(angle);
            }
            d.size_a = a / t.scale;
            d.size_b = scale_b ? b / t.scale : b;
            d.size_c = c / t.scale;
            d.height = height / t.scale + (height > 0 ? sink.base(hit, hit.point) : 0.0);
            d.fillet = fillet / t.scale;
            sink.add(hit, d);
        }
    }

    out = surface;
    std::map<const Proto*, ProtoPtr> replaced;
    for (Part& part : out.parts) {
        const auto it = sink.added.find(part.proto.get());
        if (it == sink.added.end() || it->second.empty()) continue;
        ProtoPtr& fresh = replaced[part.proto.get()];
        if (!fresh) {
            Proto copy = *part.proto;
            if (joint) {  // heads already on the surface give way to the new joint
                const double scale = sink.scale_of.at(part.proto.get());
                std::erase_if(copy.details, [&](const Detail& head) {
                    if (!head.fastener) return false;
                    const double clearance = seam_clearance < 0 ? head.extent : seam_clearance / scale;
                    for (const Detail& seam : it->second)
                        if (head_on_seam(head, seam, clearance)) return true;
                    return false;
                });
            }
            copy.details.insert(copy.details.end(), it->second.begin(), it->second.end());
            fresh = std::make_shared<const Proto>(std::move(copy));
        }
        part.proto = fresh;
    }
    return true;
}

}  // namespace detail

void set_projection_search(ProjectionSearch mode) noexcept { detail::g_search.store(mode, std::memory_order_relaxed); }
ProjectionSearch projection_search() noexcept { return detail::g_search.load(std::memory_order_relaxed); }
void profile_projection(ProjectionProfile* sink) noexcept { detail::g_profile.store(sink, std::memory_order_relaxed); }

}  // namespace pg::doc
