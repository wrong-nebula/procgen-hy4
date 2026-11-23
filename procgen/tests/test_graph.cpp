// Graph evaluator: lazy evaluation, content-hash caching, validation.
#include "testing.hpp"

#include "pg/graph.hpp"
#include "pg/quality.hpp"

using namespace pg;

TEST(graph_evaluates_chain) {
    Graph g;
    const NodeId box = g.add(BoxOp{{1, 1, 1}, 2});
    const NodeId top = g.add(ExtrudeOp{SelectByNormal{{0, 0, 1}, 0.99}, {.distance = 0.5}}, {box});
    const NodeId again = g.add(ExtrudeOp{SelectByNormal{{0, 0, 1}, 0.99}, {.distance = 0.5}}, {top});
    const MeshPtr m = g.evaluate(again);
    const QualityReport q = analyze(*m);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 2.0, 1e-12);
    CHECK_EQ(g.stats().evaluated, std::size_t(3));
}

TEST(graph_caches_and_invalidates_downstream_only) {
    Graph g;
    const NodeId box = g.add(BoxOp{{1, 1, 1}, 8});
    const NodeId ext = g.add(ExtrudeOp{SelectByNormal{{0, 0, 1}, 0.99}, {.distance = 0.2}}, {box});
    const NodeId moved = g.add(TranslateOp{{0, 0, 1}}, {ext});
    (void)g.evaluate(moved);
    CHECK_EQ(g.stats().evaluated, std::size_t(3));

    g.reset_stats();
    (void)g.evaluate(moved);
    CHECK_EQ(g.stats().evaluated, std::size_t(0));
    CHECK_EQ(g.stats().cache_hits, std::size_t(3));

    g.reset_stats();
    g.set_params(ext, ExtrudeOp{SelectByNormal{{0, 0, 1}, 0.99}, {.distance = 0.3}});
    const MeshPtr m = g.evaluate(moved);
    CHECK_EQ(g.stats().evaluated, std::size_t(2));  // extrude + translate; box reused
    CHECK_NEAR(analyze(*m).volume, 1.3, 1e-12);
}

TEST(graph_only_evaluates_needed_nodes) {
    Graph g;
    const NodeId a = g.add(BoxOp{});
    (void)g.add(CylinderOp{64, 1.0, 1.0});  // unrelated branch
    const NodeId b = g.add(TranslateOp{{2, 0, 0}}, {a});
    (void)g.evaluate(b);
    CHECK_EQ(g.stats().evaluated, std::size_t(2));
}

TEST(graph_merge_and_shared_subgraphs) {
    Graph g;
    const NodeId a = g.add(BoxOp{});
    const NodeId b = g.add(BoxOp{});  // identical content -> same cache entry
    const NodeId bt = g.add(TranslateOp{{3, 0, 0}}, {b});
    const NodeId both = g.add(MergeOp{}, {a, bt});
    const QualityReport q = analyze(*g.evaluate(both));
    CHECK(q.clean());
    CHECK_EQ(q.euler, 4);  // two spheres
    CHECK_NEAR(q.volume, 2.0, 1e-12);
    CHECK_EQ(g.stats().evaluated, std::size_t(3));
    CHECK_EQ(g.stats().cache_hits, std::size_t(1));
}

TEST(graph_rejects_bad_wiring) {
    Graph g;
    const NodeId a = g.add(BoxOp{});
    CHECK_THROWS(g.add(TranslateOp{}, {}));        // arity
    CHECK_THROWS(g.add(TranslateOp{}, {a + 5}));   // forward reference
    CHECK_THROWS(g.add(MergeOp{}, {a}));           // arity
    CHECK_THROWS(g.evaluate(42));
    CHECK_THROWS(g.set_params(a, TranslateOp{}));  // arity change
}

TEST(graph_propagates_op_errors) {
    Graph g;
    const NodeId a = g.add(BoxOp{{1, 1, 1}, 0});
    CHECK_THROWS(g.evaluate(a));
}

TEST(graph_hard_surface_part) {
    // Box -> boss extrusion -> drilled hole -> chamfer -> rivets scattered on top.
    Graph g;
    const NodeId body = g.add(BoxOp{{2, 2, 0.6}, 4});
    const NodeId boss = g.add(ExtrudeOp{SelectByNormal{{0, 0, 1}, 0.99}, {.distance = 0.2}}, {body});
    const NodeId drill = g.add(CylinderOp{24, 0.3, 4.0});
    const NodeId holed = g.add(CsgOp{BooleanOp::Difference}, {boss, drill});
    const NodeId part = g.add(BevelOp{{.width = 0.03}}, {holed});
    const NodeId rivet = g.add(CylinderOp{8, 0.03, 0.04});
    const NodeId plate = g.add(GridOp{4, 4, 1.6, 1.6});
    const NodeId rivets = g.add(ScatterOp{{.count = 40, .min_distance = 0.15, .seed = 3}, {}}, {plate, rivet});
    const QualityReport q = analyze(*g.evaluate(part));
    CHECK(q.watertight());
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
    CHECK_EQ(q.euler, 0);  // the hole makes it genus 1
    const QualityReport r = analyze(*g.evaluate(rivets));
    CHECK(r.clean());
    CHECK(r.faces > 0);
    // Editing the bevel width re-evaluates only the bevel.
    g.reset_stats();
    g.set_params(part, BevelOp{{.width = 0.02}});
    (void)g.evaluate(part);
    CHECK_EQ(g.stats().evaluated, std::size_t(1));
}

TEST(graph_organic_part) {
    // Box -> Catmull-Clark -> fBm displacement -> twist: a smooth, lumpy, twisted blob.
    Graph g;
    const NodeId base = g.add(BoxOp{{1, 1, 2}, 2});
    const NodeId smooth = g.add(SubdivideOp{3}, {base});
    const NodeId lumpy = g.add(NoiseOp{{.amplitude = 0.04, .frequency = 3.0, .seed = 9, .octaves = 3}}, {smooth});
    const NodeId twisted = g.add(DeformOp{DeformKind::Twist, 0.9}, {lumpy});
    const QualityReport q = analyze(*g.evaluate(twisted));
    CHECK(q.clean());
    CHECK_EQ(q.euler, 2);
    const QualityReport s = analyze(*g.evaluate(smooth));
    CHECK(std::abs(q.volume - s.volume) / s.volume < 0.05);  // displacement + twist ~volume-neutral
}
