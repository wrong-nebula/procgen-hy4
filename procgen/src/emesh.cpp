#include "pg/emesh.hpp"

#include "pg/quality.hpp"

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <numbers>
#include <tuple>
#include <fstream>
#include <map>
#include <stdexcept>
#include <unordered_map>

namespace pg::emesh {
namespace {

static_assert(std::endian::native == std::endian::little, "EMSH is little-endian");

using u8 = std::uint8_t;
using u16 = std::uint16_t;
using u32 = std::uint32_t;
using u64 = std::uint64_t;

// ---- on-disk structs: byte-for-byte eng/asset/mesh_format.hpp (format 1.0) ----
constexpr u32 kMagic = 0x48534D45u;  // "EMSH"
constexpr u32 kAlign = 16;
constexpr u32 kIndex32 = 1u << 0, kHasInstanceBounds = 1u << 1;
enum : u32 { kMeshes = 1, kSubmeshes, kPositions, kAttributes, kIndices, kBatches, kTransforms, kInstanceBounds,
             kMaterialSlots = 11,  // EMSH 1.2: names the slot ids, so a material pack can bind them
             kInstanceTints = 15,  // EMSH 1.3: u32 per instance (r in the low byte, alpha 255), linear; absent when all white
             kToolMetadata = 0x8001,
             kToolPorts = 0x8002,            // PortDesc (52 B): typed connection frames, world space
             kToolDeckDatums = 0x8003,       // f32 elevations (4 B), ascending
             kToolCollisionBoxes = 0x8004,   // Bounds (32 B): coarse solids, world space
             kToolWalkTriangles = 0x8005,    // WalkVertex x 3 (12 B each): walkable floor triangles, world space
             kToolInstanceTints = 0x8006,    // where packs before 1.3 carried the tints; still read
             kToolDetails = 0x8007 };        // DetailRow per surface detail, sorted by mesh; absent when there are none

struct PackHeader {
    u32 magic;
    u16 versionMajor, versionMinor;
    u32 flags, fileSize, sectionCount, sectionTableOffset;
    u32 meshCount, submeshCount, vertexCount, indexCount, batchCount, instanceCount;
    float boundsMin[3], boundsMax[3];
    u64 contentHash;
};
static_assert(sizeof(PackHeader) == 80);
struct SectionEntry {
    u32 kind;
    u16 codec, elementSize;
    u32 offset, size, rawSize, count;
};
static_assert(sizeof(SectionEntry) == 24);
struct MeshDesc {
    u64 contentKey;
    float boundsMin[3], boundsMax[3];
    float uvMin[2], uvScale[2];
    u32 firstSubmesh;
    u8 partCount, lodCount;
    u16 flags;
    float lodDistance[4];
};
static_assert(sizeof(MeshDesc) == 72);
struct SubmeshDesc {
    u32 material, firstIndex, indexCount;
    std::int32_t baseVertex;
};
static_assert(sizeof(SubmeshDesc) == 16);
struct VertexPosition { u16 x, y, z, w; };
struct VertexAttributes { u32 normal; u16 u, v; };
static_assert(sizeof(VertexPosition) == 8 && sizeof(VertexAttributes) == 8);
struct Bounds { float center[3], pad0, extent[3], pad1; };
static_assert(sizeof(Bounds) == 32);
struct BatchDesc {
    u32 mesh, firstInstance, instanceCount, flags;
    Bounds bounds;
};
static_assert(sizeof(BatchDesc) == 48);
struct InstanceTransform { float m[3][4]; };
static_assert(sizeof(InstanceTransform) == 48);
struct MaterialSlot { u64 nameHash; u32 slotId; u32 reserved; };
static_assert(sizeof(MaterialSlot) == 16);
constexpr u16 kMeshTwoSided = 1u << 1;  // EMSH 1.3: the mesh has open sheets; draw it without back-face culling
// A surface detail of one mesh, in the mesh's local space (see docs/DETAILING.md).
struct DetailRow {
    u32 mesh;                 // index into the mesh section
    u8 shape, profile;        // doc::Detail::Shape (0..5), doc::Detail::Profile (0..4)
    u16 slot;                 // material slot id shown on the footprint, 0xFFFF = the surface's own
    float origin[3], normal[3], tangent[3], end[3];
    float sizeA, sizeB, sizeC, height, fillet;
    u32 reserved;
};
static_assert(sizeof(DetailRow) == 80);
// Byte-for-byte eng/asset/mesh_format.hpp PortDesc & WalkVertex.
struct PortDesc {
    float origin[3], normal[3], up[3];
    u32 kind;      // 0 deck, 1 bearing, 2 track, 3 wall, 4 water, 5 service
    u32 owner;     // mesh index or 0xFFFFFFFF
    float params[2];
};
static_assert(sizeof(PortDesc) == 52);
struct WalkVertex {
    float x, y, z;
};
static_assert(sizeof(WalkVertex) == 12);

u32 classify_port_kind(std::string_view type) noexcept {
    auto has = [&](std::string_view sub) { return type.find(sub) != std::string_view::npos; };
    if (has("deck") || has("walk") || has("platform") || has("stair") || has("landing") || has("floor") || has("promenade")) return 0;
    if (has("bearing") || has("footing") || has("base") || has("seat") || has("plinth") || has("pedestal") || has("pier")) return 1;
    if (has("track") || has("rail") || has("tram") || has("slipway") || has("crane-rail")) return 2;
    if (has("wall") || has("bay") || has("facade") || has("gable") || has("jamb") || has("arch") || has("portal") || has("door")) return 3;
    if (has("water") || has("quay") || has("berth") || has("canal") || has("lock") || has("pipe") || has("flange") || has("drain")) return 4;
    return 5;  // service / cable / mount / generic
}

bool is_non_walkable_slot(std::string_view name) noexcept {
    for (std::string_view sub : {"glass", "glazing", "opal", "water", "foliage", "leaf", "canopy", "wire", "rope", "cable"}) {
        if (name.find(sub) != std::string_view::npos) return true;
    }
    return false;
}

u64 fnv1a64(const void* data, std::size_t size, u64 hash = 0xCBF29CE484222325ull) {
    const u8* p = static_cast<const u8*>(data);
    for (std::size_t i = 0; i < size; ++i) hash = (hash ^ p[i]) * 0x100000001B3ull;
    return hash;
}
u16 quantize16(double value, double lo, double extent) {
    if (!(extent > 0.0)) return 0;
    const double t = std::clamp((value - lo) / extent, 0.0, 1.0);
    return static_cast<u16>(std::lround(t * 65535.0));
}
u32 encode_normal(Vec3 n) {
    auto q = [](double v) { return static_cast<u32>(std::lround(std::clamp(v * 0.5 + 0.5, 0.0, 1.0) * 1023.0)); };
    return q(n.x) | (q(n.y) << 10) | (q(n.z) << 20);
}
Vec3 decode_normal(u32 packed) {
    auto d = [](u32 v) { return static_cast<double>(v & 1023u) * (2.0 / 1023.0) - 1.0; };
    return normalize_or({d(packed), d(packed >> 10), d(packed >> 20)}, {0, 0, 1});
}

// Z-up document space -> Y-up engine space: (x, y, z) -> (x, z, -y).
Vec3 to_engine(Vec3 p, bool y_up) { return y_up ? Vec3{p.x, p.z, -p.y} : p; }

struct Frame {  // canonical local -> prototype local: p = o + e1*q.x + e2*q.y + e3*q.z
    Vec3 o{}, e1{1, 0, 0}, e2{0, 1, 0}, e3{0, 0, 1};
};

struct BuiltLod {
    std::vector<Vec3> pos, nrm;
    std::vector<std::array<double, 2>> uv;
    std::vector<u32> idx;
    float distance = 0.0f;
};

struct Built {  // one mesh ready to serialise (engine space)
    std::vector<Vec3> pos, nrm;
    std::vector<std::array<double, 2>> uv;
    std::vector<u32> idx;
    float lod0_distance = 0.0f;            // max distance (metres) for LOD 0 when extra_lods is non-empty
    std::vector<BuiltLod> extra_lods;      // optional LOD 1..3 (when subdivide lodLevels is set)
    Aabb box;
    u16 material = 0;
    u64 key = 0;
    std::shared_ptr<const std::string> name;
    std::vector<InstanceTransform> instances;
    std::vector<std::array<u8, 4>> tints;  // parallel to instances
    std::vector<Aabb> instance_boxes;
    std::vector<DetailRow> details;        // mesh index filled in when serialising
    bool two_sided = false;                // not a closed solid: its sheets are seen from both sides
};

// A frame fixed by the mesh's own first non-degenerate face and its centroid:
// congruent prototypes built with the same vertex order get the same
// coordinates in it, whatever their placement.
bool canonical_frame(const Mesh& m, Frame& f) {
    Vec3 c{};
    for (const Vec3& p : m.positions) c += p;
    c = c / static_cast<double>(m.vertex_count());
    for (std::size_t face = 0; face < m.face_count(); ++face) {
        const auto fv = m.face(face);
        const Vec3 a = m.positions[fv[0]], e = m.positions[fv[1]] - a, n = face_area_vector(m, face);
        const double el = length(e), nl = length(n);
        if (!(el > 1e-9 && nl > 1e-12)) continue;
        f.o = c;
        f.e1 = e / el;
        f.e3 = n / nl;
        f.e2 = cross(f.e3, f.e1);
        return true;
    }
    return false;
}

struct SourceKey {
    const doc::Proto* proto;
    u16 material;
    bool smooth;
    float smooth_angle_deg;
    u8 normal_weight;
    bool split_creases;
    std::int8_t uv_u, uv_v;
    float tile;
    auto operator<=>(const SourceKey&) const = default;
};

// Compute per-corner smooth normals respecting Phong break angle, hard creases,
// and face/corner weighting (uniform, area, angle, areaAngle, areaSq).
std::vector<Vec3> compute_weighted_split_normals(const Mesh& src, const std::vector<Vec3>& q, float smooth_angle_deg,
                                                 u8 normal_weight, bool split_creases) {
    const std::size_t nv = src.vertex_count(), nf = src.face_count(), nc = src.corner_count();
    std::vector<Vec3> out(nc, Vec3{0, 0, 1});
    if (nf == 0 || nc == 0) return out;

    std::vector<Vec3> f_unit(nf, Vec3{0, 0, 1});
    std::vector<double> f_area(nf, 0.0);
    std::vector<Index> corner_face(nc), c_next(nc), c_prev(nc);
    for (std::size_t f = 0; f < nf; ++f) {
        const Index b = src.face_offsets[f], e = src.face_offsets[f + 1];
        Vec3 av{};
        for (Index c = b + 1; c + 1 < e; ++c) {
            av += cross(q[src.face_indices[c]] - q[src.face_indices[b]],
                        q[src.face_indices[c + 1]] - q[src.face_indices[b]]);
        }
        const double l = length(av);
        f_area[f] = 0.5 * l;
        f_unit[f] = l > 1e-20 ? av / l : Vec3{0, 0, 1};
        for (Index c = b; c < e; ++c) {
            corner_face[c] = static_cast<Index>(f);
            c_next[c] = c + 1 == e ? b : c + 1;
            c_prev[c] = c == b ? e - 1 : c - 1;
        }
    }

    // Build half-edge twin table and mark hard edges (boundary, > angle, or hard crease).
    constexpr Index none = ~Index{0};
    struct KC {
        u64 key;
        Index corner;
    };
    std::vector<KC> kc(nc);
    for (Index c = 0; c < nc; ++c) kc[c] = {edge_key(src.face_indices[c], src.face_indices[c_next[c]]), c};
    std::sort(kc.begin(), kc.end(), [](const KC& a, const KC& b) { return a.key < b.key; });

    const double cos_lim = std::cos(static_cast<double>(smooth_angle_deg) * std::numbers::pi / 180.0) - 1e-6;
    std::vector<Index> soft_twin(nc, none);
    for (std::size_t i = 0; i < nc;) {
        std::size_t j = i;
        while (j < nc && kc[j].key == kc[i].key) ++j;
        if (j - i == 2) {
            const Index a = kc[i].corner, b = kc[i + 1].corner;
            if (src.face_indices[a] != src.face_indices[b]) {
                const auto v0 = static_cast<Index>(kc[i].key >> 32);
                const auto v1 = static_cast<Index>(kc[i].key & 0xffffffffu);
                const bool crease_hard = split_creases && src.edge_crease(v0, v1) >= kCreaseInfinite - 1e-3f;
                const bool angle_hard = dot(f_unit[corner_face[a]], f_unit[corner_face[b]]) < cos_lim;
                if (!crease_hard && !angle_hard) {
                    soft_twin[a] = b;
                    soft_twin[b] = a;
                }
            }
        }
        i = j;
    }

    // Corner weight at corner c of face f.
    auto corner_weight = [&](Index c) -> double {
        const Index f = corner_face[c];
        const double area = f_area[f];
        if (normal_weight == 0) return 1.0;
        if (normal_weight == 1) return area;
        if (normal_weight == 4) return area * area;
        const Vec3 p = q[src.face_indices[c]];
        const Vec3 e1 = normalize_or(q[src.face_indices[c_next[c]]] - p, Vec3{});
        const Vec3 e2 = normalize_or(q[src.face_indices[c_prev[c]]] - p, Vec3{});
        const double ang = std::acos(std::clamp(dot(e1, e2), -1.0, 1.0));
        return normal_weight == 2 ? ang : (area * ang);
    };

    // Group corners by vertex and partition each vertex's incident corners into
    // connected components across soft edges.
    std::vector<Index> vc_off(nv + 1, 0);
    for (Index c = 0; c < nc; ++c) ++vc_off[src.face_indices[c] + 1];
    for (std::size_t v = 0; v < nv; ++v) vc_off[v + 1] += vc_off[v];
    std::vector<Index> vc(nc);
    {
        std::vector<Index> cur(vc_off.begin(), vc_off.end() - 1);
        for (Index c = 0; c < nc; ++c) vc[cur[src.face_indices[c]]++] = c;
    }

    std::vector<u8> visited(nc, 0);
    std::vector<Index> comp;
    for (std::size_t v = 0; v < nv; ++v) {
        const Index cb = vc_off[v], ce = vc_off[v + 1];
        for (Index i = cb; i < ce; ++i) {
            const Index seed = vc[i];
            if (visited[seed]) continue;
            comp.clear();
            comp.push_back(seed);
            visited[seed] = 1;
            for (std::size_t qi = 0; qi < comp.size(); ++qi) {
                const Index c = comp[qi];
                // Across outgoing edge (c -> next[c]): twin is soft_twin[c], whose corner at v is c_next[twin].
                if (soft_twin[c] != none) {
                    const Index nb = c_next[soft_twin[c]];
                    if (src.face_indices[nb] == v && !visited[nb]) {
                        visited[nb] = 1;
                        comp.push_back(nb);
                    }
                }
                // Across incoming edge (prev[c] -> c): twin is soft_twin[c_prev[c]], which is directly at v.
                const Index tin = soft_twin[c_prev[c]];
                if (tin != none && src.face_indices[tin] == v && !visited[tin]) {
                    visited[tin] = 1;
                    comp.push_back(tin);
                }
            }
            Vec3 sum{};
            for (Index c : comp) sum += f_unit[corner_face[c]] * corner_weight(c);
            const Vec3 nrm = normalize_or(sum, f_unit[corner_face[seed]]);
            for (Index c : comp) out[c] = nrm;
        }
    }
    return out;
}

}  // namespace

std::uint64_t slot_name_hash(std::string_view name) noexcept {
    u64 hash = 0xCBF29CE484222325ull;
    for (const char c : name) {
        const u8 b = static_cast<u8>(c >= 'A' && c <= 'Z' ? c + 32 : c);
        hash = (hash ^ b) * 0x100000001B3ull;
    }
    return hash;
}

std::vector<u8> write(const doc::Geometry& g, const WriteOptions& opt, WriteStats* stats_out) {
    WriteStats st;
    std::vector<Built> meshes;
    std::map<SourceKey, std::pair<std::size_t, Frame>> by_source;     // placement key -> (mesh, frame)
    std::unordered_map<u64, std::vector<std::size_t>> by_signature;   // canonical signature -> meshes
    std::vector<std::vector<long long>> signatures;                   // per mesh: rounded canonical coords

    for (const doc::Part& part : g.parts) {
        const Mesh& src = part.proto->mesh;
        if (src.face_count() == 0) continue;
        ++st.placements;
        const SourceKey skey{part.proto.get(), part.material, part.smooth, part.smooth_angle_deg, part.normal_weight,
                             part.split_creases, part.uv_u, part.uv_v, part.metres_per_tile};
        auto found = by_source.find(skey);
        if (found == by_source.end()) {
            ++st.source_prototypes;
            Frame frame;
            const bool planar_uv = part.uv_u >= 0;
            const bool canonical = opt.dedupe_congruent && !planar_uv && canonical_frame(src, frame);
            // Canonical coordinates of every vertex.
            std::vector<Vec3> q(src.vertex_count());
            for (std::size_t i = 0; i < q.size(); ++i) {
                const Vec3 d = src.positions[i] - frame.o;
                q[i] = {dot(d, frame.e1), dot(d, frame.e2), dot(d, frame.e3)};
            }
            bool drawable = false;
            for (std::size_t f = 0; f < src.face_count() && !drawable; ++f) drawable = length(face_area_vector(src, f)) > 1e-20;
            if (!drawable) {  // only zero-area faces: nothing to draw, now or for later placements
                by_source.emplace(skey, std::pair{static_cast<std::size_t>(-1), frame});
                --st.placements;
                continue;
            }
            std::size_t mesh_index = meshes.size();
            bool reuse = false;
            const bool has_explicit_normals = part.proto->normals.size() == src.corner_count();
            const bool custom_smooth = part.smooth && (part.smooth_angle_deg < 179.99f || part.normal_weight != 1 ||
                                                       (part.split_creases && !src.edge_creases.empty()));
            if (canonical) {
                const double inv = 1.0 / opt.congruence_tolerance;
                std::vector<long long> sig;
                sig.reserve(q.size() * 3 + src.corner_count() + 8);
                sig.push_back(part.material);
                sig.push_back(part.smooth);
                if (custom_smooth) {
                    sig.push_back(std::llround(static_cast<double>(part.smooth_angle_deg) * 100.0));
                    sig.push_back(part.normal_weight);
                    sig.push_back(part.split_creases ? 1 : 0);
                    for (const EdgeCrease& ec : src.edge_creases) {
                        sig.push_back(static_cast<long long>(ec.key));
                        sig.push_back(std::llround(static_cast<double>(ec.sharpness) * 100.0));
                    }
                }
                if (has_explicit_normals) {
                    sig.push_back(0x4E524Dll);
                    for (const auto& rn : part.proto->normals) {
                        const Vec3 n{rn[0], rn[1], rn[2]};
                        sig.push_back(std::llround(dot(n, frame.e1) * 1000.0));
                        sig.push_back(std::llround(dot(n, frame.e2) * 1000.0));
                        sig.push_back(std::llround(dot(n, frame.e3) * 1000.0));
                    }
                }
                if (!part.proto->lods.empty()) {
                    sig.push_back(0x4C4F4453ll);
                    sig.push_back(static_cast<long long>(part.proto->lods.size()));
                    for (const auto& ll : part.proto->lods) {
                        sig.push_back(static_cast<long long>(ll.mesh.vertex_count()));
                        sig.push_back(static_cast<long long>(ll.mesh.face_count()));
                        sig.push_back(std::llround(static_cast<double>(ll.distance) * 100.0));
                    }
                }
                sig.push_back(static_cast<long long>(src.vertex_count()));
                for (std::size_t f = 0; f <= src.face_count(); ++f) sig.push_back(src.face_offsets[f]);
                for (Index v : src.face_indices) sig.push_back(v);
                for (const Vec3& p : q) {
                    sig.push_back(std::llround(p.x * inv));
                    sig.push_back(std::llround(p.y * inv));
                    sig.push_back(std::llround(p.z * inv));
                }
                for (const doc::Detail& d : part.proto->details) {  // same shape with other details is another mesh
                    sig.push_back(static_cast<long long>(d.shape) | (static_cast<long long>(d.profile) << 8) | (static_cast<long long>(d.material) << 16));
                    const Vec3 o = d.origin - frame.o, e = d.end - frame.o;
                    for (const Vec3& v : {Vec3{dot(o, frame.e1), dot(o, frame.e2), dot(o, frame.e3)}, Vec3{dot(e, frame.e1), dot(e, frame.e2), dot(e, frame.e3)},
                                          Vec3{dot(d.normal, frame.e1), dot(d.normal, frame.e2), dot(d.normal, frame.e3)},
                                          Vec3{dot(d.tangent, frame.e1), dot(d.tangent, frame.e2), dot(d.tangent, frame.e3)},
                                          Vec3{d.size_a, d.size_b, d.height}, Vec3{d.fillet, d.size_c, 0}}) {
                        sig.push_back(std::llround(v.x * inv));
                        sig.push_back(std::llround(v.y * inv));
                        sig.push_back(std::llround(v.z * inv));
                    }
                }
                const u64 h = fnv1a64(sig.data(), sig.size() * sizeof(long long));
                for (std::size_t candidate : by_signature[h])
                    if (signatures[candidate] == sig) { mesh_index = candidate; reuse = true; break; }
                if (!reuse) {
                    by_signature[h].push_back(mesh_index);
                    signatures.resize(mesh_index + 1);
                    signatures[mesh_index] = std::move(sig);
                }
            }
            if (!reuse) {
                signatures.resize(std::max(signatures.size(), mesh_index + 1));
                Built b;
                b.material = part.material;
                b.name = part.source;
                QualityOptions closedness;
                closedness.check_self_intersections = false;
                const QualityReport shape = analyze(src, closedness);
                b.two_sided = shape.boundary_edges != 0 || shape.nonmanifold_edges != 0;
                auto build_surface = [&](const Mesh& m_src, const std::vector<Vec3>& q_src,
                                         const std::vector<std::array<float, 2>>& chart_src,
                                         const std::vector<std::array<float, 3>>& nrm_src,
                                         std::vector<Vec3>& out_pos, std::vector<Vec3>& out_nrm,
                                         std::vector<std::array<double, 2>>& out_uv, std::vector<u32>& out_idx) {
                    const bool explicit_n = nrm_src.size() == m_src.corner_count();
                    const bool custom_sm = part.smooth && (part.smooth_angle_deg < 179.99f || part.normal_weight != 1 ||
                                                           (part.split_creases && !m_src.edge_creases.empty()));
                    std::vector<Vec3> vnormal;
                    std::vector<Vec3> cnormal;
                    if (explicit_n) {
                        cnormal.resize(m_src.corner_count());
                        for (std::size_t c = 0; c < m_src.corner_count(); ++c) {
                            const auto& rn = nrm_src[c];
                            const Vec3 n{rn[0], rn[1], rn[2]};
                            cnormal[c] = normalize_or({dot(n, frame.e1), dot(n, frame.e2), dot(n, frame.e3)}, {0, 0, 1});
                        }
                    } else if (custom_sm) {
                        cnormal = compute_weighted_split_normals(m_src, q_src, part.smooth_angle_deg, part.normal_weight, part.split_creases);
                    } else if (part.smooth) {
                        vnormal.assign(q_src.size(), Vec3{});
                        for (std::size_t f = 0; f < m_src.face_count(); ++f) {
                            const auto fv = m_src.face(f);
                            Vec3 n{};
                            for (std::size_t i = 1; i + 1 < fv.size(); ++i) n += cross(q_src[fv[i]] - q_src[fv[0]], q_src[fv[i + 1]] - q_src[fv[0]]);
                            for (Index v : fv) vnormal[v] += n;
                        }
                    }
                    const bool use_cn = !cnormal.empty();
                    const bool charted = !planar_uv && chart_src.size() == m_src.corner_count();
                    std::map<std::tuple<Index, u32, std::int64_t, std::int64_t>, u32> remap;
                    auto vertex = [&](Index v, Vec3 n, std::size_t corner) {
                        const Vec3 ne = to_engine(n, opt.y_up);
                        const std::int64_t ku = charted ? std::llround(static_cast<double>(chart_src[corner][0]) * 2000.0) : 0;
                        const std::int64_t kv = charted ? std::llround(static_cast<double>(chart_src[corner][1]) * 2000.0) : 0;
                        const u32 nkey = (!use_cn && part.smooth) ? 0u : encode_normal(ne);
                        const auto [it, fresh] = remap.try_emplace({v, nkey, ku, kv}, static_cast<u32>(out_pos.size()));
                        if (fresh) {
                            out_pos.push_back(to_engine(q_src[v], opt.y_up));
                            out_nrm.push_back(ne);
                            std::array<double, 2> uv{};
                            if (charted) {
                                uv = {chart_src[corner][0], chart_src[corner][1]};
                            } else if (planar_uv) {
                                uv = {m_src.positions[v][part.uv_u], m_src.positions[v][part.uv_v]};
                            } else {
                                const double ax = std::abs(n.x), ay = std::abs(n.y), az = std::abs(n.z);
                                if (ax >= ay && ax >= az) uv = {q_src[v].y, q_src[v].z};
                                else if (ay >= az) uv = {q_src[v].x, q_src[v].z};
                                else uv = {q_src[v].x, q_src[v].y};
                            }
                            out_uv.push_back(uv);
                            b.box.expand(out_pos.back());
                        }
                        return it->second;
                    };
                    for (std::size_t f = 0; f < m_src.face_count(); ++f) {
                        const auto fv = m_src.face(f);
                        Vec3 n{};
                        for (std::size_t i = 1; i + 1 < fv.size(); ++i) n += cross(q_src[fv[i]] - q_src[fv[0]], q_src[fv[i + 1]] - q_src[fv[0]]);
                        const double nl = length(n);
                        if (!(nl > 1e-20)) continue;
                        n = n / nl;
                        const std::size_t first_corner = m_src.face_offsets[f];
                        for (std::size_t i = 1; i + 1 < fv.size(); ++i) {
                            const std::size_t local[3] = {0, i, i + 1};
                            for (const std::size_t c : local) {
                                const Vec3 cn = use_cn ? cnormal[first_corner + c]
                                                       : (part.smooth ? normalize_or(vnormal[fv[c]], n) : n);
                                out_idx.push_back(vertex(fv[c], cn, first_corner + c));
                            }
                        }
                    }
                };
                build_surface(src, q, part.proto->uv, part.proto->normals, b.pos, b.nrm, b.uv, b.idx);
                b.lod0_distance = part.proto->lod0_distance;
                for (std::size_t li = 0; li < part.proto->lods.size() && li < 3; ++li) {
                    const auto& ll = part.proto->lods[li];
                    if (ll.mesh.face_count() == 0) continue;
                    std::vector<Vec3> ql(ll.mesh.vertex_count());
                    for (std::size_t i = 0; i < ql.size(); ++i) {
                        const Vec3 d = ll.mesh.positions[i] - frame.o;
                        ql[i] = {dot(d, frame.e1), dot(d, frame.e2), dot(d, frame.e3)};
                    }
                    BuiltLod bl;
                    bl.distance = ll.distance;
                    build_surface(ll.mesh, ql, ll.uv, ll.normals, bl.pos, bl.nrm, bl.uv, bl.idx);
                    if (!bl.idx.empty()) b.extra_lods.push_back(std::move(bl));
                }
                for (const doc::Detail& d : part.proto->details) {
                    auto local = [&](Vec3 v) { return to_engine(Vec3{dot(v, frame.e1), dot(v, frame.e2), dot(v, frame.e3)}, opt.y_up); };
                    const Vec3 o = local(d.origin - frame.o), e = local(d.end - frame.o), n = local(d.normal), t = local(d.tangent);
                    DetailRow row{};
                    row.shape = static_cast<u8>(d.shape);
                    row.profile = static_cast<u8>(d.profile);
                    row.slot = d.material;
                    for (int k = 0; k < 3; ++k) {
                        row.origin[k] = static_cast<float>(o[k]);
                        row.normal[k] = static_cast<float>(n[k]);
                        row.tangent[k] = static_cast<float>(t[k]);
                        row.end[k] = static_cast<float>(e[k]);
                    }
                    row.sizeA = static_cast<float>(d.size_a);
                    row.sizeB = static_cast<float>(d.size_b);
                    row.sizeC = static_cast<float>(d.size_c);
                    row.height = static_cast<float>(d.height);
                    row.fillet = static_cast<float>(d.fillet);
                    b.details.push_back(row);
                }
                meshes.push_back(std::move(b));
            }
            found = by_source.emplace(skey, std::pair{mesh_index, frame}).first;
        }
        if (found->second.first == static_cast<std::size_t>(-1)) { --st.placements; continue; }
        Built& b = meshes[found->second.first];
        const Frame& fr = found->second.second;
        // local(canonical, engine axes) -> world(engine axes): C * T * F * C^-1.
        const doc::Mat4& T = part.transform;
        const Vec3 cols_z[3] = {T.direction(fr.e1), T.direction(fr.e2), T.direction(fr.e3)};  // images of canonical axes
        const Vec3 origin = T.point(fr.o);
        // Columns of the engine-space matrix: image of engine axis k. Engine x = canon x,
        // engine y = canon z, engine z = -canon y (when y_up).
        Vec3 ex = to_engine(cols_z[0], opt.y_up), ey, ez;
        if (opt.y_up) { ey = to_engine(cols_z[2], true); ez = to_engine(cols_z[1], true) * -1.0; }
        else { ey = cols_z[1]; ez = cols_z[2]; }
        const Vec3 t = to_engine(origin, opt.y_up);
        InstanceTransform it{};
        for (int r = 0; r < 3; ++r) {
            it.m[r][0] = static_cast<float>(ex[r]);
            it.m[r][1] = static_cast<float>(ey[r]);
            it.m[r][2] = static_cast<float>(ez[r]);
            it.m[r][3] = static_cast<float>(t[r]);
        }
        if (dot(ex, cross(ey, ez)) < 0) ++st.mirrored_instances;
        Aabb world;
        for (int corner = 0; corner < 8; ++corner) {
            const Vec3 p{(corner & 1) ? b.box.hi.x : b.box.lo.x, (corner & 2) ? b.box.hi.y : b.box.lo.y, (corner & 4) ? b.box.hi.z : b.box.lo.z};
            world.expand(t + ex * p.x + ey * p.y + ez * p.z);
        }
        b.instances.push_back(it);
        b.tints.push_back(part.tint);
        b.instance_boxes.push_back(world);
    }

    // ---- serialise --------------------------------------------------------------
    std::vector<MeshDesc> mesh_descs;
    std::vector<SubmeshDesc> submeshes;
    std::vector<VertexPosition> positions;
    std::vector<VertexAttributes> attributes;
    std::vector<u32> indices;
    std::vector<BatchDesc> batches;
    std::vector<InstanceTransform> transforms;
    std::vector<std::array<u8, 4>> tints;  // one per transform
    std::vector<DetailRow> details;
    std::vector<Bounds> instance_bounds;
    Aabb world;
    auto to_bounds = [](const Aabb& a) {
        const Vec3 c = a.center(), e = a.extent() * 0.5;
        return Bounds{{static_cast<float>(c.x), static_cast<float>(c.y), static_cast<float>(c.z)}, 0,
                      {static_cast<float>(e.x), static_cast<float>(e.y), static_cast<float>(e.z)}, 0};
    };
    for (Built& b : meshes) {
        st.index32 = st.index32 || b.pos.size() > 65536;
        for (const BuiltLod& bl : b.extra_lods) st.index32 = st.index32 || bl.pos.size() > 65536;
        MeshDesc d{};
        // Quantisation box in float, widened so every vertex is inside after rounding.
        float lo[3] = {0, 0, 0}, hi[3] = {0, 0, 0};
        for (int k = 0; k < 3; ++k) {
            lo[k] = std::nextafter(static_cast<float>(b.box.lo[k]), -std::numeric_limits<float>::infinity());
            hi[k] = std::nextafter(static_cast<float>(b.box.hi[k]), std::numeric_limits<float>::infinity());
            d.boundsMin[k] = lo[k];
            d.boundsMax[k] = hi[k];
        }
        double uv_lo[2] = {1e300, 1e300}, uv_hi[2] = {-1e300, -1e300};
        for (const auto& uv : b.uv)
            for (int k = 0; k < 2; ++k) { uv_lo[k] = std::min(uv_lo[k], uv[static_cast<std::size_t>(k)]); uv_hi[k] = std::max(uv_hi[k], uv[static_cast<std::size_t>(k)]); }
        for (const BuiltLod& bl : b.extra_lods)
            for (const auto& uv : bl.uv)
                for (int k = 0; k < 2; ++k) { uv_lo[k] = std::min(uv_lo[k], uv[static_cast<std::size_t>(k)]); uv_hi[k] = std::max(uv_hi[k], uv[static_cast<std::size_t>(k)]); }
        for (int k = 0; k < 2; ++k) { d.uvMin[k] = static_cast<float>(uv_lo[k]); d.uvScale[k] = static_cast<float>(uv_hi[k] - uv_lo[k]); }
        d.firstSubmesh = static_cast<u32>(submeshes.size());
        d.flags = b.two_sided ? kMeshTwoSided : u16{0};
        d.partCount = 1;
        d.lodCount = static_cast<u8>(1 + b.extra_lods.size());
        // Engine convention (mesh_format.hpp): lodDistance[i] is max distance for LOD i; last LOD = 0 (unlimited).
        if (!b.extra_lods.empty()) {
            d.lodDistance[0] = b.lod0_distance > 0.0f ? b.lod0_distance : 12.0f;
            for (std::size_t li = 0; li < b.extra_lods.size() && li < 3; ++li) {
                d.lodDistance[li + 1] = b.extra_lods[li].distance;
            }
        }
        const auto base = static_cast<u32>(positions.size());
        const auto first_index = static_cast<u32>(indices.size());
        for (std::size_t i = 0; i < b.pos.size(); ++i) {
            const Vec3 p = b.pos[i];
            positions.push_back({quantize16(p.x, lo[0], static_cast<double>(hi[0]) - lo[0]), quantize16(p.y, lo[1], static_cast<double>(hi[1]) - lo[1]),
                                 quantize16(p.z, lo[2], static_cast<double>(hi[2]) - lo[2]), 0});
            attributes.push_back({encode_normal(b.nrm[i]), quantize16(b.uv[i][0], d.uvMin[0], d.uvScale[0]), quantize16(b.uv[i][1], d.uvMin[1], d.uvScale[1])});
        }
        indices.insert(indices.end(), b.idx.begin(), b.idx.end());
        submeshes.push_back({b.material, first_index, static_cast<u32>(b.idx.size()), static_cast<std::int32_t>(base)});
        // Content key: the bytes the GPU will see plus the material and the box.
        u64 key = fnv1a64(&positions[base], b.pos.size() * sizeof(VertexPosition));
        key = fnv1a64(&attributes[base], b.pos.size() * sizeof(VertexAttributes), key);
        key = fnv1a64(b.idx.data(), b.idx.size() * sizeof(u32), key);
        key = fnv1a64(d.boundsMin, sizeof(float) * 6, key);
        key = fnv1a64(&b.material, sizeof(b.material), key);
        for (const BuiltLod& bl : b.extra_lods) {
            const auto lbase = static_cast<u32>(positions.size());
            const auto lfirst = static_cast<u32>(indices.size());
            for (std::size_t i = 0; i < bl.pos.size(); ++i) {
                const Vec3 p = bl.pos[i];
                positions.push_back({quantize16(p.x, lo[0], static_cast<double>(hi[0]) - lo[0]), quantize16(p.y, lo[1], static_cast<double>(hi[1]) - lo[1]),
                                     quantize16(p.z, lo[2], static_cast<double>(hi[2]) - lo[2]), 0});
                attributes.push_back({encode_normal(bl.nrm[i]), quantize16(bl.uv[i][0], d.uvMin[0], d.uvScale[0]), quantize16(bl.uv[i][1], d.uvMin[1], d.uvScale[1])});
            }
            indices.insert(indices.end(), bl.idx.begin(), bl.idx.end());
            submeshes.push_back({b.material, lfirst, static_cast<u32>(bl.idx.size()), static_cast<std::int32_t>(lbase)});
            key = fnv1a64(&positions[lbase], bl.pos.size() * sizeof(VertexPosition), key);
            key = fnv1a64(bl.idx.data(), bl.idx.size() * sizeof(u32), key);
            key = fnv1a64(&bl.distance, sizeof(bl.distance), key);
        }
        st.lod_submeshes += d.lodCount;
        if (!b.details.empty()) key = fnv1a64(b.details.data(), b.details.size() * sizeof(DetailRow), key);  // mesh index still 0 here
        d.contentKey = b.key = key;
        for (DetailRow row : b.details) {
            row.mesh = static_cast<u32>(mesh_descs.size());
            details.push_back(row);
        }
        Aabb batch_box;
        BatchDesc batch{static_cast<u32>(mesh_descs.size()), static_cast<u32>(transforms.size()), static_cast<u32>(b.instances.size()), 0, {}};
        for (std::size_t i = 0; i < b.instances.size(); ++i) {
            transforms.push_back(b.instances[i]);
            tints.push_back(b.tints[i]);
            instance_bounds.push_back(to_bounds(b.instance_boxes[i]));
            batch_box.expand(b.instance_boxes[i].lo);
            batch_box.expand(b.instance_boxes[i].hi);
        }
        batch.bounds = to_bounds(batch_box);
        world.expand(batch_box.lo);
        world.expand(batch_box.hi);
        batches.push_back(batch);
        mesh_descs.push_back(d);
        st.triangles += b.idx.size() / 3 * b.instances.size();
    }
    st.meshes = mesh_descs.size();
    for (const Built& b : meshes) st.two_sided_meshes += b.two_sided;
    st.vertices = positions.size();
    st.indices = indices.size();
    st.instances = transforms.size();
    st.batches = batches.size();

    // Tool metadata (kind >= 0x8000 is skipped by the engine): slot names so a
    // viewer can colour parts, mesh names for debugging, and the up axis.
    json::Writer meta;
    meta.begin_object().field("generator", "procgen").field("format", 1).field("up", opt.y_up ? "y" : "z").field("source", opt.source);
    meta.field("mirroredInstances", st.mirrored_instances).key("slots").begin_array();
    u16 max_slot = 0;
    for (const Built& b : meshes) max_slot = std::max(max_slot, b.material);
    for (const DetailRow& row : details)
        if (row.slot != 0xFFFF) max_slot = std::max(max_slot, row.slot);
    st.details = details.size();
    for (u16 s = 0; s <= max_slot && !meshes.empty(); ++s) {
        const Vec3 c = doc::slot_color(s);
        meta.begin_object().field("id", static_cast<std::size_t>(s)).field("name", doc::slot_name(s));
        meta.key("color").begin_array().value(c.x).value(c.y).value(c.z).end_array().end_object();
    }
    meta.end_array().key("meshes").begin_array();
    for (const Built& b : meshes) meta.begin_object().field("name", b.name ? *b.name : std::string()).field("instances", b.instances.size()).end_object();
    meta.end_array().end_object();
    const std::string& meta_json = meta.str();

    // One row per slot the meshes use (EMSH 1.2); a material pack binds materials to these names.
    std::vector<MaterialSlot> material_slots;
    for (u16 s = 0; s <= max_slot && !meshes.empty(); ++s) {
        bool used = false;
        for (const Built& b : meshes) used = used || b.material == s;
        for (const DetailRow& row : details) used = used || row.slot == s;
        if (used) material_slots.push_back({slot_name_hash(doc::slot_name(s)), s, 0});
    }

    std::vector<u16> indices16;
    if (!st.index32) indices16.assign(indices.begin(), indices.end());

    struct Section { u32 kind; u16 element; const void* data; std::size_t bytes; std::size_t count; };
    std::vector<Section> sections = {
        {kMeshes, sizeof(MeshDesc), mesh_descs.data(), mesh_descs.size() * sizeof(MeshDesc), mesh_descs.size()},
        {kSubmeshes, sizeof(SubmeshDesc), submeshes.data(), submeshes.size() * sizeof(SubmeshDesc), submeshes.size()},
        {kPositions, sizeof(VertexPosition), positions.data(), positions.size() * sizeof(VertexPosition), positions.size()},
        {kAttributes, sizeof(VertexAttributes), attributes.data(), attributes.size() * sizeof(VertexAttributes), attributes.size()},
        {kIndices, static_cast<u16>(st.index32 ? 4 : 2), st.index32 ? static_cast<const void*>(indices.data()) : indices16.data(),
         indices.size() * (st.index32 ? 4u : 2u), indices.size()},
        {kBatches, sizeof(BatchDesc), batches.data(), batches.size() * sizeof(BatchDesc), batches.size()},
        {kTransforms, sizeof(InstanceTransform), transforms.data(), transforms.size() * sizeof(InstanceTransform), transforms.size()},
        {kInstanceBounds, sizeof(Bounds), instance_bounds.data(), instance_bounds.size() * sizeof(Bounds), instance_bounds.size()},
        {kMaterialSlots, sizeof(MaterialSlot), material_slots.data(), material_slots.size() * sizeof(MaterialSlot), material_slots.size()},
        {kToolMetadata, 1, meta_json.data(), meta_json.size(), meta_json.size()},
    };
    bool tinted = false;
    for (const auto& t : tints) tinted = tinted || t != std::array<u8, 4>{255, 255, 255, 255};
    for (auto& t : tints) t[3] = 255;  // alpha is unused by the engine
    if (tinted) sections.push_back({kInstanceTints, 4, tints.data(), tints.size() * 4, tints.size()});
    if (!details.empty()) sections.push_back({kToolDetails, sizeof(DetailRow), details.data(), details.size() * sizeof(DetailRow), details.size()});

    // Engine world-structure metadata (0x8002 Ports, 0x8003 DeckDatums, 0x8004 CollisionBoxes, 0x8005 WalkTriangles).
    std::vector<PortDesc> port_descs;
    std::vector<float> datums = opt.deck_datums;
    for (const doc::PortFrame& pf : opt.ports) {
        const Vec3 o = to_engine(pf.origin, opt.y_up);
        const Vec3 n = normalize_or(to_engine(pf.normal, opt.y_up), {0, 0, 1});
        const Vec3 u = normalize_or(to_engine(pf.up, opt.y_up), opt.y_up ? Vec3{0, 1, 0} : Vec3{0, 0, 1});
        PortDesc pd{};
        pd.origin[0] = static_cast<float>(o.x); pd.origin[1] = static_cast<float>(o.y); pd.origin[2] = static_cast<float>(o.z);
        pd.normal[0] = static_cast<float>(n.x); pd.normal[1] = static_cast<float>(n.y); pd.normal[2] = static_cast<float>(n.z);
        pd.up[0] = static_cast<float>(u.x); pd.up[1] = static_cast<float>(u.y); pd.up[2] = static_cast<float>(u.z);
        pd.kind = classify_port_kind(pf.type);
        pd.owner = 0xFFFFFFFFu;
        auto pick_dim = [&](std::initializer_list<const char*> keys) -> float {
            for (const char* k : keys) {
                const auto it = pf.dimensions.find(k);
                if (it != pf.dimensions.end()) return it->second;
            }
            return 0.0f;
        };
        pd.params[0] = pick_dim({"clearWidth", "width", "gauge", "span", "diameter"});
        pd.params[1] = pick_dim({"height", "depth", "thickness", "clearance"});
        port_descs.push_back(pd);
        if (pd.kind == 0) datums.push_back(static_cast<float>(opt.y_up ? o.y : o.z));
    }
    if (!datums.empty()) {
        std::sort(datums.begin(), datums.end());
        std::vector<float> uniq;
        for (float v : datums) {
            if (uniq.empty() || std::abs(v - uniq.back()) > 0.01f) uniq.push_back(v);
        }
        datums = std::move(uniq);
    }

    std::vector<Bounds> collision_boxes;
    std::vector<WalkVertex> walk_verts;
    if (opt.emit_collision_and_walk) {
        const Vec3 world_up = opt.y_up ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
        const double walk_cos = std::cos(35.0 * std::numbers::pi / 180.0);
        for (const Built& b : meshes) {
            const std::string& sname = doc::slot_name(b.material);
            if (is_non_walkable_slot(sname)) continue;
            for (std::size_t ii = 0; ii < b.instance_boxes.size(); ++ii) {
                const Aabb& ab = b.instance_boxes[ii];
                const Vec3 ext = ab.extent();
                if (std::max({ext.x, ext.y, ext.z}) >= 0.04) {
                    collision_boxes.push_back(to_bounds(ab));
                }
                const InstanceTransform& it = b.instances[ii];
                auto xform = [&](Vec3 p) -> Vec3 {
                    return {it.m[0][0] * p.x + it.m[0][1] * p.y + it.m[0][2] * p.z + it.m[0][3],
                            it.m[1][0] * p.x + it.m[1][1] * p.y + it.m[1][2] * p.z + it.m[1][3],
                            it.m[2][0] * p.x + it.m[2][1] * p.y + it.m[2][2] * p.z + it.m[2][3]};
                };
                for (std::size_t k = 0; k + 2 < b.idx.size(); k += 3) {
                    const Vec3 p0 = xform(b.pos[b.idx[k]]);
                    const Vec3 p1 = xform(b.pos[b.idx[k + 1]]);
                    const Vec3 p2 = xform(b.pos[b.idx[k + 2]]);
                    const Vec3 cr = cross(p1 - p0, p2 - p0);
                    const double area2 = length(cr);
                    if (area2 < 0.04) continue;  // area < 0.02 m^2
                    if (dot(cr / area2, world_up) >= walk_cos) {
                        walk_verts.push_back({static_cast<float>(p0.x), static_cast<float>(p0.y), static_cast<float>(p0.z)});
                        walk_verts.push_back({static_cast<float>(p1.x), static_cast<float>(p1.y), static_cast<float>(p1.z)});
                        walk_verts.push_back({static_cast<float>(p2.x), static_cast<float>(p2.y), static_cast<float>(p2.z)});
                    }
                }
            }
        }
    }
    st.ports = port_descs.size();
    st.deck_datums = datums.size();
    st.collision_boxes = collision_boxes.size();
    st.walk_triangles = walk_verts.size() / 3;
    if (!port_descs.empty()) sections.push_back({kToolPorts, sizeof(PortDesc), port_descs.data(), port_descs.size() * sizeof(PortDesc), port_descs.size()});
    if (!datums.empty()) sections.push_back({kToolDeckDatums, sizeof(float), datums.data(), datums.size() * sizeof(float), datums.size()});
    if (!collision_boxes.empty()) sections.push_back({kToolCollisionBoxes, sizeof(Bounds), collision_boxes.data(), collision_boxes.size() * sizeof(Bounds), collision_boxes.size()});
    if (!walk_verts.empty()) sections.push_back({kToolWalkTriangles, sizeof(WalkVertex), walk_verts.data(), walk_verts.size() * sizeof(WalkVertex), walk_verts.size()});
    auto align = [](std::size_t v) { return (v + kAlign - 1) & ~static_cast<std::size_t>(kAlign - 1); };
    std::size_t cursor = align(sizeof(PackHeader) + sections.size() * sizeof(SectionEntry));
    std::vector<SectionEntry> table;
    for (const Section& s : sections) {
        table.push_back({s.kind, 0, s.element, static_cast<u32>(cursor), static_cast<u32>(s.bytes), static_cast<u32>(s.bytes), static_cast<u32>(s.count)});
        cursor = align(cursor + s.bytes);
    }
    if (cursor > 0xFFFFFFFFull) throw std::runtime_error("emesh: pack exceeds 4 GiB");
    std::vector<u8> out(cursor, 0);
    PackHeader h{};
    h.magic = kMagic;
    h.versionMajor = 1;
    h.versionMinor = 3;  // 1.2: material slot names. 1.3: instance tints, two-sided meshes (additive; 1.0 content is unchanged)
    h.flags = (st.index32 ? kIndex32 : 0u) | kHasInstanceBounds;
    h.fileSize = static_cast<u32>(cursor);
    h.sectionCount = static_cast<u32>(sections.size());
    h.sectionTableOffset = sizeof(PackHeader);
    h.meshCount = static_cast<u32>(mesh_descs.size());
    h.submeshCount = static_cast<u32>(submeshes.size());
    h.vertexCount = static_cast<u32>(positions.size());
    h.indexCount = static_cast<u32>(indices.size());
    h.batchCount = static_cast<u32>(batches.size());
    h.instanceCount = static_cast<u32>(transforms.size());
    for (int k = 0; k < 3; ++k) {
        h.boundsMin[k] = world.valid() ? static_cast<float>(world.lo[k]) : 0.0f;
        h.boundsMax[k] = world.valid() ? static_cast<float>(world.hi[k]) : 0.0f;
    }
    std::memcpy(out.data() + sizeof(PackHeader), table.data(), table.size() * sizeof(SectionEntry));
    for (std::size_t i = 0; i < sections.size(); ++i)
        if (sections[i].bytes) std::memcpy(out.data() + table[i].offset, sections[i].data, sections[i].bytes);
    h.contentHash = fnv1a64(out.data() + sizeof(PackHeader), out.size() - sizeof(PackHeader));
    std::memcpy(out.data(), &h, sizeof h);
    st.bytes = out.size();
    if (stats_out) *stats_out = st;
    return out;
}

Pack read(std::span<const u8> bytes) {
    auto bad = [](const std::string& what) -> void { throw std::runtime_error("emesh: " + what); };
    if (bytes.size() < sizeof(PackHeader)) bad("file shorter than its header");
    PackHeader h;
    std::memcpy(&h, bytes.data(), sizeof h);
    if (h.magic != kMagic) bad("bad magic");
    if (h.versionMajor != 1) bad("unsupported major version " + std::to_string(h.versionMajor));
    if (h.fileSize != bytes.size()) bad("fileSize does not match the buffer");
    if (h.sectionTableOffset + static_cast<std::size_t>(h.sectionCount) * sizeof(SectionEntry) > bytes.size()) bad("section table out of range");
    if (fnv1a64(bytes.data() + sizeof(PackHeader), bytes.size() - sizeof(PackHeader)) != h.contentHash) bad("content hash mismatch");

    std::map<u32, SectionEntry> sections;
    for (u32 i = 0; i < h.sectionCount; ++i) {
        SectionEntry e;
        std::memcpy(&e, bytes.data() + h.sectionTableOffset + i * sizeof(SectionEntry), sizeof e);
        if (e.offset % kAlign != 0 || static_cast<std::size_t>(e.offset) + e.size > bytes.size()) bad("section " + std::to_string(e.kind) + " out of range or misaligned");
        if (e.kind < 0x8000 && e.codec != 0) bad("compressed sections are not supported");
        sections[e.kind] = e;
    }
    auto load = [&]<class T>(u32 kind, std::size_t expect_count, std::vector<T>& out) {
        const auto it = sections.find(kind);
        if (it == sections.end()) {
            if (expect_count) bad("missing section " + std::to_string(kind));
            return;
        }
        const SectionEntry& e = it->second;
        if (e.elementSize != sizeof(T) || e.count != expect_count || e.size != expect_count * sizeof(T)) bad("section " + std::to_string(kind) + " has an unexpected element size or count");
        out.resize(expect_count);
        if (expect_count) std::memcpy(out.data(), bytes.data() + e.offset, e.size);
    };
    std::vector<MeshDesc> meshes;
    std::vector<SubmeshDesc> submeshes;
    std::vector<VertexPosition> positions;
    std::vector<VertexAttributes> attributes;
    std::vector<BatchDesc> batches;
    std::vector<InstanceTransform> transforms;
    load(kMeshes, h.meshCount, meshes);
    load(kSubmeshes, h.submeshCount, submeshes);
    load(kPositions, h.vertexCount, positions);
    load(kAttributes, h.vertexCount, attributes);
    load(kBatches, h.batchCount, batches);
    load(kTransforms, h.instanceCount, transforms);

    Pack p;
    const bool index32 = (h.flags & kIndex32) != 0;
    if (h.indexCount) {
        const auto it = sections.find(kIndices);
        if (it == sections.end() || it->second.count != h.indexCount || it->second.size != h.indexCount * (index32 ? 4u : 2u)) bad("index section has an unexpected size");
        p.indices.resize(h.indexCount);
        const u8* src = bytes.data() + it->second.offset;
        for (u32 i = 0; i < h.indexCount; ++i) {
            if (index32) std::memcpy(&p.indices[i], src + i * 4u, 4);
            else { u16 v; std::memcpy(&v, src + i * 2u, 2); p.indices[i] = v; }
        }
    }
    p.positions.assign(h.vertexCount, Vec3{});
    p.normals.assign(h.vertexCount, Vec3{0, 0, 1});
    for (const MeshDesc& d : meshes) {
        if (d.partCount == 0 || d.lodCount == 0 || d.firstSubmesh + static_cast<std::size_t>(d.partCount) * d.lodCount > submeshes.size()) bad("mesh submesh range out of bounds");
        const Vec3 lo{d.boundsMin[0], d.boundsMin[1], d.boundsMin[2]}, hi{d.boundsMax[0], d.boundsMax[1], d.boundsMax[2]};
        for (u32 part = 0; part < d.partCount; ++part) {
            const SubmeshDesc& s0 = submeshes[d.firstSubmesh + part];
            Pack::Mesh pm{d.contentKey, lo, hi, s0.material, s0.firstIndex, s0.indexCount, static_cast<u32>(s0.baseVertex),
                          {d.uvMin[0], d.uvMin[1]}, {d.uvScale[0], d.uvScale[1]}, (d.flags & kMeshTwoSided) != 0,
                          d.lodCount, {d.lodDistance[0], d.lodDistance[1], d.lodDistance[2], d.lodDistance[3]}, {}};
            pm.lods.reserve(d.lodCount);
            for (u32 lod = 0; lod < d.lodCount; ++lod) {
                const SubmeshDesc& s = submeshes[d.firstSubmesh + lod * d.partCount + part];
                if (static_cast<std::size_t>(s.firstIndex) + s.indexCount > p.indices.size()) bad("submesh index range out of bounds");
                pm.lods.push_back({s.firstIndex, s.indexCount, static_cast<u32>(s.baseVertex)});
                for (u32 i = 0; i < s.indexCount; ++i) {
                    const std::size_t v = static_cast<std::size_t>(s.baseVertex) + p.indices[s.firstIndex + i];
                    if (v >= positions.size()) bad("vertex index out of bounds");
                    const VertexPosition q = positions[v];
                    p.positions[v] = {lo.x + q.x / 65535.0 * (hi.x - lo.x), lo.y + q.y / 65535.0 * (hi.y - lo.y), lo.z + q.z / 65535.0 * (hi.z - lo.z)};
                    p.normals[v] = decode_normal(attributes[v].normal);
                }
            }
            p.meshes.push_back(std::move(pm));
        }
    }
    // Batches refer to meshes; expand multi-part meshes into one batch per part.
    std::vector<u32> first_part(meshes.size() + 1, 0);
    for (std::size_t i = 0; i < meshes.size(); ++i) first_part[i + 1] = first_part[i] + meshes[i].partCount;
    for (const BatchDesc& b : batches) {
        if (b.mesh >= meshes.size() || static_cast<std::size_t>(b.firstInstance) + b.instanceCount > transforms.size()) bad("batch out of range");
        for (u32 part = first_part[b.mesh]; part < first_part[b.mesh + 1]; ++part) p.batches.push_back({part, b.firstInstance, b.instanceCount});
    }
    for (const InstanceTransform& t : transforms) {
        std::array<float, 12> m{};
        std::memcpy(m.data(), t.m, sizeof t.m);
        p.transforms.push_back(m);
    }
    p.lo = {h.boundsMin[0], h.boundsMin[1], h.boundsMin[2]};
    p.hi = {h.boundsMax[0], h.boundsMax[1], h.boundsMax[2]};
    if (const auto it = sections.count(kInstanceTints) ? sections.find(kInstanceTints) : sections.find(kToolInstanceTints); it != sections.end()) {
        if (it->second.size != p.transforms.size() * 4) bad("instance tint section does not match the instance count");
        p.tints.resize(p.transforms.size());
        std::memcpy(p.tints.data(), bytes.data() + it->second.offset, it->second.size);
    }
    if (const auto it = sections.find(kToolPorts); it != sections.end()) {
        if (it->second.elementSize != sizeof(PortDesc) || it->second.size % sizeof(PortDesc) != 0) bad("ports section has an unexpected row size");
        const std::size_t n = it->second.size / sizeof(PortDesc);
        p.ports.reserve(n);
        for (std::size_t i = 0; i < n; ++i) {
            PortDesc pd;
            std::memcpy(&pd, bytes.data() + it->second.offset + i * sizeof(PortDesc), sizeof(PortDesc));
            p.ports.push_back({{pd.origin[0], pd.origin[1], pd.origin[2]}, {pd.normal[0], pd.normal[1], pd.normal[2]},
                               {pd.up[0], pd.up[1], pd.up[2]}, pd.kind, pd.owner, {pd.params[0], pd.params[1]}});
        }
    }
    if (const auto it = sections.find(kToolDeckDatums); it != sections.end()) {
        if (it->second.elementSize != sizeof(float) || it->second.size % sizeof(float) != 0) bad("deck datums section has an unexpected size");
        const std::size_t n = it->second.size / sizeof(float);
        p.deck_datums.resize(n);
        if (n) std::memcpy(p.deck_datums.data(), bytes.data() + it->second.offset, it->second.size);
    }
    if (const auto it = sections.find(kToolCollisionBoxes); it != sections.end()) {
        if (it->second.elementSize != sizeof(Bounds) || it->second.size % sizeof(Bounds) != 0) bad("collision boxes section has an unexpected size");
        const std::size_t n = it->second.size / sizeof(Bounds);
        p.collision_boxes.reserve(n);
        for (std::size_t i = 0; i < n; ++i) {
            Bounds cb;
            std::memcpy(&cb, bytes.data() + it->second.offset + i * sizeof(Bounds), sizeof(Bounds));
            p.collision_boxes.push_back({{cb.center[0], cb.center[1], cb.center[2]}, {cb.extent[0], cb.extent[1], cb.extent[2]}});
        }
    }
    if (const auto it = sections.find(kToolWalkTriangles); it != sections.end()) {
        if (it->second.elementSize != sizeof(WalkVertex) || it->second.size % (3 * sizeof(WalkVertex)) != 0)
            bad("walk triangles section has an unexpected size");
        const std::size_t n = it->second.size / (3 * sizeof(WalkVertex));
        p.walk_triangles.reserve(n);
        for (std::size_t i = 0; i < n; ++i) {
            WalkVertex wv[3];
            std::memcpy(wv, bytes.data() + it->second.offset + i * 3 * sizeof(WalkVertex), sizeof(wv));
            p.walk_triangles.push_back({Vec3{wv[0].x, wv[0].y, wv[0].z}, Vec3{wv[1].x, wv[1].y, wv[1].z}, Vec3{wv[2].x, wv[2].y, wv[2].z}});
        }
    }
    if (const auto it = sections.find(kToolDetails); it != sections.end()) {
        if (it->second.elementSize != sizeof(DetailRow) || it->second.size % sizeof(DetailRow) != 0) bad("detail section has an unexpected row size");
        // Rows name meshes of the mesh section; `p.meshes` has one entry per part, so map through.
        std::vector<u32> first_of(meshes.size() + 1, 0);
        for (std::size_t i = 0; i < meshes.size(); ++i) first_of[i + 1] = first_of[i] + meshes[i].partCount;
        for (std::size_t i = 0; i < it->second.size / sizeof(DetailRow); ++i) {
            DetailRow row;
            std::memcpy(&row, bytes.data() + it->second.offset + i * sizeof(DetailRow), sizeof row);
            if (row.mesh >= meshes.size() || row.shape > 5 || row.profile > 4) bad("detail row out of range");
            doc::Detail d;
            d.shape = static_cast<doc::Detail::Shape>(row.shape);
            d.profile = static_cast<doc::Detail::Profile>(row.profile);
            d.material = row.slot;
            d.origin = {row.origin[0], row.origin[1], row.origin[2]};
            d.normal = {row.normal[0], row.normal[1], row.normal[2]};
            d.tangent = {row.tangent[0], row.tangent[1], row.tangent[2]};
            d.end = {row.end[0], row.end[1], row.end[2]};
            d.size_a = row.sizeA;
            d.size_b = row.sizeB;
            d.size_c = row.sizeC;
            d.height = row.height;
            d.fillet = row.fillet;
            p.details.push_back({first_of[row.mesh], d});
        }
    }
    if (const auto it = sections.find(kMaterialSlots); it != sections.end()) {
        if (it->second.size % sizeof(MaterialSlot) != 0) bad("material slot section has a partial row");
        for (std::size_t i = 0; i < it->second.size / sizeof(MaterialSlot); ++i) {
            MaterialSlot row;
            std::memcpy(&row, bytes.data() + it->second.offset + i * sizeof(MaterialSlot), sizeof row);
            p.material_slots.push_back({row.nameHash, row.slotId});
        }
    }
    if (const auto it = sections.find(kToolMetadata); it != sections.end()) {
        p.metadata.assign(reinterpret_cast<const char*>(bytes.data() + it->second.offset), it->second.size);
        try {
            const json::Value meta = json::parse(p.metadata);
            if (const json::Value* up = meta.find("up"); up && up->is_string()) p.y_up = up->string != "z";
            if (const json::Value* slots = meta.find("slots"))
                for (const json::Value& s : slots->array) {
                    const json::Value* name = s.find("name");
                    const json::Value* color = s.find("color");
                    p.slot_names.push_back(name && name->is_string() ? name->string : "");
                    p.slot_colors.push_back(color && color->array.size() == 3 ? Vec3{color->array[0].number, color->array[1].number, color->array[2].number}
                                                                              : Vec3{0.6, 0.6, 0.6});
                }
        } catch (const std::runtime_error&) {
            // metadata is advisory; a pack without it still previews in grey
        }
    }
    return p;
}

std::vector<u8> read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("emesh: cannot open " + path);
    return std::vector<u8>((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}
void write_file(const std::string& path, std::span<const u8> bytes) {
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("emesh: cannot write " + path);
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
}

}  // namespace pg::emesh
