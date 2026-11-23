#include "pg/graph.hpp"

#include "pg/cleanup.hpp"
#include "pg/ops/subdivide.hpp"
#include "pg/primitives.hpp"

#include <bit>
#include <stdexcept>
#include <string>

namespace pg {
namespace {

template <class... Ts>
struct overloaded : Ts... { using Ts::operator()...; };

// 64-bit content hash (splitmix64 finaliser per word). Collisions would alias
// cache entries; at 64 bits that is negligible for interactive graph sizes.
struct Hasher {
    std::uint64_t h = 0x243f6a8885a308d3ull;
    void u64(std::uint64_t x) noexcept {
        x += 0x9e3779b97f4a7c15ull + h;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
        h = x ^ (x >> 31);
    }
    void f64(double d) noexcept { u64(std::bit_cast<std::uint64_t>(d == 0.0 ? 0.0 : d)); }  // -0 == +0
    void vec(Vec3 v) noexcept { f64(v.x); f64(v.y); f64(v.z); }
};

void hash_params(Hasher& h, const OpParams& op) {
    h.u64(op.index());
    std::visit(overloaded{
                   [&](const BoxOp& o) { h.vec(o.size); h.u64(o.segments); },
                   [&](const GridOp& o) { h.u64(o.nx); h.u64(o.ny); h.f64(o.size_x); h.f64(o.size_y); },
                   [&](const CylinderOp& o) { h.u64(o.segments); h.f64(o.radius); h.f64(o.height); },
                   [&](const ExtrudeOp& o) {
                       h.u64(o.selector.index());
                       if (const auto* s = std::get_if<SelectByNormal>(&o.selector)) { h.vec(s->direction); h.f64(s->min_cos); }
                       h.f64(o.params.distance);
                       h.u64(static_cast<std::uint64_t>(o.params.mode));
                       h.u64(o.params.even_thickness ? 1 : 0);
                       h.f64(o.params.max_thickness_scale);
                   },
                   [&](const TranslateOp& o) { h.vec(o.offset); },
                   [&](const MergeOp&) {},
                   [&](const WeldOp& o) { h.f64(o.tolerance); },
                   [&](const SubdivideOp& o) { h.u64(o.levels); },
                   [&](const DeformOp& o) { h.u64(static_cast<std::uint64_t>(o.kind)); h.f64(o.amount); },
                   [&](const NoiseOp& o) {
                       h.f64(o.params.amplitude); h.f64(o.params.frequency); h.u64(o.params.seed); h.u64(o.params.octaves);
                   },
                   [&](const BevelOp& o) { h.f64(o.params.width); h.f64(o.params.min_angle_deg); },
                   [&](const CsgOp& o) {
                       h.u64(static_cast<std::uint64_t>(o.op)); h.f64(o.params.plane_epsilon_rel); h.f64(o.params.weld_epsilon_rel); h.u64(o.params.merge_uncut_faces); h.u64(o.params.merge_coplanar_fragments);
                   },
                   [&](const ScatterOp& o) {
                       h.u64(o.points.count); h.f64(o.points.min_distance); h.u64(o.points.max_attempts); h.u64(o.points.seed);
                       h.f64(o.instance.scale); h.u64(o.instance.align_to_normal); h.u64(o.instance.random_spin); h.u64(o.instance.seed);
                   },
               },
               op);
}

std::vector<std::uint8_t> make_mask(const Mesh& m, const FaceSelector& sel) {
    return std::visit(overloaded{
                          [&](const SelectAll&) { return select_all(m); },
                          [&](const SelectByNormal& s) { return select_by_normal(m, s.direction, s.min_cos); },
                      },
                      sel);
}

Mesh run(const OpParams& op, std::span<const MeshPtr> in) {
    return std::visit(overloaded{
                          [&](const BoxOp& o) { return make_box(o.size, o.segments); },
                          [&](const GridOp& o) { return make_grid(o.nx, o.ny, o.size_x, o.size_y); },
                          [&](const CylinderOp& o) { return make_cylinder(o.segments, o.radius, o.height); },
                          [&](const ExtrudeOp& o) {
                              const auto mask = make_mask(*in[0], o.selector);
                              return extrude(*in[0], mask, o.params);
                          },
                          [&](const TranslateOp& o) {
                              Mesh m = *in[0];
                              translate(m, o.offset);
                              return m;
                          },
                          [&](const MergeOp&) { return merge(*in[0], *in[1]); },
                          [&](const WeldOp& o) {
                              Mesh m = *in[0];
                              weld_vertices(m, o.tolerance);
                              return m;
                          },
                          [&](const SubdivideOp& o) { return catmull_clark(*in[0], o.levels); },
                          [&](const DeformOp& o) {
                              Mesh m = *in[0];
                              switch (o.kind) {
                                  case DeformKind::Twist: twist(m.positions, o.amount); break;
                                  case DeformKind::Bend: bend(m.positions, o.amount); break;
                                  case DeformKind::Taper: taper(m.positions, o.amount); break;
                              }
                              return m;
                          },
                          [&](const NoiseOp& o) {
                              Mesh m = *in[0];
                              displace_noise(m, o.params);
                              return m;
                          },
                          [&](const BevelOp& o) { return bevel(*in[0], o.params); },
                          [&](const CsgOp& o) { return mesh_boolean(*in[0], *in[1], o.op, o.params); },
                          [&](const ScatterOp& o) {
                              return instance_on_points(*in[1], scatter_points(*in[0], o.points), o.instance);
                          },
                      },
                      op);
}

}  // namespace

std::size_t op_arity(const OpParams& op) noexcept {
    return std::visit(overloaded{
                          [](const BoxOp&) -> std::size_t { return 0; },
                          [](const GridOp&) -> std::size_t { return 0; },
                          [](const CylinderOp&) -> std::size_t { return 0; },
                          [](const ExtrudeOp&) -> std::size_t { return 1; },
                          [](const TranslateOp&) -> std::size_t { return 1; },
                          [](const MergeOp&) -> std::size_t { return 2; },
                          [](const WeldOp&) -> std::size_t { return 1; },
                          [](const SubdivideOp&) -> std::size_t { return 1; },
                          [](const DeformOp&) -> std::size_t { return 1; },
                          [](const NoiseOp&) -> std::size_t { return 1; },
                          [](const BevelOp&) -> std::size_t { return 1; },
                          [](const CsgOp&) -> std::size_t { return 2; },
                          [](const ScatterOp&) -> std::size_t { return 2; },
                      },
                      op);
}

const char* op_name(const OpParams& op) noexcept {
    constexpr const char* names[] = {"box",       "grid",   "cylinder", "extrude", "translate", "merge", "weld",
                                    "subdivide", "deform", "noise",    "bevel",   "boolean",   "scatter"};
    static_assert(std::size(names) == std::variant_size_v<OpParams>);
    return names[op.index()];
}

NodeId Graph::add(OpParams op, std::vector<NodeId> inputs) {
    if (inputs.size() != op_arity(op))
        throw std::invalid_argument(std::string("Graph::add: wrong input count for ") + op_name(op));
    const auto id = static_cast<NodeId>(nodes_.size());
    for (NodeId in : inputs)
        if (in >= id) throw std::invalid_argument("Graph::add: inputs must reference existing nodes");
    nodes_.push_back({std::move(op), std::move(inputs)});
    return id;
}

void Graph::set_params(NodeId id, OpParams op) {
    Node& n = nodes_.at(id);
    if (op_arity(op) != n.inputs.size()) throw std::invalid_argument("Graph::set_params: arity change");
    n.op = std::move(op);
}

MeshPtr Graph::evaluate(NodeId id) {
    if (id >= nodes_.size()) throw std::out_of_range("Graph::evaluate: no such node");
    const std::size_t n = std::size_t(id) + 1;
    std::vector<std::uint8_t> needed(n, 0);
    needed[id] = 1;
    for (std::size_t i = n; i-- > 0;)
        if (needed[i])
            for (NodeId in : nodes_[i].inputs) needed[in] = 1;

    std::vector<std::uint64_t> hash(n, 0);
    std::vector<MeshPtr> result(n);
    std::vector<MeshPtr> args;
    for (std::size_t i = 0; i < n; ++i) {
        if (!needed[i]) continue;
        const Node& node = nodes_[i];
        Hasher h;
        hash_params(h, node.op);
        for (NodeId in : node.inputs) h.u64(hash[in]);
        hash[i] = h.h;
        if (auto it = cache_.find(h.h); it != cache_.end()) {
            result[i] = it->second;
            ++stats_.cache_hits;
            continue;
        }
        args.clear();
        for (NodeId in : node.inputs) args.push_back(result[in]);
        result[i] = std::make_shared<const Mesh>(run(node.op, args));
        cache_.emplace(h.h, result[i]);
        ++stats_.evaluated;
    }
    return result[id];
}

}  // namespace pg
