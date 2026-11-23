# Tool gaps: the modeler against the old pipeline and Blender geometry nodes

2026-11-15, Opus 5.5 (medium). The inventory of the old Swift pipeline (section 2) was compiled by a Sonnet 5.5 subagent
from `GEOMETRY_NODES.md`, `Sources/` and `Agent/` in the main checkout; two of its claims were spot-checked (the
`verifyAssembly` contact audit in `Sources/Assembly.swift`, the form switch in `PlantAssembly.swift`). Some statuses
come from docs rather than a full read of the Swift. The Blender comparison is against geometry nodes 4.x.

## 1. What to build, in order

1. **Seating and contact audit** — BUILT 2026-11-15 as `pgm audit` / `pgm_audit` and the gate's contact audit stage (islands, 3 mm contact or inside, control sweep, baseline of known findings in `tests/data/audit-baseline.json`). Not yet: fastener bore and washer checks, tool access, installation stages. (`pgm audit`, and a gate stage). The old kits passed every joint through a ray test:
   each child socket must touch real parent surface within 3 mm, washers sit on their plate, bolts run on their bore
   (`verifyAssembly`, `geometry_probe`, `geometry_seam_check`). Nothing checks this today, so a floating part or a gap
   at a seat passes the gate. The search tree (`pg/closest.hpp`) is the building block.
2. **Engine metadata in packs and `pgm assemble`**: ports, collision boxes, walk triangles and deck datums (the engine
   already reads 0x8002, 0x8004, 0x8005), then building one pack from citygen's placement list, with a contact audit
   between placements.
3. **Selections**: a face or edge set chosen by normal, box, material or index, accepted by extrude, inset, delete,
   material and chamfer. This closes the biggest gap to Blender (fields and selections) and the old `edgeBevel` by
   vertex pair, without a full attribute system.
4. **Instancing and sampling**: pick one of several sources per point (by index or seeded random), and a
   nearest-surface / raycast node to drop parts onto a terrain, hull or roof and to read sizes back.
5. **Curve tools**: fillet, offset, trim, reverse, sample by arc length, and sweeping a profile with per-point radius
   and tilt.
6. **Generators the old kits had**: plants (29 forms with stage, datum and seed; the street tree needs it now),
   luminaires with mounts, railings along a path with end posts, stairs with landings, voussoir arches, flared and
   hipped roofs.
7. **Review tools**: depth, normal and part-id render passes, which part is at a pixel, section cuts, and fitting a
   camera to a reference image to compare against a reference plate.
8. Lower: text as geometry, SDF / volume modelling, UV unwrap and packing, LOD chains, physics (pendants,
   mechanisms), service networks and netlists, structural support graphs, parts schedules.

## Against Blender geometry nodes (4.x)

| Blender area | Blender nodes | pgm today | Gap |
|---|---|---|---|
| Fields and attributes | Named / Store / Capture Attribute, Index, Position, Normal, ID, selections on every op, Evaluate at Index / on Domain, Accumulate Field, Blur Attribute | Expressions with `@x @y @z @index @t @u @v` inside a few nodes (instance options, curve, patch, line) | No per-element data: no named attributes, no selections (every op takes the whole mesh), no Set Position by field. The biggest structural gap. |
| Flow | Repeat Zone, For Each Element Zone, Simulation Zone, Switch / Index Switch / Menu Switch, Bake | `select` on a condition; loops live in the Python builders | No loops or per-element iteration inside a document; counts are fixed when the builder runs unless an array/line count is an expression. |
| Mesh primitives | Cube (subdivided), Cylinder, Cone, UV / Ico Sphere, Grid, Circle, Line | cube, cylinder, grid, revolve (spheres and cones by profile) | Cone and sphere only through `revolve`; no subdivided cube. Minor. |
| Mesh operations | Extrude Mesh (faces / edges / verts, individual, by selection), Scale Elements, Split Edges, Flip Faces, Triangulate, Dual Mesh, Mesh Boolean, Subdivide Mesh, Subdivision Surface (creases), Set Shade Smooth, Merge by Distance, Delete Geometry, Duplicate Elements, Convex Hull, Bounding Box, Separate Geometry | extrude (whole profile), boxBevel, boxBoolean, meshBoolean, chamfer, subdivide, weld, smoothNormals, displace, deform | No face/edge selection, so no selective extrude, inset, delete, split or per-face scale; no edge bevel with segments (edgeBevel listed as not built); no convex hull, bounding box, mirror. |
| Curves | Primitives (arc, circle, spiral, star, bezier, quadrilateral), Curve to Mesh with profile + radius + tilt, Fillet Curve, Trim, Resample, Reverse, Subdivide, Fill Curve, Curve to Points, Sample Curve, Curve Length, Set Radius / Tilt / Normal | line, curve (expression), spline, resample, outline, courses, tube, profileSweep, loft, surfaceBand | No fillet / trim / reverse / offset / fill of curves; no per-point radius or tilt when sweeping; no sample-at-length (place things along a curve by arc length) except via detail rows. |
| Instances | Instance on Points (pick from a collection by index, rotation / scale fields, selection), Realize Instances, Rotate / Scale / Translate Instances, Instances to Points | instance (one source, rotation / scale / translation / tint per point), bake | Cannot choose among several sources per point (no "pick instance"); no per-instance random choice. |
| Points | Distribute Points on Faces (random / Poisson, density field), Points to Vertices, Points to Volume | scatter (Poisson, count, seed), pointArray, pointField | No density field or selection for scatter. |
| Sampling and queries | Raycast, Sample Nearest Surface, Proximity, Sample Index, Geometry Proximity, Attribute Statistic, Domain Size | ClosestIndex exists in C++ (used by details) but is not a node | No raycast / nearest-surface node to drop things onto a terrain or a hull, or to read a size back. |
| Volumes and SDF | Volume Cube, Mesh to Volume, Volume to Mesh, SDF grid boolean / offset / fillet (4.2+) | none (details use SDF footprints, but only as shading) | No volumetric or SDF modelling: no smooth unions, no offset surfaces, no remeshing. |
| Text | String to Curves (fonts, alignment, kerning) then Fill Curve / Curve to Mesh | detailText (single-stroke capitals and figures, shading only) | No text as geometry; no lower case or serif. |
| UVs and materials | UV Unwrap, Pack UV Islands, Set Material by selection, Material Index | construction UVs on lathe / tube / patch / sweep, planarUV, material (whole input) | No unwrap or packing; no material by selection (one slot per input). |
| Texture fields | Noise, Voronoi, Wave, Brick, Checker, Gradient, White Noise, Float Curve, Color Ramp, Map Range, Random Value | displace (fBm), heightfield noise | No Voronoi / brick / wave fields to drive positions, scale or selection. |
| Groups and interface | Node groups with typed sockets, modifier inputs, Viewer node | modules with controls (min / max / integer), group with bindings, kit imports, typed ports and connect | pgm is ahead on assembly: typed ports, seating, kit imports, provenance, gates. Behind on a viewer / inspect-a-node-output tool. |

### What pgm has that geometry nodes does not

Typed ports with seating and chains (`connect`, kit imports), a shading-level detail layer (rivets, seams, lettering, courses) on clean meshes, provenance records, per-module reviews, a control sweep, conformance receipts, and engine packs with tints and details.

## 2. Old pipeline inventory

Status key: HAS / PARTIAL / MISSING. C++ node list checked with `pgm nodes` (51 types); commands with `pgm help`
(evaluate inspect check materials catalog export render sheet nodes sweep conformance) plus `tools/pgm_mcp.py` (those + `kit_build`).
Caveat: A01-A04, E03, N02, I04 are detail-atlas record IDs (References/), not tools. "A0x" behaviours below are the checks that cited them.
Caveat: the old `Agent/geometry_api.py` and Swift docs are the spec; a few Swift-only items (Metal passes) were judged from docs, not read in full.

### 1. Geometry operations / node types
OLD spec: GEOMETRY_NODES.md "Model and review" table (L26-43) + Agent/geometry_api.py:92-101 (node option table).

| Tool | What | Where | C++ status | Note |
|---|---|---|---|---|
| cube/grid/line/cylinder | primitives, divisions | GEOMETRY_NODES.md:28-31 | HAS | `cube cylinder grid line` |
| surface (polys, holes) | explicit faces, nested hole loops | GEOMETRY_NODES.md:32,~85 | HAS | `surface`; GeometryPolygon.swift ear clip |
| extrude | XY polygon prism | Sources/GeometryExtrusion.swift | HAS | `extrude` |
| profileSolid (+holes, bevel, rimEdges) | stock profile with through-cuts, chamfered rims | GeometryProfileSolid.swift; GEOMETRY_NODES.md:~610 | HAS | `profileSolid` (rim chamfer semantics not verified) |
| sweep / tube | polyline pipe | GEOMETRY_NODES.md:33 | HAS | `sweep`, `tube` |
| profileSweep (caps, up) | XY profile along curve | GeometryNodeSpline.swift | PARTIAL | HAS node; NO per-station twist/scale in either; old also lacks it, so parity |
| spline / resample | catmullRom polyline, even resample | GeometryNodeSpline.swift | HAS | `spline resample curve outline` |
| transform / join / material / select / empty | basics; material `replace` filter | GeometryNodes.swift | HAS | `material` replace filter not checked |
| instance (@index @x @y @z @random, randomSeed) | per-point transforms | GEOMETRY_NODES.md:45-70 | HAS | `instance pointArray pointField` |
| group / groupInput / moduleImports | node groups, module registry import | GeometryNodeGroups.swift; ModuleLibrary | HAS | README kit format |
| connect (also, @node port, tolerance) | live port seating | GeometryConnect.swift; GEOMETRY_NODES.md:515 | HAS | `connect` node; "also" secondary ports unverified |
| weld / planarUV / smoothNormals | surface editing | GeometryWeld.swift; GEOMETRY_NODES.md:571 | HAS | present as nodes |
| boxBevel / boxBoolean | axis-aligned box ops | GeometryBoxEditing.swift | HAS | present (plus general `chamfer`, `meshBoolean`) |
| meshBoolean | closed-mesh union/diff/intersect, rotated tools | GeometrySolidCSG.swift | HAS | BSP in C++; README admits fragmenting on cut faces; exact predicates roadmap #7 |
| edgeBevel | chamfer chosen creases by vertex-pair, world width | GeometryEdgeBevel.swift | PARTIAL | `chamfer` bevels by dihedral threshold only; no pair selection; multi-segment/fillet absent in both; README roadmap #3 lists `edgeBevel` |
| surfaceBand | capped band along X-monotonic alignment | GeometrySurfaceBand.swift | HAS | `surfaceBand` |
| loft / revolve / patch / heightfield / courses / scatter / subdivide / deform / displace / detail* / fastener / rivetLine | NEW-only | C++ nodes | HAS (new) | the C++ side is ahead on these |
| Mirror / radial / linear / curve array | symmetry and arrays | not in OLD nodes either | MISSING (both) | done with `pointArray`/`instance` + expressions; no dedicated `mirror`/`radialArray` node |
| Inset, solidify (face ops) | face inset / shell | not in OLD | PARTIAL | `patch inset/thickness` only; no general face inset or solidify node |
| Text / Voronoi / UV unwrap beyond planar | | not in OLD | MISSING (both) | `detailText` exists for decals only |
| Per-prototype LOD | detail LOD meshes | Sources/AdaptiveDetail.swift; DETAIL_AND_WALK.md | MISSING | old = runtime tube/lathe refinement with GPU LOD lists; C++ writes one mesh per prototype, no LOD chain |
| Retained analytic tube centrelines/lathe profiles | curved-surface refinement | Sources/CurvedDetail.swift | MISSING | old kept mathematical profiles beside the mesh for close-up; C++ bakes triangles |
| Procedural material finishes (brick bond, limestone chip, iron oxide, POM) | world-aligned shader masks | GEOMETRY_NODES.md:418-444; Sources/*.metal | PARTIAL | C++ has `materials` slot table + `emat` pack; shading lives in the engine, not here |

### 2. Assembly tools
OLD specs: ASSEMBLY.md, GEOMETRY_NODES.md "Assembly probes" (L108-134), Agent/geometry_api.py:87 (ACTIONS tuple).

| Tool | What | Where | C++ status | Note |
|---|---|---|---|---|
| Typed ports (origin, normal, up, type, dimensions) | module interface | Recipes/Geometry/modules.json; GeometryModuleLibrary.swift | HAS | kitlib `port()`; `inspect` lists ports |
| geometry_connect (CLI/MCP) | solve rigid transform from port pair; type + dimension check | GeometryConnect.swift:~1; geometry_api.py | PARTIAL | node exists; no standalone `pgm connect` tool to query a pose or test a pair without authoring a doc |
| geometry_fit | compare two world port frames against system contracts | GEOMETRY_NODES.md:129-134; Recipes/assembly.systems.json | MISSING | contract (track gauge, drainage invert) is partly done by port `dimensions` mismatch |
| assembly_systems (typed system graph) | systems/links catalog, evidence records | Recipes/assembly.systems.json; Agent/assembly_systems.py | MISSING | C++ has port types and `catalog` import graph only |
| geometry_place (placed assembly doc) | modules + instances + connections, report fits by instance id | Sources/PlacedAssembly.swift | PARTIAL | `pgm assemble` is roadmap #2; citygen places, no C++ resolver of a placements file |
| geometry_probe (hit / clear / seat rays, up to 64) | ray gap, clearance corridor, seat distance | GeometryAssemblyProbes.swift; GEOMETRY_NODES.md:108 | MISSING | C++ `closest.hpp` BVH exists as a building block; no probe command |
| geometry_seam_check | cap-to-cap clipped area, signedGap, profile mismatch, `seamLimits` | GeometrySeam.swift; GEOMETRY_NODES.md:640-669 | MISSING | no cap-area seam audit between mated ports |
| geometry_mesh_check `placedIntersections` / `allowedContacts` | contact + containment between placements | GeometryMeshCheck.swift | PARTIAL | `check --intersections` is per-prototype; no placement-pair contact audit; README roadmap #4 lint (coincident instance) |
| geometry_batch_edit | up to 24 control edits, cache reuse, phase timings | geometry_batch_worker.py | PARTIAL | `sweep` covers control extremes; graph cache is content-hash; no batch-edit receipt |
| geometry_benchmark | evaluator time receipt | GEOMETRY_NODES.md:23 | PARTIAL | `bench/` dir exists, not a pgm verb |
| AssemblyJoint seating audit (ray-tests parent triangles within 3 mm) | every child socket must touch real parent surface | Sources/Assembly.swift:18-24; ASSEMBLY.md:19-29 | MISSING | the single most relied-on old check |
| Fastener bore / axis / washer-annulus contact | 4 rays per washer, bore centreline + wall ray | ASSEMBLY.md (verification layers 2-3); Machining.swift | MISSING | C++ `fastener` places hardware but nothing audits seating or bores |
| Permanent hardware envelope + tool access (58 mm radial, 200 mm reach, installation stages) | A02-style fastener exclusion/access | AssemblyRays.swift, AssemblySpatialIndex.swift; ASSEMBLY.md:13-17 | MISSING | tied to the "no rivets on seams" / reach rules in memory |
| Installation order DAG (masonry 0, framing 10, fastening 20 ... planting 60) + cycle detect | stages, tool envelopes copied through placement | ASSEMBLY.md:13-15; Recipes/README.md | MISSING | no stage metadata on instances |
| ManufacturingCut / DrilledPlate (A03 manufacturing difference) | drill grids cut once per prototype, remaining-material check | Sources/Machining.swift; ASSEMBLY.md:9 | PARTIAL | `meshBoolean`/`profileSolid holes` can cut; no named cut records or edge-distance rule |
| Structural support graph + bearing paths | load path from part to ground | Sources/StructuralSupport.swift, StructuralConnections.swift | MISSING | |
| Adversarial fixtures (floating washer, off-axis bolt, cycle must fail) | negative tests of the audit | Sources/RecipeChecks.swift; Agent/test_geometry*.py | MISSING | C++ has `tests/`; no negative-fixture suite for assembly rules |
| Parts schedule (counts by scale, bounds, cuts, materials) | bill of parts | Sources/DesignVariants.swift:1-30 | MISSING | `evaluate` gives counts and bounds only |
| Variant checks (genome ranges, 2 independent genomes) | variant sweep | Sources/VariantChecks.swift | HAS | `sweep` does both control ends and each variant |
| ArchitecturalNetlist (schematic pins resolved against instance pads) | connectivity independent of placement | Sources/ArchitecturalNetlist.swift | MISSING | |
| Service feeds / luminaire feeds (path from fixture head to mount terminal) | wiring, drainage, lighting network | LuminaireAssemblies.swift:10-16; ServiceKit.swift; NetworkChecks.swift; GreenhouseServiceAssembly.swift | MISSING | A04-style fault injection lived in the pendant/network verification (PendantVerification.swift) |
| Pendant suspension (A01) physics + fixed-length hanger rule | pendant drop, swing solver (native / MuJoCo) | PendantPhysics.swift, MuJoCoPendants.swift; Recipes/pendant-saddle.js | MISSING | `gh-pendant` module exists geometrically; no solver or suspension port |
| Mechanism physics (gears, constrained joints, swept glazing check) | clockwork, vents | MechanismPhysics.swift, VentMechanisms.swift | MISSING | |
| Collision boxes, walk triangles, deck datums in the pack | engine metadata | ENGINE_PACK_NOTES.md:10-18 (new spec); Sources/GroundWalk.swift, GantryWalk.swift, DomeTraversal.swift | MISSING | emesh.cpp writes only 0x8001/0x8006/0x8007; roadmap #1; deck ports exist as port kind only |
| Circulation graph / routes (route finding with width, headroom, 3-track sample) | passage checks | CirculationGraph.swift; geometry_route | MISSING | |
| Glazing crawler, deposit/weather exposure (sun, rain shelter, AO) | geometry_exposure | GlazingCrawler*.swift, WeatherExposure.swift | MISSING | engine-side concerns more than modeler ones |
| Sockets for scheduled mounts ("separately scheduled mounts") | mount points listed apart from the host | RooftopKit.swift roofBalustrade; Luminaire mount `prefix/mount` | PARTIAL | kitlib ports play the role; no schedule/stage separation |

### 3. Procedural generators with no C++ equivalent
| Generator | What | Where | C++ status | Note |
|---|---|---|---|---|
| PlantAssembly (29 forms: palm fan broad bloom cover pond grove fork curtain rootArch reeds orchard topiary spire stiltPalm treeFern wallVine ... ; juvenile/mature; size 0.35-15 m, count 3-30, seed; soil/waterline datum; organ tree with attachment paths) | genome -> organs (stem rachis blade fan flower bud pad fruit) | Sources/PlantAssembly.swift (1238 lines); VEGETATION.md; ASSEMBLY.md:47 | MISSING | only `recipes/site/street/tree.json` and `gh-planter`; nothing seeded/parametric by form |
| Vegetation population (moisture, light, soil-depth fields) | scatter plants by field | Sources/VegetationPopulation.swift | MISSING | C++ `scatter` is Poisson-disk without fields |
| Luminaires (botanical, column families, task, portable; bell/opal shade, ring, screw, emitters, feeds) | lamp kit with seats and feeds | Sources/LuminaireAssemblies.swift, LuminaireChecks.swift | MISSING | |
| Railings (assemblyGuardrail: shared/terminal/corner end posts, post spacing, mid-rail) | rail along a path | Sources/AssemblyStock.swift:28-60 | PARTIAL | `gh-railing` module exists; end-variant/spacing socket logic unverified |
| Stairs and landings (AssemblyStair: rise, run, treads, stringer tubes; landing) | | AssemblyStock.swift:4-27; EntranceStair.swift | PARTIAL | `island-stair-flight`, `quay-stair` exist |
| Casting families with control points (Bezier-like cast profiles, round castings w/ smooth radial normals) | lathe + spline castings | Sources/CurvedDetail.swift; Ligature.swift; SpurGear.swift | PARTIAL | `revolve` + `profileSweep` cover shapes; no cast-family generator or control-point editing |
| Islamic / girih pattern construction (I04) | pattern from construction lines | References/README.md (atlas entry only) | MISSING | nothing generating it in OLD Swift either; only atlas record |
| Leaded glass / came grids | | no Swift source found | MISSING (both) | `patch` panels with inset is the nearest |
| Voussoirs / arches (MasonryWindowBay: intrados, projecting voussoirs, drip) | arch courses | Sources/MasonryWindowBay.swift; GeometryProfileSolid | PARTIAL | `courses` + island/ashlar arch modules; no voussoir generator by name |
| Roofs (hipped flared crowns, ridge lights, conservatory roof, rooftop kit) | | Sources/RooftopKit.swift; Agent/conservatory_roof.py, build_samaris.py | PARTIAL | greenhouse roof-bay/rail-hall roofs exist; flared hip crowns, balustrades, tower caps do not (README roadmap #3) |
| Terrain / rock / shore / ground fog (LibreTerrain, RockFormations, GardenRocks, RockShore, SurfaceDeposits) | | Sources/Rock*.swift, GardenRocks.swift; LibreTerrain/ | PARTIAL | `heightfield`, `island-rock`; no strata/erosion/deposit generator |
| Gear / spur gear, mooring bollard, capstan, fairlead, marine fasteners | hardware | SpurGear.swift, MooringBollard.swift, DeckCapstan.swift, MarineFastener.swift | PARTIAL | bollard/capstan/fairlead modules already ported; SpurGear and MarineFastener family missing |
| Weapons, VFX, particles, FPS arms, weather, winter | | WeaponKit.swift, VFX*.swift | MISSING | out of modeler scope |
| Architectural flora (street trees, whorlB, gateTree, bankAvenue) | | PlantAssembly.swift:390-470 | MISSING | |
| Calvani / Mylos / Samaris / Hangar-Y / Nord-Midi / Brusel / La Tour / Utopie kits | ~50 legacy module documents | Recipes/Geometry/*.json; Agent/build_*.py | MISSING | C++ kits cover greenhouse, island, site; the legacy modules are not ported (old JSON is the same node format, so most could be fed to `pgm check` as is) |

### 4. Pipeline / tooling
| Tool | What | Where | C++ status | Note |
|---|---|---|---|---|
| geometry_catalog / modules | list modules + ports (query by id) | geometry_api.py:87 | PARTIAL | `catalog`, `inspect`; no `--query` filter |
| geometry_validate / evaluate / inspect | | | HAS | `evaluate inspect check` |
| geometry_render passes beauty/clay/falseColor/partID/depth/normals; views iso/opposite/front/side/top; pixel probe; fitted camera | | GeometryReview.swift/.metal; GEOMETRY_NODES.md:96 | PARTIAL | `render` has beauty/clay/parts and 5 views; no depth float32 / normals / pixel probe / fitted camera |
| geometry_export | OBJ with slot names | | HAS | `export --obj/--emesh` (plus engine pack, new) |
| geometry_section | cut-plane capture | Agent/geometry_section.py | MISSING | |
| geometry_drawing / drawing_check | dimensioned SVG/PNG, OCR label audit | GeometryDrawing.swift; GEOMETRY_NODES.md:186-229 | MISSING | |
| geometry_vision | contours, saliency, OCR, Core ML | GeometryVision.swift | MISSING | |
| geometry_match_camera / compare_drawing | fit camera to illustration, overlay edges | Agent/geometry_match_camera.py, geometry_compare_drawing.py | MISSING | useful for "match the reference plate" |
| geometry_reference_dimensions / candidate / fit / multi_fit | dimension proposals vs reference | Agent/geometry_reference*.py | PARTIAL | `conformance` compares against receipts |
| geometry_inspect_pixel | which part is at pixel | | MISSING | |
| geometry_module_record | shared module record from parts + ports | PlacedAssembly.swift | PARTIAL | `inspect` |
| geometry_service_assembly | greenhouse service build + checks | GreenhouseServiceAssembly.swift | MISSING | |
| Fixed-scene probes (greenhouse, shipyard native scenes) | | | n/a | scene-engine side |
| MCP tool surface | glasshouse_geometry action dispatcher; state dir Agent/state/geometry | Agent/mcp_server.py | PARTIAL | `pgm_*` tools cover 11 verbs + `kit_build` |
| Detail atlas / assembly_references / reviews | cite atlas IDs, per-module review notes | References/; assembly.references.json | HAS | `recipes/reviews.py`, `docs/KIT_REFERENCES.md` |
| Recipe JS (JavaScriptCore) runtime with kit-api.js | hot reload R, rollback boundary | Recipes/kit-api.js; RecipeRuntime.swift | PARTIAL | kitlib.py is the Python equivalent; no live reload/rollback |
| Level assembly | place kits into level, stages, views | Sources/LevelKit.swift, SceneRecipe.swift, LevelCapture | MISSING | citygen + planned `pgm assemble` |
| Engine pack metadata: ports, collision boxes, walk tris, deck datums, instances 1.1 | | ENGINE_PACK_NOTES.md; emesh.cpp | MISSING | roadmap #1 |

### Ranked list: MISSING and PARTIAL, by how much the old kits relied on them
1. Assembly seating audit: AssemblyJoint ray-test of child sockets on parent triangles (MISSING). Every foundry/greenhouse kit passed through it.
2. geometry_probe hit/clear/seat rays (MISSING) and seam_check cap-area audit (MISSING); the Nord-Midi, Hangar and Samaris joints were gated by them.
3. Placed-assembly contact/containment audit and `geometry_place` / `pgm assemble` (PARTIAL).
4. Fastener bore, washer contact, permanent hardware envelope and tool-access with installation stages (MISSING).
5. Engine-pack metadata: collision boxes, walk triangles, deck datums, ports (MISSING).
6. Standalone `connect`/`fit`/`modules --query` tools and typed system contracts (PARTIAL/MISSING).
7. Plant generator: 29 forms, stage, datum, organ tree, vegetation population (MISSING).
8. Luminaires with feeds and mounts; service/lighting/drainage networks and netlist (MISSING).
9. Edge bevel by selected edge pair, multi-segment fillet (PARTIAL).
10. Render passes (depth/normals/partID export, pixel inspect, fitted camera), section, drawing, camera-match, vision (PARTIAL/MISSING).
11. Casting families, spur gear, marine fastener family, voussoir and flared-hip roof generators (PARTIAL/MISSING).
12. legacy module set (~50 JSON docs in Recipes/Geometry/); same node format, so portable.
13. Parts schedule, structural support graph, pendant and mechanism physics, circulation routes (MISSING).
14. LOD and retained analytic tube/lathe profiles (MISSING).

Table row counts: MISSING 38 (includes a few "MISSING (both)" items the old side never had), PARTIAL 24.
