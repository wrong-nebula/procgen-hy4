#pragma once
// Geometry-graph documents: the JSON authoring format of the Glasshouse
// "Geometry Nodes" kits (Recipes/Geometry/*.json, see GEOMETRY_NODES.md in the
// main project). A document is a DAG of nodes with numeric controls, groups,
// imported modules and typed connection ports. Evaluating it yields shared
// prototype meshes placed by transforms, with a material slot per placement:
// exactly the prototype/instance shape the engine consumes.
//
// Conventions: metres, Z-up, Euler XYZ radians. Numeric expressions are
// evaluated in 32-bit floats like the reference Swift evaluator, so integer
// tests, index arithmetic and control ranges make the same decisions.

#include "pg/json.hpp"
#include "pg/mesh.hpp"

#include <array>
#include <cstdint>
#include <map>
#include <memory>
#include <span>
#include <string>
#include <unordered_map>
#include <string_view>
#include <vector>

namespace pg {
class ClosestIndex;
}

namespace pg::doc {

// Affine transform, column-major: c[column][row].
struct Mat4 {
    double c[4][4];
    [[nodiscard]] static Mat4 identity() noexcept;
    [[nodiscard]] Mat4 operator*(const Mat4& o) const noexcept;
    [[nodiscard]] Vec3 point(Vec3 p) const noexcept;
    [[nodiscard]] Vec3 direction(Vec3 d) const noexcept;
    [[nodiscard]] Vec3 column(int i) const noexcept { return {c[i][0], c[i][1], c[i][2]}; }
    [[nodiscard]] double det3() const noexcept;
};

// A small surface detail: a signed-distance shape lying on the prototype's surface with a height
// profile and, optionally, its own material. The mesh stays clean; a renderer turns details into
// shading (rounded edges, raised rivets, sunk seams). See docs/DETAILING.md.
struct Detail {
    enum class Shape : std::uint8_t { Disc, Ring, Box, Segment, Polygon, Cross };
    // Dome: half-round (a rivet head, a bead along a stroke). Flat: plateau with a rounded edge.
    // Groove: rounded V. Cone: straight slope to the middle (a stud, a countersink). Ramp: rises
    // across the footprint from one side to the other (a louvre blade, a lapped plate edge).
    enum class Profile : std::uint8_t { Dome, Flat, Groove, Cone, Ramp };
    Shape shape = Shape::Disc;
    Profile profile = Profile::Dome;
    std::uint16_t material = 0xFFFF;                   // slot shown on the footprint; 0xFFFF = the surface's own
    Vec3 origin{}, normal{0, 0, 1}, tangent{1, 0, 0};  // prototype space; tangent is the footprint's x axis
    Vec3 end{};                                        // Segment: the far end (origin is the near one)
    // Disc: radius. Ring: outer, inner. Box: half x, half y, corner radius. Segment: half width.
    // Polygon: inradius (half the distance across flats), number of sides. Cross: arm half length, arm half width.
    double size_a = 0.01, size_b = 0.0, size_c = 0.0;
    double height = 0.004;                             // raised (+) or sunk (-), prototype units; 0 = a mark (material only)
    double fillet = 0.002;                             // width of the rounded edge of a Flat profile
    bool fastener = false;                             // part of a fastener head: keeps clear of seams and of other heads
    double extent = 0;                                 // fastener: radius of the whole head, washer included
};

// A shared prototype: polygon mesh (triangles and quads) plus loose edges
// (lines and splines are edge-only prototypes).
struct Proto {
    struct LodLevel {
        Mesh mesh;
        std::vector<std::array<float, 2>> uv;
        std::vector<std::array<float, 3>> normals;
        float distance = 0.0f;  // max distance (metres) for THIS LOD; last LOD is 0 (unlimited)
    };
    Mesh mesh;
    std::vector<std::array<Index, 2>> edges;
    // Chart from construction: one UV per face corner, in metres (arc length along a sweep, perimeter
    // around it, meridian of a lathe...). Empty = no chart; the pack writer then box-projects.
    std::vector<std::array<float, 2>> uv;
    // Optional explicit per-corner shading normals (prototype space, unit vectors), e.g. exact
    // analytical Catmull-Clark limit normals. Empty = derive normals from Part shading settings.
    std::vector<std::array<float, 3>> normals;
    float lod0_distance = 0.0f;             // max distance (metres) for LOD 0 when lods is non-empty
    std::vector<LodLevel> lods;             // optional coarser LOD meshes (LOD 1..3)
    std::shared_ptr<const Mesh> sds_cage;   // optional retained pre-SDS control cage (for --wire-cage)
    std::vector<Detail> details;  // surface details, in prototype space; later ones lie on top
    // Optional per-face authoring provenance preserved by `bake` so `audit` and defect localizers
    // report the real authoring node ID ("eaves-bracket-3") instead of "cabin.baked".
    std::vector<std::shared_ptr<const std::string>> face_sources;
    // Lazily cached surface BVH reused across chains of detailStamp/fastener/stroke/text nodes sharing this mesh.
    mutable std::shared_ptr<const ClosestIndex> closest_index;
    [[nodiscard]] std::size_t triangle_count() const noexcept;
};
using ProtoPtr = std::shared_ptr<const Proto>;

// Material slots are interned process-wide: placements carry a small id.
[[nodiscard]] std::uint16_t slot_id(std::string_view name);
[[nodiscard]] const std::string& slot_name(std::uint16_t id);
[[nodiscard]] std::uint16_t slot_count();  // slots known so far (the table grows when a document names a new one)
[[nodiscard]] Vec3 slot_color(std::uint16_t id);  // review colour, linear RGB

struct Part {
    ProtoPtr proto;
    std::shared_ptr<const std::string> source;  // id of the node that built the prototype
    Mat4 transform = Mat4::identity();
    std::uint16_t material = 0;  // slot_id("stone")
    bool smooth = false;
    float smooth_angle_deg = 180.0f;  // Phong break angle (degrees); 180 = smooth across all edges
    std::uint8_t normal_weight = 1;   // 0 = uniform, 1 = area, 2 = angle, 3 = areaAngle, 4 = areaSq
    bool split_creases = true;        // hard-split normals across infinitely sharp mesh creases
    std::int8_t uv_u = -1, uv_v = -1;  // planar UV axes, -1 = none
    float metres_per_tile = 1.0f;
    std::array<std::uint8_t, 4> tint{255, 255, 255, 255};  // per-instance colour for tintable materials
};

struct Curve {
    std::vector<Vec3> points;
    bool closed = false;
    [[nodiscard]] std::size_t segments() const noexcept { return closed ? points.size() : points.size() - 1; }
};

struct Geometry {
    std::vector<Part> parts;
    std::vector<Vec3> points;
    std::vector<Curve> curves;
};

using Controls = std::map<std::string, float>;

// Module registry (modules.json) and document cache for one recipe directory.
class Library {
public:
    struct Port {
        json::Value origin, normal, up;
        std::string type;
        json::Value dimensions;  // object or null
    };
    struct Module {
        std::string document;   // file name relative to `directory`
        std::string directory;  // kit directory the module was registered in
        std::string kit;        // kit name (may be empty)
        std::map<std::string, Port> ports;
    };
    // A kit is one directory with a modules.json. Kits may import other kits
    // ("imports": ["../other-kit", ...], relative to the kit directory); the
    // modules of every kit in that closure are visible to documents.
    struct Kit {
        std::string name, directory;
        std::vector<std::string> imports;  // resolved directories
        std::vector<std::string> modules;  // ids registered by this kit
    };

    Library() = default;
    explicit Library(std::string directory);  // reads <directory>/modules.json (and imported kits) when present

    [[nodiscard]] const std::string& directory() const noexcept { return dir_; }
    [[nodiscard]] const Module* module(const std::string& id) const;
    [[nodiscard]] std::vector<std::string> module_ids() const;
    [[nodiscard]] const std::vector<Kit>& kits() const noexcept { return kits_; }  // root first
    // Parsed document of a module (cached; reference stays valid).
    [[nodiscard]] const json::Value& document(const std::string& id);

private:
    void load(const std::string& directory, int depth);

    std::string dir_;
    std::vector<Kit> kits_;
    std::map<std::string, Module> modules_;
    std::map<std::string, std::unique_ptr<json::Value>> documents_;
};

struct Result {
    Geometry geometry;
    Controls controls;  // resolved root controls
    std::size_t evaluated_nodes = 0;
    std::size_t reused_nodes = 0;  // settled-prefix nodes reused from EvalCache
};

// Persistent content-addressed node cache ("settled prefix / dirty tail").
// Keyed by 128-bit structural Merkle digests over node type, qualified path,
// resolved $control float values, and upstream input digests, so editing a
// downstream branch or tweaking an unrelated control re-evaluates only the
// dirty tail while keeping settled subgraphs intact.
class EvalCache {
public:
    explicit EvalCache(std::size_t max_entries = 8192);
    ~EvalCache();
    EvalCache(EvalCache&&) noexcept;
    EvalCache& operator=(EvalCache&&) noexcept;

    void clear() noexcept;
    [[nodiscard]] std::size_t size() const noexcept;
    [[nodiscard]] std::size_t max_entries() const noexcept;
    [[nodiscard]] std::size_t hits() const noexcept;
    [[nodiscard]] std::size_t misses() const noexcept;

    struct Impl;
    Impl* impl() noexcept { return impl_.get(); }

private:
    std::unique_ptr<Impl> impl_;
};

// Declared controls with defaults, overridden and range-checked.
[[nodiscard]] Controls control_values(const json::Value& document, const Controls& overrides = {});

// Throws std::runtime_error("<node id>: <reason>") on invalid documents.
[[nodiscard]] Result evaluate(const json::Value& document, Library& library, const Controls& overrides = {}, EvalCache* cache = nullptr);
// Library directory defaults to the document's own directory.
[[nodiscard]] Result evaluate_file(const std::string& path, const Controls& overrides = {}, EvalCache* cache = nullptr);
// Evaluate the pre-bake input node when the document's root output is a `bake` node (preserving authoring parts & sources).
[[nodiscard]] Result evaluate_unbaked(const json::Value& document, Library& library, const Controls& overrides = {}, EvalCache* cache = nullptr);
[[nodiscard]] Result evaluate_file_unbaked(const std::string& path, const Controls& overrides = {}, EvalCache* cache = nullptr);

// A typed connection frame of a module (its `modulePorts`), evaluated with the
// given controls. Module-local, Z-up.
struct PortFrame {
    std::string name, type;
    Vec3 origin{}, normal{}, up{};
    std::map<std::string, float> dimensions;
};
[[nodiscard]] std::vector<PortFrame> module_ports(const json::Value& document, const Controls& controls);

// Node reference for tools: type, family, inputs, options and a one-line summary.
struct NodeInfo {
    std::string_view type, family, inputs, options, summary;
};
[[nodiscard]] const std::vector<NodeInfo>& node_reference();
[[nodiscard]] const NodeInfo* find_node_type(std::string_view type);  // nullptr when the evaluator does not know it

// Same fields as the reference evaluator's receipt, for conformance checks.
struct Receipt {
    std::size_t instances = 0, prototypes = 0, prototype_vertices = 0;
    std::size_t placed_triangles = 0, placed_loose_edges = 0;
    std::size_t details = 0, placed_details = 0;  // surface details: per prototype, and counted once per placement
    bool has_bounds = false;
    Vec3 lo{}, hi{};  // conservative world bounds, Z-up
};
[[nodiscard]] Receipt make_receipt(const Geometry& g);

// All placements merged into one world-space mesh (winding flipped under
// mirroring transforms). With `material`, only that slot.
[[nodiscard]] Mesh flatten(const Geometry& g, std::string_view material = {});

// Height of the details at a point of the prototype's surface (p and the unit normal n in prototype
// space) and the slot showing there. This is the reference a shader or a baker has to reproduce.
struct DetailSample {
    double height = 0;                // prototype units: raised (+) or sunk (-)
    std::uint16_t material = 0xFFFF;  // slot of the topmost detail covering the point, if it names one
};
[[nodiscard]] DetailSample sample_details(std::span<const Detail> details, Vec3 p, Vec3 n);
[[nodiscard]] inline DetailSample sample_details(const Proto& proto, Vec3 p, Vec3 n) { return sample_details(std::span<const Detail>(proto.details), p, n); }

// The same answer as sample_details without visiting every detail: a hash grid over the details'
// bounds. Build once per prototype; sampling is then a lookup of the few details near the point.
class DetailIndex {
public:
    DetailIndex() = default;
    explicit DetailIndex(std::span<const Detail> details);
    [[nodiscard]] DetailSample sample(Vec3 p, Vec3 n) const;
    [[nodiscard]] bool empty() const noexcept { return details_.empty(); }

private:
    std::vector<Detail> details_;
    std::vector<std::uint32_t> everywhere_;  // details too large for the grid
    std::unordered_map<std::uint64_t, std::vector<std::uint32_t>> cells_;
    double cell_ = 1;
};

// How the detail generators find the nearest surface point (pg/closest.hpp). Every setting yields
// the same details, bit for bit; the choice is there for tests and benchmarks. Auto scans a
// prototype's triangles for its first few points and builds an index once more keep coming.
enum class ProjectionSearch : std::uint8_t { Auto, Scan, Index };
void set_projection_search(ProjectionSearch mode) noexcept;
[[nodiscard]] ProjectionSearch projection_search() noexcept;

// What projection cost, for benchmarks: counts always, times only while a profile is installed.
// Not synchronised: install one from the thread that evaluates.
struct ProjectionProfile {
    std::uint64_t queries = 0, scanned = 0;      // nearest-point queries on one prototype; those answered by a scan
    std::uint64_t builds = 0, built_triangles = 0;
    double build_seconds = 0, scan_seconds = 0, index_seconds = 0;
};
void profile_projection(ProjectionProfile* sink) noexcept;  // nullptr: off (the default)

}  // namespace pg::doc
