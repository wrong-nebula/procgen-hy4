#pragma once

#include "pg/closest.hpp"
#include "pg/doc.hpp"

#include <stdexcept>
#include <string>
#include <vector>

namespace pg::doc::detail {

[[noreturn]] inline void fail(const std::string& message) { throw std::runtime_error(message); }

// One incoming point of an instance node; exposes @index, @x, @y, @z, @random.
// Parametric nodes (curve, patch) also expose @t / @u and @v in [0, 1].
struct Point {
    int index = 0;
    Vec3 position{};
    int seed = 0;
    float u = 0, v = 0;
    bool parametric = false;
};

// A node's options with the control scope they are evaluated in. All numeric
// work is done in float, mirroring the reference evaluator.
struct Options {
    const json::Value* fields = nullptr;  // object or nullptr
    const Controls* controls = nullptr;
    const Point* point = nullptr;

    [[nodiscard]] const json::Value* get(std::string_view key) const { return fields ? fields->find(key) : nullptr; }
    [[nodiscard]] bool empty() const { return !fields || fields->object.empty(); }
    [[nodiscard]] float number(const json::Value& v) const;
    [[nodiscard]] float scalar(std::string_view key, float fallback, float lo, float hi) const;
    [[nodiscard]] int integer(std::string_view key, int fallback, int lo, int hi) const;
    [[nodiscard]] Vec3 vector(const json::Value& v) const;
    [[nodiscard]] Vec3 vector(std::string_view key, Vec3 fallback) const;
    [[nodiscard]] const std::vector<json::Value>& list(std::string_view key, std::size_t lo, std::size_t hi) const;
    [[nodiscard]] Mat4 transform() const;  // translation, rotation (Euler XYZ), scale
};

// Rotation Rz * Ry * Rx with per-axis scale and translation.
[[nodiscard]] Mat4 compose(Vec3 translation, Vec3 rotation, Vec3 scale);

// --- mesh builders (doc_nodes.cpp) -----------------------------------------
[[nodiscard]] ProtoPtr make_primitive(std::string_view type, const Options& o);  // cube grid line cylinder surface sweep
[[nodiscard]] ProtoPtr make_extrusion(const Options& o);
[[nodiscard]] ProtoPtr make_section_loft(const Options& o);
[[nodiscard]] ProtoPtr make_profile_solid(const Options& o);
[[nodiscard]] ProtoPtr make_surface_band(const std::vector<Vec3>& alignment, const Options& o);
[[nodiscard]] Curve make_spline(const Options& o);
[[nodiscard]] Curve resample_curve(const Curve& c, int count);
[[nodiscard]] ProtoPtr curve_proto(const Curve& c);
[[nodiscard]] std::vector<ProtoPtr> make_profile_sweep(const std::vector<Curve>& curves, const Options& o);
// Round tubes along curves (open curves are capped, closed curves form rings; radius and twist may use @t / @index).
[[nodiscard]] std::vector<ProtoPtr> make_tube_sweep(const std::vector<Curve>& curves, double radius, int sides, Vec3 up);
[[nodiscard]] std::vector<ProtoPtr> make_tube_sweep_options(const std::vector<Curve>& curves, const Options& o);
[[nodiscard]] Geometry box_boolean(const Geometry& stock, const Geometry& tool, std::string_view operation);
[[nodiscard]] Geometry box_bevel(const Geometry& stock, double width);

// Fill p.uv from a UV per vertex (metres). A face that crosses a seam of a closed direction is
// unwrapped by `wrap_u` / `wrap_v` (the length once round; 0 = open). `free_u` marks vertices with
// no meaningful u (the pole of a lathe): they take the mean u of their face.
void assign_chart(Proto& p, const std::vector<std::array<double, 2>>& vertex_uv, double wrap_u = 0, double wrap_v = 0,
                  const std::vector<char>* free_u = nullptr);

// --- detail generators (doc_detail.cpp): detailStamp, rivetLine, detailSeam ----
// Returns true and fills `out` when `type` is one of them.
bool evaluate_detail(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs, Geometry& out);
// A detail carried by a placement transform (used when prototypes are merged).
[[nodiscard]] Detail transformed_detail(const Detail& d, const Mat4& m);

// --- extension nodes backed by the procgen operations (doc_ext.cpp, doc_mesh_curve.cpp) ---
// Returns true and fills `out` when `type` is one of the extension nodes.
bool evaluate_mesh_curve(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs,
                         const std::shared_ptr<const std::string>& source, Geometry& out);
// Apply one Pixar hierarchical multiresolution edit object at subdivision level `level` (implemented in doc_mesh_curve.cpp).
void apply_hierarchical_subdiv_edit(const json::Value& edit_v, const Options& parent_o, Mesh& m);
bool evaluate_extension(std::string_view type, const Options& o, const std::vector<std::shared_ptr<const Geometry>>& inputs,
                        const std::shared_ptr<const std::string>& source, Geometry& out);

}  // namespace pg::doc::detail
