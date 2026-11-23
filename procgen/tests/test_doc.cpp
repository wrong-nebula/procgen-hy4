// Geometry documents: evaluator, groups, connect, extension nodes, engine packs.
#include "testing.hpp"

#include "pg/audit.hpp"
#include "pg/doc.hpp"
#include "pg/emat.hpp"
#include "pg/emesh.hpp"
#include "pg/quality.hpp"
#include "../src/doc/doc_expr.hpp"

#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <numbers>
#include <set>

using namespace pg;

#ifndef PG_TEST_DATA
#define PG_TEST_DATA "tests/data"
#endif
#ifndef PG_RECIPES
#define PG_RECIPES "recipes"
#endif

namespace {

doc::Result eval_text(const std::string& text, const doc::Controls& overrides = {}) {
    doc::Library library;
    return doc::evaluate(json::parse(text), library, overrides);
}
std::string fixture() { return std::string(PG_TEST_DATA) + "/kit-bracket.json"; }

}  // namespace

TEST(json_parses_and_rejects) {
    const json::Value v = json::parse(R"({"a": [1, 2.5, -3e2], "s": "x\né\"", "t": true, "n": null, "o": {"k": 1}})");
    CHECK(v.is_object());
    CHECK_NEAR(v.find("a")->array[2].number, -300.0, 0);
    CHECK(v.find("s")->string == "x\n\xC3\xA9\"");
    CHECK(v.find("t")->boolean);
    CHECK(v.find("n")->is_null());
    CHECK(v.find("missing") == nullptr);
    CHECK_THROWS(json::parse("{\"a\": }"));
    CHECK_THROWS(json::parse("[1, 2"));
    CHECK_THROWS(json::parse("{} trailing"));
    json::Writer w;
    w.begin_object().field("a", 1).key("b").begin_array().value("x\"y").value(true).end_array().end_object();
    CHECK(w.str() == R"({"a":1,"b":["x\"y",true]})");
    CHECK(json::parse(w.str()).find("b")->array[0].string == "x\"y");
}

TEST(doc_fixture_counts_and_bounds) {
    const doc::Result r = doc::evaluate_file(fixture());
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_EQ(rc.instances, std::size_t(11));      // plate + 4 x (shaft, cap) + rail + gusset
    CHECK_EQ(rc.prototypes, std::size_t(5));
    CHECK_EQ(rc.placed_triangles, std::size_t(488));
    CHECK_EQ(r.evaluated_nodes, std::size_t(21));
    CHECK_NEAR(rc.lo.x, -0.8, 1e-6);
    CHECK_NEAR(rc.hi.x, 0.8, 1e-6);
    CHECK_NEAR(rc.hi.z, 0.57, 1e-6);
}

TEST(doc_controls_drive_geometry_and_are_validated) {
    const doc::Result longer = doc::evaluate_file(fixture(), {{"length", 3.0f}, {"posts", 6.0f}});
    const doc::Receipt rc = doc::make_receipt(longer.geometry);
    CHECK_EQ(rc.instances, std::size_t(15));
    CHECK_NEAR(rc.hi.x, 1.5, 1e-6);
    CHECK_EQ(doc::make_receipt(doc::evaluate_file(fixture(), {{"rail", 0.0f}}).geometry).instances, std::size_t(10));  // select
    CHECK_THROWS(doc::evaluate_file(fixture(), {{"length", 99.0f}}));   // out of range
    CHECK_THROWS(doc::evaluate_file(fixture(), {{"posts", 2.5f}}));     // integer control
    CHECK_THROWS(doc::evaluate_file(fixture(), {{"nope", 1.0f}}));      // unknown control
}

TEST(doc_prototypes_are_closed_solids) {
    const doc::Result r = doc::evaluate_file(fixture());
    std::set<const doc::Proto*> seen;
    for (const doc::Part& p : r.geometry.parts) {
        if (!seen.insert(p.proto.get()).second) continue;
        const QualityReport q = analyze(p.proto->mesh);
        CHECK(q.clean());
        CHECK(q.volume > 0);
    }
    // Material routing: the group's shaft is replaced, its cap keeps bronze.
    std::set<std::string> slots;
    for (const doc::Part& p : r.geometry.parts) slots.insert(doc::slot_name(p.material));
    CHECK(slots == (std::set<std::string>{"bronze", "chipped-limestone", "steel", "weathered-iron"}));
}

TEST(doc_reports_errors_with_node_ids) {
    bool named = false;
    try {
        (void)eval_text(R"({"version":1,"nodes":[{"id":"bad","type":"cylinder","options":{"sides":2}}],"output":"bad"})");
    } catch (const std::runtime_error& e) {
        named = std::string(e.what()).rfind("bad: ", 0) == 0;
    }
    CHECK(named);
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"a","type":"join","inputs":["a"]}],"output":"a"})"));       // cycle
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"a","type":"warp"}],"output":"a"})"));                      // unknown type
    CHECK_THROWS(eval_text(R"({"version":2,"nodes":[{"id":"a","type":"empty"}],"output":"a"})"));                     // version
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"a","type":"cube","options":{"size":["$x",1,1]}}],"output":"a"})"));  // unknown control
}

TEST(doc_surface_polygons_and_holes) {
    // A square plate with a square hole, as one face with two loops, plus an n-gon.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"plate","type":"surface","options":{
        "vertices":[[0,0,0],[4,0,0],[4,4,0],[0,4,0],[1,1,0],[3,1,0],[3,3,0],[1,3,0],
                    [5,0,0],[6,0,0],[7,1,0],[6,2,0],[5,2,0]],
        "faces":[[[0,1,2,3],[4,5,6,7]],[8,9,10,11,12]]}}],"output":"plate"})");
    const Mesh& m = r.geometry.parts[0].proto->mesh;
    CHECK_EQ(m.face_count(), std::size_t(8 + 3));  // ring -> 8 triangles, pentagon -> 3
    double area = 0;
    for (std::size_t f = 0; f < m.face_count(); ++f) area += face_area(m, f);
    CHECK_NEAR(area, (16.0 - 4.0) + 3.0, 1e-9);
    for (std::size_t f = 0; f < m.face_count(); ++f) CHECK(face_normal(m, f).z > 0.999);
}

TEST(doc_spline_sweep_and_instance_fields) {
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"arc","type":"spline","options":{"points":[[0,0,0],[1,0,1],[2,0,0]],"resolution":8}},
      {"id":"even","type":"resample","inputs":["arc"],"options":{"count":17}},
      {"id":"rib","type":"profileSweep","inputs":["even"],"options":{"profile":[[-0.05,-0.05],[0.05,-0.05],[0.05,0.05],[-0.05,0.05]]}},
      {"id":"dots","type":"line","options":{"start":[0,1,0],"step":[0.5,0,0],"count":4}},
      {"id":"bead","type":"cube","options":{"size":[0.1,0.1,0.1]}},
      {"id":"beads","type":"instance","inputs":["dots","bead"],"options":{"translation":[0,0,["*","@index",0.25]],"scale":[1,1,["+",1,"@index"]]}},
      {"id":"out","type":"join","inputs":["rib","beads"]}],"output":"out"})");
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_EQ(rc.instances, std::size_t(5));
    const Mesh& rib = r.geometry.parts[0].proto->mesh;
    CHECK_EQ(rib.vertex_count(), std::size_t(17 * 4));
    CHECK(analyze(rib).clean());
    // Fourth bead: lifted by 3 * 0.25 and scaled 4x in Z.
    CHECK_NEAR(rc.hi.z, 1.05, 0.02);              // arc crown + half the rib section
    const doc::Part& last = r.geometry.parts[4];
    CHECK_NEAR(last.transform.c[3][2], 0.75, 1e-6);
    CHECK_NEAR(last.transform.c[2][2], 4.0, 1e-6);
}

TEST(doc_connect_mates_module_ports) {
    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() / "pg_doc_connect_test";
    fs::create_directories(dir);
    auto put = [&](const char* name, const std::string& text) { std::ofstream(dir / name) << text; };
    put("modules.json", R"({"version":1,"modules":{
      "deck-a":{"document":"deck-a.json","ports":{"end":{"origin":[["*","$span",0.5],0,0],"normal":[1,0,0],"up":[0,0,1],"type":"deck","dimensions":{"width":1}}}},
      "deck-b":{"document":"deck-b.json","ports":{"start":{"origin":[-1,0,0],"normal":[-1,0,0],"up":[0,0,1],"type":"deck","dimensions":{"width":1}},
                                                   "narrow":{"origin":[-1,0,0],"normal":[-1,0,0],"up":[0,0,1],"type":"deck","dimensions":{"width":0.5}}}}}})");
    put("deck-a.json", R"({"version":1,"controls":{"span":{"value":2,"min":1,"max":8}},
      "nodes":[{"id":"slab","type":"cube","options":{"size":["$span",1,0.2]}}],"output":"slab"})");
    put("deck-b.json", R"({"version":1,"nodes":[{"id":"slab","type":"cube","options":{"size":[2,1,0.2]}}],"output":"slab"})");
    const std::string main_doc = R"({"version":1,"moduleImports":{"a":"deck-a","b":"deck-b"},"nodes":[
      {"id":"first","type":"group","group":"a","bindings":{"span":4}},
      {"id":"second","type":"group","group":"b"},
      {"id":"seated","type":"connect","inputs":["second"],"options":{"a":["@first","end"],"b":["deck-b","PORT"]}},
      {"id":"out","type":"join","inputs":["first","seated"]}],"output":"out"})";
    auto with_port = [&](const std::string& port) {
        std::string text = main_doc;
        text.replace(text.find("PORT"), 4, port);
        doc::Library library(dir.string());
        return doc::evaluate(json::parse(text), library);
    };
    const doc::Receipt rc = doc::make_receipt(with_port("start").geometry);
    CHECK_NEAR(rc.lo.x, -2.0, 1e-5);  // first deck spans [-2, 2] with span bound to 4
    CHECK_NEAR(rc.hi.x, 4.0, 1e-5);   // second deck seated end to end: [2, 4]
    CHECK_THROWS(with_port("narrow"));   // deck width mismatch
    CHECK_THROWS(with_port("missing"));  // unknown port
    fs::remove_all(dir);
}

TEST(doc_extension_nodes) {
    // Lathe: a closed 24-gon prism via revolve, exact polygon-prism volume.
    const doc::Result vase = eval_text(R"({"version":1,"nodes":[
      {"id":"v","type":"revolve","options":{"profile":[[0,0],[1,0],[1,2],[0,2]],"sides":24}}],"output":"v"})");
    const QualityReport q = analyze(vase.geometry.parts[0].proto->mesh);
    CHECK(q.clean());
    CHECK_NEAR(q.volume, 0.5 * 24 * std::sin(2 * std::numbers::pi / 24) * 2.0, 1e-9);

    // A ring section that ends where it starts wraps into a closed solid (a pipe wall), outward
    // whichever way round it runs; without the repeated point it stays an open sleeve as before.
    const double prism = 0.5 * 24 * std::sin(2 * std::numbers::pi / 24) * 2.0;  // per unit radius squared
    for (const char* ring : {"[[0.5,0],[1,0],[1,2],[0.5,2],[0.5,0]]", "[[0.5,0],[0.5,2],[1,2],[1,0],[0.5,0]]"}) {
        const doc::Result pipe = eval_text(std::string(R"({"version":1,"nodes":[{"id":"p","type":"revolve","options":{"profile":)") +
                                           ring + R"(,"sides":24}}],"output":"p"})");
        const QualityReport r = analyze(pipe.geometry.parts[0].proto->mesh);
        CHECK(r.watertight());
        CHECK_NEAR(r.volume, prism * (1.0 - 0.25), 1e-9);
    }
    const doc::Result sleeve = eval_text(R"({"version":1,"nodes":[
      {"id":"s","type":"revolve","options":{"profile":[[0.5,0],[1,0],[1,2],[0.5,2]],"sides":24}}],"output":"s"})");
    CHECK(!analyze(sleeve.geometry.parts[0].proto->mesh).watertight());
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[
      {"id":"a","type":"revolve","options":{"profile":[[0,0],[1,0],[1,2],[0,2],[0,0]],"sides":24}}],"output":"a"})"));

    // Derived values: computed once after the controls, in order, read like a control; overrides of a derived
    // name and duplicates are rejected.
    const char* derived_doc = R"({"version":1,"controls":{"w":{"value":2,"min":1,"max":4}},
      "derived":[["half",["*","$w",0.5]],["area",["*","$half","$half"]]],
      "nodes":[{"id":"c","type":"cube","options":{"size":["$half","$area",1]}}],"output":"c"})";
    const doc::Result dv = eval_text(derived_doc);
    CHECK_NEAR(analyze(dv.geometry.parts[0].proto->mesh).volume, 1.0 * 1.0 * 1.0, 1e-6);
    CHECK_THROWS(eval_text(R"({"version":1,"derived":[["a",1],["a",2]],"nodes":[{"id":"c","type":"cube"}],"output":"c"})"));
    CHECK_THROWS(eval_text(R"({"version":1,"derived":[["a","$b"]],"nodes":[{"id":"c","type":"cube"}],"output":"c"})"));

    // Terrain block: closed, top follows the heights, plus scatter -> instance.
    const doc::Result land = eval_text(R"({"version":1,"nodes":[
      {"id":"ground","type":"heightfield","options":{"size":[10,6],"divisions":[20,12],"noise":{"amplitude":0.6,"frequency":0.3,"seed":4},"skirt":1}},
      {"id":"spots","type":"scatter","inputs":["ground"],"options":{"count":40,"minDistance":0.5,"seed":2}},
      {"id":"rock","type":"cube","options":{"size":[0.2,0.2,0.2]}},
      {"id":"round","type":"subdivide","inputs":["rock"],"options":{"levels":2}},
      {"id":"rocks","type":"instance","inputs":["spots","round"]},
      {"id":"out","type":"join","inputs":["ground","rocks"]}],"output":"out"})");
    CHECK(analyze(land.geometry.parts[0].proto->mesh).clean());
    CHECK_EQ(land.geometry.parts.size(), std::size_t(41));
    CHECK_EQ(land.geometry.parts[1].proto->mesh.face_count(), std::size_t(6 * 16));
    CHECK(land.geometry.parts[1].proto.get() == land.geometry.parts[40].proto.get());  // one rock prototype

    const doc::Result solid = eval_text(R"({"version":1,"nodes":[
      {"id":"b","type":"cube","options":{"size":[1,1,1]}},
      {"id":"c","type":"chamfer","inputs":["b"],"options":{"width":0.1}},
      {"id":"t","type":"deform","inputs":["c"],"options":{"kind":"taper","amount":0.4}}],"output":"t"})");
    const QualityReport qs = analyze(solid.geometry.parts[0].proto->mesh);
    CHECK(qs.clean());
    CHECK_EQ(qs.faces, std::size_t(26));
}

TEST(emesh_round_trip_and_congruent_instancing) {
    // Two cubes authored separately (so: two prototypes), one of them rotated
    // and moved. Congruence detection writes one mesh with two instances.
    const std::string text = R"({"version":1,"nodes":[
      {"id":"a","type":"cube","options":{"size":[1,2,3]}},
      {"id":"b","type":"cube","options":{"size":[1,2,3]}},
      {"id":"b-placed","type":"transform","inputs":["b"],"options":{"translation":[5,0,0],"rotation":[0,0,0.7]}},
      {"id":"c","type":"cube","options":{"size":[1,2,3.5]}},
      {"id":"c-placed","type":"transform","inputs":["c"],"options":{"translation":[0,6,0]}},
      {"id":"out","type":"join","inputs":["a","b-placed","c-placed"]}],"output":"out"})";
    const doc::Result r = eval_text(text);
    emesh::WriteStats st;
    const std::vector<std::uint8_t> bytes = emesh::write(r.geometry, {}, &st);
    CHECK_EQ(st.source_prototypes, std::size_t(3));
    CHECK_EQ(st.meshes, std::size_t(2));       // a and b collapse; c differs
    CHECK_EQ(st.instances, std::size_t(3));
    CHECK_EQ(bytes.size() % 16, std::size_t(0));
    const emesh::Pack pack = emesh::read(bytes);
    CHECK(pack.y_up);
    CHECK_EQ(pack.transforms.size(), std::size_t(3));
    CHECK_EQ(pack.indices.size(), std::size_t(2 * 36));
    // World bounds survive quantisation and the Z-up -> Y-up change: Y is up.
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_NEAR(pack.hi.y, rc.hi.z, 1e-3);
    CHECK_NEAR(pack.lo.x, rc.lo.x, 1e-3);
    CHECK_NEAR(-pack.lo.z, rc.hi.y, 1e-3);

    emesh::WriteStats plain;
    emesh::WriteOptions no_dedupe;
    no_dedupe.dedupe_congruent = false;
    (void)emesh::write(r.geometry, no_dedupe, &plain);
    CHECK_EQ(plain.meshes, std::size_t(3));

    // Corruption is detected, not drawn.
    std::vector<std::uint8_t> broken = bytes;
    broken[broken.size() / 2] ^= 0x5A;
    CHECK_THROWS(emesh::read(broken));
    broken = bytes;
    broken.resize(broken.size() - 16);
    CHECK_THROWS(emesh::read(broken));
}

TEST(emesh_fixture_matches_document_triangles) {
    const doc::Result r = doc::evaluate_file(fixture());
    emesh::WriteStats st;
    const emesh::Pack pack = emesh::read(emesh::write(r.geometry, {}, &st));
    CHECK_EQ(st.triangles, doc::make_receipt(r.geometry).placed_triangles);
    CHECK_EQ(st.instances, std::size_t(11));
    CHECK(st.meshes <= st.source_prototypes);
    CHECK(!pack.slot_names.empty());
    // Every decoded normal is unit length and every vertex lies in its mesh box.
    for (const Vec3& n : pack.normals) CHECK_NEAR(length(n), 1.0, 1e-6);
    for (const auto& m : pack.meshes)
        for (std::uint32_t i = 0; i < m.index_count; ++i) {
            const Vec3 p = pack.positions[m.base_vertex + pack.indices[m.first_index + i]];
            CHECK(p.x >= m.lo.x - 1e-6 && p.x <= m.hi.x + 1e-6 && p.z >= m.lo.z - 1e-6 && p.z <= m.hi.z + 1e-6);
        }
}

TEST(doc_expression_functions) {
    // Functions beyond the reference set, and a lazy conditional.
    const doc::Result r = eval_text(R"({"version":1,"controls":{"n":{"value":3,"min":1,"max":9}},"nodes":[
      {"id":"b","type":"cube","options":{"size":[["sqrt",16],["pow",2,"$n"],["if",[">","$n",5],["sqrt",-1],["+",["floor",6.9],["abs",-1]]]]}}],"output":"b"})");
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_NEAR(rc.hi.x - rc.lo.x, 4.0, 1e-6);
    CHECK_NEAR(rc.hi.y - rc.lo.y, 8.0, 1e-6);
    CHECK_NEAR(rc.hi.z - rc.lo.z, 7.0, 1e-6);  // the sqrt(-1) branch is never evaluated
    const doc::Result trig = eval_text(R"({"version":1,"nodes":[
      {"id":"b","type":"cube","options":{"size":[["+",1,["sin",0]],["cos",0],["+",["atan2",0,1],["+",["mod",7,4],["round",0.4]]]]}}],"output":"b"})");
    const doc::Receipt rt = doc::make_receipt(trig.geometry);
    CHECK_NEAR(rt.hi.x - rt.lo.x, 1.0, 1e-6);
    CHECK_NEAR(rt.hi.z - rt.lo.z, 3.0, 1e-6);
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"b","type":"cube","options":{"size":[["sqrt",-1],1,1]}}],"output":"b"})"));
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"b","type":"cube","options":{"size":[["cosh",1],1,1]}}],"output":"b"})"));
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"b","type":"cube","options":{"size":["@t",1,1]}}],"output":"b"})"));  // no field here
}

TEST(doc_parametric_curve_tube_loft_patch) {
    // Closed parametric curve -> ring tube: a watertight torus.
    const doc::Result ring = eval_text(R"({"version":1,"nodes":[
      {"id":"c","type":"curve","options":{"point":[["*",2,["cos",["*","@t",6.2831853]]],["*",2,["sin",["*","@t",6.2831853]]],0],"segments":32,"closed":1}},
      {"id":"t","type":"tube","inputs":["c"],"options":{"radius":0.1,"sides":8}}],"output":"t"})");
    CHECK_EQ(ring.geometry.parts.size(), std::size_t(1));
    const QualityReport qr = analyze(ring.geometry.parts[0].proto->mesh);
    CHECK(qr.clean());
    CHECK_EQ(qr.boundary_edges, std::size_t(0));
    CHECK(qr.volume > 0.33 && qr.volume < 0.40);  // 2 pi^2 R r^2 = 0.395 for the smooth torus

    // Loft through two rings: an open wall, or a closed shell with thickness.
    const std::string two_rings = R"({"version":1,"nodes":[
      {"id":"a","type":"curve","options":{"point":[["cos",["*","@t",6.2831853]],["sin",["*","@t",6.2831853]],0],"segments":16,"closed":1}},
      {"id":"b","type":"transform","inputs":["a"],"options":{"translation":[0,0,1]}},
      {"id":"l","type":"loft","inputs":["a","b"],"options":{"thickness":THICK}}],"output":"l"})";
    auto loft = [&](const char* thickness) {
        std::string text = two_rings;
        text.replace(text.find("THICK"), 5, thickness);
        return analyze(eval_text(text).geometry.parts[0].proto->mesh);
    };
    const QualityReport wall = loft("0");
    CHECK_EQ(wall.faces, std::size_t(16));
    CHECK_EQ(wall.boundary_edges, std::size_t(32));
    const QualityReport shell = loft("0.1");
    CHECK(shell.clean());
    CHECK_EQ(shell.boundary_edges, std::size_t(0));
    CHECK(shell.volume > 0.55 && shell.volume < 0.66);  // about 2 pi * 1 * 1 * 0.1

    // Patch split into inset panels with thickness: eight exact slabs.
    const doc::Result panes = eval_text(R"({"version":1,"nodes":[
      {"id":"p","type":"patch","options":{"point":[["*",2,"@u"],"@v",0],"panels":[4,2],"inset":[0.1,0.1],"divisions":[2,1],"thickness":0.02}}],"output":"p"})");
    CHECK_EQ(panes.geometry.parts.size(), std::size_t(8));
    for (const doc::Part& part : panes.geometry.parts) {
        const QualityReport q = analyze(part.proto->mesh);
        CHECK(q.clean());
        CHECK_NEAR(q.volume, 0.4 * 0.4 * 0.02, 1e-7);
    }
    // Closed direction: a cylinder wall with two rims.
    const doc::Result can = eval_text(R"({"version":1,"nodes":[
      {"id":"p","type":"patch","options":{"point":[["cos",["*","@u",6.2831853]],["sin",["*","@u",6.2831853]],"@v"],"divisions":[24,2],"closedU":1}}],"output":"p"})");
    const QualityReport qc = analyze(can.geometry.parts[0].proto->mesh);
    CHECK_EQ(qc.faces, std::size_t(48));
    CHECK_EQ(qc.boundary_edges, std::size_t(48));
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"p","type":"patch","options":{"point":[1,1,1]}}],"output":"p"})"));  // degenerate
    CHECK_THROWS(eval_text(R"({"version":1,"nodes":[{"id":"p","type":"patch","options":{"point":["@u","@v",0],"closedU":1,"panels":[2,1]}}],"output":"p"})"));
}

TEST(doc_kits_import_other_kits) {
    namespace fs = std::filesystem;
    const fs::path root = fs::temp_directory_path() / "pg_doc_kit_test";
    fs::remove_all(root);
    fs::create_directories(root / "frames");
    fs::create_directories(root / "posts");
    auto put = [&](const char* name, const std::string& text) { std::ofstream(root / name) << text; };
    put("posts/modules.json", R"({"version":1,"kit":"posts","modules":{"post":{"document":"post.json","ports":{}}}})");
    put("posts/post.json", R"({"version":1,"controls":{"h":{"value":2,"min":1,"max":9}},
      "nodes":[{"id":"p","type":"cube","options":{"size":[0.2,0.2,"$h"]}}],"output":"p"})");
    put("frames/modules.json", R"({"version":1,"kit":"frames","imports":["../posts"],"modules":{"frame":{"document":"frame.json","ports":{}}}})");
    put("frames/frame.json", R"({"version":1,"moduleImports":{"post":"post"},"nodes":[
      {"id":"a","type":"group","group":"post","bindings":{"h":3}},
      {"id":"b","type":"transform","inputs":["a"],"options":{"translation":[2,0,0]}},
      {"id":"out","type":"join","inputs":["a","b"]}],"output":"out"})");
    {
        doc::Library library((root / "frames").string());
        CHECK_EQ(library.kits().size(), std::size_t(2));
        CHECK(library.kits()[0].name == "frames" && library.kits()[1].name == "posts");
        CHECK(library.module("post") != nullptr && library.module("post")->kit == "posts");
        const doc::Result r = doc::evaluate_file((root / "frames" / "frame.json").string());
        CHECK_EQ(r.geometry.parts.size(), std::size_t(2));
        CHECK_NEAR(doc::make_receipt(r.geometry).hi.z, 1.5, 1e-6);
    }
    // The same id registered by two kits is an error, as is a missing import.
    put("posts/modules.json", R"({"version":1,"kit":"posts","modules":{"frame":{"document":"post.json","ports":{}}}})");
    CHECK_THROWS(doc::Library((root / "frames").string()));
    put("frames/modules.json", R"({"version":1,"kit":"frames","imports":["../nowhere"],"modules":{}})");
    CHECK_THROWS(doc::Library((root / "frames").string()));
    fs::remove_all(root);
}

TEST(doc_connect_chains_from_placed_modules) {
    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() / "pg_doc_chain_test";
    fs::remove_all(dir);
    fs::create_directories(dir);
    auto put = [&](const char* name, const std::string& text) { std::ofstream(dir / name) << text; };
    put("modules.json", R"({"version":1,"modules":{"deck":{"document":"deck.json","ports":{
      "start":{"origin":[["*","$span",-0.5],0,0],"normal":[-1,0,0],"up":[0,0,1],"type":"deck","dimensions":{"width":1}},
      "end":{"origin":[["*","$span",0.5],0,0],"normal":[1,0,0],"up":[0,0,1],"type":"deck","dimensions":{"width":1}}}}}})");
    put("deck.json", R"({"version":1,"controls":{"span":{"value":2,"min":1,"max":8}},
      "nodes":[{"id":"slab","type":"cube","options":{"size":["$span",1,0.2]}}],"output":"slab"})");
    // First deck placed by a transform (turned to run along +Y); the others seat on the
    // previous placed instance without restating its pose.
    const doc::Result r = doc::evaluate(json::parse(R"({"version":1,"moduleImports":{"deck":"deck"},"nodes":[
      {"id":"long","type":"group","group":"deck","bindings":{"span":4}},
      {"id":"short","type":"group","group":"deck"},
      {"id":"first","type":"transform","inputs":["long"],"options":{"translation":[10,0,0],"rotation":[0,0,1.5707963]}},
      {"id":"second","type":"connect","inputs":["short"],"options":{"a":["@first","end"],"b":["@short","start"]}},
      {"id":"third","type":"connect","inputs":["short"],"options":{"a":["@second","end"],"b":["@short","start"]}},
      {"id":"out","type":"join","inputs":["first","second","third"]}],"output":"out"})"),
                                            *std::make_unique<doc::Library>(dir.string()));
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_NEAR(rc.lo.y, -2.0, 1e-4);  // 4 m deck centred on y = 0
    CHECK_NEAR(rc.hi.y, 6.0, 1e-4);   // then two 2 m decks end to end
    CHECK_NEAR(rc.lo.x, 9.5, 1e-4);
    CHECK_NEAR(rc.hi.x, 10.5, 1e-4);
    CHECK(r.geometry.parts[1].proto.get() == r.geometry.parts[2].proto.get());  // one prototype, two seats
    fs::remove_all(dir);
}

TEST(greenhouse_kits_scale_and_mate) {
    const std::string kits = std::string(PG_RECIPES) + "/greenhouse/";
    auto house = [&](float bays) { return doc::evaluate_file(kits + "assemblies/gh-potting-house.json", {{"bays", bays}}); };
    const doc::Result two = house(2), four = house(4);
    CHECK(four.geometry.parts.size() > two.geometry.parts.size());
    const doc::Receipt r2 = doc::make_receipt(two.geometry), r4 = doc::make_receipt(four.geometry);
    CHECK_NEAR(r4.hi.y - r2.hi.y, 2 * 2.1, 0.02);                   // two more 2.1 m bays
    CHECK(r4.hi.z > 2.3 + 1.5 && r4.hi.z < 2.3 + 1.5 + 0.2);         // spring + rise, plus half a rib
    CHECK(r4.hi.x > 2.1 && r4.hi.x < 2.1 + 0.6 && r4.lo.x < -2.1);   // 4.2 m span plus the capital arms and gutter
    std::set<const doc::Proto*> seen;
    for (const doc::Part& part : four.geometry.parts) {
        if (part.proto->mesh.face_count() == 0 || !seen.insert(part.proto.get()).second) continue;
        const QualityReport q = analyze(part.proto->mesh);
        CHECK(q.watertight());                                      // baked modules: closed solids that may overlap
        CHECK_EQ(q.degenerate_faces, std::size_t(0));
    }
    CHECK(seen.size() >= 8 && seen.size() < 40);                    // baked: a few large prototypes, not hundreds
    // Section ports carry span, spring, rise and gauge: a screen of another span must not seat.
    doc::Library library(kits + "assemblies");
    CHECK(library.kits().size() >= 5);  // assemblies import nave, crossing, fittings; nave imports foundry and glazing
    const char* mismatched = R"({"version":1,"moduleImports":{"gallery":"gh-gallery","screen":"gh-end-screen"},"nodes":[
      {"id":"hall","type":"group","group":"gallery","bindings":{"bays":1}},
      {"id":"end","type":"group","group":"screen","bindings":{"span":SPAN}},
      {"id":"seated","type":"connect","inputs":["end"],"options":{"a":["@hall","north"],"b":["@end","section"]}},
      {"id":"out","type":"join","inputs":["hall","seated"]}],"output":"out"})";
    auto seat = [&](const char* span) {
        std::string text = mismatched;
        text.replace(text.find("SPAN"), 4, span);
        return doc::evaluate(json::parse(text), library);
    };
    CHECK(seat("10").geometry.parts.size() > 10);
    CHECK_THROWS(seat("8"));
}

TEST(site_kits_mate_decks_and_tracks) {
    if (!std::filesystem::exists(std::string(PG_RECIPES) + "/site/railway")) return;  // kit not shipped in this repo
    doc::Library library(std::string(PG_RECIPES) + "/site/railway");
    // Two viaduct bays (masonry, then iron) seated deck to deck with both track pairs checked,
    // and a track panel continuing track a beyond the first bay.
    const char* line = R"({"version":1,"moduleImports":{"bay":"rail-viaduct-bay","track":"rail-track-panel"},"nodes":[
      {"id":"a","type":"group","group":"bay","bindings":{"variant":1}},
      {"id":"b","type":"group","group":"bay","bindings":{"variant":0}},
      {"id":"b-at","type":"connect","inputs":["b"],"options":{"a":["@a","east"],"b":["@b","west"],
        "also":[[["@a","track-east-a"],["@b","track-west-a"]],[["@a","track-east-b"],["@b","track-west-b"]]]}},
      {"id":"panel","type":"group","group":"track","bindings":{"length":10,"gauge":GAUGE}},
      {"id":"panel-at","type":"connect","inputs":["panel"],"options":{"a":["@a","track-west-a"],"b":["@panel","east"]}},
      {"id":"out","type":"join","inputs":["a","b-at","panel-at"]}],"output":"out"})";
    auto build = [&](const char* gauge) {
        std::string text = line;
        text.replace(text.find("GAUGE"), 5, gauge);
        return doc::evaluate(json::parse(text), library);
    };
    const doc::Result r = build("1.435");
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    CHECK_NEAR(rc.lo.x, -15.0, 0.05);  // 10 m panel west of the first bay
    CHECK_NEAR(rc.hi.x, 15.0, 0.3);    // second bay spans 5..15 (parapet posts overhang slightly)
    std::set<const doc::Proto*> seen;
    for (const doc::Part& part : r.geometry.parts) {
        if (part.proto->mesh.face_count() == 0 || !seen.insert(part.proto.get()).second) continue;
        // A baked module is several closed solids that may overlap: closed and consistently
        // oriented, but not a single boolean union.
        const QualityReport q = analyze(part.proto->mesh);
        CHECK(q.watertight());
        CHECK_EQ(q.degenerate_faces, std::size_t(0));
    }
    CHECK_THROWS(build("1"));  // a metre-gauge panel must not seat on a standard-gauge track port
    // The vaulted hall is the greenhouse family at station span: its kit imports the greenhouse kits.
    CHECK(library.module("gh-gallery") != nullptr);
    CHECK(library.kits().size() >= 4);
}

TEST(doc_thick_patch_twisted_cells_do_not_self_intersect) {
    // A saddle: every cell is strongly non-planar. Both skins of the shell must split a cell along
    // the same diagonal, or they cross wherever the twist exceeds the thickness.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"p","type":"patch","options":{"point":[["*",4,"@u"],["*",2,"@v"],["*",["*",3,"@u"],"@v"]],"divisions":[2,2],"thickness":0.05}}],"output":"p"})");
    QualityOptions qo;
    qo.check_self_intersections = true;
    const QualityReport q = analyze(r.geometry.parts[0].proto->mesh, qo);
    CHECK(q.clean());
    CHECK_EQ(q.boundary_edges, std::size_t(0));
    CHECK_EQ(q.self_intersections.intersecting_pairs, std::size_t(0));
}

TEST(emesh_names_material_slots) {
    // EMSH 1.2: one row per slot in use, hashed the way the engine's material packs look names up.
    CHECK_EQ(emesh::slot_name_hash(""), std::uint64_t(0xCBF29CE484222325ull));
    CHECK_EQ(emesh::slot_name_hash("Iron"), emesh::slot_name_hash("iron"));
    CHECK(emesh::slot_name_hash("iron") != emesh::slot_name_hash("glass"));
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"a","type":"cube","options":{"size":[1,1,1]}},
      {"id":"am","type":"material","inputs":["a"],"options":{"slot":"iron"}},
      {"id":"b","type":"cube","options":{"size":[2,1,1]}},
      {"id":"bt","type":"transform","inputs":["b"],"options":{"translation":[3,0,0]}},
      {"id":"bm","type":"material","inputs":["bt"],"options":{"slot":"glass"}},
      {"id":"out","type":"join","inputs":["am","bm"]}],"output":"out"})");
    const emesh::Pack pack = emesh::read(emesh::write(r.geometry, emesh::WriteOptions{}));
    CHECK_EQ(pack.material_slots.size(), std::size_t(2));
    std::set<std::uint64_t> hashes;
    for (const emesh::Pack::MaterialSlot& slot : pack.material_slots) {
        hashes.insert(slot.name_hash);
        CHECK(slot.slot < pack.slot_names.size());
        CHECK_EQ(slot.name_hash, emesh::slot_name_hash(pack.slot_names[slot.slot]));
    }
    CHECK(hashes.count(emesh::slot_name_hash("iron")) == 1 && hashes.count(emesh::slot_name_hash("glass")) == 1);
}

TEST(emesh_planar_uvs_are_in_metres) {
    // The engine's materials tile per metre, so a planarUV scale must not shrink the UV range.
    auto uv_span = [](const char* tile) {
        std::string text = R"({"version":1,"nodes":[
          {"id":"a","type":"cube","options":{"size":[4,2,1]}},
          {"id":"u","type":"planarUV","inputs":["a"],"options":{"plane":"xy","metresPerTile":TILE}}],"output":"u"})";
        text.replace(text.find("TILE"), 4, tile);
        const emesh::Pack pack = emesh::read(emesh::write(eval_text(text).geometry, emesh::WriteOptions{}));
        return pack.meshes.at(0).uv_scale;
    };
    const auto one = uv_span("1"), half = uv_span("0.5");
    CHECK_NEAR(one[0], 4.0, 1e-5);
    CHECK_NEAR(one[1], 2.0, 1e-5);
    CHECK_NEAR(half[0], 4.0, 1e-5);
    CHECK_NEAR(half[1], 2.0, 1e-5);
}

TEST(emat_flat_pack_binds_every_slot_name) {
    const emat::Set set = emat::modeler_slots();
    CHECK(set.materials.size() > 20);
    CHECK(set.materials[0].name == "default");
    const std::vector<std::uint8_t> bytes = emat::write(set);
    const emat::Info info = emat::read_info(bytes);
    CHECK_EQ(std::size_t(info.materials), set.materials.size());
    CHECK_EQ(std::size_t(info.slots), set.bindings.size());
    CHECK_EQ(info.texture_arrays, std::uint32_t(0));
    auto material_of = [&](const char* slot) -> std::string {
        for (std::size_t i = 0; i < info.slot_hashes.size(); ++i)
            if (info.slot_hashes[i] == emesh::slot_name_hash(slot)) return info.material_names[info.slot_materials[i]];
        return "(unbound)";
    };
    CHECK(material_of("iron") == "iron");
    CHECK(material_of("Glass") == "glass");                 // names match case-insensitively
    CHECK(material_of("masonry") == "weathered-brick");     // shared names bind to the same material
    CHECK(material_of("brass") == "bronze");
    CHECK(material_of("lamp") == "opal");
    CHECK(material_of("no-such-slot") == "(unbound)");
    for (const emat::Material& m : set.materials) {
        if (m.name == "glass") CHECK(m.blend && m.opacity < 0.5);
        if (m.name == "opal") CHECK(m.emissive_strength > 0);
        if (m.name == "bronze") CHECK(m.metallic > 0.9);
    }
    std::vector<std::uint8_t> broken = bytes;
    broken[broken.size() - 3] ^= 0x40;
    CHECK_THROWS(emat::read_info(broken));                   // content hash
    emat::Set twice = set;
    twice.bindings.push_back({"IRON", 1});
    CHECK_THROWS(emat::write(twice));                        // one binding per name
}

TEST(doc_bake_merges_a_module_into_one_prototype_per_material) {
    const char* text = R"({"version":1,"nodes":[
      {"id":"brick","type":"cube","options":{"size":[1,1,1]}},
      {"id":"row-at","type":"line","options":{"start":[0,0,0],"step":[2,0,0],"count":5}},
      {"id":"row","type":"instance","inputs":["row-at","brick"]},
      {"id":"stone","type":"material","inputs":["row"],"options":{"slot":"stone"}},
      {"id":"bar","type":"cube","options":{"size":[10,0.2,0.2]}},
      {"id":"flipped","type":"transform","inputs":["bar"],"options":{"translation":[4,0,2],"scale":[1,-1,1]}},
      {"id":"iron","type":"material","inputs":["flipped"],"options":{"slot":"iron"}},
      {"id":"all","type":"join","inputs":["stone","iron"]},
      {"id":"OUT","type":"bake","inputs":["all"]}],"output":"OUT"})";
    std::string loose = text, baked = text;
    loose.replace(loose.find(R"("output":"OUT")"), 14, R"("output":"all")");
    const doc::Result a = eval_text(loose), b = eval_text(baked);
    CHECK_EQ(a.geometry.parts.size(), std::size_t(6));
    CHECK_EQ(b.geometry.parts.size(), std::size_t(2));               // one per material
    const doc::Receipt ra = doc::make_receipt(a.geometry), rb = doc::make_receipt(b.geometry);
    CHECK_EQ(ra.placed_triangles, rb.placed_triangles);
    CHECK_NEAR(ra.lo.x, rb.lo.x, 1e-9);
    CHECK_NEAR(ra.hi.x, rb.hi.x, 1e-9);
    CHECK_NEAR(rb.hi.z, 2.1, 1e-6);
    double volume = 0;
    for (const doc::Part& part : b.geometry.parts) {
        const QualityReport q = analyze(part.proto->mesh);
        CHECK(q.clean());                                             // the mirrored bar keeps outward faces
        CHECK_EQ(q.boundary_edges, std::size_t(0));
        volume += q.volume;
    }
    CHECK_NEAR(volume, 5.0 + 10 * 0.2 * 0.2, 1e-5);
    const emesh::Pack pack = emesh::read(emesh::write(b.geometry, emesh::WriteOptions{}));
    CHECK_EQ(pack.meshes.size(), std::size_t(2));
    CHECK_EQ(pack.transforms.size(), std::size_t(2));                 // was six instances
}

TEST(doc_instance_tints_reach_the_pack) {
    // A tint is a colour per placement: the prototype is shared, the pack carries one RGBA8 per instance.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"leaf","type":"cube","options":{"size":[1,1,1]}},
      {"id":"at","type":"line","options":{"start":[0,0,0],"step":[2,0,0],"count":4}},
      {"id":"row","type":"instance","inputs":["at","leaf"],"options":{"tint":[["-",1,["*","@index",0.25]],1,0.5]}},
      {"id":"out","type":"tint","inputs":["row"],"options":{"color":[1,0.5,1]}}],"output":"out"})");
    CHECK_EQ(r.geometry.parts.size(), std::size_t(4));
    CHECK(r.geometry.parts[0].proto.get() == r.geometry.parts[3].proto.get());
    CHECK_EQ(int(r.geometry.parts[0].tint[0]), 255);
    CHECK_EQ(int(r.geometry.parts[2].tint[0]), 128);   // 1 - 2 * 0.25
    CHECK_EQ(int(r.geometry.parts[2].tint[1]), 128);   // the tint node multiplies
    CHECK_EQ(int(r.geometry.parts[2].tint[2]), 128);
    const emesh::Pack pack = emesh::read(emesh::write(r.geometry, emesh::WriteOptions{}));
    CHECK_EQ(pack.meshes.size(), std::size_t(1));
    CHECK_EQ(pack.tints.size(), std::size_t(4));
    std::set<int> reds;
    for (const auto& tint : pack.tints) reds.insert(tint[0]);
    CHECK(reds == (std::set<int>{64, 128, 191, 255}));
    // Untinted geometry writes no tint section.
    const doc::Result plain = eval_text(R"({"version":1,"nodes":[{"id":"a","type":"cube","options":{"size":[1,1,1]}}],"output":"a"})");
    CHECK(emesh::read(emesh::write(plain.geometry, emesh::WriteOptions{})).tints.empty());
}

TEST(doc_uv_charts_come_from_construction) {
    auto uv_range = [](const std::string& text) {
        const emesh::Pack pack = emesh::read(emesh::write(eval_text(text).geometry, emesh::WriteOptions{}));
        return pack.meshes.at(0).uv_scale;
    };
    // Lathe: round the axis at the widest radius, along the meridian (cap, wall, cap = 1 + 2 + 1).
    const auto lathe = uv_range(R"({"version":1,"nodes":[
      {"id":"v","type":"revolve","options":{"profile":[[0,0],[1,0],[1,2],[0,2]],"sides":24}}],"output":"v"})");
    CHECK_NEAR(lathe[0], 2 * std::numbers::pi, 1e-3);
    CHECK_NEAR(lathe[1], 4.0, 1e-4);
    // Tube: girth round, arc length along. A box projection would give at most the 0.2 m width across.
    const auto tube = uv_range(R"({"version":1,"nodes":[
      {"id":"t","type":"sweep","options":{"path":[[0,0,0],[6,0,0],[6,8,0]],"radius":0.1,"sides":8}}],"output":"t"})");
    CHECK_NEAR(tube[0], 2 * std::numbers::pi * 0.1, 1e-3);
    CHECK_NEAR(tube[1], 14.0, 1e-3);
    // Half-pipe patch: arc length round the curve (pi), not its 2 m chord.
    const auto pipe = uv_range(R"({"version":1,"nodes":[
      {"id":"p","type":"patch","options":{"point":[["cos",["*","@u",3.14159265]],["*",3,"@v"],["sin",["*","@u",3.14159265]]],"divisions":[32,1]}}],"output":"p"})");
    CHECK(pipe[0] > 3.13 && pipe[0] < 3.1417);
    CHECK_NEAR(pipe[1], 3.0, 1e-4);
    // A closed ring keeps every face in one piece across the seam: no triangle spans more than its own size in UV.
    const doc::Result ring = eval_text(R"({"version":1,"nodes":[
      {"id":"c","type":"curve","options":{"point":[["*",2,["cos",["*","@t",6.2831853]]],["*",2,["sin",["*","@t",6.2831853]]],0],"segments":24,"closed":1}},
      {"id":"t","type":"tube","inputs":["c"],"options":{"radius":0.1,"sides":8}}],"output":"t"})");
    const doc::Proto& proto = *ring.geometry.parts[0].proto;
    CHECK_EQ(proto.uv.size(), proto.mesh.corner_count());
    for (std::size_t f = 0; f < proto.mesh.face_count(); ++f) {
        const std::size_t off = proto.mesh.face_offsets[f], n = proto.mesh.face(f).size();
        float lo_u = 1e9f, hi_u = -1e9f, lo_v = 1e9f, hi_v = -1e9f;
        for (std::size_t i = 0; i < n; ++i) {
            lo_u = std::min(lo_u, proto.uv[off + i][0]); hi_u = std::max(hi_u, proto.uv[off + i][0]);
            lo_v = std::min(lo_v, proto.uv[off + i][1]); hi_v = std::max(hi_v, proto.uv[off + i][1]);
        }
        CHECK(hi_u - lo_u < 0.2f && hi_v - lo_v < 0.7f);
    }
    // Baking keeps charts, and gives the uncharted parts a box projection so the merged prototype is complete.
    const doc::Result baked = eval_text(R"({"version":1,"nodes":[
      {"id":"v","type":"revolve","options":{"profile":[[0,0],[1,0],[1,2],[0,2]],"sides":12}},
      {"id":"b","type":"cube","options":{"size":[1,1,1]}},
      {"id":"bt","type":"transform","inputs":["b"],"options":{"translation":[4,0,0]}},
      {"id":"j","type":"join","inputs":["v","bt"]},
      {"id":"k","type":"bake","inputs":["j"]}],"output":"k"})");
    CHECK_EQ(baked.geometry.parts.size(), std::size_t(1));
    CHECK_EQ(baked.geometry.parts[0].proto->uv.size(), baked.geometry.parts[0].proto->mesh.corner_count());
}

TEST(doc_details_lie_on_surfaces_and_layer) {
    // A 1 x 1 x 0.1 plate (top at z = 0.05) with a stamped boss, a rivet line and a seam.
    const char* plate = R"({"version":1,"nodes":[
      {"id":"plate","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0.3,0.3,0.2]]}},
      {"id":"boss","type":"detailStamp","inputs":["plate","at"],"options":{"radius":0.05,"height":0.01}},
      {"id":"row","type":"curve","options":{"point":[["-",["*","@t",0.8],0.4],0,0.1],"segments":1}},
      {"id":"rivets","type":"rivetLine","inputs":["boss","row"],"options":{"pitch":0.1,"radius":0.012,"height":0.005}},
      {"id":"cut","type":"curve","options":{"point":[-0.3,["-",["*","@t",0.8],0.4],0.1],"segments":1}},
      {"id":"out","type":"detailSeam","inputs":["rivets","cut"],"options":{"width":0.01,"depth":0.004,"slot":"iron"}}],"output":"out"})";
    const doc::Result r = eval_text(plate);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(1));
    const doc::Proto& proto = *r.geometry.parts[0].proto;
    CHECK(analyze(proto.mesh).clean());                                  // the mesh is untouched
    CHECK_EQ(proto.mesh.face_count(), std::size_t(6));
    std::size_t discs = 0, segments = 0;
    for (const doc::Detail& d : proto.details) {
        discs += d.shape == doc::Detail::Shape::Disc;
        segments += d.shape == doc::Detail::Shape::Segment;
        CHECK_NEAR(d.normal.z, 1.0, 1e-6);                               // everything landed on the top face
        CHECK_NEAR(d.origin.z, 0.05, 1e-6);
    }
    CHECK_EQ(discs, std::size_t(1 + 8));                                 // 0.8 m at a 0.1 m pitch, centred
    CHECK_EQ(segments, std::size_t(16));                                 // 0.8 m in 0.05 m steps
    const Vec3 up{0, 0, 1};
    auto height = [&](double x, double y) { return doc::sample_details(proto, {x, y, 0.05}, up).height; };
    CHECK_NEAR(height(0.3, 0.3), 0.01, 1e-6);                            // top of the dome
    CHECK_NEAR(height(0.325, 0.3), 0.01 * std::sqrt(0.75), 1e-6);        // half way out
    CHECK_NEAR(height(0.36, 0.3), 0.0, 1e-12);                           // off the boss
    CHECK_NEAR(height(0.05, 0.0), 0.005, 1e-6);                          // a rivet: half a pitch from the middle
    CHECK_NEAR(height(0.35, 0.0), 0.005, 1e-6);                          // the last one keeps half a pitch from the end
    CHECK_NEAR(height(0.0, 0.0), 0.0, 1e-12);                            // between rivets
    CHECK_NEAR(height(-0.3, 0.2), -0.004, 1e-6);                         // bottom of the groove
    CHECK(height(-0.3025, 0.2) < -0.002 && height(-0.3025, 0.2) > -0.004);
    CHECK_NEAR(height(-0.306, 0.2), 0.0, 1e-12);
    CHECK_EQ(doc::sample_details(proto, {-0.3, 0.2, 0.05}, up).material, doc::slot_id("iron"));   // the seam shows bare iron
    CHECK_EQ(doc::sample_details(proto, {0.05, 0.0, 0.05}, up).material, std::uint16_t(0xFFFF)); // rivets keep the plate's paint
    CHECK_NEAR(doc::sample_details(proto, {0.3, 0.3, -0.05}, {0, 0, -1}).height, 0.0, 1e-12);     // the underside is bare
    CHECK_NEAR(doc::sample_details(proto, {0.5, 0.3, 0.05}, {1, 0, 0}).height, 0.0, 1e-12);       // and so are the sides

    // A rivet on the floor of a recess: sunk and raised heights add; a flat mark has no height at all.
    const doc::Result layered = eval_text(R"({"version":1,"nodes":[
      {"id":"plate","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"mid","type":"pointArray","options":{"positions":[[0,0,0.06]]}},
      {"id":"recess","type":"detailStamp","inputs":["plate","mid"],"options":{"shape":"box","profile":"flat","size":[0.4,0.2],"height":-0.006,"fillet":0.004}},
      {"id":"rivet","type":"detailStamp","inputs":["recess","mid"],"options":{"radius":0.01,"height":0.004}},
      {"id":"ring-at","type":"pointArray","options":{"positions":[[0.3,0.3,0.06]]}},
      {"id":"bezel","type":"detailStamp","inputs":["rivet","ring-at"],"options":{"shape":"ring","profile":"flat","radius":0.05,"inner":0.03,"height":0.003,"fillet":0.002}},
      {"id":"mark-at","type":"pointArray","options":{"positions":[[-0.3,-0.3,0.06]]}},
      {"id":"out","type":"detailStamp","inputs":["bezel","mark-at"],"options":{"shape":"box","profile":"flat","size":[0.1,0.02],"height":0,"slot":"brass","tangent":[0,1,0]}}],"output":"out"})");
    const doc::Proto& lp = *layered.geometry.parts[0].proto;
    auto at = [&](double x, double y) { return doc::sample_details(lp, {x, y, 0.05}, up); };
    CHECK_NEAR(at(0.0, 0.0).height, -0.006 + 0.004, 1e-6);
    CHECK_NEAR(at(0.1, 0.0).height, -0.006, 1e-6);
    CHECK(at(0.199, 0.0).height > -0.002 && at(0.199, 0.0).height < 0);  // the rounded rim
    CHECK_NEAR(at(0.0, 0.11).height, 0.0, 1e-12);                        // 0.4 x 0.2: outside across the short side
    CHECK_NEAR(at(0.34, 0.3).height, 0.003, 1e-6);                       // on the bezel
    CHECK_NEAR(at(0.3, 0.3).height, 0.0, 1e-12);                         // in its bore
    CHECK_EQ(at(-0.3, -0.26).material, doc::slot_id("brass"));           // tangent [0,1,0]: the 0.1 x 0.02 mark runs along y
    CHECK_EQ(at(-0.26, -0.3).material, std::uint16_t(0xFFFF));
    CHECK_NEAR(at(-0.3, -0.26).height, 0.0, 1e-12);

    // Errors name what is wrong.
    auto message = [](const std::string& text) {
        try { (void)eval_text(text); } catch (const std::runtime_error& e) { return std::string(e.what()); }
        return std::string();
    };
    CHECK(message(R"({"version":1,"nodes":[{"id":"p","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"far","type":"pointArray","options":{"positions":[[0,0,5]]}},
      {"id":"d","type":"detailStamp","inputs":["p","far"],"options":{"radius":0.05}}],"output":"d"})").find("reach") != std::string::npos);
    CHECK(message(R"({"version":1,"nodes":[{"id":"p","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"d","type":"detailStamp","inputs":["p","at"],"options":{"shape":"star"}}],"output":"d"})").find("disc, ring or box") != std::string::npos);
    CHECK(message(R"({"version":1,"nodes":[{"id":"p","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"d","type":"detailStamp","inputs":["p","at"],"options":{"height":0}}],"output":"d"})").find("height") != std::string::npos);
}

TEST(doc_details_follow_instances_scale_and_bake) {
    // Details belong to the prototype: instances share them, and sizes given in metres land in
    // the prototype's own units when it is placed at a scale.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"stock","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"big","type":"transform","inputs":["stock"],"options":{"scale":[2,2,2]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0.5,0,0.2]]}},
      {"id":"plate","type":"detailStamp","inputs":["big","at"],"options":{"radius":0.05,"height":0.01}},
      {"id":"row-at","type":"line","options":{"start":[0,0,0],"step":[3,0,0],"count":3}},
      {"id":"row","type":"instance","inputs":["row-at","plate"]},
      {"id":"turned","type":"transform","inputs":["row"],"options":{"translation":[0,0,4],"rotation":[1.5707963,0,0]}},
      {"id":"out","type":"bake","inputs":["turned"]}],"output":"row"})");
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));
    CHECK(r.geometry.parts[0].proto.get() == r.geometry.parts[2].proto.get());
    const doc::Proto& proto = *r.geometry.parts[0].proto;
    CHECK_EQ(proto.details.size(), std::size_t(1));
    CHECK_NEAR(proto.details[0].size_a, 0.025, 1e-6);        // 0.05 m on a prototype drawn at twice its size
    CHECK_NEAR(proto.details[0].height, 0.005, 1e-6);
    CHECK_NEAR(proto.details[0].origin.x, 0.25, 1e-6);
    CHECK_NEAR(proto.details[0].origin.z, 0.05, 1e-6);

    // Baking merges the three plates into one prototype and carries the details with them, in metres.
    doc::Library library;
    json::Value document = json::parse(R"({"version":1,"nodes":[
      {"id":"stock","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"big","type":"transform","inputs":["stock"],"options":{"scale":[2,2,2]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0.5,0,0.2]]}},
      {"id":"plate","type":"detailStamp","inputs":["big","at"],"options":{"radius":0.05,"height":0.01}},
      {"id":"row-at","type":"line","options":{"start":[0,0,0],"step":[3,0,0],"count":3}},
      {"id":"row","type":"instance","inputs":["row-at","plate"]},
      {"id":"turned","type":"transform","inputs":["row"],"options":{"translation":[0,0,4],"rotation":[1.5707963,0,0]}},
      {"id":"out","type":"bake","inputs":["turned"]}],"output":"out"})");
    const doc::Result baked = doc::evaluate(document, library, {});
    CHECK_EQ(baked.geometry.parts.size(), std::size_t(1));
    const doc::Proto& merged = *baked.geometry.parts[0].proto;
    CHECK_EQ(merged.details.size(), std::size_t(3));
    // Turned a quarter about X: the plate's top (z = 0.1) now faces -Y at y = -0.1, 4 m up.
    for (int k = 0; k < 3; ++k) {
        const doc::DetailSample s = doc::sample_details(merged, {0.5 + 3.0 * k, -0.1, 4.0}, {0, -1, 0});
        CHECK_NEAR(s.height, 0.01, 1e-6);
    }
    CHECK_NEAR(merged.details[0].size_a, 0.05, 1e-6);
    CHECK_NEAR(doc::sample_details(merged, {0.5, -0.1, 4.2}, {0, -1, 0}).height, 0.0, 1e-12);
}

TEST(doc_detail_index_matches_the_full_scan) {
    // 21 x 21 rivets, a recess far larger than they are, and a seam across them all.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"plate","type":"cube","options":{"size":[2,2,0.1]}},
      {"id":"mid","type":"pointArray","options":{"positions":[[0,0,0.06]]}},
      {"id":"recess","type":"detailStamp","inputs":["plate","mid"],"options":{"shape":"disc","profile":"flat","radius":0.6,"height":-0.004,"fillet":0.003}},
      {"id":"flat","type":"pointField","options":{"size":[1.8,1.8],"spacing":0.09}},
      {"id":"cells","type":"transform","inputs":["flat"],"options":{"translation":[0,0,0.06]}},
      {"id":"field","type":"detailStamp","inputs":["recess","cells"],"options":{"radius":0.012,"height":0.004}},
      {"id":"cut","type":"curve","options":{"point":[["-",["*","@t",1.8],0.9],["*",0.3,["sin",["*","@t",6.2831853]]],0.06],"segments":48}},
      {"id":"out","type":"detailSeam","inputs":["field","cut"],"options":{"width":0.008,"depth":0.003}}],"output":"out"})");
    const doc::Proto& proto = *r.geometry.parts[0].proto;
    std::size_t rivets = 0;
    for (const doc::Detail& d : proto.details) rivets += d.shape == doc::Detail::Shape::Disc && d.profile == doc::Detail::Profile::Dome;
    CHECK_EQ(rivets, std::size_t(441));
    // A hex field in an annulus: every point inside it, neighbours one spacing apart.
    const doc::Result hex = eval_text(R"({"version":1,"nodes":[
      {"id":"f","type":"pointField","options":{"layout":"hex","spacing":0.05,"radius":0.3,"inner":0.12}}],"output":"f"})");
    CHECK(hex.geometry.points.size() > 80 && hex.geometry.points.size() < 110);   // area / cell = 0.2375 / 0.002165 = 110 at most
    double nearest = 1e9;
    for (const Vec3& a : hex.geometry.points) {
        const double radial = std::hypot(a.x, a.y);
        CHECK(radial <= 0.3 + 1e-6 && radial >= 0.12 - 1e-6);
        for (const Vec3& b : hex.geometry.points)
            if (&a != &b) nearest = std::min(nearest, length(a - b));
    }
    CHECK_NEAR(nearest, 0.05, 1e-6);
    const doc::DetailIndex index(proto.details);
    std::uint32_t state = 12345;
    auto rnd = [&] { state = state * 1664525u + 1013904223u; return (state >> 8) / 16777216.0; };
    std::size_t raised = 0, sunk = 0;
    for (int i = 0; i < 20000; ++i) {
        const Vec3 p{rnd() * 2 - 1, rnd() * 2 - 1, 0.05};
        const doc::DetailSample a = doc::sample_details(proto, p, {0, 0, 1}), b = index.sample(p, {0, 0, 1});
        CHECK_NEAR(a.height, b.height, 1e-12);
        CHECK_EQ(a.material, b.material);
        raised += a.height > 0;
        sunk += a.height < 0;
    }
    CHECK(raised > 100 && sunk > 3000);                                   // the test looked at all three kinds
}

TEST(emesh_carries_details_in_mesh_space) {
    const char* text = R"({"version":1,"nodes":[
      {"id":"plate","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"at","type":"pointArray","options":{"positions":[[0.25,0.1,0.2]]}},
      {"id":"boss","type":"detailStamp","inputs":["plate","at"],"options":{"radius":0.05,"height":0.01}},
      {"id":"cut","type":"curve","options":{"point":[-0.3,["-",["*","@t",0.2],0.1],0.1],"segments":1}},
      {"id":"seamed","type":"detailSeam","inputs":["boss","cut"],"options":{"width":0.01,"depth":0.004,"step":0.2,"slot":"brass"}},
      {"id":"twin","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"moved","type":"transform","inputs":["twin"],"options":{"translation":[3,0,0]}},
      {"id":"painted","type":"material","inputs":["seamed"],"options":{"slot":"iron"}},
      {"id":"painted-twin","type":"material","inputs":["moved"],"options":{"slot":"iron"}},
      {"id":"out","type":"join","inputs":["painted","painted-twin"]}],"output":"out"})";
    const doc::Result r = eval_text(text);
    // Engine axes without the canonical frame: (x, y, z) -> (x, z, -y), so the top face looks along +Y.
    emesh::WriteOptions plain;
    plain.dedupe_congruent = false;
    emesh::WriteStats st;
    const emesh::Pack pack = emesh::read(emesh::write(r.geometry, plain, &st));
    CHECK_EQ(st.details, std::size_t(2));
    CHECK_EQ(pack.details.size(), std::size_t(2));
    const doc::Detail& boss = pack.details[0].detail;
    CHECK(boss.shape == doc::Detail::Shape::Disc && boss.profile == doc::Detail::Profile::Dome);
    CHECK_NEAR(boss.origin.x, 0.25, 1e-6);
    CHECK_NEAR(boss.origin.y, 0.05, 1e-6);
    CHECK_NEAR(boss.origin.z, -0.1, 1e-6);
    CHECK_NEAR(boss.normal.y, 1.0, 1e-6);
    CHECK_NEAR(boss.size_a, 0.05, 1e-6);
    CHECK_NEAR(boss.height, 0.01, 1e-6);
    const doc::Detail& seam = pack.details[1].detail;
    CHECK(seam.shape == doc::Detail::Shape::Segment && seam.profile == doc::Detail::Profile::Groove);
    CHECK_NEAR(seam.origin.z, 0.1, 1e-6);                                // y = -0.1 in the document
    CHECK_NEAR(seam.end.z, -0.1, 1e-6);
    CHECK_NEAR(seam.height, -0.004, 1e-6);
    CHECK_EQ(seam.material, doc::slot_id("brass"));
    // The detail's slot is named for the material pack even though no mesh uses it.
    bool brass = false;
    for (const auto& slot : pack.material_slots) brass = brass || slot.name_hash == emesh::slot_name_hash("brass");
    CHECK(brass);
    // The plain twin is the same shape but must stay its own mesh: it has no details.
    const emesh::Pack deduped = emesh::read(emesh::write(r.geometry, emesh::WriteOptions{}));
    CHECK_EQ(deduped.meshes.size(), std::size_t(2));
    CHECK_EQ(deduped.details.size(), std::size_t(2));
    // In the canonical frame the detail still sits on its mesh: sampling at its own origin gives its height.
    std::vector<doc::Detail> of_mesh;
    for (const auto& d : deduped.details) of_mesh.push_back(d.detail);
    CHECK_NEAR(doc::sample_details(of_mesh, of_mesh[0].origin, of_mesh[0].normal).height, 0.01, 1e-6);
    // Two bare twins do merge, and a pack without details has no detail section.
    const doc::Result bare = eval_text(R"({"version":1,"nodes":[
      {"id":"a","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"b","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"bt","type":"transform","inputs":["b"],"options":{"translation":[3,0,0]}},
      {"id":"out","type":"join","inputs":["a","bt"]}],"output":"out"})");
    const emesh::Pack bare_pack = emesh::read(emesh::write(bare.geometry, emesh::WriteOptions{}));
    CHECK_EQ(bare_pack.meshes.size(), std::size_t(1));
    CHECK(bare_pack.details.empty());
}

TEST(doc_rivet_heads_keep_clear_of_seams) {
    // A row along x (heads at +-0.05, +-0.15, +-0.25, +-0.35) crossed by a seam at x = 0.05.
    auto heads = [](const std::string& nodes, const char* output) {
        const doc::Result r = eval_text(R"({"version":1,"nodes":[
          {"id":"plate","type":"cube","options":{"size":[1,1,0.01]}},
          {"id":"row","type":"line","options":{"start":[-0.4,0,0.1],"step":[0.8,0,0],"count":2}},
          {"id":"cut","type":"line","options":{"start":[0.05,-0.4,0.1],"step":[0,0.8,0],"count":2}},
          {"id":"under","type":"line","options":{"start":[0.05,-0.4,-0.1],"step":[0,0.8,0],"count":2}},
          {"id":"col","type":"line","options":{"start":[0.35,-0.35,0.1],"step":[0,0.7,0],"count":2}},)" + nodes + R"(],"output":")" + output + R"("})");
        std::vector<Vec3> at;
        for (const doc::Detail& d : r.geometry.parts[0].proto->details)
            if (d.fastener) at.push_back(d.origin);
        return at;
    };
    const char* rivets = R"({"id":"R","type":"rivetLine","inputs":["SURFACE","row"],"options":{"pitch":0.1,"radius":0.012,"height":0.005CLEAR}})";
    const char* seam = R"({"id":"S","type":"detailSeam","inputs":["SURFACE","cut"],"options":{"width":0.01,"depth":0.004}})";
    auto node = [](const std::string& text, const std::string& surface, const std::string& clear = "") {
        std::string out;  // the template with SURFACE and CLEAR filled in
        for (std::size_t i = 0; i < text.size();) {
            if (text.compare(i, 7, "SURFACE") == 0) { out += surface; i += 7; }
            else if (text.compare(i, 5, "CLEAR") == 0) { out += clear; i += 5; }
            else out += text[i++];
        }
        return out;
    };
    CHECK_EQ(heads(node(rivets, "plate"), "R").size(), std::size_t(8));
    // Seam first, then the row: the head that would sit on the seam is left out.
    const auto after = heads(node(seam, "plate") + "," + node(rivets, "S"), "R");
    CHECK_EQ(after.size(), std::size_t(7));
    for (const Vec3& p : after) CHECK(std::abs(p.x - 0.05) > 0.012 + 0.005);
    // Row first, then the seam: the same seven heads remain.
    const auto before = heads(node(rivets, "plate") + "," + node(seam, "R"), "S");
    CHECK_EQ(before.size(), std::size_t(7));
    for (std::size_t i = 0; i < 7; ++i) CHECK_NEAR(before[i].x, after[i].x, 1e-9);
    // A wider clearance (edge to edge) takes the neighbours too: they stand 0.083 m from the groove.
    CHECK_EQ(heads(node(seam, "plate") + "," + node(rivets, "S", R"(,"clearance":0.06)"), "R").size(), std::size_t(7));
    CHECK_EQ(heads(node(seam, "plate") + "," + node(rivets, "S", R"(,"clearance":0.09)"), "R").size(), std::size_t(5));
    // A seam on the other face of a thin plate is not this face's business.
    CHECK_EQ(heads(R"({"id":"S","type":"detailSeam","inputs":["plate","under"],"options":{"width":0.01,"depth":0.004}},)" + node(rivets, "S"), "R").size(), std::size_t(8));
    // Rows meeting at a head share it: the second row leaves its copy out.
    CHECK_EQ(heads(node(rivets, "plate") + R"(,{"id":"C","type":"rivetLine","inputs":["R","col"],"options":{"pitch":0.1,"radius":0.012,"height":0.005}})", "C").size(),
             std::size_t(8 + 6));
    // Explicit stamps are the author's own placement: they stay where they are put.
    const doc::Result stamped = eval_text(R"({"version":1,"nodes":[
      {"id":"plate","type":"cube","options":{"size":[1,1,0.01]}},
      {"id":"cut","type":"line","options":{"start":[0.05,-0.4,0.1],"step":[0,0.8,0],"count":2}},
      {"id":"at","type":"pointArray","options":{"positions":[[0.05,0,0.1]]}},
      {"id":"boss","type":"detailStamp","inputs":["plate","at"],"options":{"radius":0.03,"height":0.004}},
      {"id":"S","type":"detailSeam","inputs":["boss","cut"],"options":{"width":0.01,"depth":0.004}}],"output":"S"})");
    std::size_t discs = 0;
    for (const doc::Detail& d : stamped.geometry.parts[0].proto->details) discs += d.shape == doc::Detail::Shape::Disc;
    CHECK_EQ(discs, std::size_t(1));
}

TEST(doc_details_are_the_same_however_the_surface_is_searched) {
    if (!std::filesystem::exists(std::string(PG_RECIPES) + "/site/rolling-stock")) return;  // kit not shipped in this repo
    // The nearest surface point is found by scanning every triangle or from an index (pg/closest.hpp);
    // left alone, the generators scan for the first points on a part and build the index after that.
    // All three must place every detail on the same triangle at the same point, bit for bit.
    struct Restore {
        ~Restore() { doc::set_projection_search(doc::ProjectionSearch::Auto); }
    } restore;
    auto record = [](const doc::Geometry& g) {
        std::vector<std::uint8_t> bytes = emesh::write(g, emesh::WriteOptions{});
        auto put = [&](const void* data, std::size_t n) {
            const auto* b = static_cast<const std::uint8_t*>(data);
            bytes.insert(bytes.end(), b, b + n);
        };
        for (const doc::Part& part : g.parts)
            for (const doc::Detail& d : part.proto->details) {
                const double v[] = {d.origin.x, d.origin.y, d.origin.z, d.normal.x, d.normal.y, d.normal.z, d.tangent.x, d.tangent.y, d.tangent.z,
                                    d.end.x,    d.end.y,    d.end.z,    d.size_a,   d.size_b,   d.height,   d.fillet};
                put(v, sizeof v);  // the pack keeps single precision; the document's own values must agree too
                const std::uint16_t tags[] = {static_cast<std::uint16_t>(d.shape), static_cast<std::uint16_t>(d.profile), d.material, d.fastener};
                put(tags, sizeof tags);
            }
        return bytes;
    };
    std::size_t details = 0;
    auto agree = [&](const std::function<doc::Geometry()>& evaluate) {
        std::vector<std::vector<std::uint8_t>> got;
        doc::Geometry last;
        for (const doc::ProjectionSearch mode : {doc::ProjectionSearch::Scan, doc::ProjectionSearch::Index, doc::ProjectionSearch::Auto}) {
            doc::set_projection_search(mode);
            last = evaluate();
            got.push_back(record(last));
        }
        CHECK(got[0] == got[1]);
        CHECK(got[0] == got[2]);
        for (const doc::Part& part : last.parts) details += part.proto->details.size();
        return last;
    };
    // The kits that carry details: the locomotive casing, the tender, the wheels, the plating.
    for (const char* name : {"site/rolling-stock/atlantic-shell.json", "site/rolling-stock/atlantic-tender.json", "site/rolling-stock/atlantic-driver-pair.json",
                             "site/plating/inspection-plate.json", "site/plating/riveted-panel.json"})
        agree([&] { return doc::evaluate_file(std::string(PG_RECIPES) + "/" + name).geometry; });
    CHECK(details > 2500);

    // A turned, subdivided part placed twice at different scales, with a seam and a row round each:
    // curved faces, many triangles nearly as close as the nearest, and a prototype shared by two parts.
    const doc::Geometry turned = agree([] {
        return eval_text(R"({"version":1,"nodes":[
          {"id":"lathe","type":"revolve","options":{"profile":[[0,0],[0.5,0],[0.7,0.5],[0.5,1.0],[0.2,1.3],[0,1.3]],"sides":20}},
          {"id":"fine","type":"subdivide","inputs":["lathe"],"options":{"levels":2}},
          {"id":"at","type":"pointArray","options":{"positions":[[0,0,0],[2.5,0,0]]}},
          {"id":"pair","type":"instance","inputs":["at","fine"]},
          {"id":"big","type":"transform","inputs":["fine"],"options":{"translation":[0,3,0],"scale":[1.7,1.7,1.7]}},
          {"id":"all","type":"join","inputs":["pair","big"]},
          {"id":"hoop","type":"curve","options":{"point":[["*",0.8,["cos",["*","@t",6.2831853]]],["*",0.8,["sin",["*","@t",6.2831853]]],["+",0.3,["*",0.5,"@t"]]],"segments":96}},
          {"id":"hoops","type":"instance","inputs":["at","hoop"]},
          {"id":"wide","type":"curve","options":{"point":[["*",1.3,["cos",["*","@t",6.2831853]]],["+",3,["*",1.3,["sin",["*","@t",6.2831853]]]],0.9],"segments":96,"closed":1}},
          {"id":"seamed","type":"detailSeam","inputs":["all","hoops"],"options":{"width":0.01,"depth":0.004,"step":0.03}},
          {"id":"seamed2","type":"detailSeam","inputs":["seamed","wide"],"options":{"width":0.01,"depth":0.004,"step":0.03}},
          {"id":"rows","type":"rivetLine","inputs":["seamed2","hoops"],"options":{"pitch":0.03,"radius":0.006,"height":0.003,"clearance":0}},
          {"id":"out","type":"rivetLine","inputs":["rows","wide"],"options":{"pitch":0.03,"radius":0.006,"height":0.003,"clearance":0}}],"output":"out"})").geometry;
    });
    CHECK_EQ(turned.parts.size(), std::size_t(3));
    CHECK(turned.parts[0].proto.get() == turned.parts[1].proto.get());   // the instanced pair still shares its prototype
    CHECK(turned.parts[0].proto->details.size() > 150 && turned.parts[2].proto->details.size() > 150);

    // A point exactly half way between two plates lands on the one that comes first, every time.
    const doc::Geometry between = agree([] {
        return eval_text(R"({"version":1,"nodes":[
          {"id":"low","type":"cube","options":{"size":[1,1,0.1]}},
          {"id":"high","type":"cube","options":{"size":[1,1,0.1]}},
          {"id":"lifted","type":"transform","inputs":["high"],"options":{"translation":[0,0,1]}},
          {"id":"both","type":"join","inputs":["low","lifted"]},
          {"id":"at","type":"pointArray","options":{"positions":[[0.1,0.2,0.5],[0.3,-0.2,0.5],[-0.25,-0.25,0.5]]}},
          {"id":"out","type":"detailStamp","inputs":["both","at"],"options":{"radius":0.02,"height":0.004,"reach":0.6}}],"output":"out"})").geometry;
    });
    CHECK_EQ(between.parts[0].proto->details.size(), std::size_t(3));
    CHECK_EQ(between.parts[1].proto->details.size(), std::size_t(0));
}

TEST(plating_kit_rivets_every_sheet_clear_of_its_laps) {
    if (!std::filesystem::exists(std::string(PG_RECIPES) + "/site/plating")) return;  // kit not shipped in this repo
    doc::Library library(std::string(PG_RECIPES) + "/site/plating");
    const json::Value panel = json::parse(R"({"version":1,"moduleImports":{"panel":"riveted-panel"},"nodes":[
      {"id":"out","type":"group","group":"panel","bindings":{"width":"$width","height":"$height","pitch":"$pitch","lap":"$lap"}}],
      "controls":{"width":{"value":2.4,"min":0.4,"max":12},"height":{"value":1.2,"min":0.4,"max":6},
                  "pitch":{"value":0.08,"min":0.04,"max":0.4},"lap":{"value":1.2,"min":0.4,"max":6}},"output":"out"})");
    std::size_t last_heads = 0;
    for (const doc::Controls& set : {doc::Controls{{"width", 0.8f}}, doc::Controls{{"width", 2.4f}}, doc::Controls{{"width", 3.1f}, {"pitch", 0.05f}},
                                     doc::Controls{{"width", 6.0f}, {"height", 2.0f}, {"lap", 0.9f}, {"pitch", 0.11f}}}) {
        const doc::Result r = doc::evaluate(panel, library, set);
        CHECK_EQ(r.geometry.parts.size(), std::size_t(1));                 // one sheet of metal: the plating is detail
        const doc::Proto& proto = *r.geometry.parts[0].proto;
        CHECK_EQ(proto.mesh.face_count(), std::size_t(6));
        std::size_t heads = 0, seams = 0;
        double nearest = 1e9;
        for (const doc::Detail& head : proto.details) {
            if (!head.fastener) { seams += head.shape == doc::Detail::Shape::Segment; continue; }
            ++heads;
            for (const doc::Detail& seam : proto.details) {
                if (seam.shape != doc::Detail::Shape::Segment) continue;
                const Vec3 axis = seam.end - seam.origin;
                const double t = std::clamp(dot(head.origin - seam.origin, axis) / dot(axis, axis), 0.0, 1.0);
                nearest = std::min(nearest, length(head.origin - (seam.origin + axis * t)) - head.size_a - seam.size_a);
            }
        }
        const float width = set.at("width"), lap = set.count("lap") ? set.at("lap") : 1.2f;
        const bool lapped = std::lround(width / lap) > 1;
        CHECK(heads > 20);
        CHECK(lapped == (seams > 0));
        if (lapped) CHECK(nearest > 0.011);                                 // every head at least its own radius from a groove
        if (width > 2.0f) CHECK(heads > last_heads);
        last_heads = heads;
    }
}

TEST(emesh_1_3_tints_in_the_engine_section_and_open_meshes_flagged) {
    // A tinted row of closed boxes next to an open sheet.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"box","type":"cube","options":{"size":[1,1,1]}},
      {"id":"at","type":"line","options":{"start":[0,0,0],"step":[2,0,0],"count":3}},
      {"id":"row","type":"instance","inputs":["at","box"],"options":{"tint":[["-",1,["*","@index",0.5]],1,1]}},
      {"id":"sheet","type":"grid","options":{"size":[4,4,0],"divisions":[3,3,1]}},
      {"id":"canopy","type":"transform","inputs":["sheet"],"options":{"translation":[2,0,3]}},
      {"id":"out","type":"join","inputs":["row","canopy"]}],"output":"out"})");
    emesh::WriteStats st;
    const std::vector<std::uint8_t> bytes = emesh::write(r.geometry, emesh::WriteOptions{}, &st);
    // Header: magic, then version 1.3.
    std::uint16_t major = 0, minor = 0;
    std::memcpy(&major, bytes.data() + 4, 2);
    std::memcpy(&minor, bytes.data() + 6, 2);
    CHECK_EQ(int(major), 1);
    CHECK_EQ(int(minor), 3);
    // Section table (24-byte entries after the 80-byte header): tints are engine section 15, four bytes per instance.
    std::uint32_t section_count = 0;
    std::memcpy(&section_count, bytes.data() + 16, 4);
    bool engine_tints = false, tool_tints = false;
    for (std::uint32_t i = 0; i < section_count; ++i) {
        std::uint32_t kind = 0, count = 0;
        std::uint16_t element = 0;
        std::memcpy(&kind, bytes.data() + 80 + i * 24, 4);
        std::memcpy(&element, bytes.data() + 80 + i * 24 + 6, 2);
        std::memcpy(&count, bytes.data() + 80 + i * 24 + 20, 4);
        if (kind == 15) engine_tints = element == 4 && count == 4;      // three boxes and the sheet
        tool_tints = tool_tints || kind == 0x8006;
    }
    CHECK(engine_tints);
    CHECK(!tool_tints);
    const emesh::Pack pack = emesh::read(bytes);
    CHECK_EQ(pack.tints.size(), std::size_t(4));
    std::set<int> reds;
    for (const auto& tint : pack.tints) { reds.insert(tint[0]); CHECK_EQ(int(tint[3]), 255); }
    CHECK(reds == (std::set<int>{0, 128, 255}));
    // The box is a closed solid; the sheet has an edge and must not be culled.
    CHECK_EQ(pack.meshes.size(), std::size_t(2));
    CHECK_EQ(st.two_sided_meshes, std::size_t(1));
    std::size_t open = 0;
    for (const auto& mesh : pack.meshes) open += mesh.two_sided;
    CHECK_EQ(open, std::size_t(1));
}

namespace {
// A 1 x 1 x 0.1 plate (top at z = 0.05) followed by the given nodes; returns the output's first prototype.
doc::ProtoPtr detailed_plate(const std::string& nodes, const std::string& output) {
    const doc::Result r = eval_text(R"({"version":1,"nodes":[{"id":"plate","type":"cube","options":{"size":[1,1,0.1]}},)" + nodes + R"(],"output":")" + output + R"("})");
    return r.geometry.parts.at(0).proto;
}
std::string detail_error(const std::string& nodes, const std::string& output) {
    try { (void)detailed_plate(nodes, output); } catch (const std::runtime_error& e) { return e.what(); }
    return {};
}
}  // namespace

TEST(doc_detail_shapes_and_profiles) {
    const doc::ProtoPtr proto = detailed_plate(R"(
      {"id":"p1","type":"pointArray","options":{"positions":[[-0.3,0.3,0.1]]}},
      {"id":"hex","type":"detailStamp","inputs":["plate","p1"],"options":{"shape":"polygon","sides":6,"profile":"flat","radius":0.05,"height":0.01,"fillet":0.001}},
      {"id":"p2","type":"pointArray","options":{"positions":[[0.3,0.3,0.1]]}},
      {"id":"cross","type":"detailStamp","inputs":["hex","p2"],"options":{"shape":"cross","profile":"flat","radius":0.05,"width":0.02,"height":-0.004,"fillet":0.001}},
      {"id":"p3","type":"pointArray","options":{"positions":[[-0.3,-0.3,0.1]]}},
      {"id":"hatch","type":"detailStamp","inputs":["cross","p3"],"options":{"shape":"box","profile":"flat","size":[0.2,0.1],"corner":0.03,"height":0.004,"fillet":0.001}},
      {"id":"p4","type":"pointArray","options":{"positions":[[0.3,-0.3,0.1]]}},
      {"id":"stud","type":"detailStamp","inputs":["hatch","p4"],"options":{"shape":"polygon","sides":4,"profile":"cone","radius":0.05,"height":0.01}},
      {"id":"p5","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"blade","type":"detailStamp","inputs":["stud","p5"],"options":{"shape":"box","profile":"ramp","size":[0.2,0.1],"height":0.01,"fillet":0.0005}})", "blade");
    const Vec3 up{0, 0, 1};
    auto h = [&](double x, double y) { return doc::sample_details(*proto, {x, y, 0.05}, up).height; };
    // Hexagon, a flat facing the tangent: 0.05 to the flats along x, 0.0577 to the corners along y.
    CHECK_NEAR(h(-0.3 + 0.045, 0.3), 0.01, 1e-6);
    CHECK_NEAR(h(-0.3 + 0.053, 0.3), 0.0, 1e-12);
    CHECK_NEAR(h(-0.3, 0.3 + 0.053), 0.01, 1e-6);
    CHECK_NEAR(h(-0.3, 0.3 + 0.059), 0.0, 1e-12);
    // Cross: sunk along both arms, plain in the quadrants between them.
    CHECK_NEAR(h(0.3 + 0.04, 0.3), -0.004, 1e-6);
    CHECK_NEAR(h(0.3, 0.3 - 0.04), -0.004, 1e-6);
    CHECK_NEAR(h(0.3 + 0.03, 0.3 + 0.03), 0.0, 1e-12);
    // Rounded box: the square corner is cut away.
    CHECK_NEAR(h(-0.3 + 0.09, -0.3), 0.004, 1e-6);
    CHECK_NEAR(h(-0.3 + 0.098, -0.3 + 0.048), 0.0, 1e-12);
    // Cone: straight slope from the edge to the middle of a square stud.
    CHECK_NEAR(h(0.3, -0.3), 0.01, 1e-6);
    CHECK_NEAR(h(0.3 + 0.025, -0.3), 0.005, 1e-6);
    // Ramp: a louvre blade rises across its short side.
    CHECK_NEAR(h(0, 0), 0.005, 1e-6);
    CHECK_NEAR(h(0, 0.045), 0.0095, 1e-6);
    CHECK_NEAR(h(0, -0.045), 0.0005, 1e-6);
    CHECK(detail_error(R"({"id":"p","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"d","type":"detailStamp","inputs":["plate","p"],"options":{"shape":"cross","radius":0.01,"width":0.05}})", "d").find("arm width") != std::string::npos);
    // A stamp may be turned about the normal per point: alternate tread bars at right angles.
    const doc::ProtoPtr tread = detailed_plate(R"(
      {"id":"at","type":"pointArray","options":{"positions":[[-0.1,0,0.1],[0.1,0,0.1]]}},
      {"id":"bars","type":"detailStamp","inputs":["plate","at"],"options":{"shape":"box","profile":"dome","size":[0.08,0.02],"height":0.003,
         "angle":["*",["mod","@index",2],1.5707963]}})", "bars");
    CHECK(doc::sample_details(*tread, {-0.1 + 0.03, 0, 0.05}, up).height > 0);    // the first lies along x
    CHECK_NEAR(doc::sample_details(*tread, {-0.1, 0.03, 0.05}, up).height, 0.0, 1e-12);
    CHECK(doc::sample_details(*tread, {0.1, 0.03, 0.05}, up).height > 0);         // the second along y
    CHECK_NEAR(doc::sample_details(*tread, {0.1 + 0.03, 0, 0.05}, up).height, 0.0, 1e-12);
}

TEST(doc_fasteners_have_heads_washers_and_drives) {
    const Vec3 up{0, 0, 1};
    auto heads = [](const doc::Proto& p) {
        std::vector<Vec3> at;
        for (const doc::Detail& d : p.details)
            if (d.fastener && (at.empty() || length(at.back() - d.origin) > 1e-9)) at.push_back(d.origin);
        return at;
    };
    // Hex bolt on a washer; a slotted and a cross-head screw, drives aligned with x.
    const doc::ProtoPtr bolts = detailed_plate(R"(
      {"id":"a","type":"pointArray","options":{"positions":[[-0.3,0,0.1]]}},
      {"id":"hex","type":"fastener","inputs":["plate","a"],"options":{"head":"hex","radius":0.02,"height":0.01,"washer":0.03,"turn":"aligned"}},
      {"id":"b","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"slotted","type":"fastener","inputs":["hex","b"],"options":{"head":"slotted","radius":0.02,"height":0.01,"turn":"aligned"}},
      {"id":"c","type":"pointArray","options":{"positions":[[0.3,0,0.1]]}},
      {"id":"cross","type":"fastener","inputs":["slotted","c"],"options":{"head":"cross","radius":0.02,"height":0.01,"turn":"aligned"}})", "cross");
    auto h = [&](double x, double y) { return doc::sample_details(*bolts, {x, y, 0.05}, up).height; };
    CHECK_NEAR(h(-0.3, 0), 0.01, 1e-6);                     // on the head
    CHECK_NEAR(h(-0.3 + 0.026, 0), 0.0025, 1e-6);           // on the washer round it
    CHECK_NEAR(h(-0.3 + 0.031, 0), 0.0, 1e-12);
    CHECK_NEAR(h(0, 0), 0.005, 1e-6);                       // the slot is cut half way down the dome
    CHECK_NEAR(h(0.012, 0), 0.01 * std::sqrt(1 - 0.36) - 0.005, 1e-6);   // and runs along x
    CHECK_NEAR(h(0, 0.01), 0.01 * std::sqrt(0.75), 1e-6);   // beside it the dome is whole
    CHECK_NEAR(h(0.3, 0), 0.005, 1e-6);                     // the cross is cut along x and y
    CHECK_NEAR(h(0.3, 0.008), 0.01 * std::sqrt(1 - 0.16) - 0.005, 1e-6);
    CHECK_NEAR(h(0.3 + 0.008, 0.008), 0.01 * std::sqrt(1 - 0.32), 1e-6);
    CHECK_EQ(heads(*bolts).size(), std::size_t(3));
    CHECK(detail_error(R"({"id":"a","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"f","type":"fastener","inputs":["plate","a"],"options":{"head":"torx"}})", "f").find("slotted or cross") != std::string::npos);

    // Screws are clocked at random, the same way every time.
    const char* row = R"({"id":"l","type":"line","options":{"start":[-0.4,0,0.1],"step":[0.8,0,0],"count":2}},
      {"id":"f","type":"fastener","inputs":["plate","l"],"options":{"head":"slotted","radius":0.01,"pitch":0.1,"seed":7}})";
    const doc::ProtoPtr one = detailed_plate(row, "f"), two = detailed_plate(row, "f");
    std::set<long long> angles;
    for (std::size_t i = 0; i < one->details.size(); ++i) {
        CHECK_NEAR(one->details[i].tangent.x, two->details[i].tangent.x, 1e-12);
        if (one->details[i].shape == doc::Detail::Shape::Box) angles.insert(std::llround(std::atan2(one->details[i].tangent.y, one->details[i].tangent.x) * 100));
    }
    CHECK(angles.size() >= 6);                              // eight screws, hardly two alike

    // A bolt circle: eight heads evenly round an outline, the first on +x.
    const doc::ProtoPtr ring = detailed_plate(R"(
      {"id":"flat","type":"outline","options":{"shape":"circle","radius":0.3}},
      {"id":"circle","type":"transform","inputs":["flat"],"options":{"translation":[0,0,0.1]}},
      {"id":"f","type":"fastener","inputs":["plate","circle"],"options":{"head":"hex","radius":0.015,"count":8}})", "f");
    const std::vector<Vec3> round = heads(*ring);
    CHECK_EQ(round.size(), std::size_t(8));
    CHECK_NEAR(round[0].x, 0.3, 1e-6);
    CHECK_NEAR(round[2].y, 0.3, 1e-4);
    for (const Vec3& p : round) CHECK_NEAR(std::hypot(p.x, p.y), 0.3, 1e-3);

    // Heads sit on a flat band and stand on it; a bead is a joint and they give way to it.
    const char* band = R"({"id":"l","type":"line","options":{"start":[-0.4,0,0.1],"step":[0.8,0,0],"count":2}},
      {"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"PROFILE","width":0.06,"height":0.004}},
      {"id":"f","type":"rivetLine","inputs":["s","l"],"options":{"pitch":0.1,"radius":0.01,"height":0.005}})";
    auto with = [&](const char* profile) {
        std::string text = band;
        text.replace(text.find("PROFILE"), 7, profile);
        return detailed_plate(text, "f");
    };
    const doc::ProtoPtr strapped = with("band");
    CHECK_EQ(heads(*strapped).size(), std::size_t(8));
    CHECK_NEAR(doc::sample_details(*strapped, {0.05, 0, 0.05}, up).height, 0.004 + 0.005, 1e-6);
    CHECK_NEAR(doc::sample_details(*strapped, {0.0, 0, 0.05}, up).height, 0.004, 1e-6);
    CHECK_EQ(heads(*with("bead")).size(), std::size_t(0));

    // A lapped plate carries its rivets on the lap; only the lap's edge is the joint. The lap is 40 mm
    // wide along x, its edge on the +y side (y = 0.02).
    const char* lapped = R"({"id":"l","type":"line","options":{"start":[-0.4,0,0.1],"step":[0.8,0,0],"count":2}},
      {"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"lap","width":0.04,"height":0.004}},
      {"id":"r","type":"line","options":{"start":[-0.4,OFFSET,0.1],"step":[0.8,0,0],"count":2}},
      {"id":"f","type":"rivetLine","inputs":["s","r"],"options":{"pitch":0.1,"radius":0.006,"height":0.003}})";
    auto on_lap = [&](const char* offset) {
        std::string text = lapped;
        text.replace(text.find("OFFSET"), 6, offset);
        return heads(*detailed_plate(text, "f")).size();
    };
    CHECK_EQ(on_lap("0"), std::size_t(8));          // mid-lap: 20 mm from the edge
    CHECK_EQ(on_lap("0.015"), std::size_t(0));      // 5 mm from the edge
    CHECK_EQ(on_lap("0.04"), std::size_t(8));       // on the plate beyond the edge, 20 mm clear

    // The clearance counts from the washer's edge.
    const char* near_seam = R"({"id":"cut","type":"line","options":{"start":[0.035,-0.4,0.1],"step":[0,0.8,0],"count":2}},
      {"id":"s","type":"detailSeam","inputs":["plate","cut"],"options":{"width":0.01,"depth":0.004}},
      {"id":"a","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"f","type":"fastener","inputs":["s","a"],"options":{"head":"hex","radius":0.01WASHER}})";
    auto near = [&](const char* washer) {
        std::string text = near_seam;
        text.replace(text.find("WASHER"), 6, washer);
        return heads(*detailed_plate(text, "f")).size();
    };
    CHECK_EQ(near(""), std::size_t(1));
    CHECK_EQ(near(R"(,"washer":0.03)"), std::size_t(0));
}

TEST(doc_strokes_graduations_lettering_and_courses) {
    const Vec3 up{0, 0, 1};
    auto segments = [](const doc::Proto& p) {
        std::vector<doc::Detail> out;
        for (const doc::Detail& d : p.details)
            if (d.shape == doc::Detail::Shape::Segment) out.push_back(d);
        return out;
    };
    const char* line = R"({"id":"l","type":"line","options":{"start":[-0.5,0,0.1],"step":[1,0,0],"count":2}},)";
    // A stitched line: 20 mm on, 30 mm off, along one metre.
    const doc::ProtoPtr stitched = detailed_plate(std::string(line) + R"({"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"groove","width":0.004,"depth":0.002,"dash":[0.02,0.03]}})", "s");
    CHECK_EQ(segments(*stitched).size(), std::size_t(20));
    CHECK_NEAR(doc::sample_details(*stitched, {-0.5 + 0.01, 0, 0.05}, up).height, -0.002, 1e-6);
    CHECK_NEAR(doc::sample_details(*stitched, {-0.5 + 0.035, 0, 0.05}, up).height, 0.0, 1e-12);
    // A lap: the plate edge stands proud on one side of the line and feathers out to the other.
    const doc::ProtoPtr lap = detailed_plate(std::string(line) + R"({"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"lap","width":0.04,"height":0.004,"fillet":0.0002}})", "s");
    CHECK_NEAR(doc::sample_details(*lap, {0, 0.018, 0.05}, up).height, 0.004 * 0.95, 1e-5);
    CHECK_NEAR(doc::sample_details(*lap, {0, -0.018, 0.05}, up).height, 0.004 * 0.05, 1e-5);
    // A bead is half-round.
    const doc::ProtoPtr bead = detailed_plate(std::string(line) + R"({"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"bead","width":0.02,"height":0.006}})", "s");
    CHECK_NEAR(doc::sample_details(*bead, {0, 0, 0.05}, up).height, 0.006, 1e-6);
    CHECK_NEAR(doc::sample_details(*bead, {0, 0.005, 0.05}, up).height, 0.006 * std::sqrt(0.75), 1e-6);
    // A painted line has no height and needs a slot.
    const doc::ProtoPtr stripe = detailed_plate(std::string(line) + R"({"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"mark","width":0.02,"slot":"brass"}})", "s");
    CHECK_NEAR(doc::sample_details(*stripe, {0, 0, 0.05}, up).height, 0.0, 1e-12);
    CHECK_EQ(doc::sample_details(*stripe, {0, 0, 0.05}, up).material, doc::slot_id("brass"));
    CHECK(detail_error(std::string(line) + R"({"id":"s","type":"detailStroke","inputs":["plate","l"],"options":{"profile":"mark"}})", "s").find("slot") != std::string::npos);

    // Graduations: ten ticks at 10 mm, every fifth one longer.
    const doc::ProtoPtr scale = detailed_plate(R"({"id":"l","type":"line","options":{"start":[-0.05,0,0.1],"step":[0.1,0,0],"count":2}},
      {"id":"t","type":"detailTicks","inputs":["plate","l"],"options":{"pitch":0.01,"length":0.01,"major":5,"majorLength":0.02,"depth":0.001}})", "t");
    const std::vector<doc::Detail> ticks = segments(*scale);
    CHECK_EQ(ticks.size(), std::size_t(10));
    std::size_t longer = 0;
    for (const doc::Detail& t : ticks) {
        const double len = length(t.end - t.origin);
        CHECK(std::abs(len - 0.01) < 1e-6 || std::abs(len - 0.02) < 1e-6);
        CHECK_NEAR(t.end.x, t.origin.x, 1e-9);                           // square to the line
        longer += len > 0.015;
    }
    CHECK_EQ(longer, std::size_t(2));

    // Lettering: "12.004" in brass, 60 mm high, centred on the anchor and reading along x.
    const doc::ProtoPtr number = detailed_plate(R"({"id":"a","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"t","type":"detailText","inputs":["plate","a"],"options":{"text":"12.004","size":0.06,"slot":"brass","height":0.003}})", "t");
    double lo_x = 1e9, hi_x = -1e9, lo_y = 1e9, hi_y = -1e9;
    for (const doc::Detail& d : segments(*number)) {
        CHECK_EQ(d.material, doc::slot_id("brass"));
        CHECK(d.height > 0);
        for (const Vec3& p : {d.origin, d.end}) { lo_x = std::min(lo_x, p.x); hi_x = std::max(hi_x, p.x); lo_y = std::min(lo_y, p.y); hi_y = std::max(hi_y, p.y); }
    }
    CHECK_NEAR(hi_x - lo_x, 0.255, 1e-4);                                // 2 + 4 + 0.5 + 4 + 4 + 4 units and five gaps of 1.4
    CHECK_NEAR(hi_x + lo_x, 0.0, 1e-4);
    CHECK_NEAR(lo_y, -0.03, 1e-5);
    CHECK_NEAR(hi_y, 0.03, 1e-5);
    CHECK(segments(*number).size() > 30);
    CHECK(detail_error(R"({"id":"a","type":"pointArray","options":{"positions":[[0,0,0.1]]}},
      {"id":"t","type":"detailText","inputs":["plate","a"],"options":{"text":"N#1"}})", "t").find("'#'") != std::string::npos);

    // A rounded rectangle to run a row round, and the joints of four courses of half-bond masonry.
    const doc::Result outline = eval_text(R"({"version":1,"nodes":[{"id":"o","type":"outline","options":{"shape":"rect","size":[0.4,0.2],"corner":0.05}}],"output":"o"})");
    CHECK_EQ(outline.geometry.curves.size(), std::size_t(1));
    CHECK(outline.geometry.curves[0].closed);
    CHECK_EQ(outline.geometry.curves[0].points.size(), std::size_t(28));
    for (const Vec3& p : outline.geometry.curves[0].points) {
        CHECK(std::abs(p.x) <= 0.2 + 1e-6 && std::abs(p.y) <= 0.1 + 1e-6);
        CHECK(!(std::abs(p.x) > 0.19 && std::abs(p.y) > 0.09));         // no sharp corner
    }
    const doc::Result wall = eval_text(R"({"version":1,"nodes":[{"id":"c","type":"courses","options":{"size":[2,1],"course":0.25,"block":0.5}}],"output":"c"})");
    CHECK_EQ(wall.geometry.curves.size(), std::size_t(3 + 3 + 4 + 3 + 4));
    std::size_t beds = 0;
    for (const doc::Curve& c : wall.geometry.curves) beds += std::abs(c.points[0].y - c.points[1].y) < 1e-9;
    CHECK_EQ(beds, std::size_t(3));
}

TEST(emesh_detail_rows_carry_polygons_and_corners) {
    // A hex head and a rounded hatch on a plate drawn at twice its size, then baked.
    const doc::Result r = eval_text(R"({"version":1,"nodes":[
      {"id":"stock","type":"cube","options":{"size":[1,1,0.1]}},
      {"id":"plate","type":"transform","inputs":["stock"],"options":{"scale":[2,2,2]}},
      {"id":"a","type":"pointArray","options":{"positions":[[0.5,0,0.2]]}},
      {"id":"hex","type":"fastener","inputs":["plate","a"],"options":{"head":"hex","radius":0.02,"turn":"aligned"}},
      {"id":"b","type":"pointArray","options":{"positions":[[-0.5,0,0.2]]}},
      {"id":"hatch","type":"detailStamp","inputs":["hex","b"],"options":{"shape":"box","profile":"flat","size":[0.4,0.2],"corner":0.05,"height":0.004}},
      {"id":"out","type":"bake","inputs":["hatch"]}],"output":"out"})");
    emesh::WriteOptions plain;
    plain.dedupe_congruent = false;
    const emesh::Pack pack = emesh::read(emesh::write(r.geometry, plain));
    CHECK_EQ(pack.details.size(), std::size_t(2));
    const doc::Detail& hex = pack.details[0].detail;
    CHECK(hex.shape == doc::Detail::Shape::Polygon);
    CHECK_NEAR(hex.size_a, 0.02, 1e-6);                                  // metres after the bake
    CHECK_NEAR(hex.size_b, 6.0, 1e-9);                                   // six sides at any scale
    const doc::Detail& hatch = pack.details[1].detail;
    CHECK(hatch.shape == doc::Detail::Shape::Box);
    CHECK_NEAR(hatch.size_a, 0.2, 1e-6);
    CHECK_NEAR(hatch.size_c, 0.05, 1e-6);
}

TEST(shipyard_hull_plating_is_detail_and_keeps_heads_off_butts) {
    if (!std::filesystem::exists(std::string(PG_RECIPES) + "/site/shipyard")) return;  // kit not shipped in this repo
    doc::Library library(std::string(PG_RECIPES) + "/site/shipyard");
    const json::Value hull = json::parse(R"({"version":1,"moduleImports":{"hull":"hull-on-stocks"},"nodes":[
      {"id":"out","type":"group","group":"hull","bindings":{"stage":"$stage"}}],
      "controls":{"stage":{"value":2,"min":0,"max":2,"integer":true}},"output":"out"})");
    std::size_t per_stage[3] = {0, 0, 0};
    for (int stage = 0; stage <= 2; ++stage) {
        const doc::Result r = doc::evaluate(hull, library, {{"stage", static_cast<float>(stage)}});
        std::size_t laps = 0, glass = 0, marks = 0;
        double nearest = 1e9;
        for (const doc::Part& part : r.geometry.parts) {
            const doc::Proto& proto = *part.proto;
            per_stage[stage] += proto.details.size();
            for (const doc::Detail& d : proto.details) {
                laps += d.shape == doc::Detail::Shape::Segment && d.profile == doc::Detail::Profile::Ramp;
                glass += d.material == doc::slot_id("glass");
                marks += d.material == doc::slot_id("marking");
                if (!d.fastener) continue;
                for (const doc::Detail& seam : proto.details) {      // every head stands clear of every butt groove of its face
                    if (seam.shape != doc::Detail::Shape::Segment || seam.profile != doc::Detail::Profile::Groove || dot(d.normal, seam.normal) <= 0.3) continue;
                    const Vec3 axis = seam.end - seam.origin;
                    const double t = std::clamp(dot(d.origin - seam.origin, axis) / dot(axis, axis), 0.0, 1.0);
                    nearest = std::min(nearest, length(d.origin - (seam.origin + axis * t)) - d.extent - seam.size_a);
                }
            }
        }
        if (stage == 0) { CHECK_EQ(per_stage[0], std::size_t(0)); continue; }   // frames only: nothing to plate
        CHECK(laps > 50);
        CHECK(glass >= 20);
        CHECK(marks > 10);                                                      // the draught figures' strokes
        CHECK(nearest > 0.019);                                                 // one head radius of clearance, 20 mm
    }
    CHECK(per_stage[2] > per_stage[1] && per_stage[1] > 3000);                  // closing the port midbody adds its plating
}

// pgm nodes lists what the evaluator accepts: an unlisted type is rejected by name, and every listed type gets
// past that check (it may still fail on its missing options or inputs, but not as an unknown type).
TEST(node_reference_matches_the_evaluator) {
    auto attempt = [](const std::string& type) -> std::string {
        try {
            (void)eval_text(R"({"version": 1, "nodes": [{"id": "n", "type": ")" + type + R"("}], "output": "n"})");
        } catch (const std::exception& e) {
            return e.what();
        }
        return {};
    };
    CHECK(attempt("noSuchNode").find("Unknown node type") != std::string::npos);
    std::set<std::string> seen;
    for (const doc::NodeInfo& n : doc::node_reference()) {
        CHECK(seen.insert(std::string(n.type)).second);
        CHECK(!n.summary.empty() && !n.family.empty());
        CHECK(attempt(std::string(n.type)).find("Unknown node type") == std::string::npos);
    }
}

// pgm audit: parts hold together when they touch within 3 mm or one reaches inside the other.
TEST(contact_audit_finds_floating_parts) {
    auto pieces = [](const std::string& nodes, const std::string& output) {
        return doc::audit_contacts(eval_text(R"({"version": 1, "nodes": [)" + nodes + R"(], "output": ")" + output + R"("})").geometry).components;
    };
    const std::string a = R"({"id": "a", "type": "cube", "options": {"size": [1, 1, 1]}})";
    auto box_at = [](const std::string& id, double x, double size) {
        return R"({"id": ")" + id + R"(-s", "type": "cube", "options": {"size": [)" + std::to_string(size) + "," + std::to_string(size) + "," +
               std::to_string(size) + R"(]}}, {"id": ")" + id + R"(", "type": "transform", "inputs": [")" + id + R"(-s"], "options": {"translation": [)" +
               std::to_string(x) + R"(, 0, 0]}})";
    };
    CHECK_EQ(pieces(a + "," + box_at("b", 1.0, 1.0) + R"(, {"id": "j", "type": "join", "inputs": ["a", "b"]})", "j"), 1);    // faces touch
    CHECK_EQ(pieces(a + "," + box_at("b", 1.05, 1.0) + R"(, {"id": "j", "type": "join", "inputs": ["a", "b"]})", "j"), 2);   // 5 cm gap
    CHECK_EQ(pieces(a + "," + box_at("b", 1.002, 1.0) + R"(, {"id": "j", "type": "join", "inputs": ["a", "b"]})", "j"), 1);  // 2 mm: seated
    CHECK_EQ(pieces(a + "," + box_at("b", 0.1, 0.3) + R"(, {"id": "j", "type": "join", "inputs": ["a", "b"]})", "j"), 1);    // nested
    // A long thin rod through a thin plate: none of the rod's vertices lies in the plate; edge samples find it.
    const std::string plate = R"({"id": "p", "type": "cube", "options": {"size": [2, 2, 0.03]}})";
    const std::string rod = R"({"id": "r", "type": "sweep", "options": {"path": [[0.3, 0.2, -1], [0.3, 0.2, 1]], "radius": 0.02, "sides": 6}})";
    CHECK_EQ(pieces(plate + "," + rod + R"(, {"id": "j", "type": "join", "inputs": ["p", "r"]})", "j"), 1);
    const std::string short_rod = R"({"id": "r", "type": "sweep", "options": {"path": [[0.3, 0.2, 0.2], [0.3, 0.2, 1]], "radius": 0.02, "sides": 6}})";
    CHECK_EQ(pieces(plate + "," + short_rod + R"(, {"id": "j", "type": "join", "inputs": ["p", "r"]})", "j"), 2);
}

// Texture fitting: extrude's perimeter chart unrolls the sides, and uvFit maps a height span onto a trim-sheet band.
TEST(extrude_chart_and_uv_fit) {
    const std::string ext = R"({"id": "e", "type": "extrude", "options": {"profile": [[0, 0], [2, 0], [2, 2], [0, 2]], "depth": 3, "chart": "perimeter"}})";
    const doc::Result r = eval_text(R"({"version": 1, "nodes": [)" + ext + R"(], "output": "e"})");
    const doc::Proto& p = *r.geometry.parts.at(0).proto;
    CHECK_EQ(p.uv.size(), p.mesh.corner_count());
    float umax = 0, vmax = 0;
    for (const auto& c : p.uv) { umax = std::max(umax, c[0]); vmax = std::max(vmax, c[1]); }
    CHECK(std::abs(umax - 8.0f) < 1e-5f);            // perimeter of the 2 x 2 square
    CHECK(std::abs(vmax - 3.0f) < 1e-5f);            // the depth (caps stay within the 2 x 2 profile plane)
    const doc::Result f = eval_text(R"({"version": 1, "nodes": [)" + ext +
                                    R"(, {"id": "f", "type": "uvFit", "inputs": ["e"], "options": {"span": [0, 3], "band": [0.29, 0.85]}}], "output": "f"})");
    const doc::Proto& q = *f.geometry.parts.at(0).proto;
    float vmin2 = 10, vmax2 = -10, umax2 = 0;
    const std::size_t sides0 = q.mesh.corner_count() - 16;   // the four side quads come last
    for (std::size_t i = sides0; i < q.uv.size(); ++i) {
        vmin2 = std::min(vmin2, q.uv[i][1]);
        vmax2 = std::max(vmax2, q.uv[i][1]);
        umax2 = std::max(umax2, q.uv[i][0]);
    }
    CHECK(std::abs(vmin2 - 0.29f) < 1e-5f);
    CHECK(std::abs(vmax2 - 0.85f) < 1e-5f);
    CHECK(std::abs(umax2 - 8.0f * 0.56f / 3.0f) < 1e-4f);   // u scaled like v: stones keep their proportions
    CHECK_THROWS(eval_text(R"({"version": 1, "nodes": [{"id": "c", "type": "cube", "options": {"size": [1, 1, 1]}},
        {"id": "f", "type": "uvFit", "inputs": ["c"], "options": {"span": [0, 1], "band": [0, 1]}}], "output": "f"})"));   // no chart
}

TEST(contact_audit_dangling_detects_unattached_hanging_shoe) {
    // Wall from z = 0..3 at x in [-1, 1]; cantilevered deck from x = -1..2.2 at z = 3;
    // diagonal strut from (1.8, 0, 2.1) up to (2.1, 0, 2.98); wall shoe at (1.8, 0, 2.05)
    // hanging in thin air 0.8 m past the x = +1 wall face vs a second shoe seated at x = 1.01.
    const std::string doc_bad = R"({"version": 1, "nodes": [
        {"id": "ws", "type": "cube", "options": {"size": [2.0, 1.0, 3.0]}},
        {"id": "wall", "type": "transform", "inputs": ["ws"], "options": {"translation": [0, 0, 1.5]}},
        {"id": "ds", "type": "cube", "options": {"size": [3.2, 1.0, 0.12]}},
        {"id": "deck", "type": "transform", "inputs": ["ds"], "options": {"translation": [0.6, 0, 3.04]}},
        {"id": "strut", "type": "sweep", "options": {"path": [[1.8, 0, 2.10], [2.1, 0, 3.00]], "radius": 0.04, "sides": 4}},
        {"id": "ss", "type": "cube", "options": {"size": [0.10, 0.16, 0.24]}},
        {"id": "shoe", "type": "transform", "inputs": ["ss"], "options": {"translation": [1.78, 0, 2.08]}},
        {"id": "out", "type": "join", "inputs": ["wall", "deck", "strut", "shoe"]}
    ], "output": "out"})";
    const doc::ContactReport bad = doc::audit_contacts(eval_text(doc_bad).geometry, {.check_dangling = true});
    CHECK_EQ(bad.components, 1);
    CHECK_EQ(bad.dangling.size(), std::size_t(1));

    // Clamp the strut + shoe back onto the x = +1 wall face: now `shoe` touches both `strut` and `wall`.
    const std::string doc_good = R"({"version": 1, "nodes": [
        {"id": "ws", "type": "cube", "options": {"size": [2.0, 1.0, 3.0]}},
        {"id": "wall", "type": "transform", "inputs": ["ws"], "options": {"translation": [0, 0, 1.5]}},
        {"id": "ds", "type": "cube", "options": {"size": [3.2, 1.0, 0.12]}},
        {"id": "deck", "type": "transform", "inputs": ["ds"], "options": {"translation": [0.6, 0, 3.04]}},
        {"id": "strut", "type": "sweep", "options": {"path": [[1.02, 0, 2.10], [2.1, 0, 3.00]], "radius": 0.04, "sides": 4}},
        {"id": "ss", "type": "cube", "options": {"size": [0.10, 0.16, 0.24]}},
        {"id": "shoe", "type": "transform", "inputs": ["ss"], "options": {"translation": [1.03, 0, 2.08]}},
        {"id": "out", "type": "join", "inputs": ["wall", "deck", "strut", "shoe"]}
    ], "output": "out"})";
    const doc::ContactReport good = doc::audit_contacts(eval_text(doc_good).geometry, {.check_dangling = true});
    CHECK_EQ(good.components, 1);
    CHECK_EQ(good.dangling.size(), std::size_t(0));
}

TEST(doc_raycast_and_voronoi_nodes) {
    // 1. raycast: drop a 5x5 XY point grid from z = 5 onto a hemisphere/dome and verify hits lie on the surface
    // while corners outside the dome's footprint are dropped cleanly (`miss = "drop"`).
    const std::string rc_doc = R"({"version": 1, "nodes": [
        {"id": "dome", "type": "revolve", "options": {"profile": [[0, 1.0], [0.70710678, 0.70710678], [1.0, 0.0], [0, 0.0]], "sides": 32}},
        {"id": "grid-pts", "type": "pointField", "options": {"spacing": 0.6, "size": [2.4, 2.4]}},
        {"id": "high-pts", "type": "transform", "inputs": ["grid-pts"], "options": {"translation": [0, 0, 5.0]}},
        {"id": "landed", "type": "raycast", "inputs": ["dome", "high-pts"], "options": {"direction": [0, 0, -1], "offset": 0.02, "miss": "drop"}}
    ], "output": "landed"})";
    const doc::Result rr = eval_text(rc_doc);
    CHECK(rr.geometry.points.size() >= 5 && rr.geometry.points.size() < 25);
    for (const Vec3& p : rr.geometry.points) {
        CHECK(p.z >= 0.019 && p.z <= 1.021);
        CHECK(p.x * p.x + p.y * p.y <= 1.001);
    }

    // 2. voronoi: fracture a 2.0 x 0.6 x 1.2 block into 18 stone cells with 12 mm mortar gap;
    // every emitted stone must be a closed, watertight, manifold genus-0 convex polyhedron.
    const std::string vo_doc = R"({"version": 1, "nodes": [
        {"id": "stones", "type": "voronoi", "options": {"size": [2.0, 0.6, 1.2], "count": 18, "gap": 0.012, "seed": 7}}
    ], "output": "stones"})";
    const doc::Result vr = eval_text(vo_doc);
    CHECK_EQ(vr.geometry.parts.size(), std::size_t(18));
    CHECK_EQ(vr.geometry.points.size(), std::size_t(18));
    double total_vol = 0.0;
    for (const doc::Part& pt : vr.geometry.parts) {
        const QualityReport qr = analyze(pt.proto->mesh);
        CHECK(qr.clean());
        CHECK_EQ(qr.euler, 2);
        CHECK(qr.volume > 0.0);
        total_vol += qr.volume;
    }
    // Total volume is slightly under 2.0 * 0.6 * 1.2 = 1.44 m^3 because of the 12 mm mortar gaps.
    CHECK(total_vol > 1.15 && total_vol < 1.44);
}

TEST(doc_selections_face_divide_extrude_and_chamfer) {
    // 1. Selective vertical-arris chamfer on a 1 x 1 x 3 concrete column (top/bottom horizontal edges stay crisp).
    const doc::Result col = eval_text(R"({"version": 1, "nodes": [
        {"id": "stock", "type": "cube", "options": {"size": [1.0, 1.0, 3.0]}},
        {"id": "c", "type": "chamfer", "inputs": ["stock"],
         "options": {"width": 0.06, "minAngle": 30, "selection": {"direction": [0, 0, 1], "dirAngle": 10}}}
    ], "output": "c"})");
    const QualityReport cqr = analyze(col.geometry.parts.at(0).proto->mesh, {.check_self_intersections = true});
    CHECK(cqr.clean());
    CHECK_EQ(cqr.faces, std::size_t(10));  // 4 walls + 4 vertical chamfers + 2 octagonal caps = 10 faces
    CHECK_NEAR(cqr.volume, 2.9784, 1e-6);

    // 2. Splayed trapezoidal building block divided into 3 storeys and ~1.6 m bays on its front face,
    //    keeping shared edges with roof/floor/side walls 100% watertight and volume-exact, then recessed
    //    only on bays NOT occluded by a neighbour block.
    const std::string grammar_doc = R"({"version": 1, "nodes": [
        {"id": "body", "type": "extrude", "options": {"profile": [[-3.0, 0.0], [3.0, 0.0], [2.4, 4.0], [-3.0, 4.0]], "depth": 7.0}},
        {"id": "storeys", "type": "faceDivide", "inputs": ["body"],
         "options": {"axis": "vertical", "sizes": [1.2, -1.0, -1.0, 0.6], "selection": {"normal": [0, -1, 0], "angle": 25}}},
        {"id": "bays", "type": "faceDivide", "inputs": ["storeys"],
         "options": {"axis": "horizontal", "repeat": 2.0,
                     "selection": {"normal": [0, -1, 0], "angle": 25, "box": [[-4, -0.5, 1.3], [4, 0.5, 6.3]]}}},
        {"id": "nbr-s", "type": "cube", "options": {"size": [1.8, 1.0, 8.0]}},
        {"id": "nbr", "type": "transform", "inputs": ["nbr-s"], "options": {"translation": [2.0, -0.6, 4.0]}},
        {"id": "recessed", "type": "faceExtrude", "inputs": ["bays", "nbr"],
         "options": {"inset": 0.18, "offset": -0.20, "individual": 1,
                     "selection": {"normal": [0, -1, 0], "angle": 25, "box": [[-4, -0.5, 1.3], [4, 0.5, 6.3]],
                                   "occluded": 0, "occludeDistance": 0.5}}}
    ], "output": "bays"})";
    const doc::Result divided = eval_text(grammar_doc);
    const QualityReport dqr = analyze(divided.geometry.parts.at(0).proto->mesh, {.check_self_intersections = true});
    CHECK(dqr.clean());
    // Trapezoid area = 0.5 * (6.0 + 5.4) * 4.0 = 22.8; depth = 7.0 -> exact volume = 159.6.
    CHECK_NEAR(dqr.volume, 159.6, 1e-5);

    // Evaluate `recessed` output: stays closed & clean, volume shrinks by the 4 unoccluded window recesses (2 of the 6 upper bays are occluded by `nbr` at x=2.0).
    std::string rec_doc = grammar_doc;
    rec_doc.replace(rec_doc.rfind("\"bays\""), 6, "\"recessed\"");
    const doc::Result rec = eval_text(rec_doc);
    const QualityReport rqr = analyze(rec.geometry.parts.at(0).proto->mesh, {.check_self_intersections = true});
    CHECK(rqr.clean());
    CHECK(rqr.volume < 159.6 - 0.5 && rqr.volume > 155.0);
}

TEST(doc_curve_tools_variable_sweep_and_multi_instance) {
    // 1. Fillet an L-bracket polyline, sweep a tapered + twisted I-beam along it, and check closed manifold topology.
    const doc::Result bracket = eval_text(R"({"version": 1, "nodes": [
        {"id": "raw", "type": "spline", "options": {"points": [[0, 0, 0], [0, 0, 1.2], [0.9, 0, 1.2]], "interpolation": "polyline"}},
        {"id": "fil", "type": "curveFillet", "inputs": ["raw"], "options": {"radius": 0.25, "segments": 8}},
        {"id": "beam", "type": "profileSweep", "inputs": ["fil"],
         "options": {"section": "ibeam", "width": 0.14, "height": 0.22, "flange": 0.016, "web": 0.012,
                     "scale": [1.0, ["-", 1.0, ["*", 0.45, "@t"]]], "twist": ["*", 0.25, "@t"], "up": [0, 1, 0]}}
    ], "output": "beam"})");
    const QualityReport bqr = analyze(bracket.geometry.parts.at(0).proto->mesh, {.check_self_intersections = true});
    CHECK(bqr.clean());
    CHECK(bqr.volume > 0.004);

    // 2. Tapered tube using @t in radius, plus curveTrim, curveOffset and sampleCurve.
    const doc::Result horn = eval_text(R"({"version": 1, "nodes": [
        {"id": "c", "type": "outline", "options": {"shape": "arc", "radius": 0.8, "start": 0, "sweep": 1.5707963, "segments": 16}},
        {"id": "off", "type": "curveOffset", "inputs": ["c"], "options": {"offset": 0.1}},
        {"id": "tr", "type": "curveTrim", "inputs": ["off"], "options": {"start": 0.1, "end": 0.9}},
        {"id": "tb", "type": "tube", "inputs": ["tr"], "options": {"radius": ["-", 0.06, ["*", 0.04, "@t"]], "sides": 12}}
    ], "output": "tb"})");
    const QualityReport hqr = analyze(horn.geometry.parts.at(0).proto->mesh, {.check_self_intersections = true});
    CHECK(hqr.clean());

    const doc::Result sampled = eval_text(R"({"version": 1, "nodes": [
        {"id": "l", "type": "spline", "options": {"points": [[0, 0, 0], [2, 0, 0]], "interpolation": "polyline"}},
        {"id": "pts", "type": "sampleCurve", "inputs": ["l"], "options": {"positions": [0.0, 0.25, 0.75, 1.0]}}
    ], "output": "pts"})");
    CHECK_EQ(sampled.geometry.points.size(), std::size_t(4));
    CHECK_NEAR(sampled.geometry.points[1].x, 0.5, 1e-6);
    CHECK_NEAR(sampled.geometry.points[2].x, 1.5, 1e-6);

    // 3. Multi-source instance: deterministic cycle via pick="@index" and seeded random when pick is omitted.
    const std::string multi_base = R"({"version": 1, "nodes": [
        {"id": "pts", "type": "line", "options": {"start": [0, 0, 0], "step": [1, 0, 0], "count": 12}},
        {"id": "a", "type": "cube", "options": {"size": [0.2, 0.2, 0.2]}},
        {"id": "b", "type": "cylinder", "options": {"radius": 0.1, "depth": 0.4, "sides": 8}},
        {"id": "c", "type": "cylinder", "options": {"radius": 0.1, "depth": 0.6, "sides": 12}},
        {"id": "inst", "type": "instance", "inputs": ["pts", "a", "b", "c"], "options": {OPTS}}
    ], "output": "inst"})";
    std::string cyc_doc = multi_base;
    cyc_doc.replace(cyc_doc.find("OPTS"), 4, R"("pick": "@index")");
    const doc::Result cyc = eval_text(cyc_doc);
    CHECK_EQ(cyc.geometry.parts.size(), std::size_t(12));
    CHECK_EQ(cyc.geometry.parts[0].proto->mesh.face_count(), std::size_t(6));
    CHECK_EQ(cyc.geometry.parts[1].proto->mesh.face_count(), std::size_t(8 + 12));
    CHECK_EQ(cyc.geometry.parts[2].proto->mesh.face_count(), std::size_t(12 + 20));
    CHECK_EQ(cyc.geometry.parts[3].proto->mesh.face_count(), std::size_t(6));

    std::string rnd_doc = multi_base;
    rnd_doc.replace(rnd_doc.find("OPTS"), 4, R"("randomSeed": 17)");
    const doc::Result rnd = eval_text(rnd_doc);
    std::set<std::size_t> seen_faces;
    for (const doc::Part& pt : rnd.geometry.parts) seen_faces.insert(pt.proto->mesh.face_count());
    CHECK_EQ(seen_faces.size(), std::size_t(3));  // all three candidate prototypes appear under seeded random
}

TEST(doc_bytecode_expr_tape_folds_and_matches_ast) {
    doc::Controls ctrls{{"span", 6.5f}, {"rise", 1.8f}, {"n", 8.0f}};
    // Pure control + arithmetic expression folds down to a single PushConst instruction.
    const json::Value folded_json = json::parse(R"(["+", ["*", "$span", 0.5], ["sqrt", ["+", ["pow", "$rise", 2], 0.25]]])");
    const doc::detail::CompiledExpr folded(&folded_json, &ctrls);
    CHECK(folded.is_constant());
    CHECK_EQ(folded.instruction_count(), std::size_t(1));
    const doc::detail::Options ref_opt{nullptr, &ctrls, nullptr};
    CHECK_EQ(folded.eval(nullptr), ref_opt.number(folded_json));

    // Parametric + conditional expression with short-circuit division-by-zero branch matches AST bit-for-bit.
    const json::Value dyn_json = json::parse(
        R"(["if", [">", "@u", 0.0], ["+", ["*", ["sin", ["*", "@u", 3.14159265]], "$rise"], ["*", 0.1, "@random"]], ["-", "@v", "@index"]])");
    const doc::detail::CompiledExpr dyn(&dyn_json, &ctrls);
    CHECK(!dyn.is_constant());
    for (int i = 0; i < 16; ++i) {
        doc::detail::Point pt;
        pt.index = i;
        pt.seed = 42;
        pt.parametric = true;
        pt.u = static_cast<float>(i) / 15.0f;
        pt.v = 1.0f - pt.u;
        pt.position = {pt.u * 2.0, pt.v * 3.0, -0.5};
        const doc::detail::Options pt_opt{nullptr, &ctrls, &pt};
        CHECK_EQ(dyn.eval(&pt), pt_opt.number(dyn_json));
    }

    // Short-circuit `if` must NOT evaluate the dead branch (`["/", 1, 0]` would fail range check).
    const json::Value sc_json = json::parse(R"(["if", [">", "@index", 0], 3.5, ["/", 1.0, 0.0]])");
    const doc::detail::CompiledExpr sc(&sc_json, &ctrls);
    doc::detail::Point p1;
    p1.index = 2;
    CHECK_EQ(sc.eval(&p1), 3.5f);
}

TEST(doc_settled_prefix_dirty_tail_eval_cache) {
    const std::string doc_v1 = R"({
        "version": 1,
        "controls": {
            "base_w": {"value": 4.0, "min": 2.0, "max": 8.0},
            "roof_h": {"value": 1.2, "min": 0.4, "max": 3.0}
        },
        "nodes": [
            {"id": "plinth-raw", "type": "cube", "options": {"size": ["$base_w", 3.0, 2.0]}},
            {"id": "cut-raw", "type": "cube", "options": {"size": [1.2, 4.0, 1.4]}},
            {"id": "cut", "type": "transform", "inputs": ["cut-raw"], "options": {"translation": [0, 0, -0.35]}},
            {"id": "shell", "type": "meshBoolean", "inputs": ["plinth-raw", "cut"], "options": {"operation": "difference"}},
            {"id": "chamf", "type": "chamfer", "inputs": ["shell"], "options": {"width": 0.04, "minAngle": 35}},
            {"id": "roof-raw", "type": "cube", "options": {"size": ["$base_w", 3.2, "$roof_h"]}},
            {"id": "roof", "type": "transform", "inputs": ["roof-raw"], "options": {"translation": [0, 0, ["+", 1.0, ["*", 0.5, "$roof_h"]]]}},
            {"id": "all", "type": "join", "inputs": ["chamf", "roof"]}
        ],
        "output": "all"
    })";

    doc::Library lib(".");
    doc::EvalCache cache(64);
    const json::Value j1 = json::parse(doc_v1);

    // 1. Cold pass: evaluates all 8 nodes, populates cache.
    const doc::Result r1 = doc::evaluate(j1, lib, {}, &cache);
    CHECK_EQ(r1.evaluated_nodes, std::size_t(8));
    CHECK_EQ(r1.reused_nodes, std::size_t(0));
    CHECK_EQ(cache.size(), std::size_t(8));

    // 2. Identical warm pass: 100% settled prefix, 0 dirty nodes.
    const doc::Result r2 = doc::evaluate(j1, lib, {}, &cache);
    CHECK_EQ(r2.evaluated_nodes, std::size_t(0));
    CHECK_EQ(r2.reused_nodes, std::size_t(8));
    CHECK_EQ(doc::make_receipt(r2.geometry).placed_triangles, doc::make_receipt(r1.geometry).placed_triangles);

    // 3. Control override on `roof_h` only: `plinth-raw`, `cut-raw`, `cut`, `shell`, `chamf` (5 heavy boolean/chamfer nodes!)
    //    stay settled because `$base_w` did not change; only `roof-raw`, `roof`, and `all` (3 dirty-tail nodes) re-evaluate.
    const doc::Result r3 = doc::evaluate(j1, lib, {{"roof_h", 2.0f}}, &cache);
    CHECK_EQ(r3.reused_nodes, std::size_t(5));
    CHECK_EQ(r3.evaluated_nodes, std::size_t(3));

    // 4. Document edit appending a finial on top while keeping the masonry/boolean prefix untouched:
    std::string doc_v2 = doc_v1;
    const std::string old_tail = R"({"id": "all", "type": "join", "inputs": ["chamf", "roof"]})";
    const std::string new_tail = R"({"id": "finial", "type": "cylinder", "options": {"radius": 0.15, "depth": 0.6, "sides": 12}},
            {"id": "all", "type": "join", "inputs": ["chamf", "roof", "finial"]})";
    doc_v2.replace(doc_v2.find(old_tail), old_tail.size(), new_tail);
    const doc::Result r4 = doc::evaluate(json::parse(doc_v2), lib, {}, &cache);
    CHECK_EQ(r4.reused_nodes, std::size_t(7));  // chamf (5) + roof (2) all reused from pass 1!
    CHECK_EQ(r4.evaluated_nodes, std::size_t(2));  // only finial + all evaluated!
}

TEST(doc_pace_layered_stages_and_provisional_centering) {
    // Pace-layered archway with temporary centering prop at stage=1:
    //   stage 0: plinth slab
    //   stage 1: two piers + elevated springer cap (supported by provisional timber centering ONLY at stage=1)
    //   stage 2: lintel/spandrel beam bridging both piers + keystone (centering comes down; structure still 1 component!)
    //   stage 3: roof cornice + surface detail stamp
    const std::string staged_doc = R"({
        "version": 1,
        "controls": {"stage": {"value": 3, "min": 0, "max": 3, "integer": true}},
        "nodes": [
            {"id": "none", "type": "empty"},
            {"id": "plinth-s", "type": "cube", "options": {"size": [4.0, 1.4, 0.4]}},
            {"id": "L0", "type": "transform", "inputs": ["plinth-s"], "options": {"translation": [0, 0, 0.2]}},

            {"id": "pier-s", "type": "cube", "options": {"size": [0.6, 1.0, 2.4]}},
            {"id": "pier-l", "type": "transform", "inputs": ["pier-s"], "options": {"translation": [-1.4, 0, 1.6]}},
            {"id": "pier-r", "type": "transform", "inputs": ["pier-s"], "options": {"translation": [1.4, 0, 1.6]}},
            {"id": "crown-block-s", "type": "cube", "options": {"size": [0.7, 0.9, 0.4]}},
            {"id": "crown-block", "type": "transform", "inputs": ["crown-block-s"], "options": {"translation": [0, 0, 3.0]}},
            {"id": "L1", "type": "join", "inputs": ["pier-l", "pier-r", "crown-block"]},

            {"id": "centering-s", "type": "cube", "options": {"size": [0.4, 0.6, 2.42]}},
            {"id": "P1", "type": "transform", "inputs": ["centering-s"], "options": {"translation": [0, 0, 1.6]}},

            {"id": "beam-s", "type": "cube", "options": {"size": [3.4, 1.0, 0.42]}},
            {"id": "L2", "type": "transform", "inputs": ["beam-s"], "options": {"translation": [0, 0, 3.0]}},

            {"id": "cornice-s", "type": "cube", "options": {"size": [3.8, 1.2, 0.22]}},
            {"id": "cornice-xf", "type": "transform", "inputs": ["cornice-s"], "options": {"translation": [0, 0, 3.31]}},
            {"id": "boss-pt", "type": "pointArray", "options": {"positions": [[0, -0.62, 3.31]]}},
            {"id": "L3", "type": "detailStamp", "inputs": ["cornice-xf", "boss-pt"], "options": {"radius": 0.08, "height": 0.02}},

            {"id": "g1", "type": "select", "inputs": ["L1", "none"], "options": {"condition": [">", "$stage", 0.5]}},
            {"id": "gp1", "type": "select", "inputs": ["P1", "none"], "options": {"condition": ["min", [">", "$stage", 0.5], ["<", "$stage", 1.5]]}},
            {"id": "g2", "type": "select", "inputs": ["L2", "none"], "options": {"condition": [">", "$stage", 1.5]}},
            {"id": "g3", "type": "select", "inputs": ["L3", "none"], "options": {"condition": [">", "$stage", 2.5]}},
            {"id": "out", "type": "join", "inputs": ["L0", "g1", "gp1", "g2", "g3"]}
        ],
        "output": "out"
    })";

    doc::Library lib(".");
    doc::EvalCache cache(128);
    const json::Value j = json::parse(staged_doc);

    std::size_t prev_tris = 0;
    for (int stg = 0; stg <= 3; ++stg) {
        const doc::Result r = doc::evaluate(j, lib, {{"stage", static_cast<float>(stg)}}, &cache);
        const doc::ContactReport cr = doc::audit_contacts(r.geometry, {.check_dangling = true});
        // Every stage 0..3 holds together as a single connected component with zero dangling islands!
        CHECK_EQ(cr.components, 1);
        CHECK(cr.dangling.empty());
        const doc::Receipt rc = doc::make_receipt(r.geometry);
        if (stg == 0) CHECK_EQ(rc.instances, std::size_t(1));
        if (stg == 1) CHECK_EQ(rc.instances, std::size_t(5));  // plinth + 2 piers + crown + provisional centering
        if (stg == 2) CHECK_EQ(rc.instances, std::size_t(5));  // centering gone, spanning beam bridges piers & crown!
        if (stg == 3) {
            CHECK_EQ(rc.instances, std::size_t(6));
            CHECK_EQ(rc.placed_details, std::size_t(1));
        }
        prev_tris = rc.placed_triangles;
    }
    CHECK(prev_tris > 0);
}

TEST(bake_preserves_part_provenance_gaps_and_overhang_dangling) {
    const char* doc_json = R"({
        "version": 1,
        "nodes": [
            {"id": "wall-stock", "type": "cube", "options": {"size": [4.0, 1.0, 3.0]}},
            {"id": "wall", "type": "transform", "inputs": ["wall-stock"], "options": {"translation": [0, 0, 1.5]}},
            {"id": "deck-stock", "type": "cube", "options": {"size": [5.2, 0.8, 0.12]}},
            {"id": "deck", "type": "transform", "inputs": ["deck-stock"], "options": {"translation": [0, -0.899, 2.5]}},
            {"id": "post-stock", "type": "cube", "options": {"size": [0.08, 0.08, 0.9]}},
            {"id": "post", "type": "transform", "inputs": ["post-stock"], "options": {"translation": [2.45, -1.24, 2.946]}},
            {"id": "overhang-shoe-stock", "type": "cube", "options": {"size": [0.10, 0.40, 0.55]}},
            {"id": "overhang-shoe", "type": "transform", "inputs": ["overhang-shoe-stock"], "options": {"translation": [2.45, -1.08, 2.22]}},
            {"id": "floating-bracket-stock", "type": "cube", "options": {"size": [0.20, 0.20, 0.20]}},
            {"id": "floating-bracket", "type": "transform", "inputs": ["floating-bracket-stock"], "options": {"translation": [0.0, -0.65, 1.5]}},
            {"id": "joined", "type": "join", "inputs": ["wall", "deck", "post", "overhang-shoe", "floating-bracket"]},
            {"id": "cabin.baked", "type": "bake", "inputs": ["joined"]}
        ],
        "output": "cabin.baked"
    })";
    doc::Library lib(".");
    const json::Value j = json::parse(doc_json);
    const doc::Result r = doc::evaluate(j, lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(1));  // merged into 1 material shell

    const doc::Result unbaked = doc::evaluate_unbaked(j, lib);
    CHECK_EQ(unbaked.geometry.parts.size(), std::size_t(5));  // all 5 authoring parts preserved!

    const doc::ContactReport cr = doc::audit_contacts(r.geometry, {.check_dangling = true, .compute_gaps = true});
    CHECK_EQ(cr.components, 2);
    // Floating bracket retains its exact pre-bake authoring ID ("floating-bracket-stock") and ~50 mm gap to "wall-stock"
    int det_comp = cr.main_component == 0 ? 1 : 0;
    bool saw_floating_id = false;
    for (const auto& isl : cr.islands) {
        if (isl.component == det_comp && isl.source == "floating-bracket-stock") saw_floating_id = true;
    }
    CHECK(saw_floating_id);
    CHECK(cr.detached_gaps.count(det_comp) == 1);
    const auto& gap = cr.detached_gaps.at(det_comp);
    CHECK_NEAR(gap.gap, 0.05, 0.002);
    CHECK(gap.nearest_main_source == "wall-stock");
    CHECK_NEAR(gap.delta_to_main.y, 0.05, 0.002);

    // Multi-neighbor overhanging shoe (touches BOTH deck and rail post at its top, hangs in empty air below!) is caught!
    CHECK_EQ(cr.dangling.size(), std::size_t(1));
    CHECK(cr.islands[cr.dangling[0]].source == "overhang-shoe-stock");
}

TEST(detail_chain_shares_cached_closest_index) {
    const char* doc_json = R"({
        "version": 1,
        "nodes": [
            {"id": "cyl", "type": "cylinder", "options": {"radius": 1.0, "depth": 2.0, "sides": 48}},
            {"id": "pts1", "type": "pointField", "options": {"size": [1.2, 1.2], "spacing": 0.12}},
            {"id": "pts1x", "type": "transform", "inputs": ["pts1"], "options": {"translation": [0, 0, 1.01]}},
            {"id": "d1", "type": "detailStamp", "inputs": ["cyl", "pts1x"], "options": {"radius": 0.02, "height": 0.005, "reach": 0.05}},
            {"id": "d2", "type": "detailStamp", "inputs": ["d1", "pts1x"], "options": {"radius": 0.01, "height": 0.003, "reach": 0.05}}
        ],
        "output": "d2"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_json), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(1));
    CHECK(r.geometry.parts[0].proto->closest_index != nullptr);
    CHECK(r.geometry.parts[0].proto->details.size() > 60);
}

TEST(doc_sds_creases_quad_cylinder_weighted_normals_and_shrinkwrap) {
    const std::string sds_doc = R"({
        "version": 1,
        "nodes": [
            {"id": "cap-cyl", "type": "cylinder", "options": {"radius": 0.4, "depth": 0.8, "sides": 12, "rings": 2, "cap": "quad"}},
            {"id": "cap-creased", "type": "crease", "inputs": ["cap-cyl"], "options": {"sharpness": 3.5, "minAngle": 45}},
            {"id": "cap-sds", "type": "subdivide", "inputs": ["cap-creased"], "options": {"levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1}},

            {"id": "box", "type": "cube", "options": {"size": [0.8, 0.8, 0.5]}},
            {"id": "box-held", "type": "chamfer", "inputs": ["box"], "options": {"width": 0.05, "segments": 2, "profile": 1.0, "clampOverlap": 1}},
            {"id": "box-fwn", "type": "smoothNormals", "inputs": ["box-held"], "options": {"angle": 55, "weighting": "areaSq"}},
            {"id": "box-xf", "type": "transform", "inputs": ["box-fwn"], "options": {"translation": [1.5, 0, 0]}},

            {"id": "patch-plate", "type": "grid", "options": {"size": [0.3, 0.3, 0], "divisions": [5, 5, 1]}},
            {"id": "patch-side", "type": "transform", "inputs": ["patch-plate"], "options": {"rotation": [0, 1.5707963, 0], "translation": [0.46, 0, 0]}},
            {"id": "patch-wrap", "type": "shrinkwrap", "inputs": ["patch-side", "cap-sds"], "options": {"offset": 0.005, "method": "nearest"}},

            {"id": "out", "type": "join", "inputs": ["cap-sds", "box-xf", "patch-wrap"]}
        ],
        "output": "out"
    })";

    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(sds_doc), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));

    // 1. Quad-capped cylinder -> creased -> Chaikin SDS with exact limit normals.
    const Mesh& cap_m = r.geometry.parts[0].proto->mesh;
    const QualityReport q_cap = analyze(cap_m);
    CHECK(q_cap.clean());
    CHECK_NEAR(q_cap.quad_ratio(), 1.0, 1e-12);
    CHECK_EQ(q_cap.high_valence_poles, std::size_t(0));
    CHECK_EQ(r.geometry.parts[0].proto->normals.size(), cap_m.corner_count());

    // 2. Support-loop chamfered box + FWN (areaSq + 55 deg Phong break).
    const Mesh& box_m = r.geometry.parts[1].proto->mesh;
    const QualityReport q_box = analyze(box_m);
    CHECK(q_box.clean());
    CHECK_NEAR(q_box.quad_ratio(), 1.0, 1e-12);
    CHECK_NEAR(r.geometry.parts[1].smooth_angle_deg, 55.0, 1e-4);
    CHECK_EQ(r.geometry.parts[1].normal_weight, std::uint8_t(4));

    // 3. Shrinkwrapped grid conforms tightly onto the curved cylinder radius (~0.405).
    const Mesh& wrap_m = r.geometry.parts[2].proto->mesh;
    for (const Vec3& p : wrap_m.positions) {
        const double rad = std::hypot(p.x, p.y);
        CHECK(rad > 0.37 && rad < 0.42);
    }

    // 4. emesh pack serialization succeeds cleanly with both explicit limit normals and FWN split normals.
    emesh::WriteStats ws{};
    const auto bytes = emesh::write(r.geometry, {}, &ws);
    CHECK(!bytes.empty());
    CHECK_EQ(ws.meshes, std::size_t(3));
    const emesh::Pack pack = emesh::read(bytes);
    CHECK_EQ(pack.meshes.size(), std::size_t(3));
}

TEST(doc_sds_multilevel_lods_and_retained_cage) {
    const std::string lod_doc = R"({
        "version": 1,
        "nodes": [
            {"id": "cyl", "type": "cylinder", "options": {"radius": 0.4, "depth": 0.8, "sides": 12, "rings": 2, "cap": "quad"}},
            {"id": "cr", "type": "crease", "inputs": ["cyl"], "options": {"sharpness": 3.0, "minAngle": 45}},
            {"id": "sds", "type": "subdivide", "inputs": ["cr"],
             "options": {"lodLevels": [2, 1, 0], "lodDistances": [0, 10, 28], "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1}}
        ],
        "output": "sds"
    })";

    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(lod_doc), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(1));
    const doc::Proto& proto = *r.geometry.parts[0].proto;
    CHECK(proto.sds_cage != nullptr);
    CHECK_EQ(proto.sds_cage->face_count(), std::size_t(12 * 2 + 2 * 9));  // 24 wall quads + two 3x3 quad caps
    CHECK_EQ(proto.lods.size(), std::size_t(2));  // LOD 1 + LOD 2

    emesh::WriteStats ws{};
    const auto bytes = emesh::write(r.geometry, {}, &ws);
    CHECK_EQ(ws.meshes, std::size_t(1));
    CHECK_EQ(ws.lod_submeshes, std::size_t(3));

    const emesh::Pack pack = emesh::read(bytes);
    CHECK_EQ(pack.meshes.size(), std::size_t(1));
    const auto& pm = pack.meshes[0];
    CHECK_EQ(pm.lod_count, std::uint8_t(3));
    CHECK_EQ(pm.lods.size(), std::size_t(3));
    // Engine convention (mesh_format.hpp): lodDistance[i] is max distance for LOD i; last LOD is 0 (unlimited).
    CHECK_NEAR(pm.lod_distances[0], 10.0, 1e-4);
    CHECK_NEAR(pm.lod_distances[1], 28.0, 1e-4);
    CHECK_NEAR(pm.lod_distances[2], 0.0, 1e-4);
    // Strict coarsening across LOD 0 -> LOD 1 -> LOD 2 (~4x per level).
    CHECK(pm.lods[0].index_count > pm.lods[1].index_count * 3);
    CHECK(pm.lods[1].index_count > pm.lods[2].index_count * 3);
}

TEST(doc_loopcut_quad_sweep_revolve_and_bake_preserves_cage_lods) {
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "box", "type": "cube", "options": {"size": [0.6, 0.4, 0.8]}},
            {"id": "box-loops", "type": "loopCut", "inputs": ["box"],
             "options": {"margin": 0.05, "selection": {"direction": [0, 0, 1], "dirAngle": 15}, "crease": 1.5}},
            {"id": "box-sds", "type": "subdivide", "inputs": ["box-loops"],
             "options": {"lodLevels": [2, 1, 0], "lodDistances": [0, 12, 30], "limit": 1, "limitNormals": 1}},

            {"id": "rod", "type": "sweep", "options": {"path": [[1.0, 0, 0], [1.0, 0, 0.7]], "radius": 0.12, "sides": 8, "cap": "quad"}},
            {"id": "rod-loops", "type": "loopCut", "inputs": ["rod"],
             "options": {"margin": 0.04, "selection": {"direction": [0, 0, 1], "dirAngle": 15}}},
            {"id": "rod-sds", "type": "subdivide", "inputs": ["rod-loops"],
             "options": {"lodLevels": [2, 1, 0], "limit": 1, "limitNormals": 1}},

            {"id": "lathe", "type": "revolve",
             "options": {"profile": [[0, 0], [0.22, 0], [0.26, 0.15], [0.18, 0.35], [0, 0.35]], "sides": 12, "cap": "quad"}},
            {"id": "lathe-xf", "type": "transform", "inputs": ["lathe"], "options": {"translation": [-1.0, 0, 0]}},
            {"id": "lathe-sds", "type": "subdivide", "inputs": ["lathe-xf"],
             "options": {"lodLevels": [2, 1, 0], "limit": 1, "limitNormals": 1}},

            {"id": "joined", "type": "join", "inputs": ["box-sds", "rod-sds", "lathe-sds"]},
            {"id": "baked", "type": "bake", "inputs": ["joined"]}
        ],
        "output": "baked"
    })";

    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(1));
    const doc::Proto& proto = *r.geometry.parts[0].proto;
    const QualityReport q = analyze(proto.mesh);
    CHECK(q.clean());
    CHECK_NEAR(q.quad_ratio(), 1.0, 1e-12);
    CHECK_EQ(q.high_valence_poles, std::size_t(0));
    // Bake preserved BOTH the merged SDS control cage AND the 2 coarser LOD meshes!
    CHECK(proto.sds_cage != nullptr);
    CHECK(proto.sds_cage->face_count() < proto.mesh.face_count() / 8);
    CHECK_EQ(proto.lods.size(), std::size_t(2));

    emesh::WriteStats ws{};
    const auto bytes = emesh::write(r.geometry, {}, &ws);
    CHECK_EQ(ws.meshes, std::size_t(1));
    CHECK_EQ(ws.lod_submeshes, std::size_t(3));
    const emesh::Pack pack = emesh::read(bytes);
    CHECK_EQ(pack.meshes[0].lod_count, std::uint8_t(3));
    CHECK(pack.meshes[0].lods[0].index_count > pack.meshes[0].lods[1].index_count * 3);
    CHECK(pack.meshes[0].lods[1].index_count > pack.meshes[0].lods[2].index_count * 3);
}

TEST(emesh_engine_metadata_ports_datums_collision_and_walk) {
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "slab", "type": "cube", "options": {"size": [3.0, 2.0, 0.2]}},
            {"id": "deck", "type": "transform", "inputs": ["slab"], "options": {"translation": [0, 0, 1.5]}},
            {"id": "stone", "type": "material", "inputs": ["deck"], "options": {"slot": "limestone"}}
        ],
        "output": "stone",
        "modulePorts": {
            "walk-west": {"origin": [-1.5, 0, 1.6], "normal": [-1, 0, 0], "up": [0, 0, 1], "type": "deck", "dimensions": {"clearWidth": 2.0}},
            "base": {"origin": [0, 0, 1.4], "normal": [0, 0, -1], "up": [1, 0, 0], "type": "footing", "dimensions": {"width": 3.0}}
        }
    })";
    doc::Library lib(".");
    const json::Value j = json::parse(doc_s);
    const doc::Result r = doc::evaluate(j, lib);

    emesh::WriteOptions wo;
    wo.y_up = true;
    wo.ports = doc::module_ports(j, r.controls);
    wo.emit_collision_and_walk = true;

    emesh::WriteStats ws{};
    const auto bytes = emesh::write(r.geometry, wo, &ws);
    CHECK_EQ(ws.ports, std::size_t(2));
    CHECK_EQ(ws.deck_datums, std::size_t(1));
    CHECK_EQ(ws.collision_boxes, std::size_t(1));
    CHECK_EQ(ws.walk_triangles, std::size_t(2));  // top face of the 3x2 m slab = 2 walkable triangles

    const emesh::Pack pack = emesh::read(bytes);
    CHECK_EQ(pack.ports.size(), std::size_t(2));
    CHECK_EQ(pack.deck_datums.size(), std::size_t(1));
    CHECK_NEAR(pack.deck_datums[0], 1.6, 1e-4);  // Z=1.6 -> Y=1.6 in Y-up engine space
    CHECK_EQ(pack.collision_boxes.size(), std::size_t(1));
    CHECK_NEAR(pack.collision_boxes[0].half_extent.x, 1.5, 1e-3);
    CHECK_NEAR(pack.collision_boxes[0].half_extent.y, 0.1, 1e-3);
    CHECK_NEAR(pack.collision_boxes[0].half_extent.z, 1.0, 1e-3);
    CHECK_EQ(pack.walk_triangles.size(), std::size_t(2));
    for (const auto& tri : pack.walk_triangles) {
        for (const Vec3& v : tri) CHECK_NEAR(v.y, 1.6, 1e-3);  // all walkable vertices at top deck Y=1.6
    }
}

TEST(doc_tube_quad_cap_and_extrude_inset_cap_sds) {
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "path", "type": "curve", "options": {"point": [0, ["*", "@t", 0.6], ["*", 0.15, ["sin", ["*", "@t", 3.14159]]]], "segments": 10}},
            {"id": "whip", "type": "tube", "inputs": ["path"], "options": {"radius": 0.04, "sides": 8, "cap": "quad"}},
            {"id": "whip-cr", "type": "crease", "inputs": ["whip"], "options": {"sharpness": 2.4, "minAngle": 50}},
            {"id": "whip-sds", "type": "subdivide", "inputs": ["whip-cr"], "options": {"levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1}},

            {"id": "bracket", "type": "extrude",
             "options": {"profile": [[0, 0], [0.4, 0], [0.4, 0.12], [0.18, 0.35], [0, 0.35]], "depth": 0.06, "cap": "inset", "capInset": 0.18, "chart": "perimeter"}},
            {"id": "bracket-cr", "type": "crease", "inputs": ["bracket"], "options": {"sharpness": 2.2, "minAngle": 35}},
            {"id": "bracket-sds", "type": "subdivide", "inputs": ["bracket-cr"], "options": {"levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1}},
            {"id": "bracket-xf", "type": "transform", "inputs": ["bracket-sds"], "options": {"translation": [0.6, 0, 0]}},

            {"id": "out", "type": "join", "inputs": ["whip-sds", "bracket-xf"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(2));
    for (const auto& part : r.geometry.parts) {
        const QualityReport q = analyze(part.proto->mesh);
        CHECK(q.clean());
        CHECK_NEAR(q.quad_ratio(), 1.0, 1e-12);
        CHECK_EQ(q.high_valence_poles, std::size_t(0));
        CHECK_EQ(part.proto->uv.size(), part.proto->mesh.corner_count());
    }
}

TEST(doc_loft_sections_extrude_holes_and_geo_selection) {
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "sq2rd", "type": "loft", "options": {
                "sections": [
                    {"profile": [[-0.25, -0.25], [0.25, -0.25], [0.25, 0.25], [-0.25, 0.25]], "z": 0.0},
                    {"profile": [[-0.20, -0.20], [0.20, -0.20], [0.20, 0.20], [-0.20, 0.20]], "z": 0.08},
                    {"profile": [[0.14, 0.0], [0.1212, 0.07], [0.07, 0.1212], [0.0, 0.14],
                                 [-0.07, 0.1212], [-0.1212, 0.07], [-0.14, 0.0], [-0.1212, -0.07],
                                 [-0.07, -0.1212], [0.0, -0.14], [0.07, -0.1212], [0.1212, -0.07]], "z": 0.32}
                ],
                "segments": 16, "rings": 2, "smooth": 1, "cap": "quad"
            }},
            {"id": "sq2rd-cr-base", "type": "crease", "inputs": ["sq2rd"], "options": {
                "sharpness": 3.8, "minAngle": 0,
                "selection": {"plane": {"normal": [0, 0, 1], "dist": 0.0, "tol": 0.005}}
            }},
            {"id": "sq2rd-cr-top", "type": "crease", "inputs": ["sq2rd-cr-base"], "options": {
                "sharpness": 3.2, "minAngle": 0,
                "selection": {"zRange": [0.31, 0.33], "radius": {"axis": "z", "center": [0, 0, 0], "min": 0.10, "max": 0.16}}
            }},
            {"id": "sq2rd-sds", "type": "subdivide", "inputs": ["sq2rd-cr-top"], "options": {
                "levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1, "lodLevels": [2, 1, 0]
            }},

            {"id": "annulus-quad", "type": "extrude", "options": {
                "profile": [[0.20, 0.0], [0.1414, 0.1414], [0.0, 0.20], [-0.1414, 0.1414],
                            [-0.20, 0.0], [-0.1414, -0.1414], [0.0, -0.20], [0.1414, -0.1414]],
                "holes": [[[0.08, 0.0], [0.0566, 0.0566], [0.0, 0.08], [-0.0566, 0.0566],
                           [-0.08, 0.0], [-0.0566, -0.0566], [0.0, -0.08], [0.0566, -0.0566]]],
                "depth": 0.06, "cap": "quad"
            }},
            {"id": "annulus-xf", "type": "transform", "inputs": ["annulus-quad"], "options": {"translation": [0.7, 0, 0]}},

            {"id": "pierced-plate", "type": "extrude", "options": {
                "profile": [[-0.30, -0.20], [0.30, -0.20], [0.22, 0.28], [-0.22, 0.28]],
                "holes": [
                    [[0.07, 0.06], [0.0, 0.13], [-0.07, 0.06], [0.0, -0.01]],
                    [[-0.14, -0.06], [-0.18, -0.02], [-0.22, -0.06], [-0.18, -0.10]]
                ],
                "depth": 0.05
            }},
            {"id": "pierced-xf", "type": "transform", "inputs": ["pierced-plate"], "options": {"translation": [1.4, 0, 0]}},

            {"id": "out", "type": "join", "inputs": ["sq2rd-sds", "annulus-xf", "pierced-xf"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));

    // 1. Lofted square-to-round housing + selective plane/zRange/radius creases + SDS LODs
    const QualityReport q0 = analyze(r.geometry.parts[0].proto->mesh);
    CHECK(q0.clean());
    CHECK_NEAR(q0.quad_ratio(), 1.0, 1e-12);
    CHECK(q0.creased_edges > 0);
    CHECK(q0.volume > 0.02);
    CHECK_EQ(r.geometry.parts[0].proto->lods.size(), std::size_t(2));  // LOD1 + LOD2 (LOD0 is proto->mesh)

    // 2. All-quad annular extrude with matching hole
    const QualityReport q1 = analyze(r.geometry.parts[1].proto->mesh);
    CHECK(q1.clean());
    CHECK_NEAR(q1.quad_ratio(), 1.0, 1e-12);
    CHECK_EQ(q1.high_valence_poles, std::size_t(0));
    CHECK(q1.volume > 0.005);

    // 3. Multi-hole pierced cheek plate
    const QualityReport q2 = analyze(r.geometry.parts[2].proto->mesh);
    CHECK(q2.clean());
    CHECK(q2.volume > 0.008);
}

TEST(doc_sds_wpia_interpolate_subdiv_normals_and_tuned_ev) {
    // Compare classic CC vs. W-PIA interpolate=1 + subdivNormals=1 + tunedEV=1 on a 12-sided cylinder & creased cube.
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "cyl-raw", "type": "cylinder", "options": {"radius": 0.20, "depth": 0.40, "sides": 12, "rings": 2, "cap": "quad"}},
            {"id": "cyl-cr", "type": "crease", "inputs": ["cyl-raw"], "options": {"sharpness": 3.0, "minAngle": 60}},
            {"id": "cyl-classic", "type": "subdivide", "inputs": ["cyl-cr"], "options": {
                "levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1
            }},
            {"id": "cyl-wpia", "type": "subdivide", "inputs": ["cyl-cr"], "options": {
                "levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1,
                "interpolate": 1, "subdivNormals": 1, "tunedEV": 1
            }},
            {"id": "box-raw", "type": "cube", "options": {"size": [0.30, 0.30, 0.30]}},
            {"id": "box-cr", "type": "crease", "inputs": ["box-raw"], "options": {
                "sharpness": 2.0, "minAngle": 45, "selection": {"box": [[-0.2, -0.2, 0.0], [0.2, 0.05, 0.2]]}
            }},
            {"id": "box-tuned", "type": "subdivide", "inputs": ["box-cr"], "options": {
                "levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1,
                "subdivNormals": 1, "tunedEV": 1
            }},
            {"id": "out", "type": "join", "inputs": ["cyl-classic", "cyl-wpia", "box-tuned"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));

    auto max_radial_r = [](const Mesh& m) {
        double rmax = 0.0;
        for (const Vec3& p : m.positions) rmax = std::max(rmax, std::hypot(p.x, p.y));
        return rmax;
    };
    const Mesh& m_classic = r.geometry.parts[0].proto->mesh;
    const Mesh& m_wpia = r.geometry.parts[1].proto->mesh;
    const Mesh& m_box = r.geometry.parts[2].proto->mesh;

    CHECK(analyze(m_classic).clean());
    CHECK(analyze(m_wpia).clean());
    CHECK(analyze(m_box).clean());
    CHECK_NEAR(analyze(m_wpia).quad_ratio(), 1.0, 1e-12);
    CHECK_NEAR(analyze(m_box).quad_ratio(), 1.0, 1e-12);

    // Classic 12-sided CC shrinks noticeably below R=0.20; W-PIA interpolate=1 recovers exact R=0.200!
    const double r_classic = max_radial_r(m_classic);
    const double r_wpia = max_radial_r(m_wpia);
    CHECK(r_classic < 0.197);
    CHECK(std::abs(r_wpia - 0.20) < 0.0015);

    // Subdivision shading normals exist and are unit-length at every corner.
    CHECK_EQ(r.geometry.parts[1].proto->normals.size(), m_wpia.corner_count());
    CHECK_EQ(r.geometry.parts[2].proto->normals.size(), m_box.corner_count());
    for (const auto& fn : r.geometry.parts[2].proto->normals) {
        const double l = std::sqrt(double(fn[0]) * fn[0] + double(fn[1]) * fn[1] + double(fn[2]) * fn[2]);
        CHECK(std::abs(l - 1.0) < 1e-4);
    }
}

TEST(doc_sds_nonuniform_nurss_and_transfer_normals) {
    // 1. Wide flat panel (1.6 m) next to tight 2.5 cm bevel with NO extra support loops:
    //    Uniform CC pillows/domes the wide panel; NURSS (nonUniform=1) keeps it dramatically flatter!
    // 2. transferNormals: transfer smooth cylindrical normals from an un-pierced clean proxy cylinder
    //    onto a shrinkwrapped curved patch -> every patch corner aligns with exact radial cylinder normal!
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "panel-raw", "type": "cube", "options": {"size": [1.60, 0.80, 0.08]}},
            {"id": "panel-bev", "type": "chamfer", "inputs": ["panel-raw"], "options": {"width": 0.025, "segments": 1}},
            {"id": "panel-unif", "type": "subdivide", "inputs": ["panel-bev"], "options": {"levels": 2, "limit": 1, "limitNormals": 1}},
            {"id": "panel-nurss", "type": "subdivide", "inputs": ["panel-bev"], "options": {"levels": 2, "nonUniform": 1, "limit": 1, "limitNormals": 1}},

            {"id": "proxy-raw", "type": "cylinder", "options": {"radius": 0.40, "depth": 0.80, "sides": 32, "cap": "quad"}},
            {"id": "proxy-sds", "type": "subdivide", "inputs": ["proxy-raw"], "options": {"levels": 1, "limit": 1, "limitNormals": 1}},

            {"id": "patch-raw", "type": "cube", "options": {"size": [0.20, 0.04, 0.20], "divisions": [4, 2, 4]}},
            {"id": "patch-pos", "type": "transform", "inputs": ["patch-raw"], "options": {"translation": [0.0, -0.40, 0.0]}},
            {"id": "patch-wrap", "type": "shrinkwrap", "inputs": ["patch-pos", "proxy-sds"], "options": {"offset": 0.005, "selection": {"normal": [0, -1, 0], "angle": 40}}},
            {"id": "patch-tn", "type": "transferNormals", "inputs": ["patch-wrap", "proxy-sds"], "options": {
                "maxDistance": 0.03, "blend": 1.0, "selection": {"normal": [0, -1, 0], "angle": 45}
            }},

            {"id": "out", "type": "join", "inputs": ["panel-unif", "panel-nurss", "patch-tn"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));

    // Measure flatness of the top panel interior (|x| < 0.65, |y| < 0.25, z > 0.0) relative to authored z = 0.040.
    auto interior_sag = [](const Mesh& m) {
        double max_sag = 0.0;
        for (const Vec3& p : m.positions) {
            if (std::abs(p.x) < 0.65 && std::abs(p.y) < 0.25 && p.z > 0.0) {
                max_sag = std::max(max_sag, std::abs(0.040 - p.z));
            }
        }
        return max_sag;
    };
    const Mesh& m_unif = r.geometry.parts[0].proto->mesh;
    const Mesh& m_nurss = r.geometry.parts[1].proto->mesh;
    CHECK(analyze(m_unif).clean());
    CHECK(analyze(m_nurss).clean());
    CHECK_NEAR(analyze(m_nurss).quad_ratio(), 1.0, 1e-12);

    const double sag_unif = interior_sag(m_unif);
    const double sag_nurss = interior_sag(m_nurss);
    // NURSS chord-length knots keep the wide panel dramatically flatter than uniform CC!
    CHECK(sag_nurss < sag_unif * 0.45);
    CHECK(sag_nurss < 0.0015);

    // Check transferNormals wrote exact unit normals matching the radial cylinder normal on the front face.
    const doc::Proto& pt = *r.geometry.parts[2].proto;
    CHECK_EQ(pt.normals.size(), pt.mesh.corner_count());
    int checked_front = 0;
    for (std::size_t f = 0; f < pt.mesh.face_count(); ++f) {
        if (face_normal(pt.mesh, f).y > -0.7) continue;
        for (Index c = pt.mesh.face_offsets[f]; c < pt.mesh.face_offsets[f + 1]; ++c) {
            const Vec3 pos = pt.mesh.positions[pt.mesh.face_indices[c]];
            const Vec3 expected = normalize_or({pos.x, pos.y, 0.0}, {0, -1, 0});
            const auto& fn = pt.normals[c];
            const Vec3 got{fn[0], fn[1], fn[2]};
            CHECK(dot(got, expected) > 0.99);
            ++checked_front;
        }
    }
    CHECK(checked_front > 16);
}

TEST(doc_sds_rational_and_adaptive_flat_coarsening) {
    // 1. Rational R^4 Catmull-Clark: assigning higher vertexWeight=4.0 to the top face pulls the
    //    subdivided surface cleanly toward the heavy vertices (exact projective NURBS behavior!).
    // 2. Feature-Adaptive flat coarsening (`adaptive=1`): collapses flat regular interior 2x2 quad
    //    blocks on a subdivided beveled panel, slashing face count while staying 100% watertight & clean!
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "box-a", "type": "cube", "options": {"size": [0.40, 0.40, 0.40]}},
            {"id": "box-unif", "type": "subdivide", "inputs": ["box-a"], "options": {"levels": 2, "limit": 1}},
            {"id": "box-wt", "type": "crease", "inputs": ["box-a"], "options": {
                "sharpness": 0.0, "vertexWeight": 4.0, "minAngle": 0,
                "selection": {"plane": {"normal": [0, 0, 1], "dist": 0.20, "tol": 0.01}}
            }},
            {"id": "box-rat", "type": "subdivide", "inputs": ["box-wt"], "options": {"levels": 2, "rational": 1, "limit": 1}},

            {"id": "plate-raw", "type": "cube", "options": {"size": [1.40, 0.80, 0.10]}},
            {"id": "plate-cr", "type": "crease", "inputs": ["plate-raw"], "options": {"sharpness": 3.0, "minAngle": 45}},
            {"id": "plate-full", "type": "subdivide", "inputs": ["plate-cr"], "options": {"levels": 4, "limit": 1, "limitNormals": 1}},
            {"id": "plate-adap", "type": "subdivide", "inputs": ["plate-cr"], "options": {"levels": 4, "limit": 1, "limitNormals": 1, "adaptive": 1, "adaptiveAngle": 2.0}},

            {"id": "out", "type": "join", "inputs": ["box-unif", "box-rat", "plate-full", "plate-adap"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(4));

    const Mesh& m_unif = r.geometry.parts[0].proto->mesh;
    const Mesh& m_rat = r.geometry.parts[1].proto->mesh;
    const Mesh& m_full = r.geometry.parts[2].proto->mesh;
    const Mesh& m_adap = r.geometry.parts[3].proto->mesh;

    CHECK(analyze(m_rat).clean());
    CHECK(analyze(m_full).clean());
    CHECK(analyze(m_adap).clean());

    // Rational weight=4.0 on top vertices pulls top max Z measurably higher than uniform weight=1.0!
    auto max_z = [](const Mesh& m) {
        double z = -1e9;
        for (const Vec3& p : m.positions) z = std::max(z, p.z);
        return z;
    };
    CHECK(max_z(m_rat) > max_z(m_unif) + 0.015);

    // Adaptive coarsening significantly reduces placed triangle count on flat interiors while keeping clean watertightness!
    auto tri_count = [](const Mesh& m) {
        std::size_t t = 0;
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            const std::size_t k = m.face(f).size();
            if (k >= 3) t += k - 2;
        }
        return t;
    };
    CHECK(tri_count(m_adap) < tri_count(m_full) * 70 / 100);
    CHECK_EQ(r.geometry.parts[3].proto->normals.size(), m_adap.corner_count());
}

TEST(doc_bridge_loops_quad_fill_hole_and_curvature) {
    // 1. bridgeLoops: delete top face of cube A & bottom face of rotated/offset cube B -> bridge with 5 smooth
    //    Hermite quad rings + Chaikin SDS -> ONE clean, watertight, 100%-quad continuous casting!
    // 2. quadFillHole: delete top face of a 12-sided cylinder -> close with 3x3 all-quad Coons-patch dome -> 0 high-valence poles!
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "a-raw", "type": "cube", "options": {"size": [0.30, 0.30, 0.14]}},
            {"id": "a-open", "type": "deleteFaces", "inputs": ["a-raw"], "options": {"selection": {"normal": [0, 0, 1], "angle": 20}}},

            {"id": "b-raw", "type": "cube", "options": {"size": [0.22, 0.22, 0.14]}},
            {"id": "b-xf", "type": "transform", "inputs": ["b-raw"], "options": {"translation": [0.22, 0.0, 0.44], "rotation": [0, 0.65, 0]}},
            {"id": "b-open", "type": "deleteFaces", "inputs": ["b-xf"], "options": {"selection": {"normal": [-0.6, 0, -0.8], "angle": 35}}},

            {"id": "bridged", "type": "bridgeLoops", "inputs": ["a-open", "b-open"], "options": {"segments": 5, "smooth": 1, "tension": 0.5, "profileScale": 0.88}},
            {"id": "bridged-cr", "type": "crease", "inputs": ["bridged"], "options": {"sharpness": 3.0, "minAngle": 55, "selection": {"plane": {"normal": [0, 0, 1], "dist": -0.07, "tol": 0.005}}}},
            {"id": "bridged-sds", "type": "subdivide", "inputs": ["bridged-cr"], "options": {"levels": 2, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1, "subdivNormals": 1}},

            {"id": "cyl-raw", "type": "cylinder", "options": {"radius": 0.20, "depth": 0.30, "sides": 12, "cap": "quad"}},
            {"id": "cyl-open", "type": "deleteFaces", "inputs": ["cyl-raw"], "options": {"selection": {"normal": [0, 0, 1], "angle": 20}}},
            {"id": "cyl-filled", "type": "quadFillHole", "inputs": ["cyl-open"], "options": {"bulge": 0.08}},
            {"id": "cyl-sds", "type": "subdivide", "inputs": ["cyl-filled"], "options": {"levels": 1, "limit": 1, "limitNormals": 1}},
            {"id": "cyl-xf", "type": "transform", "inputs": ["cyl-sds"], "options": {"translation": [0.75, 0, 0.15]}},

            {"id": "out", "type": "join", "inputs": ["bridged-sds", "cyl-xf"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(2));

    const QualityReport q0 = analyze(r.geometry.parts[0].proto->mesh);
    CHECK(q0.clean());
    CHECK_NEAR(q0.quad_ratio(), 1.0, 1e-12);
    CHECK(q0.volume > 0.015);

    const QualityReport q1 = analyze(r.geometry.parts[1].proto->mesh);
    CHECK(q1.clean());
    CHECK_NEAR(q1.quad_ratio(), 1.0, 1e-12);
    CHECK_EQ(q1.high_valence_poles, std::size_t(0));
    CHECK_EQ(q1.pinched_poles, std::size_t(0));
}

TEST(doc_sds_g2_fair_hierarchical_edits_and_displace_details) {
    // 1. g2Fair=1: quadratic osculating G2 EV completion on an extruded boss box -> clean 100%-quad & lower EV curvature!
    // 2. Pixar hierarchical edits: inset+recess panel at level 1 + fluted ribs at level 1, smoothed cleanly to level 3!
    // 3. displaceDetails: bake a 0x8007 detailStamp boss into REAL 3D geometry along analytical limit normals!
    const std::string doc_s = R"({
        "version": 1,
        "nodes": [
            {"id": "box-raw", "type": "cube", "options": {"size": [0.40, 0.40, 0.30]}},
            {"id": "box-g2", "type": "subdivide", "inputs": ["box-raw"], "options": {
                "levels": 2, "limit": 1, "limitNormals": 1, "g2Fair": 1, "g2Weight": 0.7
            }},

            {"id": "hier-cyl", "type": "cylinder", "options": {"radius": 0.22, "depth": 0.44, "sides": 12, "rings": 2, "cap": "quad"}},
            {"id": "hier-cr", "type": "crease", "inputs": ["hier-cyl"], "options": {"sharpness": 3.5, "minAngle": 60}},
            {"id": "hier-sds", "type": "subdivide", "inputs": ["hier-cr"], "options": {
                "levels": 3, "creaseMethod": "chaikin", "limit": 1, "limitNormals": 1, "g2Fair": 1,
                "edits": [
                    {"level": 1, "selection": {"zRange": [-0.14, 0.14]}, "flutes": {"axis": "z", "count": 12, "amplitude": 0.012}},
                    {"level": 1, "selection": {"normal": [0, 0, 1], "angle": 20}, "inset": 0.012, "offset": -0.014, "crease": 2.0}
                ]
            }},

            {"id": "plate-raw", "type": "cube", "options": {"size": [0.40, 0.40, 0.08]}},
            {"id": "boss-pt", "type": "pointArray", "options": {"positions": [[0, 0, 0.06]]}},
            {"id": "plate-det", "type": "detailStamp", "inputs": ["plate-raw", "boss-pt"], "options": {
                "shape": "disc", "profile": "dome", "radius": 0.10, "height": 0.025
            }},
            {"id": "plate-disp", "type": "displaceDetails", "inputs": ["plate-det"], "options": {
                "levels": 2, "scale": 1.0, "clearDetails": 1
            }},

            {"id": "out", "type": "join", "inputs": ["box-g2", "hier-sds", "plate-disp"]}
        ],
        "output": "out"
    })";
    doc::Library lib(".");
    const doc::Result r = doc::evaluate(json::parse(doc_s), lib);
    CHECK_EQ(r.geometry.parts.size(), std::size_t(3));

    const QualityReport q0 = analyze(r.geometry.parts[0].proto->mesh);
    CHECK(q0.clean());
    CHECK_NEAR(q0.quad_ratio(), 1.0, 1e-12);

    const QualityReport q1 = analyze(r.geometry.parts[1].proto->mesh);
    CHECK(q1.clean());
    CHECK_NEAR(q1.quad_ratio(), 1.0, 1e-12);
    CHECK(q1.faces > 1000);

    // displaceDetails baked the 25 mm dome stamp into REAL +Z vertex geometry (> 0.04 + 0.015 = 0.055)!
    const Mesh& m2 = r.geometry.parts[2].proto->mesh;
    CHECK(analyze(m2).clean());
    CHECK(r.geometry.parts[2].proto->details.empty());
    double max_z = -1e9;
    for (const Vec3& p : m2.positions) max_z = std::max(max_z, p.z);
    CHECK(max_z > 0.055);
}
