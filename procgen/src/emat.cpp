// Untextured EMAT 1.0 writer. Layout: engine/asset/include/eng/asset/material_format.hpp.
#include "pg/emat.hpp"

#include "pg/doc.hpp"
#include "pg/emesh.hpp"

#include <bit>
#include <cstring>
#include <fstream>
#include <map>
#include <stdexcept>

namespace pg::emat {
namespace {

using u8 = std::uint8_t;
using u16 = std::uint16_t;
using u32 = std::uint32_t;
using u64 = std::uint64_t;
static_assert(std::endian::native == std::endian::little, "EMAT is little-endian");

constexpr u32 kMagic = 0x54414D45u;  // "EMAT"
constexpr std::size_t kAlign = 16;
enum : u32 { kMaterials = 1, kSlots = 2, kStrings = 3 };
enum : u32 { kCutout = 1u << 0, kBlend = 1u << 1, kDoubleSided = 1u << 2, kTintable = 1u << 3 };

struct Header {
    u32 magic;
    u16 versionMajor, versionMinor;
    u32 flags, fileSize, sectionCount, sectionTableOffset, materialCount, slotCount, textureArrayCount, reserved;
    u64 contentHash;
};
static_assert(sizeof(Header) == 48);
struct SectionEntry {
    u32 kind;
    u16 codec, elementSize;
    u32 offset, size, rawSize, count;
};
static_assert(sizeof(SectionEntry) == 24);
struct MaterialDesc {
    float baseColor[4], emissive[3], emissiveStrength, metallic, roughness, normalScale, alphaCutoff, tiling, detailTiling, detailStrength, scattering;
    std::int32_t baseColorLayer, normalLayer, ormLayer, emissiveLayer, detailNormalLayer, detailAlbedoLayer;
    u32 flags, nameOffset;
};
static_assert(sizeof(MaterialDesc) == 96);
struct SlotBinding {
    u64 nameHash;
    u32 material, nameOffset;
};
static_assert(sizeof(SlotBinding) == 16);

u64 fnv1a64(const u8* data, std::size_t size) {
    u64 hash = 0xCBF29CE484222325ull;
    for (std::size_t i = 0; i < size; ++i) hash = (hash ^ data[i]) * 0x100000001B3ull;
    return hash;
}
[[noreturn]] void bad(const std::string& what) { throw std::runtime_error("emat: " + what); }

// What a slot is made of, by name. Colour comes from the slot table.
struct Class {
    double metallic, roughness, opacity, emissive, scattering;
    bool blend, double_sided, tintable;
};
Class class_of(const std::string& n) {
    auto has = [&](const char* s) { return n.find(s) != std::string::npos; };
    if (n == "glass" || has("glazing")) return {0, 0.05, 0.28, 0, 0, true, false, false};
    if (n == "water") return {0, 0.06, 0.62, 0, 0, true, false, false};
    if (n == "opal") return {0, 0.45, 1, 3.5, 0.6, false, false, false};          // lamp glass: lit from inside
    if (n == "leaf" || n == "grass") return {0, 0.6, 1, 0, 0.5, false, true, true};
    if (n == "bronze") return {1, 0.38, 1, 0, 0, false, false, false};
    if (n == "copper") return {1, 0.42, 1, 0, 0, false, false, false};
    if (n == "slate") return {0, 0.6, 1, 0, 0, false, false, false};
    if (n == "steel" || has("nickel")) return {1, 0.3, 1, 0, 0, false, false, false};
    if (has("boiler-steel") || has("wheel-steel")) return {1, 0.45, 1, 0, 0, false, false, false};
    if (has("zinc")) return {0.9, 0.5, 1, 0, 0, false, false, false};
    if (n == "weathered-iron" || n == "hangar-iron") return {0.55, 0.8, 1, 0, 0, false, false, false};   // rusted: part oxide
    if (has("lacquer")) return {0, 0.22, 1, 0, 0, false, false, true};
    if (n == "iron" || n == "black" || n == "patina") return {0, 0.5, 1, 0, 0, false, false, true};    // painted or patinated metal
    if (n == "wood") return {0, 0.75, 1, 0, 0, false, false, true};
    if (n == "marking") return {0, 0.6, 1, 0, 0, false, false, false};                                 // paint mark: keeps its own colour
    return {0, 0.85, 1, 0, 0, false, false, true};                                                     // stone, brick, tile, earth, ballast
}

}  // namespace

Set modeler_slots() {
    Set set;
    set.materials.push_back(Material{.name = "default"});
    for (std::uint16_t id = 0; id < doc::slot_count(); ++id) {
        const std::string& name = doc::slot_name(id);
        const Class c = class_of(name);
        Material m;
        m.name = name;
        m.base_color = doc::slot_color(id);
        m.opacity = c.opacity;
        m.metallic = c.metallic;
        m.roughness = c.roughness;
        m.scattering = c.scattering;
        m.blend = c.blend;
        m.double_sided = c.double_sided;
        m.tintable = c.tintable;
        if (c.emissive > 0) {
            m.emissive = m.base_color;
            m.emissive_strength = c.emissive;
        }
        set.bindings.push_back({name, static_cast<u32>(set.materials.size())});
        set.materials.push_back(std::move(m));
    }
    // Names other producers use for the same thing bind to the same material, so citygen's
    // content and the engine's starter names draw from this library too.
    const std::pair<const char*, const char*> aliases[] = {{"masonry", "weathered-brick"}, {"brass", "bronze"}, {"lamp", "opal"}};
    for (const auto& [alias, target] : aliases)
        for (const Binding& b : std::vector<Binding>(set.bindings))
            if (b.slot == target) set.bindings.push_back({alias, b.material});
    return set;
}

std::vector<u8> write(const Set& set) {
    if (set.materials.empty()) bad("a pack needs at least the default material");
    std::string strings(1, '\0');  // offset 0 is the empty name
    std::map<std::string, u32> interned;
    auto intern = [&](const std::string& s) {
        if (s.empty()) return u32{0};
        const auto [it, fresh] = interned.try_emplace(s, static_cast<u32>(strings.size()));
        if (fresh) strings.append(s).push_back('\0');
        return it->second;
    };
    std::vector<MaterialDesc> materials;
    for (const Material& m : set.materials) {
        MaterialDesc d{};
        const auto f = [](double v) { return static_cast<float>(v); };
        d.baseColor[0] = f(m.base_color.x); d.baseColor[1] = f(m.base_color.y); d.baseColor[2] = f(m.base_color.z); d.baseColor[3] = f(m.opacity);
        d.emissive[0] = f(m.emissive.x); d.emissive[1] = f(m.emissive.y); d.emissive[2] = f(m.emissive.z);
        d.emissiveStrength = f(m.emissive_strength);
        d.metallic = f(m.metallic);
        d.roughness = f(m.roughness);
        d.normalScale = 1.0f;
        d.alphaCutoff = 0.5f;
        d.tiling = f(m.tiling);
        d.detailTiling = 0;
        d.detailStrength = 0;
        d.scattering = f(m.scattering);
        d.baseColorLayer = d.normalLayer = d.ormLayer = d.emissiveLayer = d.detailNormalLayer = d.detailAlbedoLayer = -1;
        d.flags = (m.cutout ? kCutout : 0u) | (m.blend ? kBlend : 0u) | (m.double_sided ? kDoubleSided : 0u) | (m.tintable ? kTintable : 0u);
        d.nameOffset = intern(m.name);
        materials.push_back(d);
    }
    std::vector<SlotBinding> slots;
    std::map<u64, std::string> seen;
    for (const Binding& b : set.bindings) {
        if (b.material >= set.materials.size()) bad("slot " + b.slot + " names a material that does not exist");
        const u64 hash = emesh::slot_name_hash(b.slot);
        if (!seen.emplace(hash, b.slot).second) bad("slot " + b.slot + " is bound twice (names are case-insensitive)");
        slots.push_back({hash, b.material, intern(b.slot)});
    }
    while (strings.size() % 4 != 0) strings.push_back('\0');

    struct Section { u32 kind; u16 element; const void* data; std::size_t bytes; std::size_t count; };
    const Section sections[] = {
        {kMaterials, sizeof(MaterialDesc), materials.data(), materials.size() * sizeof(MaterialDesc), materials.size()},
        {kSlots, sizeof(SlotBinding), slots.data(), slots.size() * sizeof(SlotBinding), slots.size()},
        {kStrings, 1, strings.data(), strings.size(), strings.size()},
    };
    auto align = [](std::size_t v) { return (v + kAlign - 1) & ~(kAlign - 1); };
    std::size_t cursor = align(sizeof(Header) + std::size(sections) * sizeof(SectionEntry));
    std::vector<SectionEntry> table;
    for (const Section& s : sections) {
        table.push_back({s.kind, 0, s.element, static_cast<u32>(cursor), static_cast<u32>(s.bytes), static_cast<u32>(s.bytes), static_cast<u32>(s.count)});
        cursor = align(cursor + s.bytes);
    }
    std::vector<u8> out(cursor, 0);
    Header h{};
    h.magic = kMagic;
    h.versionMajor = 1;
    h.versionMinor = 0;
    h.fileSize = static_cast<u32>(cursor);
    h.sectionCount = static_cast<u32>(std::size(sections));
    h.sectionTableOffset = sizeof(Header);
    h.materialCount = static_cast<u32>(materials.size());
    h.slotCount = static_cast<u32>(slots.size());
    std::memcpy(out.data() + sizeof(Header), table.data(), table.size() * sizeof(SectionEntry));
    for (std::size_t i = 0; i < std::size(sections); ++i)
        if (sections[i].bytes) std::memcpy(out.data() + table[i].offset, sections[i].data, sections[i].bytes);
    h.contentHash = fnv1a64(out.data() + sizeof(Header), out.size() - sizeof(Header));
    std::memcpy(out.data(), &h, sizeof h);
    return out;
}

void write_file(const std::string& path, const Set& set) {
    const std::vector<u8> bytes = write(set);
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (!out) bad("cannot write " + path);
}

Info read_info(std::span<const u8> bytes) {
    if (bytes.size() < sizeof(Header)) bad("file shorter than its header");
    Header h;
    std::memcpy(&h, bytes.data(), sizeof h);
    if (h.magic != kMagic) bad("bad magic");
    if (h.versionMajor != 1) bad("unsupported major version");
    if (h.fileSize != bytes.size()) bad("fileSize does not match the buffer");
    if (h.sectionTableOffset + static_cast<std::size_t>(h.sectionCount) * sizeof(SectionEntry) > bytes.size()) bad("section table out of range");
    if (fnv1a64(bytes.data() + sizeof(Header), bytes.size() - sizeof(Header)) != h.contentHash) bad("content hash mismatch");
    Info info;
    info.materials = h.materialCount;
    info.slots = h.slotCount;
    info.texture_arrays = h.textureArrayCount;
    std::span<const u8> strings;
    std::vector<MaterialDesc> materials;
    for (u32 i = 0; i < h.sectionCount; ++i) {
        SectionEntry e;
        std::memcpy(&e, bytes.data() + h.sectionTableOffset + i * sizeof(SectionEntry), sizeof e);
        if (e.offset % kAlign != 0 || static_cast<std::size_t>(e.offset) + e.size > bytes.size()) bad("section out of range or misaligned");
        if (e.kind == kStrings) strings = bytes.subspan(e.offset, e.size);
        if (e.kind == kMaterials) {
            if (e.elementSize != sizeof(MaterialDesc) || e.count != h.materialCount || e.size != e.count * sizeof(MaterialDesc)) bad("material section has an unexpected size");
            materials.resize(e.count);
            std::memcpy(materials.data(), bytes.data() + e.offset, e.size);
        }
        if (e.kind == kSlots) {
            if (e.elementSize != sizeof(SlotBinding) || e.count != h.slotCount || e.size != e.count * sizeof(SlotBinding)) bad("slot section has an unexpected size");
            for (u32 k = 0; k < e.count; ++k) {
                SlotBinding b;
                std::memcpy(&b, bytes.data() + e.offset + k * sizeof(SlotBinding), sizeof b);
                if (b.material >= h.materialCount) bad("slot binding names a material that does not exist");
                info.slot_hashes.push_back(b.nameHash);
                info.slot_materials.push_back(b.material);
            }
        }
    }
    for (const MaterialDesc& d : materials) {
        if (d.nameOffset >= strings.size()) bad("material name out of range");
        info.material_names.emplace_back(reinterpret_cast<const char*>(strings.data() + d.nameOffset));
    }
    return info;
}

}  // namespace pg::emat
