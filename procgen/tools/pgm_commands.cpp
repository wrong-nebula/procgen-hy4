#include "pgm_commands.hpp"

#include "pg/audit.hpp"
#include "pg/emat.hpp"
#include "pg/emesh.hpp"
#include "pg/quality.hpp"
#include "pg/topology.hpp"
#include "preview.hpp"

#include <cstdlib>
#include <fstream>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <thread>

namespace pgm {
namespace {

using pg::Aabb;
using pg::Vec3;
using pg::json::Writer;
namespace doc = pg::doc;
namespace fs = std::filesystem;

double now_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void write_vec(Writer& w, Vec3 v) { w.begin_array().value(v.x).value(v.y).value(v.z).end_array(); }

// Field names match the reference evaluator's receipt.
void write_receipt(Writer& w, const doc::Result& r) {
    const doc::Receipt rc = doc::make_receipt(r.geometry);
    w.field("evaluatedNodes", r.evaluated_nodes).field("instances", rc.instances).field("prototypes", rc.prototypes);
    w.field("prototypeVertices", rc.prototype_vertices).field("placedTriangles", rc.placed_triangles);
    if (rc.details) w.field("details", rc.details).field("placedDetails", rc.placed_details);
    w.field("placedLooseEdges", rc.placed_loose_edges);
    w.key("boundsZUp");
    if (rc.has_bounds) { w.begin_array(); write_vec(w, rc.lo); write_vec(w, rc.hi); w.end_array(); }
    else w.null();
}

// Four-axis efficiency receipt (speed, incremental work, structural reuse, wire pack bytes).
void write_efficiency(Writer& w, const doc::Result& r, const std::string& source_path, double eval_ms) {
    pg::emesh::WriteOptions wo;
    wo.source = source_path;
    pg::emesh::WriteStats st;
    const double t_pack0 = now_ms();
    (void)pg::emesh::write(r.geometry, wo, &st);
    const double pack_ms = now_ms() - t_pack0;
    const std::size_t total_nodes = r.evaluated_nodes + r.reused_nodes;
    const double settled_frac = total_nodes > 0 ? static_cast<double>(r.reused_nodes) / static_cast<double>(total_nodes) : 0.0;
    const double reuse_ratio = st.meshes > 0 ? static_cast<double>(st.instances) / static_cast<double>(st.meshes) : 0.0;
    const double bytes_per_tri = st.triangles > 0 ? static_cast<double>(st.bytes) / static_cast<double>(st.triangles) : 0.0;

    w.begin_object();
    w.key("speed").begin_object().field("evalMS", eval_ms).field("packMS", pack_ms).end_object();
    w.key("work").begin_object()
        .field("dirtyNodes", r.evaluated_nodes)
        .field("settledNodes", r.reused_nodes)
        .field("settledFraction", settled_frac)
        .end_object();
    w.key("structure").begin_object()
        .field("dedupedMeshes", st.meshes)
        .field("sourcePrototypes", st.source_prototypes)
        .field("instances", st.instances)
        .field("batches", st.batches)
        .field("instanceReuseRatio", reuse_ratio)
        .field("prototypeVertices", st.vertices)
        .field("placedTriangles", st.triangles)
        .end_object();
    w.key("wire").begin_object()
        .field("emeshBytes", st.bytes)
        .field("bytesPerPlacedTriangle", bytes_per_tri)
        .field("details", st.details)
        .end_object();
    w.end_object();
}

int cmd_evaluate(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document");
    const double t0 = now_ms();
    const doc::Result r = doc::evaluate_file(path, a.controls);
    const double eval_ms = now_ms() - t0;
    w.begin_object().field("passed", true);
    write_receipt(w, r);
    w.field("evaluationMS", eval_ms);
    if (a.has("efficiency")) {
        w.key("efficiency");
        write_efficiency(w, r, path, eval_ms);
    }
    w.end_object();
    return 0;
}

int cmd_conformance(const Args& a, Writer& w) {
    const std::string dir = a.at(0, "directory");
    const pg::json::Value receipts = pg::json::parse_file(a.at(1, "receipts.json"));
    std::size_t matched = 0, total = 0;
    Writer issues;
    issues.begin_array();
    for (const pg::json::Member& m : receipts.object) {
        ++total;
        std::vector<std::string> diffs;
        try {
            const doc::Result r = doc::evaluate_file(dir + "/" + m.key + ".json");
            const doc::Receipt rc = doc::make_receipt(r.geometry);
            auto expect = [&](const char* field, std::size_t got) {
                const pg::json::Value* v = m.value.find(field);
                if (!v || static_cast<std::size_t>(v->number) != got)
                    diffs.push_back(std::string(field) + " expected " + (v ? std::to_string(static_cast<long long>(v->number)) : "?") + " got " + std::to_string(got));
            };
            expect("instances", rc.instances);
            expect("prototypes", rc.prototypes);
            expect("prototypeVertices", rc.prototype_vertices);
            expect("placedTriangles", rc.placed_triangles);
            expect("placedLooseEdges", rc.placed_loose_edges);
            expect("evaluatedNodes", r.evaluated_nodes);
            if (const pg::json::Value* b = m.value.find("boundsZUp"); b && b->is_array() && rc.has_bounds) {
                for (int side = 0; side < 2; ++side)
                    for (int k = 0; k < 3; ++k) {
                        const double want = b->array[static_cast<std::size_t>(side)].array[static_cast<std::size_t>(k)].number;
                        const double got = (side == 0 ? rc.lo : rc.hi)[k];
                        if (std::abs(want - got) > 1e-3 + 1e-5 * std::abs(want))
                            diffs.push_back("bounds[" + std::to_string(side) + "][" + std::to_string(k) + "] expected " + std::to_string(want) + " got " + std::to_string(got));
                    }
            }
        } catch (const std::exception& e) {
            diffs.push_back(std::string("error: ") + e.what());
        }
        if (diffs.empty()) { ++matched; continue; }
        issues.begin_object().field("document", m.key).key("differences").begin_array();
        for (const std::string& d : diffs) issues.value(d);
        issues.end_array().end_object();
    }
    issues.end_array();
    w.begin_object().field("passed", matched == total).field("documents", total).field("matched", matched);
    w.key("issues").raw(issues.str()).end_object();
    return matched == total ? 0 : 1;
}


std::string directory_of(const std::string& path) {
    const std::size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? "." : path.substr(0, slash);
}
bool ends_with(const std::string& s, const char* suffix) {
    const std::size_t n = std::strlen(suffix);
    return s.size() >= n && s.compare(s.size() - n, n, suffix) == 0;
}

int cmd_inspect(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document");
    const pg::json::Value document = pg::json::parse_file(path);
    doc::Library library(directory_of(path));
    const doc::Result r = doc::evaluate(document, library, a.controls);
    w.begin_object().field("passed", true).field("document", path);
    w.key("controls").begin_object();
    if (const pg::json::Value* controls = document.find("controls"))
        for (const pg::json::Member& m : controls->object) {
            w.key(m.key).begin_object();
            for (const char* k : {"value", "min", "max"})
                if (const pg::json::Value* v = m.value.find(k)) w.field(k, v->number);
            if (const pg::json::Value* v = m.value.find("integer"); v && v->boolean) w.field("integer", true);
            w.field("resolved", static_cast<double>(r.controls.at(m.key))).end_object();
        }
    w.end_object();
    // Provenance recorded by the kit author: atlas recipes, atlas sources, plate library ids, notes.
    if (const pg::json::Value* refs = document.find("references"); refs && refs->is_object()) {
        w.key("references").begin_object();
        for (const pg::json::Member& m : refs->object) {
            if (m.value.is_string()) { w.field(m.key, m.value.string); continue; }
            if (!m.value.is_array()) continue;
            w.key(m.key).begin_array();
            for (const pg::json::Value& v : m.value.array)
                if (v.is_string()) w.value(v.string);
            w.end_array();
        }
        w.end_object();
    }
    w.key("moduleImports").begin_object();
    if (const pg::json::Value* imports = document.find("moduleImports"))
        for (const pg::json::Member& m : imports->object) w.field(m.key, m.value.string);
    w.end_object();
    w.key("ports").begin_array();
    for (const doc::PortFrame& p : doc::module_ports(document, r.controls)) {
        w.begin_object().field("name", p.name).field("type", p.type);
        w.key("origin"); write_vec(w, p.origin);
        w.key("normal"); write_vec(w, p.normal);
        w.key("up"); write_vec(w, p.up);
        w.key("dimensions").begin_object();
        for (const auto& [k, v] : p.dimensions) w.field(k, static_cast<double>(v));
        w.end_object().end_object();
    }
    w.end_array();
    // Node types in the document and its groups.
    std::map<std::string, std::size_t> types;
    auto count = [&](const pg::json::Value* nodes) {
        if (!nodes) return;
        for (const pg::json::Value& n : nodes->array)
            if (const pg::json::Value* t = n.find("type"); t && t->is_string()) ++types[t->string];
    };
    count(document.find("nodes"));
    if (const pg::json::Value* groups = document.find("groups"))
        for (const pg::json::Member& g : groups->object) count(g.value.find("nodes"));
    w.key("nodeTypes").begin_object();
    for (const auto& [t, n] : types) w.field(t, n);
    w.end_object();
    // Placements and triangles per material slot.
    std::map<std::uint16_t, std::pair<std::size_t, std::size_t>> slots;
    for (const doc::Part& part : r.geometry.parts) {
        const std::size_t tris = part.proto->triangle_count();
        if (!tris) continue;
        auto& s = slots[part.material];
        ++s.first;
        s.second += tris;
    }
    w.key("materials").begin_array();
    for (const auto& [slot, s] : slots)
        w.begin_object().field("slot", doc::slot_name(slot)).field("placements", s.first).field("triangles", s.second).end_object();
    w.end_array();
    w.key("receipt").begin_object();
    write_receipt(w, r);
    w.end_object().end_object();
    return 0;
}

struct Tally {
    std::size_t prototypes = 0, closed = 0, open = 0, nonmanifold = 0, inconsistent = 0, inside_out = 0, degenerate = 0, intersecting = 0;
    std::size_t tri_faces = 0, quad_faces = 0, ngon_faces = 0;
    std::size_t poles_v3 = 0, poles_v5 = 0, high_valence_poles = 0;
    std::size_t creased_edges = 0, creased_vertices = 0, sds_cages = 0, multi_lod = 0;
    std::size_t pinched_poles = 0;
    double min_edge_length = std::numeric_limits<double>::infinity();
    double max_edge_ratio = 0.0;
    double max_ev_curvature = 0.0;
    [[nodiscard]] bool clean(bool allow_open = false) const {
        return (allow_open || open == 0) && nonmanifold == 0 && inconsistent == 0 && inside_out == 0 && intersecting == 0;
    }
};

// Topology of every distinct prototype; `issues` (optional) lists the offending parts, at most 40.
Tally tally(const doc::Result& r, bool intersections, Writer* issues, bool allow_open = false) {
    std::set<const doc::Proto*> seen;
    Tally t;
    std::size_t listed = 0;
    for (const doc::Part& part : r.geometry.parts) {
        const doc::Proto& proto = *part.proto;
        if (proto.mesh.face_count() == 0 || !seen.insert(&proto).second) continue;
        ++t.prototypes;
        pg::QualityOptions qo;
        qo.check_self_intersections = intersections;
        const pg::QualityReport q = pg::analyze(proto.mesh, qo);
        t.tri_faces += q.tri_faces;
        t.quad_faces += q.quad_faces;
        t.ngon_faces += q.ngon_faces;
        t.poles_v3 += q.poles_valence_3;
        t.poles_v5 += q.poles_valence_5;
        t.high_valence_poles += q.high_valence_poles;
        t.creased_edges += q.creased_edges;
        t.creased_vertices += q.creased_vertices;
        t.pinched_poles += q.pinched_poles;
        t.max_ev_curvature = std::max(t.max_ev_curvature, q.max_ev_curvature);
        if (q.edge_lengths.count > 0) {
            t.min_edge_length = std::min(t.min_edge_length, q.edge_lengths.min);
            t.max_edge_ratio = std::max(t.max_edge_ratio, q.edge_lengths.ratio());
        }
        if (proto.sds_cage) ++t.sds_cages;
        if (!proto.lods.empty()) ++t.multi_lod;
        const bool is_closed = q.boundary_edges == 0;
        t.closed += is_closed ? 1 : 0;
        t.open += is_closed ? 0 : 1;
        const bool flipped = is_closed && q.nonmanifold_edges == 0 && q.inconsistent_edges == 0 && q.volume < 0;
        t.nonmanifold += q.nonmanifold_edges ? 1 : 0;
        t.inconsistent += q.inconsistent_edges ? 1 : 0;
        t.inside_out += flipped ? 1 : 0;
        t.degenerate += q.degenerate_faces ? 1 : 0;
        t.intersecting += q.self_intersections.intersecting_pairs ? 1 : 0;
        const bool bad_open = !allow_open && !is_closed;
        if (issues && (bad_open || q.nonmanifold_edges || q.inconsistent_edges || flipped || q.self_intersections.intersecting_pairs) && listed < 40) {
            ++listed;
            std::string src_label = part.source ? *part.source : std::string();
            if (!proto.face_sources.empty()) {
                std::set<std::string> distinct;
                for (const auto& fs : proto.face_sources)
                    if (fs && !fs->empty() && distinct.size() < 6) distinct.insert(*fs);
                if (!distinct.empty()) {
                    src_label = doc::slot_name(part.material) + " (";
                    bool first = true;
                    for (const std::string& s : distinct) {
                        if (!first) src_label += ", ";
                        src_label += s;
                        first = false;
                    }
                    src_label += ")";
                }
            }
            issues->begin_object().field("source", src_label).field("material", doc::slot_name(part.material));
            issues->field("boundaryEdges", q.boundary_edges);
            issues->field("nonManifoldEdges", q.nonmanifold_edges);
            issues->field("inconsistentEdges", q.inconsistent_edges).field("insideOut", flipped);
            issues->field("selfIntersections", q.self_intersections.intersecting_pairs).end_object();
        }
    }
    return t;
}

// Every document under a path (a file, or a directory searched recursively; modules.json is a manifest, not a document).
std::vector<std::string> documents_under(const std::string& path) {
    std::vector<std::string> out;
    if (!fs::is_directory(path)) return {path};
    for (const auto& e : fs::recursive_directory_iterator(path))
        if (e.is_regular_file() && e.path().extension() == ".json" && e.path().filename() != "modules.json") out.push_back(e.path().string());
    std::sort(out.begin(), out.end());
    return out;
}

// The settings a sweep tries: defaults, then each control at its range ends (one at a time), and every value of a
// small integer control (a variant).
std::vector<doc::Controls> sweep_cases(const pg::json::Value& d) {
    std::vector<doc::Controls> cases{{}};
    const pg::json::Value* controls = d.find("controls");
    if (!controls) return cases;
    for (const pg::json::Member& m : controls->object) {
        const pg::json::Value* lo = m.value.find("min");
        const pg::json::Value* hi = m.value.find("max");
        const pg::json::Value* integer = m.value.find("integer");
        if (!lo || !hi || !lo->is_number() || !hi->is_number()) continue;
        const double a = lo->number, b = hi->number;
        std::vector<double> values{a, b};
        if (integer && integer->kind == pg::json::Value::Kind::Bool && integer->boolean && b - a <= 4)
            for (double v = a + 1; v < b; v += 1) values.push_back(v);
        for (double v : values) cases.push_back({{m.key, static_cast<float>(v)}});
    }
    return cases;
}

int cmd_sweep(const Args& a, Writer& w) {
    const bool intersections = a.has("intersections");
    std::size_t documents = 0, cases = 0, failed = 0;
    Writer failures;
    failures.begin_array();
    for (const std::string& path : documents_under(a.at(0, "path"))) {
        const pg::json::Value d = pg::json::parse_file(path);
        if (!d.find("nodes")) continue;  // a sheet or another file that is not a geometry document
        ++documents;
        for (const doc::Controls& c : sweep_cases(d)) {
            ++cases;
            std::string error;
            try {
                const doc::Result r = doc::evaluate_file(path, c);
                if (!tally(r, intersections, nullptr).clean()) error = "not a clean solid (run pgm check with these settings)";
            } catch (const std::exception& e) {
                error = e.what();
            }
            if (error.empty()) continue;
            if (++failed > 40) continue;
            failures.begin_object().field("document", path).key("set").begin_object();
            for (const auto& [k, v] : c) failures.field(k, v);
            failures.end_object().field("error", error).end_object();
        }
    }
    failures.end_array();
    w.begin_object().field("passed", failed == 0).field("documents", documents).field("cases", cases).field("failed", failed);
    w.key("failures").raw(failures.str()).end_object();
    return failed == 0 ? 0 : 1;
}

void write_sds(Writer& w, const Tally& t) {
    const std::size_t total_faces = t.tri_faces + t.quad_faces + t.ngon_faces;
    const double qratio = total_faces > 0 ? static_cast<double>(t.quad_faces) / static_cast<double>(total_faces) : 0.0;
    w.key("sds").begin_object()
        .field("triFaces", t.tri_faces)
        .field("quadFaces", t.quad_faces)
        .field("ngonFaces", t.ngon_faces)
        .field("quadRatio", qratio)
        .field("polesValence3", t.poles_v3)
        .field("polesValence5", t.poles_v5)
        .field("highValencePoles", t.high_valence_poles)
        .field("creasedEdges", t.creased_edges)
        .field("creasedVertices", t.creased_vertices)
        .field("sdsCages", t.sds_cages)
        .field("multiLodPrototypes", t.multi_lod)
        .field("minEdgeLength", std::isfinite(t.min_edge_length) ? t.min_edge_length : 0.0)
        .field("maxEdgeRatio", t.max_edge_ratio)
        .field("maxEvCurvature", t.max_ev_curvature)
        .field("pinchedPoles", t.pinched_poles)
        .end_object();
}

int cmd_check(const Args& a, Writer& w) {
    const doc::Result r = doc::evaluate_file(a.at(0, "document"), a.controls);
    const bool intersections = a.has("intersections");
    const bool allow_open = a.has("allow-open");
    Writer issues;
    issues.begin_array();
    const Tally t = tally(r, intersections, &issues, allow_open);
    issues.end_array();
    const bool ok = t.clean(allow_open);
    w.begin_object().field("passed", ok).field("clean", ok).field("prototypes", t.prototypes);
    w.field("closedPrototypes", t.closed).field("openPrototypes", t.open).field("nonManifoldPrototypes", t.nonmanifold);
    w.field("inconsistentPrototypes", t.inconsistent).field("insideOutPrototypes", t.inside_out).field("prototypesWithDegenerateFaces", t.degenerate);
    w.field("selfIntersectionChecked", intersections).field("selfIntersectingPrototypes", t.intersecting);
    write_sds(w, t);
    w.key("issues").raw(issues.str()).end_object();
    return ok ? 0 : 1;
}

int cmd_materials(const Args& a, Writer& w) {
    const pg::emat::Set set = pg::emat::modeler_slots();
    const std::string out = a.get("emat");
    w.begin_object().field("passed", true);
    if (!out.empty()) {
        pg::emat::write_file(out, set);
        w.field("emat", out).field("bytes", pg::emat::write(set).size());
    }
    w.key("materials").begin_array();
    for (const pg::emat::Material& m : set.materials) {
        w.begin_object().field("name", m.name).key("baseColor");
        write_vec(w, m.base_color);
        w.field("opacity", m.opacity).field("metallic", m.metallic).field("roughness", m.roughness);
        w.field("emissiveStrength", m.emissive_strength).field("scattering", m.scattering);
        w.field("blend", m.blend).field("doubleSided", m.double_sided).field("tintable", m.tintable).end_object();
    }
    w.end_array().key("slots").begin_array();
    for (const pg::emat::Binding& b : set.bindings)
        w.begin_object().field("slot", b.slot).field("material", set.materials[b.material].name).end_object();
    w.end_array().end_object();
    return 0;
}

int cmd_catalog(const Args& a, Writer& w) {
    const std::string dir = a.at(0, "directory");
    const doc::Library library(dir);
    std::vector<std::string> files;
    for (const auto& entry : fs::directory_iterator(dir)) {
        const std::string name = entry.path().filename().string();
        if (entry.is_regular_file() && ends_with(name, ".json") && name != "modules.json") files.push_back(name);
    }
    std::sort(files.begin(), files.end());
    w.begin_object().field("passed", true).field("directory", dir);
    // Kits visible from this directory: its own and every kit it imports (root first).
    w.key("kits").begin_array();
    for (const doc::Library::Kit& kit : library.kits()) {
        w.begin_object().field("name", kit.name).field("directory", kit.directory).key("imports").begin_array();
        for (const std::string& target : kit.imports) w.value(target);
        w.end_array().key("modules").begin_array();
        for (const std::string& id : kit.modules) w.value(id);
        w.end_array().end_object();
    }
    w.end_array().key("documents").begin_array();
    for (const std::string& name : files) {
        w.begin_object().field("name", name.substr(0, name.size() - 5));
        try {
            const pg::json::Value d = pg::json::parse_file(dir + "/" + name);
            const pg::json::Value* nodes = d.find("nodes");
            const pg::json::Value* controls = d.find("controls");
            w.field("nodes", nodes ? nodes->array.size() : std::size_t{0});
            w.key("controls").begin_array();
            if (controls)
                for (const pg::json::Member& m : controls->object) w.value(m.key);
            w.end_array();
            const std::string id = name.substr(0, name.size() - 5);
            if (const doc::Library::Module* mod = library.module(id)) {
                w.field("module", true).key("ports").begin_array();
                for (const auto& [port, p] : mod->ports) w.value(port);
                w.end_array().key("portTypes").begin_object();
                for (const auto& [port, p] : mod->ports) w.field(port, p.type);
                w.end_object();
            }
        } catch (const std::exception& e) {
            w.field("error", e.what());
        }
        w.end_object();
    }
    w.end_array().field("modules", library.module_ids().size()).end_object();
    return 0;
}

void write_obj(const std::string& path, const doc::Geometry& g) {
    std::ofstream out(path);
    if (!out) throw std::runtime_error("cannot write " + path);
    out << "# procgen pgm export, Z-up, metres\n";
    std::size_t base = 1, uv_base = 1;
    std::map<const std::string*, int> occurrence;
    for (const doc::Part& part : g.parts) {
        const pg::Mesh& m = part.proto->mesh;
        if (m.face_count() == 0) continue;
        out << "o " << *part.source << "." << occurrence[part.source.get()]++ << "\nusemtl " << doc::slot_name(part.material) << "\n";
        for (const Vec3& p : m.positions) {
            const Vec3 q = part.transform.point(p);
            out << "v " << q.x << ' ' << q.y << ' ' << q.z << '\n';
        }
        const bool mirrored = part.transform.det3() < 0;
        // The construction chart, one vt per face corner, in metres as in the pack (OBJ's v is upward: written as -v so
        // that v = 0 at the image top stays the image top in tools that read OBJ the usual way).
        const bool charted = part.proto->uv.size() == m.corner_count();
        if (charted)
            for (const auto& c : part.proto->uv) out << "vt " << c[0] << ' ' << -c[1] << '\n';
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            const auto fv = m.face(f);
            out << 'f';
            for (std::size_t i = 0; i < fv.size(); ++i) {
                const std::size_t k = mirrored ? fv.size() - 1 - i : i;
                out << ' ' << base + fv[k];
                if (charted) out << '/' << uv_base + m.face_offsets[f] + k;
            }
            out << '\n';
        }
        base += m.vertex_count();
        if (charted) uv_base += m.corner_count();
    }
}

void write_pack_stats(Writer& w, const pg::emesh::WriteStats& st) {
    w.field("placements", st.placements).field("sourcePrototypes", st.source_prototypes).field("meshes", st.meshes);
    w.field("instances", st.instances).field("batches", st.batches).field("vertices", st.vertices).field("indices", st.indices);
    w.field("placedTriangles", st.triangles).field("mirroredInstances", st.mirrored_instances).field("index32", st.index32).field("bytes", st.bytes);
    if (st.details) w.field("details", st.details);
    if (st.lod_submeshes > st.meshes) w.field("lodSubmeshes", st.lod_submeshes);
    if (st.ports) w.field("ports", st.ports);
    if (st.deck_datums) w.field("deckDatums", st.deck_datums);
    if (st.collision_boxes) w.field("collisionBoxes", st.collision_boxes);
    if (st.walk_triangles) w.field("walkTriangles", st.walk_triangles);
    w.field("twoSidedMeshes", st.two_sided_meshes);
}

int cmd_export(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document");
    const doc::Result r = doc::evaluate_file(path, a.controls);
    w.begin_object().field("passed", true);
    if (a.has("emesh")) {
        pg::emesh::WriteOptions wo;
        wo.dedupe_congruent = !a.has("no-dedupe");
        wo.y_up = !a.has("zup");
        wo.source = path;
        if (!a.has("no-meta")) {
            wo.ports = doc::module_ports(pg::json::parse_file(path), r.controls);
            wo.emit_collision_and_walk = true;
        }
        pg::emesh::WriteStats st;
        const std::vector<std::uint8_t> bytes = pg::emesh::write(r.geometry, wo, &st);
        (void)pg::emesh::read(bytes);  // round-trip validation before anything is written
        pg::emesh::write_file(a.get("emesh"), bytes);
        w.key("emesh").begin_object().field("path", a.get("emesh")).field("up", wo.y_up ? "y" : "z");
        write_pack_stats(w, st);
        w.end_object();
    }
    if (a.has("obj")) {
        write_obj(a.get("obj"), r.geometry);
        w.key("obj").begin_object().field("path", a.get("obj")).field("up", "z").end_object();
    }
    if (!a.has("emesh") && !a.has("obj")) throw std::runtime_error("export needs --emesh FILE and/or --obj FILE");
    w.end_object();
    return 0;
}

doc::Mat4 make_rigid_transform(Vec3 t, Vec3 rot_xyz, Vec3 s) {
    const double cx = std::cos(rot_xyz.x), sx = std::sin(rot_xyz.x);
    const double cy = std::cos(rot_xyz.y), sy = std::sin(rot_xyz.y);
    const double cz = std::cos(rot_xyz.z), sz = std::sin(rot_xyz.z);
    // Rz * Ry * Rx * diag(s)
    doc::Mat4 m = doc::Mat4::identity();
    m.c[0][0] = (cz * cy) * s.x;
    m.c[0][1] = (sz * cy) * s.x;
    m.c[0][2] = (-sy) * s.x;
    m.c[1][0] = (cz * sy * sx - sz * cx) * s.y;
    m.c[1][1] = (sz * sy * sx + cz * cx) * s.y;
    m.c[1][2] = (cy * sx) * s.y;
    m.c[2][0] = (cz * sy * cx + sz * sx) * s.z;
    m.c[2][1] = (sz * sy * cx - cz * sx) * s.z;
    m.c[2][2] = (cy * cx) * s.z;
    m.c[3][0] = t.x;
    m.c[3][1] = t.y;
    m.c[3][2] = t.z;
    return m;
}

doc::Mat4 solve_port_mate(const doc::PortFrame& target_world, const doc::PortFrame& local_port) {
    auto basis_of = [](Vec3 n_raw, Vec3 u_raw) {
        const Vec3 n = pg::normalize_or(n_raw, {0, 0, 1});
        Vec3 u = u_raw - n * pg::dot(u_raw, n);
        if (!(pg::length(u) > 1e-9)) u = std::abs(n.z) < 0.9 ? Vec3{0, 0, 1} : Vec3{1, 0, 0};
        u = pg::normalize_or(u - n * pg::dot(u, n), {0, 0, 1});
        const Vec3 r = pg::cross(u, n);
        return std::array<Vec3, 3>{r, u, n};
    };
    const auto [rA, uA, nA] = basis_of(target_world.normal, target_world.up);
    const auto [rB, uB, nB] = basis_of(local_port.normal, local_port.up);
    // Target columns: [-rA, uA, -nA]; local columns: [rB, uB, nB]; R = A_mat * B_mat^T.
    const Vec3 a_cols[3] = {rA * -1.0, uA, nA * -1.0};
    const Vec3 b_cols[3] = {rB, uB, nB};
    doc::Mat4 M = doc::Mat4::identity();
    for (int col = 0; col < 3; ++col) {
        Vec3 c{};
        for (int k = 0; k < 3; ++k) c += a_cols[k] * b_cols[k][col];
        M.c[col][0] = c.x;
        M.c[col][1] = c.y;
        M.c[col][2] = c.z;
    }
    const Vec3 rot_oB = M.direction(local_port.origin);
    const Vec3 t = target_world.origin - rot_oB;
    M.c[3][0] = t.x;
    M.c[3][1] = t.y;
    M.c[3][2] = t.z;
    return M;
}

pgtools::PreviewOptions preview_options(const Args& a);

int cmd_assemble(const Args& a, Writer& w) {
    const std::string spec_path = a.at(0, "assembly.json");
    const pg::json::Value spec = pg::json::parse_file(spec_path);
    std::string root = a.get("root");
    if (root.empty()) {
        if (const pg::json::Value* rv = spec.find("root"); rv && rv->is_string()) {
            const fs::path rp(rv->string);
            root = rp.is_absolute() ? rp.string() : (fs::path(directory_of(spec_path)) / rp).lexically_normal().string();
        } else {
            root = directory_of(spec_path);
        }
    }
    const pg::json::Value* plist = spec.find("placements");
    if (!plist || !plist->is_array() || plist->array.empty()) throw std::runtime_error("assemble needs a non-empty placements array");

    auto read_vec3 = [](const pg::json::Value* v, Vec3 fallback) {
        if (!v || !v->is_array() || v->array.size() != 3) return fallback;
        return Vec3{v->array[0].number, v->array[1].number, v->array[2].number};
    };

    doc::Geometry combined;
    std::vector<doc::PortFrame> all_world_ports;
    std::map<std::string, std::map<std::string, doc::PortFrame>> placed_ports;
    doc::EvalCache cache(8192);
    std::size_t placement_count = 0;
    std::size_t mated_connections = 0;

    for (const pg::json::Value& item : plist->array) {
        const pg::json::Value* dv = item.find("document");
        if (!dv || !dv->is_string()) throw std::runtime_error("each placement needs a document path");
        const std::string pid = (item.find("id") && item.find("id")->is_string())
                                    ? item.find("id")->string
                                    : ("p" + std::to_string(placement_count));
        const std::string doc_path = dv->string.empty() || dv->string[0] == '/' ? dv->string : (root + "/" + dv->string);
        doc::Controls ctrls;
        if (const pg::json::Value* cv = item.find("controls"); cv && cv->is_object()) {
            for (const pg::json::Member& m : cv->object)
                if (m.value.is_number()) ctrls[m.key] = static_cast<float>(m.value.number);
        }
        const pg::json::Value doc_json = pg::json::parse_file(doc_path);
        doc::Library lib(directory_of(doc_path));
        const doc::Result r = doc::evaluate(doc_json, lib, ctrls, &cache);
        const std::vector<doc::PortFrame> local_ports = doc::module_ports(doc_json, r.controls);
        std::map<std::string, doc::PortFrame> local_port_map;
        for (const doc::PortFrame& pf : local_ports) local_port_map[pf.name] = pf;

        doc::Mat4 xform = make_rigid_transform(read_vec3(item.find("translation"), {}),
                                               read_vec3(item.find("rotation"), {}),
                                               read_vec3(item.find("scale"), {1, 1, 1}));
        if (const pg::json::Value* conn = item.find("connect"); conn && conn->is_object()) {
            const pg::json::Value* to_v = conn->find("to");
            const pg::json::Value* tp_v = conn->find("targetPort");
            const pg::json::Value* mp_v = conn->find("myPort");
            if (!to_v || !to_v->is_string() || !tp_v || !tp_v->is_string() || !mp_v || !mp_v->is_string())
                throw std::runtime_error(pid + ": connect needs to, targetPort and myPort");
            const auto pit = placed_ports.find(to_v->string);
            if (pit == placed_ports.end()) throw std::runtime_error(pid + ": connect target placement not found: " + to_v->string);
            const auto tpit = pit->second.find(tp_v->string);
            if (tpit == pit->second.end()) throw std::runtime_error(pid + ": targetPort not found: " + tp_v->string);
            const auto mpit = local_port_map.find(mp_v->string);
            if (mpit == local_port_map.end()) throw std::runtime_error(pid + ": myPort not found: " + mp_v->string);
            if (tpit->second.type != mpit->second.type)
                throw std::runtime_error(pid + ": port type mismatch (" + tpit->second.type + " vs " + mpit->second.type + ")");
            xform = solve_port_mate(tpit->second, mpit->second) * xform;
            ++mated_connections;
        }

        for (doc::Part p : r.geometry.parts) {
            p.transform = xform * p.transform;
            combined.parts.push_back(std::move(p));
        }
        auto& world_map = placed_ports[pid];
        for (doc::PortFrame pf : local_ports) {
            pf.origin = xform.point(pf.origin);
            pf.normal = pg::normalize_or(xform.direction(pf.normal), {0, 0, 1});
            pf.up = pg::normalize_or(xform.direction(pf.up), {0, 0, 1});
            pf.name = pid + "." + pf.name;
            world_map[pf.name.substr(pid.size() + 1)] = pf;
            all_world_ports.push_back(std::move(pf));
        }
        ++placement_count;
    }

    const bool do_audit = a.has("audit") || (spec.find("audit") && spec.find("audit")->kind == pg::json::Value::Kind::Bool && spec.find("audit")->boolean);
    doc::ContactReport cr;
    bool audit_ok = true;
    if (do_audit) {
        cr = doc::audit_contacts(combined, {.check_dangling = a.has("dangling"), .compute_gaps = true});
        audit_ok = cr.components <= 1 && cr.dangling.empty();
    }

    w.begin_object().field("passed", audit_ok).field("placements", placement_count).field("connections", mated_connections);
    w.field("worldPorts", all_world_ports.size());
    if (do_audit) {
        w.key("audit").begin_object()
            .field("passed", audit_ok)
            .field("components", cr.components)
            .field("islands", cr.islands.size())
            .field("detached", cr.components > 1 ? cr.components - 1 : 0)
            .field("dangling", cr.dangling.size())
            .end_object();
    }

    pg::emesh::WriteOptions wo;
    wo.dedupe_congruent = !a.has("no-dedupe");
    wo.y_up = !a.has("zup");
    wo.source = spec_path;
    if (!a.has("no-meta")) {
        wo.ports = all_world_ports;
        wo.emit_collision_and_walk = true;
    }
    pg::emesh::WriteStats st;
    const std::vector<std::uint8_t> bytes = pg::emesh::write(combined, wo, &st);
    const pg::emesh::Pack pack = pg::emesh::read(bytes);
    if (a.has("emesh")) {
        pg::emesh::write_file(a.get("emesh"), bytes);
        w.key("emesh").begin_object().field("path", a.get("emesh")).field("up", wo.y_up ? "y" : "z");
        write_pack_stats(w, st);
        w.end_object();
    }
    if (a.has("out")) {
        pgtools::PreviewOptions po = preview_options(a);
        pgtools::PreviewInfo info;
        pgtools::save_png(pgtools::render_pack(pack, po, &info), a.get("out"));
        w.key("render").begin_object().field("path", a.get("out")).field("triangles", info.triangles).end_object();
    }
    w.end_object();
    return audit_ok ? 0 : 1;
}

// Evaluate a document (or load a pack) into a decoded pack for previewing.
// When `unbake` is true (used by `--pass parts`), evaluates the pre-bake input node so every
// authoring instance keeps its own part colour instead of collapsing into merged material blobs.
pg::emesh::Pack load_for_preview(const std::string& path, const doc::Controls& controls, pg::emesh::WriteStats* st,
                                 std::vector<std::uint8_t>* bytes_out = nullptr, bool unbake = false,
                                 std::vector<pgtools::CageEdge>* cage_out = nullptr) {
    std::vector<std::uint8_t> bytes;
    if (ends_with(path, ".emesh")) {
        bytes = pg::emesh::read_file(path);
    } else {
        const doc::Result r = unbake ? doc::evaluate_file_unbaked(path, controls) : doc::evaluate_file(path, controls);
        if (cage_out) {
            for (const doc::Part& p : r.geometry.parts) {
                const pg::Mesh& cage = p.proto->sds_cage ? *p.proto->sds_cage : p.proto->mesh;
                if (cage.face_count() == 0) continue;
                const auto hes = pg::sorted_half_edges(cage);
                pg::for_each_edge_group(std::span<const pg::HalfEdge>(hes), [&](std::span<const pg::HalfEdge> g) {
                    const pg::Index lo = g[0].lo(), hi = g[0].hi();
                    const float sharp = g.size() != 2 ? pg::kCreaseInfinite : cage.edge_crease(lo, hi);
                    cage_out->push_back({p.transform.point(cage.positions[lo]), p.transform.point(cage.positions[hi]), sharp});
                });
            }
        }
        pg::emesh::WriteOptions wo;
        wo.source = path;
        bytes = pg::emesh::write(r.geometry, wo, st);
    }
    pg::emesh::Pack pack = pg::emesh::read(bytes);
    if (bytes_out) *bytes_out = std::move(bytes);
    return pack;
}

void parse_section_spec(const std::string& spec, pgtools::PreviewOptions& o) {
    if (spec.empty()) return;
    const std::size_t eq = spec.find('=');
    if (eq != std::string::npos) {
        const std::string lhs = spec.substr(0, eq);
        const double val = std::stod(spec.substr(eq + 1));
        double sign = 1.0;
        std::size_t idx = 0;
        if (!lhs.empty() && (lhs[0] == '+' || lhs[0] == '-')) {
            if (lhs[0] == '-') sign = -1.0;
            idx = 1;
        }
        if (lhs.size() != idx + 1) throw std::runtime_error("--section expects [+-]x|y|z=OFFSET or nx,ny,nz,offset");
        const char ax = lhs[idx];
        if (ax == 'x') o.section_normal = {sign, 0, 0};
        else if (ax == 'y') o.section_normal = {0, sign, 0};
        else if (ax == 'z') o.section_normal = {0, 0, sign};
        else throw std::runtime_error("--section axis must be x, y or z");
        o.section_offset = sign * val;
        o.has_section = true;
        return;
    }
    double vals[4]{};
    std::size_t pos = 0;
    for (int k = 0; k < 4; ++k) {
        const std::size_t comma = spec.find(',', pos);
        if (k < 3 && comma == std::string::npos) throw std::runtime_error("--section expects [+-]x|y|z=OFFSET or nx,ny,nz,offset");
        vals[k] = std::stod(comma == std::string::npos ? spec.substr(pos) : spec.substr(pos, comma - pos));
        pos = comma == std::string::npos ? spec.size() : comma + 1;
    }
    const pg::Vec3 n{vals[0], vals[1], vals[2]};
    const double len = pg::length(n);
    if (!(len > 1e-9)) throw std::runtime_error("--section normal must be non-zero");
    o.section_normal = n / len;
    o.section_offset = vals[3] / len;
    o.has_section = true;
}

void parse_frame_spec(const std::string& spec, pgtools::PreviewOptions& o) {
    if (spec.empty()) return;
    double vals[6]{};
    std::size_t pos = 0;
    for (int k = 0; k < 6; ++k) {
        const std::size_t comma = spec.find(',', pos);
        if (k < 5 && comma == std::string::npos) throw std::runtime_error("--frame expects xmin,ymin,zmin,xmax,ymax,zmax");
        vals[k] = std::stod(comma == std::string::npos ? spec.substr(pos) : spec.substr(pos, comma - pos));
        pos = comma == std::string::npos ? spec.size() : comma + 1;
    }
    o.frame_lo = {std::min(vals[0], vals[3]), std::min(vals[1], vals[4]), std::min(vals[2], vals[5])};
    o.frame_hi = {std::max(vals[0], vals[3]), std::max(vals[1], vals[4]), std::max(vals[2], vals[5])};
    o.has_frame = true;
}

void apply_pace_layer(int layer, const std::string& doc_path, doc::Controls& controls, pgtools::PreviewOptions& o, bool explicit_pass) {
    if (layer < 0 || layer > 3) throw std::runtime_error("pace-layer must be 0, 1, 2 or 3");
    bool has_stage_ctrl = false;
    if (!ends_with(doc_path, ".emesh") && fs::exists(doc_path)) {
        try {
            const pg::json::Value d = pg::json::parse_file(doc_path);
            if (const pg::json::Value* ctrls = d.find("controls"))
                if (const pg::json::Value* stg = ctrls->find("stage")) {
                    has_stage_ctrl = true;
                    const double lo = stg->find("min") && stg->find("min")->is_number() ? stg->find("min")->number : 0.0;
                    const double hi = stg->find("max") && stg->find("max")->is_number() ? stg->find("max")->number : 3.0;
                    if (!controls.count("stage")) controls["stage"] = static_cast<float>(std::clamp(static_cast<double>(layer), lo, hi));
                }
        } catch (...) {}
    }
    if (layer == 0 && !explicit_pass) o.pass = "clay";
    if (layer <= 2) o.show_details = false;
    if (!has_stage_ctrl && layer <= 1) o.strip_glazing = true;
}

pgtools::PreviewOptions preview_options(const Args& a) {
    pgtools::PreviewOptions o;
    o.view = a.get("view", "iso");
    o.pass = a.get("pass", "beauty");
    if (o.pass != "beauty" && o.pass != "clay" && o.pass != "parts" && o.pass != "materials" && o.pass != "zebra" && o.pass != "matcap" && o.pass != "curvature")
        throw std::runtime_error("pass must be beauty, clay, parts, materials, zebra, matcap or curvature");
    if (o.view != "iso" && o.view != "opposite" && o.view != "front" && o.view != "side" && o.view != "top") throw std::runtime_error("view must be iso, opposite, front, side or top");
    if (a.has("size")) {
        const std::string s = a.get("size");
        const std::size_t x = s.find('x');
        if (x == std::string::npos) throw std::runtime_error("--size needs WxH");
        o.width = std::stoi(s.substr(0, x));
        o.height = std::stoi(s.substr(x + 1));
        if (o.width < 16 || o.height < 16 || o.width > 4096 || o.height > 4096) throw std::runtime_error("size must be within 16..4096");
    }
    if (a.has("yaw") || a.has("pitch")) {
        o.custom_angles = true;
        o.yaw_deg = std::stod(a.get("yaw", "-52"));
        o.pitch_deg = std::stod(a.get("pitch", "28"));
    }
    if (a.has("section")) parse_section_spec(a.get("section"), o);
    if (a.has("frame")) parse_frame_spec(a.get("frame"), o);
    if (a.has("frame-strict")) o.frame_expand = false;
    if (a.has("no-details")) o.show_details = false;
    if (a.has("lod")) {
        o.lod = std::stoi(a.get("lod"));
        if (o.lod < 0 || o.lod > 3) throw std::runtime_error("--lod must be in 0..3");
    }
    o.perspective = a.get("projection", "ortho") == "perspective";
    return o;
}

int cmd_render(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document or pack");
    const std::string out = a.get("out");
    if (out.empty()) throw std::runtime_error("render needs --out FILE.png");
    pgtools::PreviewOptions po = preview_options(a);
    doc::Controls controls = a.controls;
    if (a.has("pace-layer")) {
        apply_pace_layer(std::stoi(a.get("pace-layer")), path, controls, po, a.has("pass"));
    }
    std::optional<pg::emesh::Pack> ghost_holder;
    if (a.has("ghost")) {
        ghost_holder = load_for_preview(a.get("ghost"), {}, nullptr);
        po.ghost_pack = &(*ghost_holder);
        if (a.has("ghost-opacity")) po.ghost_opacity = std::clamp(std::stod(a.get("ghost-opacity")), 0.0, 1.0);
    }
    std::vector<pgtools::CageEdge> cage_edges;
    const bool want_wire_cage = a.has("wire-cage");
    pg::emesh::WriteStats st;
    std::vector<std::uint8_t> bytes;
    pg::emesh::Pack pack;
    bool retained_fallback = false;
    std::string primary_error;
    try {
        pack = load_for_preview(path, controls, &st, &bytes, po.pass == "parts", want_wire_cage ? &cage_edges : nullptr);
        if (want_wire_cage) po.cage_edges = &cage_edges;
    } catch (const std::exception& e) {
        if (!ghost_holder) throw;
        // Controlled incompleteness: primary document failed to evaluate; retain the last-valid ghost pack!
        retained_fallback = true;
        primary_error = e.what();
        po.ghost_opacity = 0.85;
    }
    std::string backend = "software", note;
    const std::string requested = a.get("backend", "software");
    if (requested == "engine" && !retained_fallback) {
        const char* cmd = std::getenv("PGM_ENGINE_PREVIEW");
        if (cmd && *cmd) {
            const std::string pack_path = out + ".emesh";
            pg::emesh::write_file(pack_path, bytes);
            const std::string line = std::string(cmd) + " '" + pack_path + "' '" + out + "' " + po.view + " " + std::to_string(po.width) + " " + std::to_string(po.height);
            if (std::system(line.c_str()) == 0 && fs::exists(out)) backend = "engine";
            else note = "engine preview command failed; rendered with the software backend";
        } else {
            note = "engine backend requested but PGM_ENGINE_PREVIEW is not set (the engine cannot draw yet); rendered with the software backend from the same .emesh bytes";
        }
    } else if (requested != "software" && requested != "engine") {
        throw std::runtime_error("backend must be software or engine");
    }
    pgtools::PreviewInfo info;
    if (backend == "software") {
        pgtools::Image img = pgtools::render_pack(pack, po, &info);
        if (retained_fallback) {
            pgtools::draw_text(img, 10, 10, "PROVISIONAL: RETAINED LAST VALID", {0.92, 0.68, 0.34}, 2);
        }
        pgtools::save_png(img, out);
    }
    w.begin_object().field("passed", !retained_fallback).field("path", out).field("backend", backend);
    if (retained_fallback) {
        w.field("retainedLastValid", true).field("primaryError", primary_error);
    }
    if (!note.empty()) w.field("note", note);
    w.field("view", po.view).field("pass", po.pass).field("width", po.width).field("height", po.height);
    w.field("triangles", info.triangles).field("instances", info.instances).field("meshes", info.meshes);
    w.field("metresPerPixel", info.metres_per_pixel);
    if (po.has_frame) w.field("frameOverflowed", info.frame_overflowed);
    w.key("cameraCenterZUp"); write_vec(w, info.camera_center);
    w.key("boundsZUp").begin_array(); write_vec(w, info.lo); write_vec(w, info.hi); w.end_array();
    w.end_object();
    return retained_fallback ? 1 : 0;
}

// A kit sheet: several previews with captions on one image.
//   {"title": "...", "width": 1730, "height": 1006, "layout": "feature" | "grid", "columns": 2,
//    "panels": [{"document": "x.json", "set": {"radius": 6}, "view": "iso", "pass": "beauty", "caption": "..."}]}
// "feature": the first panel fills the left 58%, the rest stack on the right.
int cmd_sheet(const Args& a, Writer& w) {
    const std::string spec_path = a.at(0, "sheet.json");
    const std::string out = a.get("out");
    if (out.empty()) throw std::runtime_error("sheet needs --out FILE.png");
    const pg::json::Value spec = pg::json::parse_file(spec_path);
    const std::string base = a.get("root", directory_of(spec_path));
    auto number = [&](const pg::json::Value& v, const char* k, double fallback) {
        const pg::json::Value* f = v.find(k);
        return f && f->is_number() ? f->number : fallback;
    };
    auto text = [&](const pg::json::Value& v, const char* k, const std::string& fallback) {
        const pg::json::Value* f = v.find(k);
        return f && f->is_string() ? f->string : fallback;
    };
    const int W = static_cast<int>(number(spec, "width", 1730)), H = static_cast<int>(number(spec, "height", 1006));
    const pg::json::Value* panels = spec.find("panels");
    if (!panels || panels->array.empty()) throw std::runtime_error("sheet needs panels");
    const std::size_t n = panels->array.size();
    const std::string layout = text(spec, "layout", n >= 3 ? "feature" : "grid");
    const pgtools::Rgb bg{0.165, 0.165, 0.165}, ink{0.80, 0.80, 0.78};
    pgtools::Image sheet = pgtools::solid(W, H, bg);
    const int caption_h = 44, pad = 18;
    struct Rect { int x, y, w, h; };
    std::vector<Rect> rects;
    if (layout == "feature" && n >= 2) {
        const int left = static_cast<int>(W * 0.58);
        rects.push_back({0, 0, left, H});
        const int rows = static_cast<int>(n) - 1;
        for (int i = 0; i < rows; ++i) rects.push_back({left, i * H / rows, W - left, H / rows});
    } else {
        const int cols = static_cast<int>(number(spec, "columns", n <= 2 ? static_cast<double>(n) : 2.0));
        const int rows = (static_cast<int>(n) + cols - 1) / cols;
        for (int i = 0; i < static_cast<int>(n); ++i) rects.push_back({(i % cols) * W / cols, (i / cols) * H / rows, W / cols, H / rows});
    }
    auto read_json_frame = [](const pg::json::Value* fv, pgtools::PreviewOptions& dst) {
        if (!fv || !fv->is_array() || fv->array.size() != 2) return;
        const auto& a0 = fv->array[0];
        const auto& a1 = fv->array[1];
        if (!a0.is_array() || a0.array.size() != 3 || !a1.is_array() || a1.array.size() != 3) return;
        dst.frame_lo = {std::min(a0.array[0].number, a1.array[0].number),
                        std::min(a0.array[1].number, a1.array[1].number),
                        std::min(a0.array[2].number, a1.array[2].number)};
        dst.frame_hi = {std::max(a0.array[0].number, a1.array[0].number),
                        std::max(a0.array[1].number, a1.array[1].number),
                        std::max(a0.array[2].number, a1.array[2].number)};
        dst.has_frame = true;
    };
    w.begin_object().field("passed", true).field("path", out).field("width", W).field("height", H).field("layout", layout);
    w.key("panels").begin_array();
    for (std::size_t i = 0; i < n; ++i) {
        const pg::json::Value& p = panels->array[i];
        const Rect r = rects[i];
        doc::Controls controls;
        if (const pg::json::Value* set = p.find("set"))
            for (const pg::json::Member& m : set->object) controls[m.key] = static_cast<float>(m.value.number);
        std::string document = text(p, "document", "");
        if (document.empty()) throw std::runtime_error("panel needs a document");
        if (document[0] != '/') document = base + "/" + document;
        pgtools::PreviewOptions po;
        po.width = r.w - 2 * pad;
        po.height = r.h - caption_h - 2 * pad;
        po.view = text(p, "view", "iso");
        po.pass = text(p, "pass", "beauty");
        if (p.find("yaw") || p.find("pitch")) {
            po.custom_angles = true;
            po.yaw_deg = number(p, "yaw", -52);
            po.pitch_deg = number(p, "pitch", 28);
        }
        if (const std::string sec = text(p, "section", ""); !sec.empty()) parse_section_spec(sec, po);
        read_json_frame(spec.find("frame"), po);
        read_json_frame(p.find("frame"), po);
        if (const pg::json::Value* pl = p.find("paceLayer"); pl && pl->is_number()) {
            apply_pace_layer(static_cast<int>(pl->number), document, controls, po, p.find("pass") != nullptr);
        }
        if (const pg::json::Value* lv = p.find("lod"); lv && lv->is_number()) {
            po.lod = std::clamp(static_cast<int>(lv->number), 0, 3);
        }
        const pg::json::Value* wc = p.find("wireCage");
        const bool wire_cage = wc && ((wc->kind == pg::json::Value::Kind::Bool && wc->boolean) ||
                                      (wc->is_number() && wc->number != 0.0));
        po.background = bg;
        pgtools::PreviewInfo info;
        std::vector<pgtools::CageEdge> cage_edges;
        const pg::emesh::Pack pack = load_for_preview(document, controls, nullptr, nullptr, po.pass == "parts",
                                                      wire_cage ? &cage_edges : nullptr);
        if (wire_cage) po.cage_edges = &cage_edges;
        pgtools::blit(sheet, pgtools::render_pack(pack, po, &info), r.x + pad, r.y + pad);
        const std::string caption = text(p, "caption", "");
        pgtools::draw_text(sheet, r.x + pad + 8, r.y + r.h - caption_h + 6, caption, ink, 2);
        w.begin_object().field("document", document).field("caption", caption).field("view", po.view);
        w.key("rect").begin_array().value(r.x).value(r.y).value(r.w).value(r.h).end_array();
        w.field("triangles", info.triangles).field("instances", info.instances).field("meshes", info.meshes).end_object();
    }
    w.end_array();
    if (const std::string title = text(spec, "title", ""); !title.empty()) w.field("title", title);
    pgtools::save_png(sheet, out);
    w.end_object();
    return 0;
}

int cmd_nodes(const Args& a, Writer& w) {
    const std::string only = a.positional.empty() ? std::string() : a.positional[0];
    w.begin_object();
    if (!only.empty() && !doc::find_node_type(only)) {
        w.field("passed", false).field("error", "unknown node type " + only).end_object();
        return 1;
    }
    w.field("passed", true).key("nodes").begin_array();
    for (const doc::NodeInfo& n : doc::node_reference()) {
        if (!only.empty() && n.type != only) continue;
        w.begin_object().field("type", std::string(n.type)).field("family", std::string(n.family)).field("inputs", std::string(n.inputs));
        w.field("options", std::string(n.options)).field("summary", std::string(n.summary)).end_object();
    }
    w.end_array().end_object();
    return 0;
}

// A module may declare that it is meant to be in several pieces: "audit": {"components": 2, "why": "..."}.
int declared_components(const pg::json::Value& d, std::string* why) {
    int allowed = 1;
    if (const pg::json::Value* au = d.find("audit")) {
        if (const pg::json::Value* c = au->find("components"); c && c->is_number()) allowed = static_cast<int>(c->number);
        if (const pg::json::Value* y = au->find("why"); y && y->is_string() && why) *why = y->string;
    }
    return allowed;
}

void write_detached(Writer& w, const doc::ContactReport& rep, std::size_t limit) {
    std::vector<int> others;
    for (int c = 0; c < rep.components; ++c)
        if (c != rep.main_component) others.push_back(c);
    std::sort(others.begin(), others.end(), [&](int x, int y) {
        return rep.component_triangles[static_cast<std::size_t>(x)] < rep.component_triangles[static_cast<std::size_t>(y)];
    });
    w.key("detached").begin_array();
    for (std::size_t k = 0; k < others.size() && k < limit; ++k) {
        const int c = others[k];
        Aabb box;
        std::map<std::string, int> sources;
        for (const doc::ContactIsland& isl : rep.islands)
            if (isl.component == c) {
                box.expand(isl.bounds.lo);
                box.expand(isl.bounds.hi);
                ++sources[isl.source];
            }
        w.begin_object().field("triangles", rep.component_triangles[static_cast<std::size_t>(c)]).key("sources").begin_object();
        for (const auto& [s, count] : sources) w.field(s, count);
        w.end_object();
        if (const auto git = rep.detached_gaps.find(c); git != rep.detached_gaps.end()) {
            w.field("gap", git->second.gap).field("nearestMainSource", git->second.nearest_main_source);
            w.key("deltaToMain");
            write_vec(w, git->second.delta_to_main);
        }
        w.key("lo");
        write_vec(w, box.lo);
        w.key("hi");
        write_vec(w, box.hi);
        w.end_object();
    }
    w.end_array();
}

void write_dangling(Writer& w, const doc::ContactReport& rep, std::size_t limit) {
    w.key("dangling").begin_array();
    for (std::size_t k = 0; k < std::min<std::size_t>(rep.dangling.size(), limit); ++k) {
        const doc::ContactIsland& isl = rep.islands[rep.dangling[k]];
        w.begin_object().field("source", isl.source).field("triangles", isl.triangles).key("lo");
        write_vec(w, isl.bounds.lo);
        w.key("hi");
        write_vec(w, isl.bounds.hi);
        w.end_object();
    }
    w.end_array().field("danglingCount", rep.dangling.size());
}

int cmd_audit(const Args& a, Writer& w) {
    const std::string path = a.at(0, "path");
    const double t0 = now_ms();
    doc::ContactOptions copts;
    copts.check_dangling = a.has("dangling");
    if (!fs::is_directory(path) && !a.has("sweep") && !a.has("baseline")) {
        const doc::Result r = doc::evaluate_file(path, a.controls);
        const doc::ContactReport rep = doc::audit_contacts(r.geometry, copts);
        std::string why;
        const int allowed = declared_components(pg::json::parse_file(path), &why);
        const bool pass = rep.components <= allowed && (!copts.check_dangling || rep.dangling.empty());
        w.begin_object().field("passed", pass).field("components", rep.components).field("allowed", allowed);
        if (!why.empty()) w.field("why", why);
        w.field("islands", rep.islands.size()).field("pairsTested", rep.pairs_tested);
        write_detached(w, rep, 24);
        if (copts.check_dangling) write_dangling(w, rep, 24);
        w.field("ms", now_ms() - t0).end_object();
        return pass ? 0 : 1;
    }
    // Many documents (and with --sweep, many settings): only the failures. A baseline file
    // {"documents": {"<path relative to the audited directory>": {"components": N, "why": "..."}}} records known
    // defects; a document doing better than its baseline is reported too, so the baseline is tightened.
    std::map<std::string, int> baseline;
    if (a.has("baseline")) {
        const pg::json::Value bl = pg::json::parse_file(a.get("baseline"));
        if (const pg::json::Value* docs = bl.find("documents"))
            for (const pg::json::Member& m : docs->object)
                if (const pg::json::Value* c = m.value.find("components"); c && c->is_number()) baseline[m.key] = static_cast<int>(c->number);
    }
    const fs::path root = fs::is_directory(path) ? fs::path(path) : fs::path(path).parent_path();
    std::size_t documents = 0, cases = 0, failed = 0;
    Writer failures, loose;
    failures.begin_array();
    loose.begin_array();
    for (const std::string& doc_path : documents_under(path)) {
        const pg::json::Value d = pg::json::parse_file(doc_path);
        if (!d.find("nodes")) continue;
        ++documents;
        const std::string rel = fs::relative(doc_path, root).generic_string();
        const auto known = baseline.find(rel);
        const int allowed = known != baseline.end() ? known->second : declared_components(d, nullptr);
        int worst = 0;
        const std::vector<doc::Controls> settings = a.has("sweep") ? sweep_cases(d) : std::vector<doc::Controls>{{}};
        for (const doc::Controls& c : settings) {
            ++cases;
            std::string error;
            doc::ContactReport rep;
            doc::ContactOptions fast_opts = copts;
            fast_opts.compute_gaps = false;
            try {
                const doc::Result ev_res = doc::evaluate_file(doc_path, c);
                rep = doc::audit_contacts(ev_res.geometry, fast_opts);
                const bool dang_fail_pre = copts.check_dangling && !rep.dangling.empty();
                if ((rep.components > allowed || dang_fail_pre) && failed < 40) {
                    rep = doc::audit_contacts(ev_res.geometry, copts);
                }
            } catch (const std::exception& e) {
                error = e.what();
            }
            worst = std::max(worst, rep.components);
            const bool dang_fail = copts.check_dangling && !rep.dangling.empty();
            if (error.empty() && rep.components <= allowed && !dang_fail) continue;
            if (++failed > 40) continue;
            failures.begin_object().field("document", rel).key("set").begin_object();
            for (const auto& [k, v] : c) failures.field(k, v);
            failures.end_object().field("components", rep.components).field("allowed", allowed);
            if (!error.empty()) {
                failures.field("error", error);
            } else {
                write_detached(failures, rep, 8);
                if (copts.check_dangling) write_dangling(failures, rep, 8);
            }
            failures.end_object();
        }
        if (known != baseline.end() && worst < known->second)
            loose.begin_object().field("document", rel).field("baseline", known->second).field("now", worst).end_object();
    }
    failures.end_array();
    loose.end_array();
    const bool tight = loose.str() == "[]";
    w.begin_object().field("passed", failed == 0 && tight).field("documents", documents).field("cases", cases).field("failed", failed);
    w.key("failures").raw(failures.str()).key("baselineToTighten").raw(loose.str()).field("ms", now_ms() - t0).end_object();
    return failed == 0 && tight ? 0 : 1;
}

// Incremental "settled prefix / dirty tail" daemon: keeps EvalCache warm across edits
// or interactive control updates, optionally rendering a preview on every change.
int cmd_watch(const Args& a, Writer& w) {
    std::string path = a.at(0, "document");
    doc::Controls controls = a.controls;
    const std::string out_png = a.get("out");
    const bool stdin_mode = a.has("stdin");
    const bool once = a.has("once");
    const int max_events = a.has("max-events") ? std::max(1, std::stoi(a.get("max-events"))) : (once ? 1 : 0);
    const int interval_ms = std::clamp(a.has("interval-ms") ? std::stoi(a.get("interval-ms")) : 150, 10, 10000);
    pgtools::PreviewOptions po = preview_options(a);
    doc::EvalCache cache(8192);
    std::size_t events = 0;

    std::optional<pg::json::Value> cached_doc;
    std::optional<doc::Library> cached_lib;
    std::string cached_dir;

    auto ensure_loaded = [&](bool force_reload) {
        const std::string dir = directory_of(path);
        if (force_reload || !cached_lib || dir != cached_dir) {
            cached_lib.emplace(dir);
            cached_dir = dir;
        }
        if (force_reload || !cached_doc) {
            cached_doc = pg::json::parse_file(path);
        }
    };

    auto emit_step = [&](bool stream_line, bool reload_files) {
        ++events;
        Writer evw;
        try {
            ensure_loaded(reload_files);
            const double t0 = now_ms();
            const doc::Result r = doc::evaluate(*cached_doc, *cached_lib, controls, &cache);
            const double eval_ms = now_ms() - t0;
            evw.begin_object()
                .field("event", events)
                .field("passed", true)
                .field("document", path)
                .field("settledNodes", r.reused_nodes)
                .field("dirtyNodes", r.evaluated_nodes)
                .field("cacheEntries", cache.size())
                .field("evaluationMS", eval_ms);
            evw.key("efficiency");
            write_efficiency(evw, r, path, eval_ms);
            if (!out_png.empty()) {
                pg::emesh::WriteOptions wo;
                wo.source = path;
                const std::vector<std::uint8_t> bytes = pg::emesh::write(r.geometry, wo, nullptr);
                const pg::emesh::Pack pack = pg::emesh::read(bytes);
                pgtools::PreviewInfo pinfo;
                pgtools::save_png(pgtools::render_pack(pack, po, &pinfo), out_png);
                evw.field("renderPath", out_png);
            }
            evw.end_object();
        } catch (const std::exception& e) {
            evw.begin_object()
                .field("event", events)
                .field("passed", false)
                .field("document", path)
                .field("error", e.what())
                .end_object();
        }
        if (stream_line) {
            std::printf("%s\n", evw.str().c_str());
            std::fflush(stdout);
        } else {
            w = std::move(evw);
        }
    };

    if (once) {
        emit_step(false, true);
        return 0;
    }

    if (stdin_mode) {
        emit_step(true, true);
        std::string line;
        while ((max_events == 0 || static_cast<int>(events) < max_events) && std::getline(std::cin, line)) {
            while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) line.pop_back();
            if (line == "quit" || line == "exit") break;
            if (line == "clear") {
                cache.clear();
                continue;
            }
            bool reload_files = true;
            if (line.rfind("set ", 0) == 0) {
                reload_files = false;
                std::istringstream iss(line.substr(4));
                std::string kv;
                while (iss >> kv) {
                    const std::size_t eq = kv.find('=');
                    if (eq != std::string::npos && eq > 0 && eq + 1 < kv.size()) {
                        controls[kv.substr(0, eq)] = std::stof(kv.substr(eq + 1));
                    }
                }
            } else if (line.rfind("file ", 0) == 0) {
                path = line.substr(5);
                cached_doc.reset();
            }
            emit_step(true, reload_files);
        }
        w.begin_object()
            .field("passed", true)
            .field("events", events)
            .field("cacheEntries", cache.size())
            .field("totalHits", cache.hits())
            .field("totalMisses", cache.misses())
            .end_object();
        return 0;
    }

    auto dir_stamp = [&]() {
        std::uint64_t stamp = 0;
        const std::string dir = directory_of(path);
        std::error_code ec;
        for (const auto& entry : fs::directory_iterator(dir, ec)) {
            if (!entry.is_regular_file(ec) || entry.path().extension() != ".json") continue;
            const auto wt = entry.last_write_time(ec).time_since_epoch().count();
            stamp ^= static_cast<std::uint64_t>(wt) * 0x9E3779B97F4A7C15ULL + std::hash<std::string>{}(entry.path().filename().string());
        }
        return stamp;
    };

    std::uint64_t last_stamp = dir_stamp();
    emit_step(true, true);
    while (max_events == 0 || static_cast<int>(events) < max_events) {
        std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
        const std::uint64_t cur = dir_stamp();
        if (cur != last_stamp) {
            last_stamp = cur;
            emit_step(true, true);
        }
    }
    w.begin_object()
        .field("passed", true)
        .field("events", events)
        .field("cacheEntries", cache.size())
        .field("totalHits", cache.hits())
        .field("totalMisses", cache.misses())
        .end_object();
    return 0;
}

// 2D bounding-box -> 3D pre-bake authoring part back-projection (`jev-1.14` / VLM defect localization).
int cmd_pick(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document");
    const double t0 = now_ms();
    doc::Result r = doc::evaluate_file_unbaked(path, a.controls);
    struct Entry {
        std::string source;
        std::string material;
        Aabb bounds;
        std::size_t triangles = 0;
    };
    std::vector<Entry> part_meta;
    doc::Geometry unique_geom;
    for (const doc::Part& p : r.geometry.parts) {
        if (!p.proto || p.proto->mesh.face_count() == 0) continue;
        Aabb wb;
        const Aabb lb = pg::bounds(p.proto->mesh);
        if (lb.valid()) {
            for (int k = 0; k < 8; ++k)
                wb.expand(p.transform.point({k & 1 ? lb.hi.x : lb.lo.x, k & 2 ? lb.hi.y : lb.lo.y, k & 4 ? lb.hi.z : lb.lo.z}));
        }
        doc::Part copy = p;
        copy.proto = std::make_shared<const doc::Proto>(*p.proto);  // keep 1-to-1 batch/instance index
        unique_geom.parts.push_back(std::move(copy));
        part_meta.push_back(Entry{p.source ? *p.source : std::string(), doc::slot_name(p.material), wb, p.proto->triangle_count()});
    }
    pg::emesh::WriteOptions wo;
    wo.dedupe_congruent = false;
    wo.source = path;
    const pg::emesh::Pack pack = pg::emesh::read(pg::emesh::write(unique_geom, wo));

    pgtools::PreviewOptions po = preview_options(a);
    if (!a.has("size")) { po.width = 480; po.height = 360; }
    po.pass = "parts";
    po.shadows = false;
    po.outline = 0.0;
    po.contact_ao = false;
    po.show_details = false;
    po.translucent = false;
    po.record_instance_map = true;
    pgtools::PreviewInfo info;
    (void)pgtools::render_pack(pack, po, &info);

    int x0 = 0, y0 = 0, x1 = po.width - 1, y1 = po.height - 1;
    if (a.has("box")) {
        double b[4]{0, 0, 1, 1};
        std::size_t pos = 0;
        const std::string bs = a.get("box");
        for (int k = 0; k < 4; ++k) {
            const std::size_t cm = bs.find(',', pos);
            if (k < 3 && cm == std::string::npos) throw std::runtime_error("--box expects xmin,ymin,xmax,ymax");
            b[k] = std::stod(cm == std::string::npos ? bs.substr(pos) : bs.substr(pos, cm - pos));
            pos = cm == std::string::npos ? bs.size() : cm + 1;
        }
        const std::string coord = a.get("coord", (std::max({b[0], b[1], b[2], b[3]}) <= 1.001) ? "norm" : "1000");
        const double sx = coord == "px" ? 1.0 : (coord == "1000" ? po.width / 1000.0 : static_cast<double>(po.width));
        const double sy = coord == "px" ? 1.0 : (coord == "1000" ? po.height / 1000.0 : static_cast<double>(po.height));
        x0 = std::clamp(static_cast<int>(std::floor(std::min(b[0], b[2]) * sx)), 0, po.width - 1);
        x1 = std::clamp(static_cast<int>(std::ceil(std::max(b[0], b[2]) * sx)), 0, po.width - 1);
        y0 = std::clamp(static_cast<int>(std::floor(std::min(b[1], b[3]) * sy)), 0, po.height - 1);
        y1 = std::clamp(static_cast<int>(std::ceil(std::max(b[1], b[3]) * sy)), 0, po.height - 1);
    }

    struct Agg {
        std::size_t pixels = 0;
        std::size_t triangles = 0;
        std::string material;
        Aabb bounds;
    };
    std::map<std::string, Agg> hits;
    std::size_t fg_pixels = 0;
    for (int y = y0; y <= y1; ++y) {
        for (int x = x0; x <= x1; ++x) {
            const std::uint32_t inst = info.instance_map[static_cast<std::size_t>(y) * static_cast<std::size_t>(po.width) + static_cast<std::size_t>(x)];
            if (inst == 0 || inst > part_meta.size()) continue;
            ++fg_pixels;
            const Entry& e = part_meta[inst - 1];
            Agg& ag = hits[e.source];
            ++ag.pixels;
            ag.triangles = std::max(ag.triangles, e.triangles);
            ag.material = e.material;
            ag.bounds.expand(e.bounds.lo);
            ag.bounds.expand(e.bounds.hi);
        }
    }
    std::vector<std::pair<std::string, Agg>> ranked(hits.begin(), hits.end());
    std::sort(ranked.begin(), ranked.end(), [](const auto& a1, const auto& a2) { return a1.second.pixels > a2.second.pixels; });

    w.begin_object().field("passed", true).field("view", po.view).field("foregroundPixels", fg_pixels);
    w.key("pixelRect").begin_array().value(x0).value(y0).value(x1).value(y1).end_array();
    w.key("parts").begin_array();
    for (std::size_t k = 0; k < std::min<std::size_t>(ranked.size(), 16); ++k) {
        const auto& [src, ag] = ranked[k];
        w.begin_object().field("source", src).field("material", ag.material).field("pixels", ag.pixels)
            .field("share", fg_pixels ? static_cast<double>(ag.pixels) / static_cast<double>(fg_pixels) : 0.0)
            .field("triangles", ag.triangles).key("lo");
        write_vec(w, ag.bounds.lo);
        w.key("hi");
        write_vec(w, ag.bounds.hi);
        w.end_object();
    }
    w.end_array().field("ms", now_ms() - t0).end_object();
    return 0;
}

bool tri_overlaps_box(Vec3 a, Vec3 b, Vec3 c, Vec3 lo, Vec3 hi) {
    // Shrink clearance box by 0.25 mm so coplanar jambs/sills/soffits on the boundary do not false-trigger.
    const Vec3 center = (lo + hi) * 0.5;
    const Vec3 e{std::max(0.0, (hi.x - lo.x) * 0.5 - 2.5e-4),
                 std::max(0.0, (hi.y - lo.y) * 0.5 - 2.5e-4),
                 std::max(0.0, (hi.z - lo.z) * 0.5 - 2.5e-4)};
    if (e.x <= 0 || e.y <= 0 || e.z <= 0) return false;
    const Vec3 v0 = a - center, v1 = b - center, v2 = c - center;
    if (std::min({v0.x, v1.x, v2.x}) > e.x || std::max({v0.x, v1.x, v2.x}) < -e.x) return false;
    if (std::min({v0.y, v1.y, v2.y}) > e.y || std::max({v0.y, v1.y, v2.y}) < -e.y) return false;
    if (std::min({v0.z, v1.z, v2.z}) > e.z || std::max({v0.z, v1.z, v2.z}) < -e.z) return false;
    const Vec3 f[3]{v1 - v0, v2 - v1, v0 - v2};
    const Vec3 u[3]{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) {
            const Vec3 ax = cross(u[i], f[j]);
            const double r = e.x * std::abs(ax.x) + e.y * std::abs(ax.y) + e.z * std::abs(ax.z);
            const double p0 = dot(v0, ax), p1 = dot(v1, ax), p2 = dot(v2, ax);
            if (std::min({p0, p1, p2}) > r + 1e-9 || std::max({p0, p1, p2}) < -r - 1e-9) return false;
        }
    }
    const Vec3 n = cross(f[0], f[1]);
    const double rn = e.x * std::abs(n.x) + e.y * std::abs(n.y) + e.z * std::abs(n.z);
    if (std::abs(dot(n, v0)) > rn + 1e-9) return false;
    return true;
}

// Single-process warm verification (`EvalCache` + `Library` shared across check, sweep, audit, inspect & contract cases).
int cmd_batch_verify(const Args& a, Writer& w) {
    const std::string path = a.at(0, "document");
    const double t0 = now_ms();
    const bool intersections = a.has("intersections");
    const bool check_dangling = a.has("dangling");
    const bool allow_open = a.has("allow-open");

    const pg::json::Value d = pg::json::parse_file(path);
    doc::Library lib(directory_of(path));
    doc::EvalCache cache(8192);

    // 1. Default evaluation: check + inspect + audit (--dangling)
    const doc::Result r0 = doc::evaluate(d, lib, a.controls, &cache);
    const doc::Receipt rec0 = doc::make_receipt(r0.geometry);
    Writer chk_issues;
    chk_issues.begin_array();
    const Tally tal0 = tally(r0, intersections, &chk_issues, allow_open);
    chk_issues.end_array();
    const bool check_ok = tal0.clean(allow_open);

    const int allowed_comp = declared_components(d, nullptr);
    doc::ContactOptions copts0;
    copts0.check_dangling = true;
    const doc::ContactReport aud0 = doc::audit_contacts(r0.geometry, copts0);

    // 2. Sweep + Audit-Sweep sharing the warm EvalCache!
    const std::vector<doc::Controls> sw_settings = sweep_cases(d);
    std::size_t sw_failed = 0, au_failed = 0;
    Writer sw_failures, au_failures;
    sw_failures.begin_array();
    au_failures.begin_array();
    for (const doc::Controls& sc : sw_settings) {
        std::string err;
        doc::Result rs;
        try {
            rs = doc::evaluate(d, lib, sc, &cache);
            if (!tally(rs, false, nullptr, allow_open).clean(allow_open)) err = "not a clean closed solid";
        } catch (const std::exception& e) {
            err = e.what();
        }
        if (!err.empty()) {
            if (++sw_failed <= 16) {
                sw_failures.begin_object().key("set").begin_object();
                for (const auto& [k, v] : sc) sw_failures.field(k, v);
                sw_failures.end_object().field("error", err).end_object();
            }
            if (++au_failed <= 16) {
                au_failures.begin_object().key("set").begin_object();
                for (const auto& [k, v] : sc) au_failures.field(k, v);
                au_failures.end_object().field("error", err).end_object();
            }
            continue;
        }
        doc::ContactOptions fast_c;
        fast_c.compute_gaps = false;
        fast_c.check_dangling = check_dangling;
        doc::ContactReport rep = doc::audit_contacts(rs.geometry, fast_c);
        const bool df = check_dangling && !rep.dangling.empty();
        if (rep.components > allowed_comp || df) {
            if (++au_failed <= 16) {
                fast_c.compute_gaps = true;
                rep = doc::audit_contacts(rs.geometry, fast_c);
                au_failures.begin_object().key("set").begin_object();
                for (const auto& [k, v] : sc) au_failures.field(k, v);
                au_failures.end_object().field("components", rep.components).field("allowed", allowed_comp);
                write_detached(au_failures, rep, 6);
                if (check_dangling) write_dangling(au_failures, rep, 6);
                au_failures.end_object();
            }
        }
    }
    sw_failures.end_array();
    au_failures.end_array();

    // 3. Optional extra contract cases (--cases '[{"width":4.4}]' or '[{"set":{"width":4.4},"clearances":[{"name":"door","lo":[-0.4,-0.5,0],"hi":[0.4,0.5,2.0]}]}]')
    Writer extra_cases;
    extra_cases.begin_array();
    if (a.has("cases")) {
        const pg::json::Value cv = pg::json::parse(a.get("cases"));
        if (cv.is_array()) {
            for (const pg::json::Value& item : cv.array) {
                doc::Controls cc;
                const pg::json::Value* set_obj = item.is_object() ? item.find("set") : nullptr;
                const pg::json::Value* src_obj = (set_obj && set_obj->is_object()) ? set_obj : (item.is_object() ? &item : nullptr);
                if (src_obj) {
                    for (const pg::json::Member& m : src_obj->object)
                        if (m.value.is_number()) cc[m.key] = static_cast<float>(m.value.number);
                }
                struct ClrBox {
                    std::string name;
                    Vec3 lo{}, hi{};
                };
                std::vector<ClrBox> clr_boxes;
                if (const pg::json::Value* clrs = item.is_object() ? item.find("clearances") : nullptr; clrs && clrs->is_array()) {
                    for (const pg::json::Value& cb : clrs->array) {
                        if (!cb.is_object()) continue;
                        const pg::json::Value* clo = cb.find("lo");
                        const pg::json::Value* chi = cb.find("hi");
                        if (!clo || !chi || !clo->is_array() || !chi->is_array() || clo->array.size() < 3 || chi->array.size() < 3) continue;
                        ClrBox bx;
                        if (const pg::json::Value* nm = cb.find("name"); nm && nm->is_string()) bx.name = nm->string;
                        bx.lo = {clo->array[0].number, clo->array[1].number, clo->array[2].number};
                        bx.hi = {chi->array[0].number, chi->array[1].number, chi->array[2].number};
                        clr_boxes.push_back(std::move(bx));
                    }
                }
                extra_cases.begin_object().key("set").begin_object();
                for (const auto& [k, v] : cc) extra_cases.field(k, v);
                extra_cases.end_object();
                try {
                    const doc::Result rc = doc::evaluate(d, lib, cc, &cache);
                    const doc::Receipt recc = doc::make_receipt(rc.geometry);
                    extra_cases.field("passed", true).field("placedTriangles", recc.placed_triangles);
                    if (recc.has_bounds) {
                        extra_cases.key("boundsZUp").begin_array();
                        write_vec(extra_cases, recc.lo);
                        write_vec(extra_cases, recc.hi);
                        extra_cases.end_array();
                    }
                    extra_cases.key("ports").begin_array();
                    for (const doc::PortFrame& p : doc::module_ports(d, rc.controls)) {
                        extra_cases.begin_object().field("name", p.name).field("type", p.type);
                        extra_cases.key("origin"); write_vec(extra_cases, p.origin);
                        extra_cases.key("normal"); write_vec(extra_cases, p.normal);
                        extra_cases.key("up"); write_vec(extra_cases, p.up);
                        extra_cases.key("dimensions").begin_object();
                        for (const auto& [dk, dv] : p.dimensions) extra_cases.field(dk, static_cast<double>(dv));
                        extra_cases.end_object().end_object();
                    }
                    extra_cases.end_array();
                    if (!clr_boxes.empty()) {
                        extra_cases.key("clearanceViolations").begin_array();
                        for (const ClrBox& bx : clr_boxes) {
                            std::size_t hits = 0;
                            std::set<std::string> hit_sources;
                            for (const doc::Part& pt : rc.geometry.parts) {
                                if (!pt.proto || pt.proto->mesh.face_count() == 0) continue;
                                // Exclude translucent glass/water panes if clearance is purely physical walkthrough? No: glass panes block archways/doorways unless open; keep strict!
                                const pg::Mesh& m = pt.proto->mesh;
                                const auto& pos = m.positions;
                                // Fast part-AABB cull first
                                const Aabb lb = pg::bounds(m);
                                if (!lb.valid()) continue;
                                Aabb wb;
                                for (int k = 0; k < 8; ++k)
                                    wb.expand(pt.transform.point({k & 1 ? lb.hi.x : lb.lo.x, k & 2 ? lb.hi.y : lb.lo.y, k & 4 ? lb.hi.z : lb.lo.z}));
                                if (wb.hi.x < bx.lo.x || wb.lo.x > bx.hi.x ||
                                    wb.hi.y < bx.lo.y || wb.lo.y > bx.hi.y ||
                                    wb.hi.z < bx.lo.z || wb.lo.z > bx.hi.z) continue;
                                for (std::size_t f = 0; f < m.face_count(); ++f) {
                                    const auto fv = m.face(f);
                                    if (fv.size() < 3) continue;
                                    const Vec3 p0 = pt.transform.point(pos[fv[0]]);
                                    for (std::size_t k = 1; k + 1 < fv.size(); ++k) {
                                        const Vec3 p1 = pt.transform.point(pos[fv[k]]);
                                        const Vec3 p2 = pt.transform.point(pos[fv[k + 1]]);
                                        if (tri_overlaps_box(p0, p1, p2, bx.lo, bx.hi)) {
                                            ++hits;
                                            if (hit_sources.size() < 6) {
                                                if (f < pt.proto->face_sources.size() && pt.proto->face_sources[f] && !pt.proto->face_sources[f]->empty())
                                                    hit_sources.insert(*pt.proto->face_sources[f]);
                                                else if (pt.source && !pt.source->empty())
                                                    hit_sources.insert(*pt.source);
                                            }
                                        }
                                    }
                                }
                            }
                            if (hits > 0) {
                                extra_cases.begin_object().field("name", bx.name).field("intrudingTriangles", hits).key("sources").begin_array();
                                for (const std::string& hs : hit_sources) extra_cases.value(hs);
                                extra_cases.end_array().end_object();
                            }
                        }
                        extra_cases.end_array();
                    }
                } catch (const std::exception& e) {
                    extra_cases.field("passed", false).field("error", e.what());
                }
                extra_cases.end_object();
            }
        }
    }
    extra_cases.end_array();

    // Inspect fields at default controls
    std::map<std::uint16_t, std::pair<std::size_t, std::size_t>> slots;
    for (const doc::Part& part : r0.geometry.parts) {
        auto& s = slots[part.material];
        ++s.first;
        s.second += part.proto->triangle_count();
    }

    const bool audit_ok = au_failed == 0 && (!check_dangling || aud0.dangling.empty());
    const bool all_ok = check_ok && sw_failed == 0 && audit_ok;
    w.begin_object().field("passed", all_ok);
    w.key("check").begin_object().field("passed", check_ok).field("clean", check_ok)
        .field("prototypes", tal0.prototypes).field("closedPrototypes", tal0.closed).field("openPrototypes", tal0.open)
        .field("nonManifoldPrototypes", tal0.nonmanifold).field("inconsistentPrototypes", tal0.inconsistent)
        .field("insideOutPrototypes", tal0.inside_out).field("selfIntersectingPrototypes", tal0.intersecting);
    write_sds(w, tal0);
    w.key("issues").raw(chk_issues.str()).end_object();
    w.key("sweep").begin_object().field("passed", sw_failed == 0).field("cases", sw_settings.size())
        .field("failed", sw_failed).key("failures").raw(sw_failures.str()).end_object();
    w.key("audit").begin_object().field("passed", audit_ok).field("components", aud0.components).field("allowed", allowed_comp)
        .field("failed", au_failed).key("failures").raw(au_failures.str());
    write_detached(w, aud0, 8);
    write_dangling(w, aud0, 8);
    w.end_object();
    w.key("inspect").begin_object();
    w.key("controls").begin_object();
    if (const pg::json::Value* controls = d.find("controls")) {
        for (const pg::json::Member& m : controls->object) {
            w.key(m.key).begin_object().field("value", r0.controls.at(m.key));
            for (const char* k : {"min", "max"})
                if (const pg::json::Value* v = m.value.find(k); v && v->is_number()) w.field(k, v->number);
            w.end_object();
        }
    }
    w.end_object();
    w.key("ports").begin_array();
    for (const doc::PortFrame& p : doc::module_ports(d, r0.controls)) {
        w.begin_object().field("name", p.name).field("type", p.type);
        w.key("origin"); write_vec(w, p.origin);
        w.key("normal"); write_vec(w, p.normal);
        w.key("up"); write_vec(w, p.up);
        w.key("dimensions").begin_object();
        for (const auto& [dk, dv] : p.dimensions) w.field(dk, static_cast<double>(dv));
        w.end_object().end_object();
    }
    w.end_array();
    w.key("materials").begin_array();
    for (const auto& [slot, s] : slots)
        w.begin_object().field("slot", doc::slot_name(slot)).field("placements", s.first).field("triangles", s.second).end_object();
    w.end_array();
    w.key("receipt").begin_object();
    write_receipt(w, r0);
    w.end_object();
    if (rec0.has_bounds) {
        w.key("boundsZUp").begin_array(); write_vec(w, rec0.lo); write_vec(w, rec0.hi); w.end_array();
    }
    w.end_object();
    w.key("extraCases").raw(extra_cases.str());
    w.field("cacheEntries", cache.size()).field("cacheHits", cache.hits()).field("cacheMisses", cache.misses());
    w.field("ms", now_ms() - t0).end_object();
    return all_ok ? 0 : 1;
}

}  // namespace

const std::vector<Command>& commands() {
    static const std::vector<Command> table = {
        {"evaluate", "pgm evaluate <doc.json> [--set name=value]... [--efficiency]", "Evaluate a geometry document: counts, bounds and optional 4-axis efficiency receipt.", cmd_evaluate},
        {"batch-verify", "pgm batch-verify <doc.json> [--intersections] [--dangling] [--allow-open] [--cases JSON]",
         "Single-process warm verification (check, sweep, audit --sweep, inspect and extra contract cases sharing one EvalCache).", cmd_batch_verify},
        {"pick", "pgm pick <doc.json> [--view iso|opposite|front|side|top] [--box xmin,ymin,xmax,ymax] [--coord norm|1000|px] [--size WxH]",
         "Back-project a 2D viewport box onto pre-bake 3D authoring parts (returns ranked source node IDs, pixel share and Z-up bounds).", cmd_pick},
        {"watch", "pgm watch <doc.json> [--set name=value]... [--stdin] [--once] [--max-events N] [--interval-ms N] [--out file.png] [--view V] [--pass P]",
         "Incremental settled-prefix / dirty-tail daemon: keeps node Merkle cache warm across file edits or stdin commands.", cmd_watch},
        {"inspect", "pgm inspect <doc.json> [--set name=value]...", "Controls, module imports, ports, node types and material slots.", cmd_inspect},
        {"check", "pgm check <doc.json> [--set name=value]... [--intersections]", "Topology of every prototype: closed, manifold, orientation, plus SDS/quad/pole/LOD stats.", cmd_check},
        {"materials", "pgm materials [--emat out.emat]",
         "List the material of every slot name (class, colour, flags) and write the flat engine material pack that binds them.", cmd_materials},
        {"catalog", "pgm catalog <directory>", "List the documents, modules and kits (with imported kits) of a recipe directory.", cmd_catalog},
        {"export", "pgm export <doc.json> [--emesh out.emesh] [--obj out.obj] [--no-dedupe] [--zup] [--no-meta] [--set name=value]...",
         "Write an engine mesh pack (.emesh, Y-up, with 0x8002-0x8005 engine metadata) and/or a Z-up OBJ.", cmd_export},
        {"assemble", "pgm assemble <assembly.json> [--root DIR] [--emesh out.emesh] [--out preview.png] [--audit] [--zup] [--no-meta]",
         "Place and port-mate multiple modules into one deduplicated multi-LOD world .emesh pack with inter-module contact audit.", cmd_assemble},
        {"render", "pgm render <doc.json|pack.emesh> --out file.png [--view iso|opposite|front|side|top] [--pass beauty|clay|parts|materials] [--size WxH] "
                   "[--yaw deg --pitch deg] [--projection ortho|perspective] [--section [+-]x|y|z=OFFSET] [--frame xmin,ymin,zmin,xmax,ymax,zmax] "
                   "[--frame-strict] [--pace-layer 0|1|2|3] [--no-details] [--wire-cage] [--lod 0..3] [--ghost prev.json] [--backend software|engine]",
         "Preview a kit, rendered from its .emesh bytes.", cmd_render},
        {"sheet", "pgm sheet <sheet.json> --out file.png [--root dir]", "Multi-panel kit sheet with captions.", cmd_sheet},
        {"audit", "pgm audit <doc.json|directory> [--set name=value]... [--sweep] [--dangling] [--baseline known.json]",
         "Contact audit: split the parts into solid islands and check they hold together within 3 mm; lists detached pieces.", cmd_audit},
        {"nodes", "pgm nodes [type]", "Node reference: every node type with its inputs and options.", cmd_nodes},
        {"sweep", "pgm sweep <doc.json|directory> [--intersections]",
         "Check every document at its defaults, at both ends of every control and at every variant; lists only the failures.", cmd_sweep},
        {"conformance", "pgm conformance <directory> <receipts.json>", "Compare every document against reference receipts.", cmd_conformance},
    };
    return table;
}

}  // namespace pgm
