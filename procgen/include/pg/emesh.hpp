#pragma once
// Engine mesh packs ("EMSH", .emesh): the runtime format defined by the engine
// in engine/asset/include/eng/asset/mesh_format.hpp. This writer turns an
// evaluated geometry document into one pack: a prototype library (quantised
// vertex buffers, one material part per mesh) plus instance batches.
//
// What the hand-off asks of the modeler (engine/docs/DATA_HANDOFF.md):
//   * prototypes, not finished scenes: repetition stays explicit;
//   * a stable 64-bit content key per prototype (equal keys = same mesh);
//   * instances as 3x4 transforms; Z-up documents converted to the engine's
//     Y-up: (x, y, z) -> (x, z, -y).
// Congruence detection goes one step further than equal bytes: prototypes that
// are the same shape under a rigid motion (the globe's cells, repeated bays
// baked in place) collapse into one mesh, the motion moving into the instance.

#include "pg/doc.hpp"

#include <array>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace pg::emesh {

struct WriteOptions {
    bool dedupe_congruent = true;        // false: only prototypes shared in the graph are instanced
    double congruence_tolerance = 1e-4;  // metres; canonical coordinates are compared on this grid
    bool y_up = true;                    // engine space; false keeps the document's Z-up
    std::string source;                  // recorded in the metadata section
    // Engine world-structure metadata (tool sections 0x8002..0x8005, ENGINE_PACK_NOTES.md):
    std::vector<doc::PortFrame> ports;   // non-empty -> emits 0x8002 Ports (and 0x8003 DeckDatums for deck ports)
    std::vector<float> deck_datums;      // optional explicit walkable deck elevations (engine up axis, metres)
    bool emit_collision_and_walk = false; // true -> derives 0x8004 CollisionBoxes & 0x8005 WalkTriangles
};

struct WriteStats {
    std::size_t placements = 0;           // drawable parts in the document
    std::size_t source_prototypes = 0;    // distinct (prototype, material) pairs before dedupe
    std::size_t meshes = 0;               // meshes written
    std::size_t vertices = 0, indices = 0, triangles = 0, instances = 0, batches = 0;
    std::size_t mirrored_instances = 0;   // negative-determinant placements (winding flips at draw time)
    std::size_t details = 0;              // surface details written (tool section 0x8007)
    std::size_t two_sided_meshes = 0;     // meshes that are not closed solids (flagged for the engine's culling)
    std::size_t lod_submeshes = 0;        // total submeshes across all LOD levels
    std::size_t ports = 0;                // section 0x8002
    std::size_t deck_datums = 0;          // section 0x8003
    std::size_t collision_boxes = 0;      // section 0x8004
    std::size_t walk_triangles = 0;       // section 0x8005 (triangle count; 3 WalkVertex per triangle)
    std::size_t bytes = 0;
    bool index32 = false;
};

// Serialises `g`. Edge-only prototypes (lines, splines) are not drawable and
// are skipped. Throws std::runtime_error when the pack would exceed 4 GiB.
[[nodiscard]] std::vector<std::uint8_t> write(const doc::Geometry& g, const WriteOptions& options = {}, WriteStats* stats = nullptr);

// A decoded pack: what the engine's loader sees, with the quantisation undone
// on the CPU. Used by the previewer and by round-trip tests.
// Hash a material pack matches slot names by: FNV-1a 64 over the bytes with ASCII letters lower-cased
// (the engine's eng::asset::nameHash).
[[nodiscard]] std::uint64_t slot_name_hash(std::string_view name) noexcept;

struct Pack {
    struct LodRange {
        std::uint32_t first_index = 0, index_count = 0, base_vertex = 0;
    };
    struct Mesh {
        std::uint64_t content_key = 0;
        Vec3 lo{}, hi{};
        std::uint32_t material = 0;  // slot id from the metadata section
        std::uint32_t first_index = 0, index_count = 0, base_vertex = 0;
        std::array<float, 2> uv_min{}, uv_scale{};  // UV range in metres (the engine's materials tile per metre)
        bool two_sided = false;      // EMSH 1.3: has open sheets, drawn without back-face culling
        std::uint8_t lod_count = 1;
        std::array<float, 4> lod_distances{0, 0, 0, 0};
        std::vector<LodRange> lods;  // LOD 0 .. lod_count-1
    };
    struct Batch {
        std::uint32_t mesh = 0, first_instance = 0, instance_count = 0;
    };
    std::vector<Mesh> meshes;
    std::vector<Vec3> positions;  // mesh-local, dequantised
    std::vector<Vec3> normals;    // unit
    std::vector<std::uint32_t> indices;
    std::vector<Batch> batches;
    std::vector<std::array<float, 12>> transforms;  // 3 rows of 4, local -> world
    std::vector<std::array<std::uint8_t, 4>> tints; // per instance (section 15, EMSH 1.3); empty = all white
    // Surface details (tool section 0x8007), sorted by mesh: each lies on its mesh in mesh-local
    // space (the space of `positions`), sizes in mesh units, `material` a slot id or 0xFFFF.
    struct MeshDetail {
        std::uint32_t mesh = 0;
        doc::Detail detail;
    };
    std::vector<MeshDetail> details;
    struct MaterialSlot {
        std::uint64_t name_hash = 0;  // slot_name_hash() of the slot name
        std::uint32_t slot = 0;       // the id submeshes carry
    };
    std::vector<MaterialSlot> material_slots;       // section 11 (EMSH 1.2): how a material pack finds these slots
    std::vector<std::string> slot_names;            // by material id (from metadata; may be empty)
    std::vector<Vec3> slot_colors;
    struct PortRecord {
        Vec3 origin{}, normal{}, up{};
        std::uint32_t kind = 5;  // 0 deck, 1 bearing, 2 track, 3 wall, 4 water, 5 service
        std::uint32_t owner = 0xFFFFFFFFu;
        std::array<float, 2> params{0.0f, 0.0f};
    };
    struct CollisionBox {
        Vec3 center{}, half_extent{};
    };
    std::vector<PortRecord> ports;                   // section 0x8002
    std::vector<float> deck_datums;                  // section 0x8003 (metres, ascending)
    std::vector<CollisionBox> collision_boxes;       // section 0x8004
    std::vector<std::array<Vec3, 3>> walk_triangles; // section 0x8005
    bool y_up = true;
    Vec3 lo{}, hi{};  // header world bounds
    std::string metadata;  // raw JSON of the tool section, if present
};

// Validates the header, section table and hash; throws std::runtime_error
// naming what is wrong.
[[nodiscard]] Pack read(std::span<const std::uint8_t> bytes);

[[nodiscard]] std::vector<std::uint8_t> read_file(const std::string& path);
void write_file(const std::string& path, std::span<const std::uint8_t> bytes);

}  // namespace pg::emesh
