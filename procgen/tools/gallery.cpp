// Renders a gallery of procgen outputs to gallery/*.png (visual QA).
#include "render.hpp"

#include "pg/ops/bevel.hpp"
#include "pg/ops/boolean.hpp"
#include "pg/ops/deform.hpp"
#include "pg/ops/extrude.hpp"
#include "pg/ops/scatter.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"
#include "pg/quality.hpp"

#include <cmath>
#include <cstdio>
#include <string>

using namespace pg;
using namespace pgtools;

namespace {

const Rgb kSteel{0.62, 0.66, 0.72}, kBrass{0.86, 0.66, 0.32}, kClay{0.80, 0.52, 0.42}, kMint{0.45, 0.72, 0.62},
    kSky{0.48, 0.62, 0.86};

void report(const char* name, const Mesh& m) {
    const QualityReport q = analyze(m);
    std::printf("%-22s faces %6zu  watertight %d  euler %3lld  SI %zu  volume %.5f  edge ratio %.1f\n", name, q.faces,
                q.watertight(), static_cast<long long>(q.euler), q.self_intersections.intersecting_pairs, q.volume,
                q.edge_lengths.ratio());
    std::fflush(stdout);
}

Mesh top_faces(const Mesh& m) {
    Mesh out;
    out.positions = m.positions;
    for (std::size_t f = 0; f < m.face_count(); ++f)
        if (face_normal(m, f).z > 0.99) out.add_face(m.face(f));
    return out;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string dir = argc > 1 ? argv[1] : "gallery";

    // 1. Hard-surface part: plate -> raised boss -> drilled through -> chamfered -> rivets on top.
    Mesh plate = make_box({2.0, 1.4, 0.3}, 6);
    std::vector<std::uint8_t> boss_mask(plate.face_count(), 0);
    for (std::size_t f = 0; f < plate.face_count(); ++f) {
        const Vec3 c = face_centroid(plate, f);
        boss_mask[f] = (c.z > 0.14 && std::abs(c.x) < 0.5 && std::abs(c.y) < 0.4) ? 1 : 0;
    }
    const Mesh bossed = extrude(plate, boss_mask, {.distance = 0.25});
    Mesh drill = make_cylinder(40, 0.22, 3.0);
    const Mesh holed = mesh_boolean(bossed, drill, BooleanOp::Difference);
    Mesh slot = make_box({0.9, 0.12, 1.0}, 1);
    translate(slot, {0.0, -0.58, 0.15});  // clear of the boss (a 7 mm sliver wall would be narrower than the bevel)
    const Mesh slotted = mesh_boolean(holed, slot, BooleanOp::Difference);
    const Mesh part = bevel(slotted, {.width = 0.012});  // every wall > 2 bevel widths
    const Mesh rivet = make_cylinder(10, 0.035, 0.05);
    Mesh rim_area = top_faces(make_box({1.9, 1.3, 0.3}, 6));
    const ScatterPoints pts = scatter_points(rim_area, {.count = 400, .min_distance = 0.16, .seed = 7});
    ScatterPoints keep;
    for (std::size_t i = 0; i < pts.size(); ++i) {  // only on the plate rim, away from the boss
        const Vec3 p = pts.positions[i];
        const bool over_slot = std::abs(p.x) < 0.52 && p.y > -0.7 && p.y < -0.46;
        if ((std::abs(p.x) > 0.62 || std::abs(p.y) > 0.5) && !over_slot) {
            keep.positions.push_back(p + Vec3{0, 0, 0.0});
            keep.normals.push_back(pts.normals[i]);
            keep.faces.push_back(pts.faces[i]);
        }
    }
    const Mesh rivets = instance_on_points(rivet, keep, {.random_spin = true, .seed = 3});
    report("hard_surface part", part);
    report("rivets", rivets);
    save_png(render({{&part, kSteel}, {&rivets, kBrass}}, {.target = {0, 0, 0.1}, .yaw_deg = -55, .pitch_deg = 38, .distance = 4.6},
                    1100, 760),
             dir + "/hard_surface.png");

    // 2. Organic: Catmull-Clark blob, fBm displacement, twist and bend.
    Mesh blob = catmull_clark(make_box({0.9, 0.9, 2.0}, 2), 4);
    displace_noise(blob, {.amplitude = 0.06, .frequency = 2.6, .seed = 4, .octaves = 4});
    twist(blob.positions, 1.1);
    Mesh tentacle = catmull_clark(make_box({0.35, 0.35, 3.0}, 3), 3);
    taper(tentacle.positions, -0.25);
    for (Vec3& p : tentacle.positions) p = {p.z, p.y, -p.x};  // rotate onto X (a swap would mirror it inside out)
    bend(tentacle.positions, 0.55);
    translate(tentacle, {0.0, 1.0, -0.6});
    report("organic blob", blob);
    report("organic tentacle", tentacle);
    save_png(render({{&blob, kClay, false}, {&tentacle, kMint, false}}, {.target = {0, 0.4, 0}, .yaw_deg = -30, .pitch_deg = 18, .distance = 6.5},
                    1100, 760, 0.0),
             dir + "/organic.png");

    // 3. Boolean slivers before/after the uncut-face reassembly (top view of a drilled box).
    const Mesh box8 = make_box({1, 1, 1}, 8);
    const Mesh cyl48 = make_cylinder(48, 0.2, 2.0);
    const Mesh before = mesh_boolean(box8, cyl48, BooleanOp::Difference, {.merge_uncut_faces = false});
    const Mesh after = mesh_boolean(box8, cyl48, BooleanOp::Difference);
    report("boolean before", before);
    report("boolean after", after);
    const Camera top{.target = {0, 0, 0}, .yaw_deg = -90, .pitch_deg = 62, .distance = 2.9};
    save_png(hconcat({render({{&before, kSky}}, top, 640, 600, 0.8), render({{&after, kSky}}, top, 640, 600, 0.8)}),
             dir + "/boolean_slivers.png");

    // 4. Operation sheet: extrude, bevel, subdivide, union, scatter.
    const Mesh base = make_box({1, 1, 1}, 4);
    std::vector<std::uint8_t> m2(base.face_count(), 0);
    for (std::size_t f = 0; f < base.face_count(); ++f) {
        const Vec3 c = face_centroid(base, f);
        m2[f] = (c.z > 0.49 || c.x > 0.49) && (static_cast<int>(std::floor((c.x + c.y + 4) * 4)) % 2 == 0) ? 1 : 0;
    }
    const Mesh ext = extrude(base, m2, {.distance = 0.18, .mode = ExtrudeMode::Individual});
    const Mesh bev = bevel(extrude(base, select_by_normal(base, {0, 0, 1}, 0.99), {.distance = 0.3}), {.width = 0.06});
    const Mesh sub = catmull_clark(ext, 2);
    Mesh ball = catmull_clark(make_box({1, 1, 1}, 1), 3);
    for (Vec3& p : ball.positions) p = p * 2.4;
    Mesh b2 = make_box({0.7, 0.7, 0.7}, 1);
    translate(b2, {0.35, 0.3, 0.3});
    const Mesh uni = mesh_boolean(ball, b2, BooleanOp::Union);
    const Mesh sphere = catmull_clark(make_box({1, 1, 1}, 1), 4);
    const ScatterPoints sp = scatter_points(sphere, {.count = 3000, .min_distance = 0.045, .seed = 11});
    const Mesh spikes = instance_on_points(make_cylinder(6, 0.012, 0.09), sp, {});
    for (auto [n, m] : {std::pair{"extrude individual", &ext}, {"bevel", &bev}, {"subdivided extrude", &sub}, {"union", &uni}})
        report(n, *m);
    const Camera tile{.target = {0, 0, 0.05}, .yaw_deg = -50, .pitch_deg = 30, .distance = 3.3};
    save_png(hconcat({render({{&ext, kSteel}}, tile, 420, 420), render({{&bev, kSteel}}, tile, 420, 420),
                      render({{&sub, kClay}}, tile, 420, 420, 0.25), render({{&uni, kSky}}, {.target = {0.2, 0.15, 0.1}, .yaw_deg = -40, .pitch_deg = 25, .distance = 6.2}, 420, 420),
                      render({{&sphere, kMint, false}, {&spikes, kBrass, false}}, {.yaw_deg = -50, .pitch_deg = 30, .distance = 2.0},
                             420, 420, 0.0)}),
             dir + "/ops_sheet.png");
    std::printf("wrote %s/{hard_surface,organic,boolean_slivers,ops_sheet}.png\n", dir.c_str());
}
