// Graph evaluation for geometry documents: expressions, controls, groups,
// imported modules, port mating (connect) and the node dispatch. Semantics
// follow the reference Swift evaluator (GeometryNodes, GeometryNodeGroups,
// GeometryConnect, GeometryModuleLibrary); groups are evaluated recursively
// with scoped controls instead of being flattened first, which yields the same
// parts in the same order.
#include "doc_internal.hpp"
#include "doc_expr.hpp"

#include "pg/cleanup.hpp"
#include "pg/ops/boolean.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <set>
#include <unordered_map>

namespace pg::doc {

// ---- Mat4 --------------------------------------------------------------------

Mat4 Mat4::identity() noexcept {
    Mat4 m{};
    for (int i = 0; i < 4; ++i) m.c[i][i] = 1.0;
    return m;
}
Mat4 Mat4::operator*(const Mat4& o) const noexcept {
    Mat4 r{};
    for (int col = 0; col < 4; ++col)
        for (int row = 0; row < 4; ++row) {
            double s = 0;
            for (int k = 0; k < 4; ++k) s += c[k][row] * o.c[col][k];
            r.c[col][row] = s;
        }
    return r;
}
Vec3 Mat4::point(Vec3 p) const noexcept {
    return {c[0][0] * p.x + c[1][0] * p.y + c[2][0] * p.z + c[3][0], c[0][1] * p.x + c[1][1] * p.y + c[2][1] * p.z + c[3][1],
            c[0][2] * p.x + c[1][2] * p.y + c[2][2] * p.z + c[3][2]};
}
Vec3 Mat4::direction(Vec3 d) const noexcept {
    return {c[0][0] * d.x + c[1][0] * d.y + c[2][0] * d.z, c[0][1] * d.x + c[1][1] * d.y + c[2][1] * d.z,
            c[0][2] * d.x + c[1][2] * d.y + c[2][2] * d.z};
}
double Mat4::det3() const noexcept { return dot(column(0), cross(column(1), column(2))); }

std::size_t Proto::triangle_count() const noexcept {
    std::size_t n = 0;
    for (std::size_t f = 0; f < mesh.face_count(); ++f) n += mesh.face(f).size() - 2;
    return n;
}

// ---- material slots ------------------------------------------------------------

namespace {
struct SlotTable {
    std::mutex mutex;
    std::vector<std::string> names;
    std::vector<Vec3> colors;
    SlotTable() {
        const std::pair<const char*, Vec3> known[] = {
            {"stone", {0.62, 0.56, 0.45}},        {"iron", {0.19, 0.39, 0.34}},          {"bronze", {0.63, 0.40, 0.20}},
            {"glass", {0.52, 0.72, 0.73}},        {"brick", {0.53, 0.30, 0.22}},         {"wood", {0.50, 0.35, 0.24}},
            {"tile", {0.74, 0.69, 0.57}},         {"steel", {0.49, 0.51, 0.51}},         {"black", {0.21, 0.22, 0.22}},
            {"opal", {0.93, 0.81, 0.51}},         {"hangar-brick", {0.55, 0.29, 0.19}},  {"hangar-iron", {0.29, 0.25, 0.20}},
            {"hangar-zinc", {0.37, 0.43, 0.42}},  {"hangar-glazing", {0.51, 0.72, 0.67}}, {"weathered-brick", {0.56, 0.31, 0.20}},
            {"chipped-limestone", {0.74, 0.66, 0.49}}, {"weathered-iron", {0.31, 0.29, 0.25}},
            // procgen extension slots (terrain and water for levels)
            {"earth", {0.46, 0.38, 0.28}},        {"water", {0.22, 0.34, 0.38}},         {"grass", {0.36, 0.44, 0.24}},
            {"leaf", {0.27, 0.43, 0.21}},         {"terracotta", {0.66, 0.37, 0.25}},    {"patina", {0.37, 0.57, 0.50}},
            {"ballast", {0.41, 0.39, 0.36}},
            // the isle's strands (citygen terrain.gravel / terrain.rockfield): dry gravel, coarse shingle toward the points,
            // the damp swash band and the wet edge under the waterline
            {"strand-gravel", {0.60, 0.56, 0.49}}, {"strand-shingle", {0.52, 0.49, 0.44}},
            {"strand-damp", {0.44, 0.41, 0.36}},   {"strand-wet", {0.29, 0.28, 0.25}},
            // textured trim sheets (bands fitted with uvFit); colours stand in until the engine binds the texture
            {"pier-ashlar", {0.70, 0.66, 0.56}},
            // names the engine's starter library and citygen also use (shared where the meaning matches)
            {"copper", {0.72, 0.43, 0.24}},       {"slate", {0.27, 0.29, 0.32}},         {"concrete", {0.58, 0.57, 0.54}},
            {"sand", {0.76, 0.69, 0.52}},
            // liveries of the old project's Type 12 materials
            {"type12-hunter-green-lacquer", {0.11, 0.25, 0.18}}, {"douce-blued-boiler-steel", {0.17, 0.20, 0.24}},
            {"douce-oiled-wheel-steel", {0.25, 0.26, 0.27}},     {"type12-streamliner-nickel", {0.72, 0.72, 0.69}},
            // paint laid on another finish by a surface detail: witness marks, stencils, draught marks
            {"marking", {0.86, 0.84, 0.76}},
        };
        for (const auto& [n, c] : known) { names.emplace_back(n); colors.push_back(c); }
    }
};
SlotTable& slots() { static SlotTable t; return t; }
}  // namespace

std::uint16_t slot_id(std::string_view name) {
    SlotTable& t = slots();
    const std::scoped_lock lock(t.mutex);
    for (std::size_t i = 0; i < t.names.size(); ++i)
        if (t.names[i] == name) return static_cast<std::uint16_t>(i);
    t.names.emplace_back(name);
    t.colors.push_back({0.6, 0.6, 0.6});
    return static_cast<std::uint16_t>(t.names.size() - 1);
}
const std::string& slot_name(std::uint16_t id) { return slots().names.at(id); }
std::uint16_t slot_count() {
    SlotTable& t = slots();
    const std::scoped_lock lock(t.mutex);
    return static_cast<std::uint16_t>(t.names.size());
}
Vec3 slot_color(std::uint16_t id) { return slots().colors.at(id); }

// ---- options and expressions ------------------------------------------------------

namespace detail {

Mat4 compose(Vec3 p, Vec3 r, Vec3 s) {
    const double cx = std::cos(r.x), sx = std::sin(r.x), cy = std::cos(r.y), sy = std::sin(r.y), cz = std::cos(r.z), sz = std::sin(r.z);
    // R = Rz * Ry * Rx, columns scaled.
    Mat4 m = Mat4::identity();
    m.c[0][0] = cz * cy * s.x;                  m.c[0][1] = sz * cy * s.x;                  m.c[0][2] = -sy * s.x;
    m.c[1][0] = (cz * sy * sx - sz * cx) * s.y; m.c[1][1] = (sz * sy * sx + cz * cx) * s.y; m.c[1][2] = cy * sx * s.y;
    m.c[2][0] = (cz * sy * cx + sz * sx) * s.z; m.c[2][1] = (sz * sy * cx - cz * sx) * s.z; m.c[2][2] = cy * cx * s.z;
    m.c[3][0] = p.x; m.c[3][1] = p.y; m.c[3][2] = p.z;
    return m;
}

float Options::number(const json::Value& v) const {
    float result = 0;
    if (v.is_number()) {
        result = static_cast<float>(v.number);
    } else if (v.is_string()) {
        const std::string& key = v.string;
        if (key.starts_with("@")) {
            if (!point) fail("Unknown or unavailable point field " + key);
            if (key == "@index") result = static_cast<float>(point->index);
            else if (key == "@x") result = static_cast<float>(point->position.x);
            else if (key == "@y") result = static_cast<float>(point->position.y);
            else if (key == "@z") result = static_cast<float>(point->position.z);
            else if ((key == "@t" || key == "@u") && point->parametric) result = point->u;
            else if (key == "@v" && point->parametric) result = point->v;
            else if (key == "@random") {
                // Stable, platform-independent mixing with 24 bits of float precision.
                std::uint32_t bits = static_cast<std::uint32_t>(point->index) + static_cast<std::uint32_t>(point->seed) * 0x9E3779B9u;
                bits = (bits ^ (bits >> 16)) * 0x7FEB352Du;
                bits = (bits ^ (bits >> 15)) * 0x846CA68Bu;
                bits ^= bits >> 16;
                result = static_cast<float>(bits >> 8) / 16777216.0f;
            } else fail("Unknown or unavailable point field " + key);
        } else {
            if (!key.starts_with("$") || !controls) fail("Unknown numeric control " + key);
            const auto it = controls->find(key.substr(1));
            if (it == controls->end()) fail("Unknown numeric control " + key);
            result = it->second;
        }
    } else if (v.is_array()) {
        if (v.array.size() < 2 || v.array.size() > 4 || !v.array[0].is_string()) fail("Expected [operator, left, right]");
        const std::string& op = v.array[0].string;
        if (v.array.size() == 3) {
            const float a = number(v.array[1]), b = number(v.array[2]);
            if (op == "+") result = a + b;
            else if (op == "-") result = a - b;
            else if (op == "*") result = a * b;
            else if (op == "/") result = a / b;
            else if (op == "min") result = std::min(a, b);
            else if (op == "max") result = std::max(a, b);
            else if (op == ">") result = a > b ? 1.0f : 0.0f;
            // procgen extensions (the reference evaluator stops here)
            else if (op == "<") result = a < b ? 1.0f : 0.0f;
            else if (op == "pow") result = std::pow(a, b);
            else if (op == "atan2") result = std::atan2(a, b);
            else if (op == "mod") result = std::fmod(a, b);
            else fail("Unknown numeric operator " + op);
        } else if (v.array.size() == 2) {
            const float a = number(v.array[1]);
            if (op == "sin") result = std::sin(a);
            else if (op == "cos") result = std::cos(a);
            else if (op == "tan") result = std::tan(a);
            else if (op == "sqrt") result = std::sqrt(a);
            else if (op == "abs") result = std::abs(a);
            else if (op == "neg") result = -a;
            else if (op == "floor") result = std::floor(a);
            else if (op == "ceil") result = std::ceil(a);
            else if (op == "round") result = std::round(a);
            else fail("Unknown numeric function " + op);
        } else {
            // ["if", condition, then, else]: only the chosen branch is evaluated.
            if (op != "if") fail("Expected [operator, left, right]");
            result = number(v.array[number(v.array[1]) != 0 ? 2 : 3]);
        }
    } else {
        fail("Expected a number, control or expression");
    }
    if (!(std::isfinite(result) && std::abs(result) <= 1'000'000.0f)) fail("Numeric expression exceeds its finite range");
    return result;
}

float Options::scalar(std::string_view key, float fallback, float lo, float hi) const {
    const json::Value* v = get(key);
    const float n = v ? number(*v) : fallback;
    if (!(n >= lo && n <= hi)) fail(std::string(key) + " is outside its range");
    return n;
}
int Options::integer(std::string_view key, int fallback, int lo, int hi) const {
    const float n = scalar(key, static_cast<float>(fallback), static_cast<float>(lo), static_cast<float>(hi));
    if (n != std::round(n)) fail(std::string(key) + " must be an integer");
    return static_cast<int>(n);
}
Vec3 Options::vector(const json::Value& v) const {
    if (!v.is_array() || v.array.size() != 3) fail("Expected a three-component vector");
    return {number(v.array[0]), number(v.array[1]), number(v.array[2])};
}
Vec3 Options::vector(std::string_view key, Vec3 fallback) const {
    const json::Value* v = get(key);
    return v ? vector(*v) : fallback;
}
const std::vector<json::Value>& Options::list(std::string_view key, std::size_t lo, std::size_t hi) const {
    const json::Value* v = get(key);
    if (!v || !v->is_array() || v->array.size() < lo || v->array.size() > hi)
        fail(std::string(key) + " must contain " + std::to_string(lo) + "…" + std::to_string(hi) + " entries");
    return v->array;
}
Mat4 Options::transform() const {
    const Vec3 p = vector("translation", {}), r = vector("rotation", {}), s = vector("scale", {1, 1, 1});
    const double smin = std::min({std::abs(s.x), std::abs(s.y), std::abs(s.z)}), smax = std::max({std::abs(s.x), std::abs(s.y), std::abs(s.z)});
    if (!(smin >= 0.0001 && smax <= 1000)) fail("Scale magnitude must be between 0.0001 and 1000");
    return compose(p, r, s);
}

}  // namespace detail

using detail::fail;
using detail::Options;

// ---- controls ----------------------------------------------------------------------

namespace {

bool valid_id(std::string_view id) {
    if (id.empty() || id.size() > 64) return false;
    for (const char ch : id)
        if (!(std::isalnum(static_cast<unsigned char>(ch)) || ch == '-' || ch == '.' || ch == '_')) return false;
    return true;
}

struct Declared {
    float value, min, max;
    bool integer;
};
Declared declared(const json::Value& control) {
    const json::Value* v = control.find("value");
    const json::Value* lo = control.find("min");
    const json::Value* hi = control.find("max");
    const json::Value* integer = control.find("integer");
    if (!v || !lo || !hi || !v->is_number() || !lo->is_number() || !hi->is_number()) fail("Control needs numeric value, min and max");
    return {static_cast<float>(v->number), static_cast<float>(lo->number), static_cast<float>(hi->number),
            integer && integer->kind == json::Value::Kind::Bool && integer->boolean};
}
float checked(const std::string& name, const Declared& d, float value, const char* what) {
    auto ok = [](float x) { return std::isfinite(x) && std::abs(x) <= 100000.0f; };
    if (!valid_id(name) || !ok(value) || !ok(d.value) || !ok(d.min) || !ok(d.max) || !(d.min <= d.value && d.value <= d.max) ||
        !(d.min <= value && value <= d.max) || (d.integer && (std::round(value) != value || std::round(d.value) != d.value)))
        fail(std::string(what) + " " + name);
    return value;
}

}  // namespace

Controls control_values(const json::Value& document, const Controls& overrides) {
    Controls values;
    if (const json::Value* controls = document.find("controls"))
        for (const json::Member& m : controls->object) {
            const Declared d = declared(m.value);
            const auto it = overrides.find(m.key);
            values[m.key] = checked(m.key, d, it != overrides.end() ? it->second : d.value, "Invalid control value or range for");
        }
    for (const auto& [name, v] : overrides)
        if (!values.count(name)) fail("Unknown geometry control " + name);
    // Derived values: [name, expression] pairs evaluated in order after the controls, each able to use the controls
    // and the derived values before it, and read like a control ("$name"). They keep documents small: a value used
    // in many places is written once instead of as a copy of its whole expression at every use.
    if (const json::Value* derived = document.find("derived")) {
        if (!derived->is_array() || derived->array.size() > 4096) fail("derived must be a list of at most 4096 [name, expression] pairs");
        for (const json::Value& e : derived->array) {
            if (!e.is_array() || e.array.size() != 2 || !e.array[0].is_string()) fail("Expected [name, expression] in derived");
            const std::string& name = e.array[0].string;
            if (!valid_id(name) || values.count(name)) fail("Invalid or duplicate derived value " + name);
            const Options o{nullptr, &values};
            const float value = o.number(e.array[1]);
            if (!std::isfinite(value)) fail("Derived value " + name + " is not finite");
            values[name] = value;
        }
    }
    return values;
}

// ---- library -------------------------------------------------------------------------

Library::Library(std::string directory) : dir_(std::move(directory)) { load(dir_, 0); }

void Library::load(const std::string& directory, int depth) {
    const std::string dir = std::filesystem::path(directory).lexically_normal().string();
    for (const Kit& k : kits_)
        if (k.directory == dir) return;  // already loaded (diamond or cyclic imports)
    if (depth > 8) fail("Kit imports nest too deeply at " + dir);
    json::Value manifest;
    try {
        manifest = json::parse_file(dir + "/modules.json");
    } catch (const std::runtime_error&) {
        if (depth > 0) fail("Imported kit has no modules.json: " + dir);
        return;  // a directory without a registry simply has no modules
    }
    const std::size_t self = kits_.size();
    kits_.emplace_back();
    kits_[self].directory = dir;
    if (const json::Value* name = manifest.find("kit"); name && name->is_string()) kits_[self].name = name->string;
    if (const json::Value* modules = manifest.find("modules"))
        for (const json::Member& m : modules->object) {
            Module mod;
            mod.directory = dir;
            mod.kit = kits_[self].name;
            if (const json::Value* d = m.value.find("document"); d && d->is_string()) mod.document = d->string;
            if (const json::Value* ports = m.value.find("ports"))
                for (const json::Member& p : ports->object) {
                    Port port;
                    if (const json::Value* v = p.value.find("origin")) port.origin = *v;
                    if (const json::Value* v = p.value.find("normal")) port.normal = *v;
                    if (const json::Value* v = p.value.find("up")) port.up = *v;
                    if (const json::Value* v = p.value.find("type"); v && v->is_string()) port.type = v->string;
                    if (const json::Value* v = p.value.find("dimensions")) port.dimensions = *v;
                    mod.ports.emplace(p.key, std::move(port));
                }
            if (const auto it = modules_.find(m.key); it != modules_.end())
                fail("Geometry module " + m.key + " is registered by two kits: " + it->second.directory + " and " + dir);
            kits_[self].modules.push_back(m.key);
            modules_.emplace(m.key, std::move(mod));
        }
    if (const json::Value* imports = manifest.find("imports")) {
        if (!imports->is_array() || imports->array.size() > 32) fail("Kit imports must be a list of directories: " + dir);
        for (const json::Value& v : imports->array) {
            if (!v.is_string() || v.string.empty()) fail("Kit imports must be a list of directories: " + dir);
            const std::string target = (std::filesystem::path(dir) / v.string).lexically_normal().string();
            kits_[self].imports.push_back(target);
            load(target, depth + 1);
        }
    }
}
const Library::Module* Library::module(const std::string& id) const {
    const auto it = modules_.find(id);
    return it == modules_.end() ? nullptr : &it->second;
}
std::vector<std::string> Library::module_ids() const {
    std::vector<std::string> ids;
    for (const auto& [id, m] : modules_) ids.push_back(id);
    return ids;
}
const json::Value& Library::document(const std::string& id) {
    if (const auto it = documents_.find(id); it != documents_.end()) return *it->second;
    const Module* mod = module(id);
    if (!mod) fail("Unknown geometry module " + id);
    auto doc = std::make_unique<json::Value>(json::parse_file(mod->directory + "/" + mod->document));
    return *documents_.emplace(id, std::move(doc)).first->second;
}

// ---- port mating -------------------------------------------------------------------------

namespace {

struct Frame {
    Vec3 origin, normal, up;
};
Frame make_frame(Vec3 origin, Vec3 normal, Vec3 up, const std::string& label) {
    const double nl = length(normal), ul = length(up);
    if (!(nl > 1e-9 && ul > 1e-9)) fail("Port " + label + " needs nonzero normal and up directions");
    const Vec3 n = normal / nl, d = up / ul;
    const Vec3 projected = d - n * dot(n, d);
    if (!(length(projected) > 1e-6)) fail("Port " + label + " needs an up direction independent of its normal");
    return {origin, n, projected / length(projected)};
}
Frame moved(const Frame& f, const Mat4& pose, const std::string& label) {
    return make_frame(pose.direction(f.origin) + pose.column(3), pose.direction(f.normal), pose.direction(f.up), label);
}

struct Endpoint {
    std::string module, port;
    Controls controls;
    Mat4 pose = Mat4::identity();  // where the module instance already sits (placed "@node" endpoints)
};
struct ResolvedPort {
    Frame frame;
    std::string type;
    std::map<std::string, float> dimensions;
};

ResolvedPort resolve_port(const Endpoint& e, Library& lib) {
    const std::string label = e.module + "." + e.port;
    const Library::Module* mod = lib.module(e.module);
    if (!mod) fail("Unknown geometry module " + e.module);
    const auto it = mod->ports.find(e.port);
    if (it == mod->ports.end()) fail("Unknown geometry module port " + label);
    const Library::Port& port = it->second;
    const Controls controls = control_values(lib.document(e.module), e.controls);
    const Options o{nullptr, &controls};
    ResolvedPort r;
    r.type = port.type;
    if (port.dimensions.is_object())
        for (const json::Member& m : port.dimensions.object) r.dimensions[m.key] = o.number(m.value);
    static const std::map<std::string, std::set<std::string>> required = {
        {"wall", {"width", "height"}}, {"deck", {"width"}}, {"track", {"gauge"}}, {"water", {"width", "height"}}};
    bool ok = valid_id(port.type) && r.dimensions.size() <= 32;
    if (const auto req = required.find(port.type); ok && req != required.end())
        for (const std::string& k : req->second) ok = ok && r.dimensions.count(k);
    for (const auto& [k, v] : r.dimensions) ok = ok && valid_id(k) && v > 0;
    if (!ok) fail("Invalid interface type or dimensions for " + label);
    r.frame = make_frame(o.vector(port.origin), o.vector(port.normal), o.vector(port.up), label);
    return r;
}

// Rigid pose seating `moving` on `fixed` (origins coincide, normals oppose,
// ups align). With `checking`, validates that pose instead of solving one.
Mat4 mate(const ResolvedPort& fixed, const ResolvedPort& moving, const Mat4& fixed_pose, double tolerance, const Mat4* checking,
          const std::string& label) {
    if (!(tolerance > 0 && tolerance <= 0.01)) fail("Tolerance must be in (0, 0.01] metres");
    if (fixed.type != moving.type) fail(label + ": interface type mismatch: " + fixed.type + " and " + moving.type);
    bool same_keys = fixed.dimensions.size() == moving.dimensions.size();
    for (const auto& [k, v] : fixed.dimensions) same_keys = same_keys && moving.dimensions.count(k);
    if (!same_keys) fail(label + ": interface dimension keys differ");
    for (const auto& [k, v] : fixed.dimensions)
        if (std::abs(static_cast<double>(v) - static_cast<double>(moving.dimensions.at(k))) > tolerance) fail(label + ": " + k + " mismatch");

    const Frame target = moved(fixed.frame, fixed_pose, "fixed port");
    // rotation = basis(target, opposed) * basis(moving)^T, bases as columns (facing, up, facing x up).
    const Vec3 tf = -target.normal, tu = target.up, tw = cross(tf, tu);
    const Vec3 mf = moving.frame.normal, mu = moving.frame.up, mw = cross(mf, mu);
    double rot[3][3];  // rot[col][row]
    for (int col = 0; col < 3; ++col)
        for (int row = 0; row < 3; ++row) rot[col][row] = tf[row] * mf[col] + tu[row] * mu[col] + tw[row] * mw[col];
    // Euler XYZ extraction, then rebuild in float like the graph transform does.
    const double cos_y = std::hypot(rot[0][0], rot[0][1]);
    const double y = std::atan2(-rot[0][2], cos_y);
    Vec3 euler;
    if (cos_y > 1e-8) euler = {std::atan2(rot[1][2], rot[2][2]), y, std::atan2(rot[0][1], rot[0][0])};
    else euler = {0, y, std::atan2(-rot[1][0], rot[1][1])};  // gimbal lock: X = 0, rest in Z
    euler = {static_cast<double>(static_cast<float>(euler.x)), static_cast<double>(static_cast<float>(euler.y)),
             static_cast<double>(static_cast<float>(euler.z))};
    Mat4 solved = detail::compose({}, euler, {1, 1, 1});
    const Vec3 offset = target.origin - solved.direction(moving.frame.origin);
    if (!(std::max({std::abs(offset.x), std::abs(offset.y), std::abs(offset.z)}) <= 1'000'000)) fail("Connection translation exceeds the graph transform range");
    solved.c[3][0] = static_cast<float>(offset.x);
    solved.c[3][1] = static_cast<float>(offset.y);
    solved.c[3][2] = static_cast<float>(offset.z);

    const Mat4& pose = checking ? *checking : solved;
    const Frame seated = moved(moving.frame, pose, "moving port");
    const double origin_error = length(target.origin - seated.origin);
    const double normal_dot = std::clamp(dot(target.normal, seated.normal), -1.0, 1.0);
    const double up_dot = std::clamp(dot(target.up, seated.up), -1.0, 1.0);
    if (origin_error > tolerance) fail(label + ": port gap " + std::to_string(origin_error) + " m exceeds tolerance");
    if (normal_dot > -0.9999) fail(label + ": tangent error " + std::to_string(std::acos(-normal_dot) * 180 / 3.14159265358979) + " degrees");
    if (up_dot < 0.9999) fail(label + ": up directions must align");
    return pose;
}

// ---- settled-prefix / dirty-tail Merkle cache -------------------------------

struct Hash128 {
    std::uint64_t lo = 0, hi = 0;
    bool operator==(const Hash128&) const noexcept = default;
};

struct Hash128Hasher {
    std::size_t operator()(const Hash128& h) const noexcept {
        return static_cast<std::size_t>(h.lo ^ (h.hi * 0x9E3779B97F4A7C15ULL));
    }
};

class Hasher128 {
public:
    void bytes(const void* data, std::size_t len) noexcept {
        const auto* p = static_cast<const std::uint8_t*>(data);
        for (std::size_t i = 0; i < len; ++i) {
            lo_ = (lo_ ^ p[i]) * 0x100000001B3ULL;
            hi_ = (hi_ ^ (static_cast<std::uint64_t>(p[i]) + 0x9E3779B97F4A7C15ULL)) * 0xBF58476D1CE4E5B9ULL;
            hi_ ^= (lo_ >> 29);
        }
    }
    void byte(std::uint8_t b) noexcept { bytes(&b, 1); }
    void u64(std::uint64_t v) noexcept { bytes(&v, sizeof(v)); }
    void u32(std::uint32_t v) noexcept { bytes(&v, sizeof(v)); }
    void str(std::string_view s) noexcept {
        u64(s.size());
        bytes(s.data(), s.size());
    }
    void digest_in(const Hash128& d) noexcept {
        u64(d.lo);
        u64(d.hi);
    }
    [[nodiscard]] Hash128 finish() const noexcept {
        auto mix = [](std::uint64_t x) noexcept {
            x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
            x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
            return x ^ (x >> 31);
        };
        return {mix(lo_ ^ (hi_ >> 17)), mix(hi_ ^ (lo_ >> 23))};
    }

private:
    std::uint64_t lo_ = 0xCBF29CE484222325ULL;
    std::uint64_t hi_ = 0x84222325CBF29CE4ULL;
};

void hash_resolved_json(Hasher128& h, const json::Value& v, const Controls& controls) {
    switch (v.kind) {
        case json::Value::Kind::Null:
            h.byte(0);
            break;
        case json::Value::Kind::Bool:
            h.byte(v.boolean ? 2 : 1);
            break;
        case json::Value::Kind::Number: {
            h.byte(3);
            const double canon = (v.number == 0.0) ? 0.0 : v.number;
            std::uint64_t bits = 0;
            std::memcpy(&bits, &canon, sizeof(bits));
            h.u64(bits);
            break;
        }
        case json::Value::Kind::String: {
            if (v.string.starts_with("$")) {
                const auto it = controls.find(v.string.substr(1));
                if (it != controls.end()) {
                    h.byte(4);
                    const float canon = (it->second == 0.0f) ? 0.0f : it->second;
                    std::uint32_t bits = 0;
                    std::memcpy(&bits, &canon, sizeof(bits));
                    h.u32(bits);
                    break;
                }
            }
            h.byte(5);
            h.str(v.string);
            break;
        }
        case json::Value::Kind::Array:
            h.byte(6);
            h.u64(v.array.size());
            for (const json::Value& e : v.array) hash_resolved_json(h, e, controls);
            break;
        case json::Value::Kind::Object: {
            h.byte(7);
            h.u64(v.object.size());
            std::vector<const json::Member*> sorted;
            sorted.reserve(v.object.size());
            for (const json::Member& m : v.object) sorted.push_back(&m);
            std::sort(sorted.begin(), sorted.end(), [](const json::Member* a, const json::Member* b) { return a->key < b->key; });
            for (const json::Member* m : sorted) {
                h.str(m->key);
                hash_resolved_json(h, m->value, controls);
            }
            break;
        }
    }
}

}  // namespace

struct EvalCache::Impl {
    std::size_t max_entries = 8192;
    std::size_t hit_count = 0;
    std::size_t miss_count = 0;
    std::unordered_map<Hash128, std::shared_ptr<const Geometry>, Hash128Hasher> map;

    void put(const Hash128& k, const std::shared_ptr<const Geometry>& v) {
        if (max_entries == 0) return;
        if (map.size() >= max_entries && !map.count(k)) {
            // Bounded retained memory: evict older half in O(N) only on cap overflow.
            auto it = map.begin();
            for (std::size_t k_evict = 0, target = map.size() / 2; k_evict < target && it != map.end(); ++k_evict) {
                it = map.erase(it);
            }
        }
        map[k] = v;
    }
};

EvalCache::EvalCache(std::size_t max_cap) : impl_(std::make_unique<Impl>()) { impl_->max_entries = max_cap; }
EvalCache::~EvalCache() = default;
EvalCache::EvalCache(EvalCache&&) noexcept = default;
EvalCache& EvalCache::operator=(EvalCache&&) noexcept = default;
void EvalCache::clear() noexcept {
    if (impl_) {
        impl_->map.clear();
        impl_->hit_count = 0;
        impl_->miss_count = 0;
    }
}
std::size_t EvalCache::size() const noexcept { return impl_ ? impl_->map.size() : 0; }
std::size_t EvalCache::max_entries() const noexcept { return impl_ ? impl_->max_entries : 0; }
std::size_t EvalCache::hits() const noexcept { return impl_ ? impl_->hit_count : 0; }
std::size_t EvalCache::misses() const noexcept { return impl_ ? impl_->miss_count : 0; }

namespace {

// ---- evaluator ------------------------------------------------------------------------------

using Value = std::shared_ptr<const Geometry>;

struct Scope {
    const json::Value* document = nullptr;  // owner of groups / moduleImports for this scope
    const json::Value* nodes = nullptr;
    Controls controls;
    std::map<std::string, Value> sockets;
    std::map<std::string, Hash128> socket_digests;
    std::string prefix;
    std::vector<std::pair<std::string, std::string>> stack;  // (module, group) call chain
    std::unordered_map<std::string, const json::Value*> by_id;
    std::unordered_map<std::string, Value> cache;
    std::unordered_map<std::string, Hash128> digests;
    std::unordered_map<const json::Value*, Mat4> seated;  // solved poses of transform / connect nodes
    std::set<std::string> active;
    int seating = 0;
};

struct Evaluator {
    Library& library;
    EvalCache::Impl* ext_cache = nullptr;
    std::size_t evaluated = 0;
    std::size_t reused = 0;
    std::size_t calls = 0;

    void index(Scope& s) {
        if (!s.nodes || !s.nodes->is_array() || s.nodes->array.empty()) fail("A graph needs a list of nodes");
        for (const json::Value& n : s.nodes->array) {
            const json::Value* id = n.find("id");
            if (!id || !id->is_string() || !valid_id(id->string) || !s.by_id.emplace(id->string, &n).second)
                fail("Invalid or duplicate node ID " + (id && id->is_string() ? id->string : std::string("?")));
        }
    }

    // Group definition for `name` in scope `s`: nodes, controls, sockets, output, module id.
    struct Definition {
        const json::Value* document;
        const json::Value* nodes;
        const json::Value* controls;
        const json::Value* inputs;
        std::string output, module;
    };
    Definition definition(const Scope& s, const std::string& name) {
        if (const json::Value* groups = s.document->find("groups"))
            if (const json::Value* g = groups->find(name)) {
                const json::Value* out = g->find("output");
                return {s.document, g->find("nodes"), g->find("controls"), g->find("geometryInputs"),
                        out && out->is_string() ? out->string : "", ""};
            }
        if (const json::Value* imports = s.document->find("moduleImports"))
            if (const json::Value* id = imports->find(name); id && id->is_string()) {
                const json::Value& doc = library.document(id->string);
                const json::Value* out = doc.find("output");
                return {&doc, doc.find("nodes"), doc.find("controls"), nullptr, out && out->is_string() ? out->string : "", id->string};
            }
        fail("Unknown geometry group " + name);
    }
    Controls bind(const Definition& def, const json::Value* bindings, const Controls& parent, const std::string& name) {
        const Options o{nullptr, &parent};
        Controls child;
        std::size_t used = 0;
        if (def.controls)
            for (const json::Member& m : def.controls->object) {
                const Declared d = declared(m.value);
                const json::Value* b = bindings ? bindings->find(m.key) : nullptr;
                used += b ? 1 : 0;
                child[m.key] = checked(m.key, d, b ? o.number(*b) : d.value, ("Invalid group control " + name + ".").c_str());
            }
        if (bindings && used != bindings->object.size()) fail("Unknown numeric group binding for " + name);
        return child;
    }

    Endpoint endpoint(Scope& s, const Options& o, const json::Value* v) {
        if (!v || !v->is_array() || v->array.size() < 2 || v->array.size() > 3 || !v->array[0].is_string() || !v->array[1].is_string())
            fail("Expected [module, port, optional control pairs]");
        Endpoint e{v->array[0].string, v->array[1].string, {}, Mat4::identity()};
        if (e.module.starts_with("@") && v->array.size() == 2) {
            // A live port of an imported group in this scope: same module, same resolved controls.
            // The reference may also name a transform / connect node that places such a group
            // (procgen extension); the endpoint then carries the pose the module already has,
            // so modules can be chained without restating poses.
            const std::string name = e.module.substr(1);
            std::string id = name;
            const json::Value* node = nullptr;
            for (int depth = 0;; ++depth) {
                const auto it = s.by_id.find(id);
                if (it == s.by_id.end() || depth > 64) fail("Unknown imported module instance " + name);
                node = it->second;
                const json::Value* type = node->find("type");
                const bool seat = type && type->is_string() && type->string == "connect";
                if (!seat && !(type && type->is_string() && type->string == "transform")) break;
                const json::Value* in = node->find("inputs");
                if (!in || !in->is_array() || in->array.size() != 1 || !in->array[0].is_string()) fail("Unknown imported module instance " + name);
                auto known = s.seated.find(node);
                if (known == s.seated.end()) {
                    if (++s.seating > 256) fail("Connection chain is cyclic or too deep at " + name);
                    const Options no{node->find("options"), &s.controls};
                    known = s.seated.emplace(node, seat ? connect(s, no) : no.transform()).first;
                    --s.seating;
                }
                e.pose = e.pose * known->second;
                id = in->array[0].string;
            }
            const json::Value* group = node->find("group");
            if (!group || !group->is_string()) fail("Unknown imported module instance " + name);
            const Definition def = definition(s, group->string);
            if (def.module.empty()) fail("Unknown imported module instance " + name);
            e.controls = bind(def, node->find("bindings"), s.controls, group->string);
            e.module = def.module;
            return e;
        }
        if (v->array.size() == 3) {
            if (!v->array[2].is_array() || v->array[2].array.size() > 128) fail("Invalid port controls");
            for (const json::Value& pair : v->array[2].array) {
                if (!pair.is_array() || pair.array.size() != 2 || !pair.array[0].is_string() || e.controls.count(pair.array[0].string))
                    fail("Expected unique [control, expression] pairs");
                e.controls[pair.array[0].string] = o.number(pair.array[1]);
            }
        }
        return e;
    }
    Mat4 connect(Scope& s, const Options& o) {
        const Vec3 t = o.vector("translation", {}), r = o.vector("rotation", {});
        const double tolerance = o.scalar("tolerance", 0.003f, 0, 0.01f);
        const Endpoint a = endpoint(s, o, o.get("a")), b = endpoint(s, o, o.get("b"));
        // An explicit pose wins; otherwise the fixed module sits where its placing node put it.
        const Mat4 fixed_pose = o.get("translation") || o.get("rotation") ? detail::compose(t, r, {1, 1, 1}) : a.pose;
        const Mat4 pose = mate(resolve_port(a, library), resolve_port(b, library), fixed_pose, tolerance, nullptr, a.port + " / " + b.port);
        if (o.get("also"))
            for (const json::Value& pair : o.list("also", 1, 16)) {
                if (!pair.is_array() || pair.array.size() != 2) fail("Expected two secondary ports");
                const Endpoint f = endpoint(s, o, &pair.array[0]), m = endpoint(s, o, &pair.array[1]);
                if (f.module != a.module || m.module != b.module || f.controls != a.controls || m.controls != b.controls)
                    fail("Secondary ports must use the same modules and controls");
                (void)mate(resolve_port(f, library), resolve_port(m, library), fixed_pose, tolerance, &pose, f.port + " / " + m.port);
            }
        return pose;
    }

    static Part make_part(ProtoPtr proto, const std::shared_ptr<const std::string>& source) {
        Part p;
        p.proto = std::move(proto);
        p.source = source;
        return p;
    }
    static void check_transform(const Mat4& m) {
        bool ok = std::abs(m.det3()) >= 1e-16;
        for (int c = 0; ok && c < 4; ++c)
            for (int r = 0; r < 3; ++r) ok = ok && std::isfinite(m.c[c][r]) && std::abs(m.c[c][r]) <= 10'000'000;
        if (!ok) fail("Transform is singular or exceeds the coordinate budget");
    }

    Value eval(Scope& s, const std::string& id) {
        if (const auto hit = s.cache.find(id); hit != s.cache.end()) return hit->second;
        const auto found = s.by_id.find(id);
        if (found == s.by_id.end()) fail("Unknown geometry node " + id);
        const json::Value& node = *found->second;
        const json::Value* type_v = node.find("type");
        if (!type_v || !type_v->is_string()) fail(id + ": node needs a type");
        const std::string& type = type_v->string;
        if (!find_node_type(type)) fail(id + ": Unknown node type " + type + " (pgm nodes lists them)");
        if (type == "groupInput") {
            const auto socket = s.sockets.find(id);
            if (socket == s.sockets.end()) fail("Unknown geometry input " + id);
            if (ext_cache) s.digests[id] = s.socket_digests[id];
            return socket->second;
        }
        if (!s.active.insert(id).second) fail("Geometry dependency cycle at " + id);
        std::vector<std::string> inputs;
        if (const json::Value* in = node.find("inputs"))
            for (const json::Value& v : in->array) inputs.push_back(v.is_string() ? v.string : std::string());
        const Options o{node.find("options"), &s.controls};
        const std::string qualified = s.prefix.empty() ? id : s.prefix + "." + id;
        const auto source = std::make_shared<const std::string>(qualified);
        auto need = [&](std::size_t lo, std::size_t hi) {
            if (inputs.size() < lo || inputs.size() > hi) fail("Invalid inputs for " + type);
        };
        Hash128 node_digest{};
        bool group_all_settled = false;
        bool select_settled = false;
        Geometry out;
        try {
            if (ext_cache && type != "select" && type != "group") {
                // Pre-evaluate upstream inputs (memoized in s.cache) and check the 128-bit
                // settled-prefix Merkle cache before running geometry operations.
                Hasher128 h;
                h.str(type);
                h.str(qualified);
                h.u32(static_cast<std::uint32_t>(projection_search()));
                h.u64(inputs.size());
                for (const std::string& in_id : inputs) {
                    (void)eval(s, in_id);
                    h.digest_in(s.digests[in_id]);
                }
                if (type == "connect") {
                    need(1, 1);
                    const Mat4 pose = connect(s, o);
                    s.seated[&node] = pose;
                    h.bytes(&pose.c[0][0], sizeof(pose.c));
                } else if (const json::Value* opt = node.find("options")) {
                    h.byte(1);
                    hash_resolved_json(h, *opt, s.controls);
                } else {
                    h.byte(0);
                }
                node_digest = h.finish();
                if (const auto hit = ext_cache->map.find(node_digest); hit != ext_cache->map.end()) {
                    s.active.erase(id);
                    ++reused;
                    ++ext_cache->hit_count;
                    s.cache.emplace(id, hit->second);
                    s.digests.emplace(id, node_digest);
                    return hit->second;
                }
            }
            if (type == "select") {
                need(2, 2);
                const std::size_t branch = o.scalar("condition", 1, 0, 1) != 0 ? 0 : 1;
                const Value v = eval(s, inputs[branch]);
                if (ext_cache) {
                    Hasher128 h;
                    h.str("select");
                    h.str(qualified);
                    h.u64(branch);
                    h.digest_in(s.digests[inputs[branch]]);
                    node_digest = h.finish();
                    if (const auto hit = ext_cache->map.find(node_digest); hit != ext_cache->map.end()) {
                        select_settled = true;
                        out = *hit->second;
                    } else {
                        out = *v;
                    }
                } else {
                    out = *v;
                }
            } else if (type == "group") {
                const json::Value* name = node.find("group");
                if (!name || !name->is_string()) fail("Missing geometry group");
                const Definition def = definition(s, name->string);
                const std::pair<std::string, std::string> key{def.module.empty() ? s.stack.empty() ? "" : s.stack.back().first : def.module,
                                                              def.module.empty() ? name->string : ""};
                if (std::find(s.stack.begin(), s.stack.end(), key) != s.stack.end() || s.stack.size() >= 16 || calls >= 1024)
                    fail("Geometry group cycle or expansion budget exceeded at " + name->string);
                ++calls;
                Scope child;
                child.document = def.document;
                child.nodes = def.nodes;
                child.controls = bind(def, node.find("bindings"), s.controls, name->string);
                child.prefix = qualified;
                child.stack = s.stack;
                child.stack.push_back(key);
                const std::size_t sockets = def.inputs ? def.inputs->array.size() : 0;
                if (sockets != inputs.size()) fail("Invalid geometry sockets for " + name->string);
                for (std::size_t i = 0; i < sockets; ++i) {
                    const std::string& sock_name = def.inputs->array[i].string;
                    child.sockets[sock_name] = eval(s, inputs[i]);
                    if (ext_cache) child.socket_digests[sock_name] = s.digests[inputs[i]];
                }
                index(child);
                const std::size_t eval_before = evaluated;
                out = *eval(child, def.output);
                if (ext_cache) {
                    Hasher128 h;
                    h.str("group");
                    h.str(qualified);
                    h.digest_in(child.digests[def.output]);
                    node_digest = h.finish();
                    group_all_settled = (evaluated == eval_before) && (ext_cache->map.find(node_digest) != ext_cache->map.end());
                }
            } else if (type == "join") {
                need(1, std::numeric_limits<std::size_t>::max());
                for (const std::string& in : inputs) {
                    const Value v = eval(s, in);
                    out.parts.insert(out.parts.end(), v->parts.begin(), v->parts.end());
                    out.points.insert(out.points.end(), v->points.begin(), v->points.end());
                    out.curves.insert(out.curves.end(), v->curves.begin(), v->curves.end());
                }
            } else if (type == "transform" || type == "connect") {
                need(1, 1);
                Mat4 m;
                if (type == "connect") {
                    if (const auto st = s.seated.find(&node); st != s.seated.end()) m = st->second;
                    else m = connect(s, o);
                } else {
                    m = o.transform();
                }
                out = *eval(s, inputs[0]);
                for (Part& p : out.parts) {
                    p.transform = m * p.transform;
                    check_transform(p.transform);
                }
                for (Vec3& p : out.points) p = m.point(p);
                for (Curve& c : out.curves)
                    for (Vec3& p : c.points) p = m.point(p);
            } else if (type == "material") {
                need(1, 1);
                const json::Value* slot = o.get("slot");
                if (!slot || !slot->is_string()) fail("Unknown or missing material slot");
                const std::uint16_t to = slot_id(slot->string);
                const json::Value* replace = o.get("replace");
                const bool filtered = replace && replace->is_string();
                const std::uint16_t from = filtered ? slot_id(replace->string) : 0;
                out = *eval(s, inputs[0]);
                for (Part& p : out.parts)
                    if (!filtered || p.material == from) p.material = to;
            } else if (type == "tint") {
                // Multiply the instance tint of everything below: a colour per placement, not per mesh.
                need(1, 1);
                const Vec3 c = o.vector("color", {1, 1, 1});
                out = *eval(s, inputs[0]);
                const double k[3] = {c.x, c.y, c.z};
                for (Part& p : out.parts)
                    for (std::size_t i = 0; i < 3; ++i)
                        p.tint[i] = static_cast<std::uint8_t>(std::lround(std::clamp(p.tint[i] * k[i], 0.0, 255.0)));
            } else if (type == "instance") {
                need(2, 65);
                const Value positions = eval(s, inputs[0]);
                if (positions->points.empty()) fail("First instance input must supply points");
                std::vector<Value> prototypes;
                prototypes.reserve(inputs.size() - 1);
                std::size_t max_each = 0;
                for (std::size_t k = 1; k < inputs.size(); ++k) {
                    Value pr = eval(s, inputs[k]);
                    max_each = std::max(max_each, std::max(pr->parts.size(), pr->curves.size()));
                    prototypes.push_back(std::move(pr));
                }
                if (positions->points.size() * max_each > 24000)
                    fail("Geometry instance, point, or triangle budget exceeded");
                out.parts.reserve(positions->points.size() * max_each);
                if (prototypes.size() == 1 && o.empty()) {
                    const Geometry& prototype = *prototypes[0];
                    for (const Vec3& p : positions->points) {
                        for (Part part : prototype.parts) {
                            part.transform.c[3][0] += p.x;
                            part.transform.c[3][1] += p.y;
                            part.transform.c[3][2] += p.z;
                            out.parts.push_back(std::move(part));
                        }
                        for (Curve c : prototype.curves) {  // guides repeat with what they guide
                            for (Vec3& q : c.points) q += p;
                            out.curves.push_back(std::move(c));
                        }
                    }
                } else {
                    const int seed = o.integer("randomSeed", 0, -1'000'000, 1'000'000);
                    const json::Value* pick_v = o.get("pick");
                    const json::Value* tint_v = o.get("tint");
                    detail::CompiledExpr pick_expr;
                    if (prototypes.size() > 1 && pick_v) pick_expr = detail::CompiledExpr(pick_v, o.controls, 0.0f);
                    std::optional<detail::CompiledTransform> xform_expr;
                    try {
                        xform_expr.emplace(o);
                    } catch (const std::runtime_error& e) {
                        fail(std::string("point 0: ") + e.what());
                    }
                    detail::CompiledVec3 tint_expr;
                    if (tint_v) tint_expr = detail::CompiledVec3(tint_v, o.controls, {1, 1, 1});
                    int index = 0;
                    for (const Vec3& p : positions->points) {
                        const detail::Point point{index, p, seed};
                        std::size_t chosen = 0;
                        if (prototypes.size() > 1) {
                            if (pick_v) {
                                const double raw = pick_expr.eval(&point);
                                if (!std::isfinite(raw)) fail("point " + std::to_string(index) + ": non-finite instance pick");
                                const auto m_cnt = static_cast<long long>(prototypes.size());
                                const auto idx = static_cast<long long>(std::floor(raw));
                                chosen = static_cast<std::size_t>(((idx % m_cnt) + m_cnt) % m_cnt);
                            } else {
                                std::uint32_t bits = static_cast<std::uint32_t>(index) + static_cast<std::uint32_t>(seed) * 0x9E3779B9u;
                                bits = (bits ^ (bits >> 16)) * 0x7FEB352Du;
                                bits = (bits ^ (bits >> 15)) * 0x846CA68Bu;
                                bits ^= bits >> 16;
                                const double u01 = static_cast<double>(bits >> 8) / 16777216.0;
                                chosen = std::min(prototypes.size() - 1, static_cast<std::size_t>(u01 * static_cast<double>(prototypes.size())));
                            }
                        }
                        const Geometry& prototype = *prototypes[chosen];
                        Mat4 placement;
                        try {
                            placement = xform_expr->eval(&point);
                        } catch (const std::runtime_error& e) {
                            fail("point " + std::to_string(index) + ": " + e.what());
                        }
                        placement.c[3][0] += p.x;
                        placement.c[3][1] += p.y;
                        placement.c[3][2] += p.z;
                        // Optional per-instance tint, e.g. [["+", 0.8, ["*", 0.4, "@random"]], 1, 1].
                        std::array<double, 3> tint{1, 1, 1};
                        if (tint_v) {
                            const Vec3 c = tint_expr.eval(&point);
                            tint = {c.x, c.y, c.z};
                        }
                        for (Part part : prototype.parts) {
                            part.transform = placement * part.transform;
                            check_transform(part.transform);
                            for (std::size_t k = 0; k < 3; ++k)
                                part.tint[k] = static_cast<std::uint8_t>(std::lround(std::clamp(part.tint[k] * tint[k], 0.0, 255.0)));
                            out.parts.push_back(std::move(part));
                        }
                        for (Curve c : prototype.curves) {
                            for (Vec3& q : c.points) q = placement.point(q);
                            out.curves.push_back(std::move(c));
                        }
                        ++index;
                    }
                }
            } else if (type == "smoothNormals" || type == "planarUV") {
                need(1, 1);
                out = *eval(s, inputs[0]);
                if (type == "smoothNormals") {
                    const float angle = o.scalar("angle", 180.0f, 0.0f, 180.0f);
                    const bool split_creases = o.integer("splitCreases", 1, 0, 1) != 0;
                    std::uint8_t weight_mode = 1;  // area (legacy default)
                    if (const json::Value* wv = o.get("weighting")) {
                        if (!wv->is_string()) fail("smoothNormals weighting must be uniform, area, angle, areaAngle or areaSq");
                        if (wv->string == "uniform") weight_mode = 0;
                        else if (wv->string == "area") weight_mode = 1;
                        else if (wv->string == "angle") weight_mode = 2;
                        else if (wv->string == "areaAngle") weight_mode = 3;
                        else if (wv->string == "areaSq") weight_mode = 4;
                        else fail("smoothNormals weighting must be uniform, area, angle, areaAngle or areaSq");
                    }
                    for (Part& p : out.parts) {
                        p.smooth = true;
                        p.smooth_angle_deg = angle;
                        p.normal_weight = weight_mode;
                        p.split_creases = split_creases;
                    }
                } else {
                    const json::Value* plane = o.get("plane");
                    const std::string name = plane && plane->is_string() ? plane->string : "xy";
                    if (name != "xy" && name != "xz" && name != "yz") fail("UV plane must be xy, xz or yz");
                    const float scale = o.scalar("metresPerTile", 1, 0.0001f, 10000);
                    for (Part& p : out.parts) {
                        p.uv_u = name == "yz" ? 1 : 0;
                        p.uv_v = name == "xy" ? 1 : 2;
                        p.metres_per_tile = scale;
                    }
                }
            } else if (type == "uvFit") {
                // Fit a chart to a band of a trim-sheet texture: the chart's v from span[0] to span[1] (metres) maps onto the
                // texture rows band[0]..band[1] (fractions of the image height from its top), u is scaled the same so the
                // texture keeps its proportions. The result is written in metres of a texture `tile` metres wide, as every
                // UV in a pack (the engine tiles materials per metre).
                need(1, 1);
                const auto pair = [&](const char* key) {
                    const auto& l = o.list(key, 2, 2);
                    return std::array<double, 2>{o.number(l[0]), o.number(l[1])};
                };
                const auto span = pair("span"), band = pair("band");
                const double tile = o.scalar("tile", 1, 0.001f, 1000);
                const double u0 = o.scalar("uOffset", 0, -10000, 10000);
                if (!(std::abs(span[1] - span[0]) > 1e-9)) fail("uvFit span must not be empty");
                if (!(band[0] >= -1 && band[0] <= 2 && band[1] >= -1 && band[1] <= 2)) fail("uvFit band is in image heights, 0 at the top");
                const double k = (band[1] - band[0]) / (span[1] - span[0]);
                out = *eval(s, inputs[0]);
                std::map<const Proto*, ProtoPtr> fitted;
                for (Part& p : out.parts) {
                    if (p.proto->uv.size() != p.proto->mesh.corner_count()) fail("uvFit needs a chart (extrude chart perimeter, revolve, tube, patch, sweep)");
                    ProtoPtr& f = fitted[p.proto.get()];
                    if (!f) {
                        Proto copy = *p.proto;
                        for (auto& c : copy.uv) {
                            const double tu = (c[0] + u0) * std::abs(k), tv = band[0] + (c[1] - span[0]) * k;
                            c = {static_cast<float>(tu * tile), static_cast<float>(tv * tile)};
                        }
                        f = std::make_shared<const Proto>(std::move(copy));
                    }
                    p.proto = f;
                    p.uv_u = p.uv_v = -1;                           // the chart, not a planar projection
                }
            } else if (type == "weld") {
                need(1, 1);
                const double tolerance = o.scalar("tolerance", 0.0001f, 0.000001f, 0.1f);
                out = *eval(s, inputs[0]);
                std::map<const Proto*, ProtoPtr> welded;
                for (Part& p : out.parts) {
                    ProtoPtr& w = welded[p.proto.get()];
                    if (!w) {
                        Proto copy = *p.proto;
                        copy.closest_index.reset();
                        copy.face_sources.clear();
                        if (copy.mesh.face_count() > 0) weld_vertices(copy.mesh, tolerance);
                        if (copy.uv.size() != copy.mesh.corner_count()) copy.uv.clear();  // the chart no longer fits
                        w = std::make_shared<const Proto>(std::move(copy));
                    }
                    p.proto = w;
                }
            } else if (type == "boxBoolean" || type == "boxBevel" || type == "meshBoolean") {
                const Value stock = eval(s, inputs.at(0));
                if (type == "boxBevel") {
                    need(1, 1);
                    out = detail::box_bevel(*stock, o.scalar("width", 0.01f, 0.000001f, 1000));
                } else {
                    need(2, 2);
                    const json::Value* op = o.get("operation");
                    const std::string operation = op && op->is_string() ? op->string : (op ? "?" : "difference");
                    if (operation != "difference" && operation != "union" && operation != "intersection") fail("Expected difference, union or intersection");
                    const Value tool = eval(s, inputs[1]);
                    if (type == "boxBoolean") {
                        out = detail::box_boolean(*stock, *tool, operation);
                    } else {
                        if (stock->parts.size() != 1 || tool->parts.size() != 1) fail("meshBoolean needs one closed mesh per input");
                        const BooleanOp bop = operation == "union" ? BooleanOp::Union : (operation == "intersection" ? BooleanOp::Intersection : BooleanOp::Difference);
                        Proto p;
                        p.mesh = mesh_boolean(flatten(*stock), flatten(*tool), bop);
                        Part part = stock->parts[0];
                        part.proto = std::make_shared<const Proto>(std::move(p));
                        part.transform = Mat4::identity();
                        out.parts.push_back(std::move(part));
                    }
                }
                for (Part& p : out.parts) p.source = source;
            } else if (type == "pointArray") {
                for (const json::Value& v : o.list("positions", 1, 24000)) out.points.push_back(o.vector(v));
            } else if (type == "empty") {
                // nothing
            } else if (type == "surfaceBand") {
                need(1, 1);
                out.parts.push_back(make_part(detail::make_surface_band(eval(s, inputs[0])->points, o), source));
            } else if (type == "profileSolid") {
                out.parts.push_back(make_part(detail::make_profile_solid(o), source));
            } else if (type == "extrude") {
                out.parts.push_back(make_part(detail::make_extrusion(o), source));
            } else if (type == "spline" || type == "resample") {
                std::vector<Curve> curves;
                if (type == "spline") {
                    curves.push_back(detail::make_spline(o));
                } else {
                    need(1, 1);
                    const Value in = eval(s, inputs[0]);
                    if (in->curves.empty()) fail("Input must supply at least one spline");
                    const int count = o.integer("count", 32, 2, 4096);
                    for (const Curve& c : in->curves) curves.push_back(detail::resample_curve(c, count));
                }
                for (const Curve& c : curves) {
                    out.parts.push_back(make_part(detail::curve_proto(c), source));
                    out.points.insert(out.points.end(), c.points.begin(), c.points.end());
                }
                out.curves = std::move(curves);
            } else if (type == "profileSweep") {
                need(1, 1);
                const Value in = eval(s, inputs[0]);
                if (in->curves.empty()) fail("Input must supply at least one spline");
                for (ProtoPtr& p : detail::make_profile_sweep(in->curves, o)) out.parts.push_back(make_part(std::move(p), source));
            } else if (type == "cube" || type == "grid" || type == "line" || type == "cylinder" || type == "surface" || type == "sweep") {
                ProtoPtr proto = detail::make_primitive(type, o);
                if (type == "line") {
                    out.points = proto->mesh.positions;
                    if (proto->mesh.positions.size() > 1) out.curves.push_back(Curve{proto->mesh.positions, false});
                }
                out.parts.push_back(make_part(std::move(proto), source));
            } else {
                std::vector<Value> in;
                for (const std::string& name : inputs) in.push_back(eval(s, name));
                if (!detail::evaluate_extension(type, o, in, source, out)) fail("Unknown node type " + type);
            }
        } catch (const std::runtime_error& e) {
            const std::string what = e.what();
            // Prefix once with the innermost failing node, like the reference.
            fail(what.rfind(id + ": ", 0) == 0 ? what : id + ": " + what);
        }
        s.active.erase(id);
        Value result = std::make_shared<const Geometry>(std::move(out));
        if (ext_cache) {
            if (group_all_settled || select_settled) {
                ++reused;
                ++ext_cache->hit_count;
            } else {
                ++evaluated;
                ++ext_cache->miss_count;
                ext_cache->put(node_digest, result);
            }
            s.digests.emplace(id, node_digest);
        } else {
            ++evaluated;
        }
        s.cache.emplace(id, result);
        return result;
    }
};

}  // namespace

Result evaluate(const json::Value& document, Library& library, const Controls& overrides, EvalCache* cache) {
    const json::Value* version = document.find("version");
    if (!version || !version->is_number() || version->number != 1) fail("Expected version 1");
    const json::Value* output = document.find("output");
    if (!output || !output->is_string()) fail("A graph needs an output node");
    Evaluator ev{library, cache ? cache->impl() : nullptr};
    Scope root;
    root.document = &document;
    root.nodes = document.find("nodes");
    root.controls = control_values(document, overrides);
    ev.index(root);
    Result r;
    r.geometry = *ev.eval(root, output->string);
    r.controls = root.controls;
    r.evaluated_nodes = ev.evaluated;
    r.reused_nodes = ev.reused;
    return r;
}

Result evaluate_file(const std::string& path, const Controls& overrides, EvalCache* cache) {
    const std::size_t slash = path.find_last_of('/');
    Library library(slash == std::string::npos ? "." : path.substr(0, slash));
    return evaluate(json::parse_file(path), library, overrides, cache);
}

Result evaluate_unbaked(const json::Value& document, Library& library, const Controls& overrides, EvalCache* cache) {
    const json::Value* version = document.find("version");
    if (!version || !version->is_number() || version->number != 1) fail("Expected version 1");
    const json::Value* output = document.find("output");
    if (!output || !output->is_string()) fail("A graph needs an output node");
    Evaluator ev{library, cache ? cache->impl() : nullptr};
    Scope root;
    root.document = &document;
    root.nodes = document.find("nodes");
    root.controls = control_values(document, overrides);
    ev.index(root);
    std::string target_id = output->string;
    if (const auto it = root.by_id.find(target_id); it != root.by_id.end()) {
        const json::Value* t = it->second->find("type");
        const json::Value* ins = it->second->find("inputs");
        if (t && t->is_string() && t->string == "bake" && ins && ins->is_array() && ins->array.size() == 1 && ins->array[0].is_string()) {
            target_id = ins->array[0].string;
        }
    }
    Result r;
    r.geometry = *ev.eval(root, target_id);
    if (target_id != output->string) {
        std::erase_if(r.geometry.parts, [](const Part& p) { return !p.proto || p.proto->mesh.face_count() == 0; });
    }
    r.controls = root.controls;
    r.evaluated_nodes = ev.evaluated;
    r.reused_nodes = ev.reused;
    return r;
}

Result evaluate_file_unbaked(const std::string& path, const Controls& overrides, EvalCache* cache) {
    const std::size_t slash = path.find_last_of('/');
    Library library(slash == std::string::npos ? "." : path.substr(0, slash));
    return evaluate_unbaked(json::parse_file(path), library, overrides, cache);
}

std::vector<PortFrame> module_ports(const json::Value& document, const Controls& controls) {
    std::vector<PortFrame> out;
    const json::Value* ports = document.find("modulePorts");
    if (!ports) return out;
    const Options o{nullptr, &controls};
    for (const json::Member& m : ports->object) {
        PortFrame f;
        f.name = m.key;
        if (const json::Value* t = m.value.find("type"); t && t->is_string()) f.type = t->string;
        const json::Value* origin = m.value.find("origin");
        const json::Value* normal = m.value.find("normal");
        const json::Value* up = m.value.find("up");
        if (!origin || !normal || !up) fail("Port " + m.key + " needs origin, normal and up");
        f.origin = o.vector(*origin);
        f.normal = o.vector(*normal);
        f.up = o.vector(*up);
        if (const json::Value* dims = m.value.find("dimensions"))
            for (const json::Member& d : dims->object) f.dimensions[d.key] = o.number(d.value);
        out.push_back(std::move(f));
    }
    return out;
}

Receipt make_receipt(const Geometry& g) {
    Receipt r;
    std::set<const Proto*> seen;
    Aabb box;
    for (const Part& part : g.parts) {
        const Proto& proto = *part.proto;
        if (seen.insert(&proto).second) {
            r.prototype_vertices += proto.mesh.vertex_count();
            r.details += proto.details.size();
        }
        r.placed_details += proto.details.size();
        r.placed_triangles += proto.triangle_count();
        r.placed_loose_edges += proto.edges.size();
        const Aabb local = bounds(proto.mesh);
        if (!local.valid()) continue;
        // Conservative: transform the local box's centre and extents.
        const Vec3 center = part.transform.point(local.center()), e = local.extent() * 0.5;
        Vec3 ext{};
        for (int k = 0; k < 3; ++k) {
            const Vec3 col = part.transform.column(k);
            ext += Vec3{std::abs(col.x), std::abs(col.y), std::abs(col.z)} * e[k];
        }
        box.expand(center - ext);
        box.expand(center + ext);
    }
    r.instances = g.parts.size();
    r.prototypes = seen.size();
    r.has_bounds = box.valid();
    if (r.has_bounds) { r.lo = box.lo; r.hi = box.hi; }
    return r;
}

Mesh flatten(const Geometry& g, std::string_view material) {
    Mesh out;
    const bool filtered = !material.empty();
    const std::uint16_t wanted = filtered ? slot_id(material) : 0;
    std::vector<Index> poly;
    for (const Part& part : g.parts) {
        if (filtered && part.material != wanted) continue;
        const Mesh& m = part.proto->mesh;
        if (m.face_count() == 0) continue;
        const auto base = static_cast<Index>(out.vertex_count());
        for (const Vec3& p : m.positions) out.positions.push_back(part.transform.point(p));
        for (const EdgeCrease& ec : m.edge_creases) {
            const auto lo = static_cast<Index>(ec.key >> 32) + base;
            const auto hi = static_cast<Index>(ec.key & 0xffffffffu) + base;
            out.edge_creases.push_back({edge_key(lo, hi), ec.sharpness});
        }
        for (const VertexCrease& vc : m.vertex_creases) {
            out.vertex_creases.push_back({vc.vertex + base, vc.sharpness});
        }
        const bool mirrored = part.transform.det3() < 0;
        for (std::size_t f = 0; f < m.face_count(); ++f) {
            poly.assign(m.face(f).begin(), m.face(f).end());
            for (Index& v : poly) v += base;
            if (mirrored) std::reverse(poly.begin(), poly.end());
            out.add_face(poly);
        }
    }
    if (!out.edge_creases.empty() || !out.vertex_creases.empty()) out.normalize_creases();
    return out;
}

}  // namespace pg::doc
