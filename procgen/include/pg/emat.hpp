#pragma once
// Engine material packs (.emat, EMAT 1.0): the untextured subset.
//
// The engine binds materials to meshes by slot name (see emesh.hpp,
// MaterialSlots). This writer produces a small pack with one flat material per
// slot the modeler uses, so its mesh packs draw with the intended colour,
// metalness, roughness, opacity and emission before any textures exist. The
// layout follows engine/asset/include/eng/asset/material_format.hpp; texture
// arrays are left out (every layer index is "none").

#include "pg/math.hpp"

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace pg::emat {

struct Material {
    std::string name;
    Vec3 base_color{0.6, 0.6, 0.6};  // linear RGB
    double opacity = 1.0;
    Vec3 emissive{0, 0, 0};          // linear RGB
    double emissive_strength = 0.0;
    double metallic = 0.0, roughness = 0.7;
    double scattering = 0.0;         // light coming through from behind (leaves, opal glass)
    double tiling = 1.0;             // repeats per metre, for when textures arrive
    bool blend = false;              // glass, water: drawn after the opaque pass
    bool cutout = false;
    bool double_sided = false;
    bool tintable = false;           // the per-instance tint multiplies the base colour
};

struct Binding {
    std::string slot;        // slot name as meshes publish it
    std::uint32_t material;  // index into Set::materials
};

struct Set {
    std::vector<Material> materials;  // materials[0] is the default for unknown slot names
    std::vector<Binding> bindings;
};

// One material per slot name known to the modeler, classed by what the slot is
// (stone, painted iron, bare metal, glass, emissive glass, foliage, water).
[[nodiscard]] Set modeler_slots();

// Bytes of an EMAT 1.0 pack holding the set (no textures).
[[nodiscard]] std::vector<std::uint8_t> write(const Set& set);
void write_file(const std::string& path, const Set& set);

// What a pack holds, read back for checks.
struct Info {
    std::uint32_t materials = 0, slots = 0, texture_arrays = 0;
    std::vector<std::string> material_names;
    std::vector<std::uint64_t> slot_hashes;
    std::vector<std::uint32_t> slot_materials;
};
[[nodiscard]] Info read_info(std::span<const std::uint8_t> bytes);  // validates header, sections and hash

}  // namespace pg::emat
