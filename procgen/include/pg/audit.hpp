#pragma once
// Contact audit: do the parts of an evaluated document hold together?
//
// The old pipeline ray-tested every joint (a child socket must touch real parent surface within 3 mm).
// The modeler's modules are graphs, not joint lists, so the audit works on what they produce: every
// placed part is split into its solid islands (faces joined by shared positions), and two islands are in
// contact when one comes within `tolerance` of the other's surface or reaches inside it. A module should
// be one connected piece; every other component is a floating part or a gap at a seat.
//
// Samples are the island's vertices plus points along its edges every `edge_step`, so a bar passing
// through a plate is found even when none of its vertices lies in the plate. Inside is judged by the side
// of the nearest surface's normal, which assumes closed, outward-facing islands (the kits' check stage
// already requires that).
#include "pg/doc.hpp"
#include "pg/math.hpp"

#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace pg::doc {

struct ContactOptions {
    double tolerance = 0.003;         // contact gap, metres (the old seating rule)
    double edge_step = 0.01;          // spacing of samples along edges, metres
    std::size_t max_samples = 60000;  // per island; beyond this edges are sampled more coarsely
    bool check_dangling = false;      // flag elevated islands hanging from above over empty air
    bool compute_gaps = true;         // compute nearest main-component gap & delta vector for detached components
};

struct ContactIsland {
    std::size_t part = 0;         // index into Geometry::parts
    std::string source;           // pre-bake authoring node that built the island
    std::uint16_t material = 0;
    Aabb bounds;                  // world space
    std::size_t triangles = 0;
    int component = -1;
};

struct ComponentGap {
    double gap = 0.0;                 // minimum surface distance (m) from detached component to main component
    Vec3 delta_to_main{};             // world vector (main_pt - detached_pt) needed to seat the component
    std::string nearest_main_source;  // authoring node ID on the main component nearest to this component
};

struct ContactReport {
    std::vector<ContactIsland> islands;
    std::vector<std::size_t> component_triangles;  // per component, indexed by ContactIsland::component
    std::map<int, ComponentGap> detached_gaps;     // populated for every component != main_component
    std::vector<std::size_t> dangling;             // indices into `islands` hanging from above over empty air
    int components = 0;
    int main_component = -1;                       // the component with the most triangles
    std::size_t pairs_tested = 0;
};

[[nodiscard]] ContactReport audit_contacts(const Geometry& g, const ContactOptions& options = {});

}  // namespace pg::doc
