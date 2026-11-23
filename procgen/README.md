# procgen — procedural modeling core (C++20)

> In this repository only `recipes/greenhouse/` ships as a generated kit family. Other kit folders this file mentions (`site/`, `island-kit/`, `sheets/`) are not included; the tests that need them skip.


Data-oriented procedural mesh generation with built-in quality verification.
Standalone; it does not depend on the Swift/Metal renderer in the repo root.

## Build

```bash
cmake --preset release && cmake --build --preset release -j 8 && ./build-release/pg_tests
cmake --preset asan    && cmake --build --preset asan -j 8    && ./build-asan/pg_tests   # ASan + UBSan + libc++ hardening
./build-release/pg_bench            # micro-benchmarks, each with a quality verdict
./build-release/pg_bench_closest    # closest-point search: scan, index and the structures tried beside it
```

`-Wall -Wextra -Wpedantic -Wshadow -Wconversion -Werror` is on by default.
CMake detects a broken Command Line Tools install (empty toolchain libc++
headers) and falls back to the SDK's headers.

## Architecture

| Layer | Files | Design |
|---|---|---|
| Mesh | `mesh.hpp` | Compressed-row polygon mesh: flat `positions`, `face_offsets`, `face_indices`. Doubles. No per-element objects. |
| Topology | `topology.hpp` | Adjacency derived on demand as a sorted half-edge array (64-bit undirected key + face + from). No persistent half-edge structure to keep consistent. |
| Quality | `quality.hpp` | `analyze()`: boundary / non-manifold / inconsistently oriented edges, Euler characteristic, signed volume, area, edge-length distribution, degenerate faces, self-intersections (sweep-and-prune + orientation-predicate triangle crossing). |
| Closest point | `closest.hpp` | Nearest point of a mesh surface within a distance: `closest_point_scan()` is the reference, `ClosestIndex` a bounding-volume tree (binned surface-area splits, 64-byte nodes holding both children's boxes) that returns the same point, normal and triangle bit for bit, ties included. Measurements: [docs/experiments/closest-point-index.md](docs/experiments/closest-point-index.md). |
| Ops | `primitives.hpp`, `ops/extrude.hpp`, `cleanup.hpp` | Pure functions `Mesh -> Mesh`. |
| Graph | `graph.hpp` | DAG whose nodes hold `std::variant` params. Inputs must precede a node, so it is acyclic by construction. Lazy evaluation of the requested cone, with a content-hash cache, so edits re-evaluate only downstream nodes and identical subgraphs share results. |

### Operations

| Op | File | Guarantees (tested) |
|---|---|---|
| Primitives | `primitives.hpp` | grid, segmented box (exact shared seams), cylinder |
| Extrude | `ops/extrude.hpp` | region / individual, even-thickness shell offset; closed stays closed; exact volume change |
| Bevel | `ops/bevel.hpp` | single- and multi-segment chamfer (`segments=1` chamfer, `segments=2, profile=1.0` SDS support loops, `segments=3+, profile=0.5` circular fillet), `clampOverlap=1`, crease tagging; watertight by construction |
| Boolean | `ops/boolean.hpp` | union / intersection / difference (BSP), weld + T-junction repair → watertight; exact volumes incl. genus-1 drill, coplanar faces |
| Subdivision | `ops/subdivide.hpp` | OpenSubdiv-grade DeRose Catmull–Clark with sparse edge/vertex creases (`0..10`), `creaseMethod: uniform|chaikin`, Halstead–Kass–DeRose exact `limit=1` surface snap (zero shrinkage) + analytical `limitNormals=1`, `lodLevels` / `lodDistances`, UV rules |
| Scatter | `ops/scatter.hpp` | area-uniform samples, Poisson-disk minimum distance, instancing aligned to normal with seeded spin |
| Deform | `ops/deform.hpp` | twist, bend (Barr), taper (exact frustum volume), fBm gradient-noise displacement |
| Cleanup | `cleanup.hpp` | deterministic weld (lowest-index representative), collapsed-face removal, compaction |

Every op is also a graph node (`graph.hpp`), so a whole hard-surface part
(box → extrude → drill → bevel, plus scattered rivets) or an organic part
(subdivide → noise → twist) is a cached DAG. Editing one node re-evaluates only
its downstream nodes.

### Known limits (deliberate, documented)

- Self-intersection reports *proper* crossings only. Coplanar overlap and
  edge-on contact pass, because detecting them needs exact predicates with
  symbolic perturbation (planned for boolean/CSG).
- Triangle pairs that share a vertex index are skipped as adjacency.
- Boolean v1 is BSP-based. Input faces the cut never touches are emitted
  whole (`merge_uncut_faces`), but faces that are actually cut still fragment
  (edge ratio ~1,500:1 on box-minus-cylinder). Exact predicates are the next step.
- Bevel (`chamfer`) needs a closed, consistently oriented manifold; pass `clampOverlap=1` to avoid self-intersections on tight corners.

## Geometry documents: the kit format

`pg::doc` evaluates the project's geometry-graph documents (the JSON format of
`Recipes/Geometry/*.json`, specified in the main checkout's `GEOMETRY_NODES.md`):
controls with ranges, numeric expressions, groups, imported modules, typed
ports and `connect` nodes that mate them. Evaluation yields shared prototypes
placed by transforms with a material slot each. A document may also carry a
`derived` list of `[name, expression]` pairs (an extension of the format):
values computed once after the controls, in order, and read like a control
(`"$name"`). kitlib writes them with `Graph.let(name, expr)`; they keep documents
small when one value is used in many places.

- **Conformance:** all 71 reference documents (9,352 nodes, 794,318
  triangles, 24,920 instances) produce the same receipt as the Swift reference
  evaluator: node count, instances, prototypes, vertices, triangles, bounds.
  Receipts are pinned in `tests/data/reference-receipts.json`; `byteask verify`
  re-checks them whenever the recipe directory is reachable.
- **Node set:** `cube grid line cylinder surface extrude sweep spline resample
  profileSweep profileSolid surfaceBand pointArray boxBevel boxBoolean
  meshBoolean transform material join instance select group groupInput connect
  empty weld planarUV smoothNormals`. Not yet: `edgeBevel` (no document uses it).
- **Extension nodes** (procgen operations, a superset of the reference):
  `revolve` (lathe; a profile that ends where it starts is a ring section and
  turns into a closed solid, so pipes and bored flanges stay watertight: kitlib
  `Graph.annulus` and `Graph.pipe`; ready-made parts built on them are in `recipes/kitparts.py`,
  see `docs/KITPARTS.md`), `heightfield` (terrain block from heights or fBm),
  `subdivide` (DeRose/OpenSubdiv Catmull-Clark with `creaseMethod: uniform|chaikin`, exact `limit=1` + `limitNormals=1`, `lodLevels` / `lodDistances`), `crease` (semi-sharp `0..10` edge/vertex weights), `loopCut` (all-quad edge-ring cuts by `cuts`, `positions`, or `margin`), `shrinkwrap` (project detail cages onto curved SDS surfaces), `smoothNormals` (Phong break `angle`, FWN `weighting: areaSq|areaAngle`, `splitCreases`), `displace`, `deform`, `scatter`, `chamfer` (`segments`, `profile`, `clampOverlap`, `crease`), SDS-clean caps (`cap="quad"|"inset"` on `cylinder`, `revolve`, `sweep`, `tube`, `profileSweep`, `extrude`), and the parametric
  set: `curve` (points from `[x, y, z]` expressions in `@t`), `tube` (round
  tube along curves, closed curves become rings), `loft` (skin through curves)
  and `patch` (surface from expressions in `@u`, `@v`, optionally split into
  inset `panels`, which is how glazing is made). `thickness` turns a loft or
  patch into a closed shell.
- **For the engine:** `bake` merges a module into one prototype per material
  (kit builders bake every leaf module, so the winter garden is 253 instances
  of 40 meshes instead of 6,713 of 215; baked prototypes are closed solids
  that may overlap, not one boolean union). `tint` and the `tint` option of
  `instance` give a colour per placement for tintable materials.
- **Surface details** (`docs/DETAILING.md`, `docs/DETAIL_VOCABULARY.md`).
  Rivet rows, bolts and screws, seams, beads, bands, laps, louvres, bores,
  bezels, graduations, lettering, stone joints and paint marks are data lying
  on a clean mesh, not geometry. `detailStamp`, `fastener`, `detailStroke`,
  `detailTicks` and `detailText` project signed-distance shapes with a height
  profile and an optional material onto a part; `pointField`, `outline` and
  `courses` lay out where. Heads keep clear of joints and of each other, and
  raised details stand on what carries them. Packs carry the details per mesh
  (tool section `0x8007`) and the previewer shades them. The locomotive
  carries its plating and its number this way; the reference plate is 12
  triangles.
- **UV charts from construction.** Lathes, tubes, section sweeps and
  parametric skins carry a UV per face corner in metres (girth by arc length,
  meridian by circumference, arc length both ways), so a tiling material
  follows the part instead of smearing through a box projection. Flat-sided
  parts keep the box projection.
- **Expressions** also accept `sin cos tan sqrt abs neg floor ceil round`,
  `pow atan2 mod <` and a lazy `["if", c, a, b]`, so arcs, domes and counts
  that follow a dimension stay parametric.
- **Kits import kits.** A kit is a directory with a `modules.json`; `"imports":
  ["../other-kit"]` makes that kit's modules visible too (transitively, each
  module id owned by exactly one kit).
- **Chained seating.** A `connect` endpoint may name a node that already
  places a module (`["@wing-north", "north"]`); the pose is taken from that
  node, so modules chain port to port without restating poses.

### pgm: CLI and MCP

Every command prints one JSON object; errors are `{"passed":false,"error":...}`
with exit code 1. `tools/pgm_mcp.py` exposes the same commands as MCP tools
(`pgm_evaluate`, `pgm_render`, ...), returning renders inline.

| Command | What it does |
|---|---|
| `pgm evaluate <doc> [--set k=v]` | counts and Z-up bounds |
| `pgm inspect <doc>` | controls with ranges, module imports, ports, node types, slots |
| `pgm check <doc> [--intersections]` | every prototype: closed, manifold, oriented, plus `"sds"` stats (`quadRatio`, `sdsCages`, `multiLodPrototypes`, `highValencePoles`) |
| `pgm materials [--emat out.emat]` | material of every slot name (class, colour, flags) and the flat engine material pack that binds them |
| `pgm catalog <dir>` | documents, modules, port types and the kit import graph of a directory |
| `pgm export <doc> --emesh out.emesh [--obj out.obj]` | engine pack and/or OBJ |
| `pgm render <doc or .emesh> --out x.png [--view ...] [--pass beauty,clay,parts] [--wire-cage] [--lod 0..3]` | preview (with optional SDS control-cage overlay and LOD level) |
| `pgm sheet <sheet.json> --out x.png` | multi-panel kit sheet with captions |
| `pgm conformance <dir> <receipts.json>` | compare against reference receipts |
| `pgm sweep <doc or dir> [--intersections]` | every document at its defaults, both ends of each control range and each variant; lists only failures (the gate's control sweep) |
| `pgm audit <doc or dir> [--sweep] [--baseline f]` | contact audit: parts split into solid islands must hold together within 3 mm (or reach inside each other); lists detached pieces with sources and bounds. A module meant to be in pieces declares `"audit": {"components": N, "why": ...}`; known defects live in `tests/data/audit-baseline.json`. The gate's contact audit stage runs it over every recipe and its control sweep |
| `pgm nodes [type]` | node reference: every node type with inputs and options (the evaluator rejects any type not listed) |

Texture fitting (charts on `extrude`, `uvFit` onto trim-sheet bands, UVs in `pgm export --obj`, `tools/texture_preview.py`): docs/TEXTURE_FITTING.md.

Every command is an MCP tool of the same name (`pgm_evaluate`, ..., `pgm_sweep`, `pgm_nodes`), plus
`kit_build` (`site`, `greenhouse`, `island` or a builder path), which regenerates kits from their Python
builders. Authoring a kit therefore needs no source reading: `pgm_nodes` for the vocabulary, `kit_build`,
`pgm_sweep` on the kit directory, `pgm_render` or `pgm_sheet` to look.

```bash
claude mcp add procgen -- python3 "$PWD/tools/pgm_mcp.py"
```

### Engine packs (`.emesh`) and previews

`pg::emesh` writes the engine's runtime format (EMSH 1.2: 1.0 content plus
the material slot name section, so a material pack can bind materials to the
modeler's slot names;
`engine/asset/include/eng/asset/mesh_format.hpp`): Y-up, quantised positions,
packed normals, one material part per mesh, instance batches with baked
bounds, plus a tool metadata section (slot names, mesh names). Prototypes that
are congruent under a rigid motion collapse into one mesh, the motion moving
into the instance transform: the Utopie pavilion goes from 288 prototypes to
46 meshes (601 KB to 198 KB).

`pg::emat` writes the matching material pack (EMAT 1.0, untextured): one
material per slot name the modeler knows, classed as stone, painted iron,
bare metal, glass (blended), emissive opal, foliage or water, with the names
other producers use for the same thing (`masonry`, `brass`, `lamp`) bound to
the same materials. The engine's `matc --info` reads it and its sandbox
draws modeler packs with it. Per-instance tints travel in tool section
`0x8006` (RGBA8 per instance) until the engine defines a section for them.

`pgm render` draws from the pack bytes, so a preview shows what the engine will
load. The rasteriser in `tools/preview.cpp` is the stand-in backend until the
engine draws; `--backend engine` runs `$PGM_ENGINE_PREVIEW` instead. The gate
checks packs with the engine's own `meshc --info` and `sandbox --mesh-pack
--verify`.

### Kits

- `recipes/island-kit/`: the lock island as seven parametric modules with typed
  ports (silo, shed, ashlar bay, lock-wall bay, viaduct bay, stair flight,
  rock) and `lock-island-assembly.json`, an example that places them and mates
  the viaduct bays port to port. Generated by `recipes/build_island_kit.py`.
  Layout belongs to a layout tool (citygen); the kit is what it places.
- `recipes/greenhouse/`: the greenhouse as **six kits that import each other**,
  generated by `recipes/build_greenhouse_kits.py` (helpers in
  `recipes/kitlib.py`):

  | Kit | Modules |
  |---|---|
  | `foundry` | `gh-vault-rib`, `gh-column`, `gh-splice`, `gh-station` (rib seated on two columns) |
  | `glazing` | `gh-roof-bay`, `gh-wall-bay`, `gh-end-screen` |
  | `nave` (imports foundry, glazing) | `gh-bay`, `gh-gallery` (N bays), `gh-lean-to` (aisle bay) |
  | `crossing` (imports foundry) | `gh-crossing` (domed octagon, four portals) |
  | `fittings` | `gh-railing`, `gh-planter`, `gh-pendant`, `gh-bridge-bay`, `gh-terrace` |
  | `assemblies` (imports nave, crossing, fittings) | `gh-potting-house`, `gh-orangery`, `gh-winter-garden`, `gh-palm-house` |

  Every structural module takes `span`, `spring`, `rise`, `pitch` and `gauge`
  (the weight of the ironwork; member sizes, pane widths and counts follow
  it). Ports: `greenhouse.section {span, spring, rise, gauge}` between bays,
  screens and crossing portals; `greenhouse.side {pitch, spring, gauge}` for
  wall bays and aisles; `deck {width}` for doors, bridges and terraces. A
  section joint has one frame owner: galleries own their south frame, the
  crossing owns its portals, screens own none. The four examples run from a
  4.2 m potting house to a 16 m palm house with aisles; the winter garden
  (6,481 instances) exports to 206 meshes, 1.3 MB, accepted by the engine.
- `recipes/site/`: the structures a city layout places, under the ids the city
  generator emits (`recipes/build_site_kits.py`; list and status in
  [docs/SITE_KIT_BACKLOG.md](docs/SITE_KIT_BACKLOG.md)):

  | Kit | Modules |
  |---|---|
  | `railway` (imports greenhouse/nave) | `rail-track-panel`, `rail-viaduct-bay` (iron trestle, masonry arch, inhabited arch), `rail-long-span` (X-braced through truss, through lattice arch), `rail-hall` (gabled trussed frames, or the greenhouse vault at station span) |
  | `quay` | `quay-wall-bay`, `quay-bollard`, `quay-stair` |
  | `tramway` | `tram-track-panel`, `tram-contact-wire`, `tram-pole` |
  | `canal` | `canal-footbridge` (trussed girder, humpback), `canal-road-bridge` (plate girder, stone arch; 1 to 3 openings), `lock-gates`, `lock-cabin`, `rowboat` |
  | `harbour` (imports quay) | `pier` |
  | `shipyard` | ported from the old project's shipyard model: `dry-dock`, `hull-on-stocks` (frames only, partly plated, plated), `gantry-crane`, `erection-shed`, `slipway`, `deck-capstan`, `roller-fairlead` |
  | `rolling-stock` | the Atlantic night mail as a kit: `atlantic-shell`, `atlantic-driver-pair`, `atlantic-running-gear`, `atlantic-leading-truck`, `atlantic-trailing-axle`, `atlantic-tender`, assembled port to port as `rail-locomotive` (black or Type 12 green livery); `rail-carriage` |

  Frame: x along the way, y across, z up. Ends carry a `deck {width}` port and
  two `track {gauge}` ports, so bays chain and a metre-gauge panel cannot seat
  on a standard-gauge port.
- `recipes/sheets/*.json`: kit sheets (`utopie-globe`, `island-kit`,
  `greenhouse-kits`, `greenhouse-sizes`, `site-kits`, `site-canal`,
  `site-rolling-stock`, `site-shipyard`).

### Grounding in the references

New kits are derived from the project's reference material, not from invented
proportions. Each module document carries a `references` block: atlas design
recipes and source records (the detail atlas, `References/data.json` in the
main checkout: Eiffel plates, Vitry's orangery, Laeken and Kew, reference
prints) and plate library images (`experiments/references/plates`, ids
`sNN`), with a note on what was taken and which dimensions are design
assumptions. `pgm inspect` prints the block,
[docs/KIT_REFERENCES.md](docs/KIT_REFERENCES.md) tabulates all of them, and the
gate fails when a module has no references or cites an id that does not
resolve. Examples: the masonry viaduct follows atlas recipe SY32 (alternating
voussoirs, brick spandrels, stone curbs, iron rails) and reference's Revoir
Paris arches; the lattice arch follows Le viaduc d'Austerlitz and the arch
girder of Eiffel plate VII; the column shoe is the 0.60 m square with anchors
at +/-0.21 m of recipe E02.

### Materials

The material architecture was decided by the project owner (2026-11-15) and
starts on the engine side: a runtime material pack format parallel to EMSH
(lightweight metallic-roughness, material ids, emissive, alpha cutout, detail
maps, per-instance tint), defined and loaded by the engine, with baking on the
modeler's side later. It is the engine's roadmap item 1 and not started.
Until that format exists the modeler's contract is unchanged: parts carry a
slot name, `planarUV` scale and smoothing; packs list slots by name.

## Optimization kits

**Thermite is the optimizer.** The judge kits live in their own repositories,
out of the agents' reach:

- `~/thermite-kits/procgen-weld`: weld, exact match against a Python reference weld.
- `~/thermite-kits/procgen-ops`: boolean, subdivide, bevel, extrude and scatter,
  checked independently with analytic expectations; includes quality metrics.

Each has a `selftest.sh` that emulates the judge in the harness image, and
contracts ready to fill in with commits. The local driver below came before
Thermite was installed and retires once the campaigns run. `pg_bench` and
`pg_tests` stay as developer tools.

`kits/kit.py` is a dependency-free hill-climb driver. A kit (`kits/<name>/kit.json`) fixes:

- **constraints**: release + ASan builds succeed, both test suites pass, and
  every bench case's quality checks pass (watertight, no self-intersections,
  volume relative error < 1e-9);
- **objective**: one bench case's median time;
- **guards**: other cases that must not regress more than 10% (best trial
  against best trial).

A candidate is accepted only with ≥ 5% median gain **and** non-overlapping trial
ranges. This rule also rejects noise: an unchanged op's apparent "+8%" is refused.

```bash
python3 kits/kit.py score weld <label>
python3 kits/kit.py compare weld <base> <cand>
```

### Thermite campaigns (judge on rig `local-1`)

Kits and contracts live in `~/thermite-kits/`. `~/thermite-kits/live/assign.sh`
bundles the project branch and a kit into the rig's inbox, pins both commits,
validates the contract, and assigns a run over MCP. No model provider is set
up in Thermite yet, so runs are judge-only (`--agents off`): a candidate is
judged by seeding a run of the same campaign with its commit.

**`procgen-boolean-quality`** (minimize `edge_ratio`, guard `seconds`; cell `boolean_main`):

| Run | Seed | Edge ratio | Faces | Time | Calibration |
|---|---|---|---|---|---|
| `procgen-boolean-quality-4` | baseline `46a075b` | 21,186 | 2,066 | 0.549 s ± 0.007 | A/A σ 0.00% |
| `procgen-boolean-quality-5` | `91cebe3` | **1,475 (−93%)** | **806** | **0.190 s** | A/A σ 0.00% |
| `procgen-boolean-quality-6` **autonomous** (Opus 5.5 worker) | champion #2 → `e4557ab` | **231.5 (6.4× better)** | **512** | 0.227 s (guard ok) | win_confirmed |
| `procgen-boolean-quality-6` **autonomous** | champion #3 → `6ac7c7a` | **38.2 (another 6×)** | **496** | guard ok | win_confirmed |

Run 6 was Thermite's first autonomous hill climb. After 8 minutes its
worker's hypothesis (merge the fragments of each cut planar face into one loop, drop
stitch vertices only two faces use, repartition into convex pieces with no new
vertices) was judged `win_confirmed`. It was integrated with `tools/byteask.py
integrate`, whose gate (three builds, GCC 12, both kits, gallery) was green.

Run 5's candidate emits uncut input faces whole. Getting it to run end to end
also exposed and fixed two real bugs: chained booleans broke watertightness,
and non-planar inputs made the BSP re-split forever. Both are now covered by
regression tests. Held-out cell `boolean_held` (local judge emulation): faces
3,566 → 1,340, edge ratio unchanged (its shortest edge lies on the cut).

## ByteAsk

`tools/byteask.py` is the implementation and verification gate:
`verify` (debug, release and ASan+UBSan builds with tests, GCC 12 in the
Thermite harness image, both judge kits, gallery quality; JSON report in
`.byteask/`), `integrate <patch>` (apply a Thermite champion on a scratch
branch, verify, commit on green, discard on red), `bench`, `status`.

The full gate takes 5–7 minutes, mostly the judge kits and GCC 12, so it
belongs before a commit or merge, not after every step. Between steps run
`verify --auto`. It runs the full gate when C++, CMake, tests, kits or
`byteask.py` changed since this checkout's last green full verify. Otherwise
(recipes, kit builders, docs) it runs the release build and every recipe stage,
and lists the stages it skipped in the report. `--quick` always runs the light
set.

## Visual QA

`./build-release/pg_gallery gallery` renders `gallery/*.png` with a
dependency-free rasterizer (`tools/render.cpp`): the hard-surface part, organic
forms, the boolean before/after comparison, and an operations sheet. Each scene also prints
its quality report. The gallery has already caught three bugs that the unit
tests missed.

### Campaign 1: `weld` (polygon soup, 393k corners → 98k vertices)

| Step | Change | Median | Verdict |
|---|---|---|---|
| baseline | 27-cell neighbourhood, binary search into sorted cells | 659 ms | — |
| cand1 | cells 2·tol wide, probe only the 2×2×2 near octant (8 lookups) | 253 ms | ACCEPT (−62%) |
| cand2 | open-addressing hash: one probe per cell instead of binary search | 70 ms | ACCEPT (−72%) |
| cand3 | sort-free single pass; only representatives enter the grid | 31.5 ms | ACCEPT (−55%) |
| final | re-scored integrated tree | 30 ms | **−95% vs baseline** |

Semantics are pinned by `weld_matches_bruteforce_reference`, which compares
against an O(n²) reference on clusters placed on cell boundaries at
0.999999·tol. A planted bug (probing 1 cell instead of 8) makes it fail. Side
effect: `make_box(128)` went from 128 ms to 10.7 ms.

## Roadmap

1. Pack writer, per [docs/ENGINE_PACK_NOTES.md](docs/ENGINE_PACK_NOTES.md): one mesh with N material parts per prototype; ports, deck datums and collision boxes as tool sections; optional compact instances (format 1.1)
2. Shipyard pieces from the old project that are not in the island level (the atlas's Shipyard family: dry dock, gantry, erection shed, service bridge, harbour quays and breakwaters, signal towers), then the remaining site kits ([docs/SITE_KIT_BACKLOG.md](docs/SITE_KIT_BACKLOG.md)): swing bridge, barge, tram stop and car, signal, vessels, street furniture, roof heads; then `pgm assemble` to turn citygen's placement list into one pack
3. Rooftop pieces of the glasshouse level (masonry building, balustrades, pavilion, tower) as kits; `edgeBevel` node
4. A coincident-instance lint in `pgm check` (two owners of one section frame)
5. DP-NNS exact nearest-neighbour index for scatter, weld and SDFs: [docs/DP_NNS_SPEC.md](docs/DP_NNS_SPEC.md)
6. Thermite campaigns resume (subdivision speed v4 is paused; boolean, bevel, extrude, scatter drafted); evaluator and pack writer are new targets
7. Exact-predicate boolean; multi-segment bevel; faster self-intersection broad phase
