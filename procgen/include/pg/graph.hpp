#pragma once

#include "pg/mesh.hpp"
#include "pg/ops/bevel.hpp"
#include "pg/ops/boolean.hpp"
#include "pg/ops/deform.hpp"
#include "pg/ops/extrude.hpp"
#include "pg/ops/scatter.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <unordered_map>
#include <variant>
#include <vector>

namespace pg {

// --- Node parameter types (plain data; hashed for caching) -----------------

struct BoxOp { Vec3 size{1.0, 1.0, 1.0}; std::uint32_t segments = 1; };
struct GridOp { std::uint32_t nx = 1, ny = 1; double size_x = 1.0, size_y = 1.0; };
struct CylinderOp { std::uint32_t segments = 16; double radius = 0.5, height = 1.0; };

struct SelectAll {};
struct SelectByNormal { Vec3 direction{0.0, 0.0, 1.0}; double min_cos = 0.999; };
using FaceSelector = std::variant<SelectAll, SelectByNormal>;

struct ExtrudeOp { FaceSelector selector{}; ExtrudeParams params{}; };
struct TranslateOp { Vec3 offset{}; };
struct MergeOp {};
struct WeldOp { double tolerance = 1e-9; };
struct SubdivideOp { std::uint32_t levels = 1; };
enum class DeformKind : std::uint8_t { Twist, Bend, Taper };
struct DeformOp { DeformKind kind = DeformKind::Twist; double amount = 0.0; };
struct NoiseOp { NoiseParams params{}; };
struct BevelOp { BevelParams params{}; };
struct CsgOp { BooleanOp op = BooleanOp::Union; BooleanParams params{}; };  // inputs: (a, b)
struct ScatterOp { ScatterParams points{}; InstanceParams instance{}; };    // inputs: (surface, instance)

using OpParams = std::variant<BoxOp, GridOp, CylinderOp, ExtrudeOp, TranslateOp, MergeOp, WeldOp, SubdivideOp,
                              DeformOp, NoiseOp, BevelOp, CsgOp, ScatterOp>;

[[nodiscard]] std::size_t op_arity(const OpParams& op) noexcept;
[[nodiscard]] const char* op_name(const OpParams& op) noexcept;

using NodeId = std::uint32_t;
using MeshPtr = std::shared_ptr<const Mesh>;

// Procedural DAG. A node may only consume nodes created before it, so the
// insertion order is a topological order and cycles are impossible by
// construction. Results are cached by a content hash of (op, params, input
// hashes): editing one node re-evaluates only its downstream cone, and two
// structurally identical subgraphs share one result.
class Graph {
public:
    NodeId add(OpParams op, std::vector<NodeId> inputs = {});
    void set_params(NodeId id, OpParams op);
    [[nodiscard]] const OpParams& params(NodeId id) const { return nodes_.at(id).op; }
    [[nodiscard]] std::size_t size() const noexcept { return nodes_.size(); }

    // Evaluates only the nodes `id` depends on.
    MeshPtr evaluate(NodeId id);

    struct Stats { std::size_t evaluated = 0, cache_hits = 0; };
    [[nodiscard]] const Stats& stats() const noexcept { return stats_; }
    void reset_stats() noexcept { stats_ = {}; }
    void clear_cache() noexcept { cache_.clear(); }
    [[nodiscard]] std::size_t cache_size() const noexcept { return cache_.size(); }

private:
    struct Node {
        OpParams op;
        std::vector<NodeId> inputs;
    };
    std::vector<Node> nodes_;
    std::unordered_map<std::uint64_t, MeshPtr> cache_;
    Stats stats_;
};

}  // namespace pg
