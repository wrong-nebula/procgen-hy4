# Surface details

Status: **built on the modeler side** (data, generators, pack section, preview
shading, first kits). The engine side is open; see the questions at the end.

Requested by the project owner on 2026-11-15: reference's work and the kits are
full of rivet lines, panel seams, counterweights, witness marks and small
mechanical parts, and the engine should draw them from a procedural
description. The quality reference is the bevel-shader plate (a recessed ring
with a field of bored holes and two keyed notches, shaded with rounded edges on
flat geometry).

## The idea

Meshes stay clean. Fine relief is **data lying on the surface**: a small
signed-distance shape with a height profile and, optionally, its own material.
A renderer turns it into shading (bent normals, shade in recesses, a material
change), either per pixel or baked into a tile.

`recipes/site/plating/inspection-plate.json` is the reference plate: a slab of
12 triangles carrying 132 details. `pgm sheet recipes/sheets/detailing.json
--root recipes/site --out sheet.png` draws it with the other examples.

What is detail and what is geometry:

- Anything shallower than its own width and smaller than a hand is a detail.
- Parts that change the silhouette (a balance weight, a boss, a bracket) stay
  geometry and carry details of their own.
- Fasteners follow reach (owner's note, 2026-11-15). Out of reach, up in the
  structure, a bolt is detail only: head, washer, nut and bolt end
  (`gh-splice`, the gusset rivets of the trestle, the truss and the gantry).
  Where a player can walk up to it, the nut stays geometry and carries its
  washer, its turned face and the bolt end as detail (`gh-column`, the quay
  bollard, the capstan, the fairlead). `G.close_bolts` in `kitlib.py` builds
  the second kind.

## Data

A prototype owns a list of details in its own space (`pg::doc::Detail`):

| Field | Meaning |
|---|---|
| `shape` | footprint in the tangent plane: `Disc`, `Ring`, `Box` (corners may be rounded), `Polygon` (regular, a flat facing the tangent), `Cross`, or `Segment` (a stroke with round ends from `origin` to `end`) |
| `profile` | `Dome` (half-round: a rivet head, a bead), `Flat` (plateau with a rounded edge `fillet` wide), `Groove`, `Cone` (straight slope to the middle: a stud), `Ramp` (rises across the footprint: a louvre blade, a lapped edge) |
| `origin`, `normal`, `tangent` | where it sits and which way the footprint's x axis points |
| `size_a`, `size_b`, `size_c` | disc radius; ring outer and inner; box half sizes and corner radius; segment half width; polygon inradius and number of sides; cross arm half length and half width |
| `height` | raised (+) or sunk (−); 0 makes a flat mark that only changes the material |
| `material` | slot shown on the footprint, or none (the surface keeps its own) |
| `fastener`, `extent` | authoring only: part of a fastener head and the head's whole radius, for the clearance rules |

Sizes are in the prototype's units, so a detail scales with its part. Every
instance of a prototype shows its details; `bake` carries them into the merged
prototype.

**Layering.** At a point, raised details contribute their highest value, sunk
details their deepest, and the two add: a slot cut in a screw head, a rivet on
the floor of a recess. The last detail in the list that covers the point with
a material of its own shows it.

**Standing on.** When a raised detail is authored, its height is added to the
raised height earlier nodes left at its place, so a bead on a door leaf, a
screw on a bezel and a rivet on a strap stand proud of what carries them.

`pg::doc::sample_details(details, p, n)` is the reference: height and material
at a surface point. `pg::doc::DetailIndex` gives the same answer through a hash
grid (tested equal on 20,000 points) and is what a baker or a CPU renderer
should use.

## Generators

Nodes of the geometry document. The first input is the part to detail, the
second says where. Sizes are in metres whatever scale the part is placed at.

| Node | Second input | Options |
|---|---|---|
| `detailStamp` | points (or a curve's vertices) | `shape` disc/ring/box/polygon/cross, `profile` dome/flat/groove/cone/ramp, `radius`, `inner`, `size` [along tangent, across], `corner`, `sides`, `width`, `height`, `fillet`, `slot`, `tangent`, `angle`, `reach` |
| `fastener` | points, or curves with `pitch` or `count` | `head` rivet/hex/square/cap/stud/slotted/cross, `radius`, `height`, `washer`, `turn` aligned/random, `seed`, `slot`, `clearance`, `reach` |
| `rivetLine` | curves | `fastener` with head rivet at a `pitch` |
| `detailStroke` | curves | `profile` groove/bead/band/lap/mark, `width`, `depth` or `height`, `dash` [on, off], `step`, `slot`, `clearance`, `reach` |
| `detailSeam` | curves | `detailStroke` with profile groove |
| `detailTicks` | curves | `pitch` or `count`, `length`, `major`, `majorLength`, `width`, `depth` or `slot`, `side` |
| `detailText` | anchor points | `text`, `size`, `direction`, `align`, `profile` bead/band/groove/mark, `height`, `width`, `spacing`, `slot` |

Guides, laid out in the XY plane round the origin and placed with a
`transform`:

| Node | Gives | Options |
|---|---|---|
| `pointField` | points | `layout` grid/hex, `spacing`, and `size` [x, y], or `radius` and `inner` |
| `outline` | one curve | `shape` circle/arc/rect/polygon, `radius`, `size`, `corner`, `sides`, `start`, `sweep`, `segments` |
| `courses` | curves | joints of coursed work: `size` [x, y], `course`, `block`, `bond`, `minimum` |

- **Projection.** Each point is moved to the nearest point of the part's
  surface and takes that face's normal. A point further than `reach` (0.25 m
  by default) is an error, so a detail cannot float (atlas W04: no rivets on
  floating surfaces). When several triangles are equally near, the first in
  the mesh wins (faces in order, each fanned from its first vertex), and the
  first part among several. The search is `pg::ClosestIndex`
  (`pg/closest.hpp`): the first 16 points on a part scan its triangles, the
  rest use a tree built once for that node. Both give the same answer bit for
  bit, so the switch cannot be seen in the output.
- **Rows.** With a `pitch`, heads are spaced by arc length, centred on the
  curve, the first and last half a pitch from its ends (edge distance). With a
  `count`, or on a closed curve, they are divided evenly: a bolt circle is a
  `count` on an `outline` circle.
- **Heads keep clear of joints.** A fastener closer to a groove, lap or bead
  of the same face than `clearance` (edge to edge, one head radius by default,
  measured from the washer if there is one) is left out, whichever of the two
  is authored first. A row that crosses a plate joint is therefore interrupted
  there. On a lapped plate only the lap's edge is the joint: its rivets sit on
  the lap. Heads that would overlap an existing head are left out too, so rows
  that meet at a corner share one head. Flat bands and paint marks carry
  heads. Stamps are the author's own placement and are never removed.
- **Clocking.** Screw and bolt heads are turned at random, the same way on
  every evaluation (`seed`); `turn` aligned lines them up.
- **Lettering.** `detailText` (`G.text`) draws single-stroke figures, capitals,
  `. : - /` and spaces; **its `at` point is ALREADY the vertical midpoint
  (mid-cap-height) of the line, NOT the baseline** (pass `valign="baseline"` or
  `"top"` if anchoring to an edge). For signage, plaques, friezes and arched
  plates, prefer `recipes/kittype.py` (`inscribe`, `arch_inscribe`,
  `sign_board`, `number_plaque`, `arch_sign_board`; see `docs/KITTYPE.md`),
  which centres lettering on its panel, clamps cap-height across control sweeps,
  handles rear faces without mirroring, and adds `"roman"` and `"nouveau"`
  stroke faces (with French accents) via `detailStroke`.
- **Guides.** `line`, `curve`, `spline` and `outline` give curves; `instance`
  repeats the curves of what it places, so one sheet's rows can be arrayed.
- **Order.** Detail a part after shaping it. Nodes that rebuild a mesh (`weld`,
  `subdivide`, `chamfer`, `loopCut`, `shrinkwrap`, the booleans) drop its details;
  `transform`, `material`, `instance`, `displace`, `smoothNormals` and `bake` keep them.
  Always apply `G.fasteners`, `G.rivets`, `G.seam`, `G.stroke`, `G.stamp`, `G.text`
  **after** `G.subdivide` / `G.chamfer` / `G.shrinkwrap`. For raised 3D plaques or ribs
  that must hug a curved SDS surface, use `G.shrinkwrap` (`kitparts.curved_boss_patch`).

In a kit builder (`recipes/kitlib.py`):

```python
G.box("sheet", [W, H, 0.012], [0, 0, 0.006])
G.guide("lap", [0, -H / 2, 0.03], [0, H / 2, 0.03])
G.seam("seamed", "sheet", "lap", width=0.007, depth=0.003)
G.guide("row", [-W / 2 + 0.035, -H / 2 + 0.035, 0.03], [W / 2 - 0.035, -H / 2 + 0.035, 0.03])
G.rivets("riveted", "seamed", "row", pitch=0.08, radius=0.011, height=0.005)
G.outline("ring", at=[0, 0, 0.03], shape="circle", radius=0.2)
G.fasteners("bolted", "riveted", "ring", head="hex", radius=0.012, washer=0.02, count=8)
G.text("numbered", "bolted", [0, 0.3, 0.03], "12.004", size=0.1, slot="bronze")
```

`G.stamp`, `G.stroke`, `G.ticks`, `G.field`, `G.courses` and `G.points` cover
the rest.

## In the pack

Tool section `0x8007`, present only when a pack has details. One 80-byte row
per detail, sorted by mesh:

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | mesh index (into the mesh section) |
| 4 | u8 | shape: 0 disc, 1 ring, 2 box, 3 segment, 4 polygon, 5 cross |
| 5 | u8 | profile: 0 dome, 1 flat, 2 groove, 3 cone, 4 ramp |
| 6 | u16 | material slot id, `0xFFFF` = the surface's own |
| 8 | f32[3] | origin |
| 20 | f32[3] | normal |
| 32 | f32[3] | tangent |
| 44 | f32[3] | end (segments) |
| 56 | f32 ×5 | sizeA, sizeB, sizeC, height, fillet |
| 76 | u32 | reserved, 0 |

Positions and directions are in the mesh's local space, the same space as its
vertices, so an instance transform applies to them unchanged. A slot used only
by a detail still gets a row in the material slot section. Two prototypes of
the same shape with different details stay separate meshes, and the details
are part of the mesh's content key.

`pgm evaluate` and `pgm export` report `details`.

## Preview

`pgm render` and `pgm sheet` shade details from the pack: per pixel the height
field of the few details nearby bends the shading normal (central differences
one pixel wide, so relief fades with distance instead of aliasing), recesses
are shaded by how far their surroundings stand above them, and a detail's
material replaces the surface's. This is a CPU stand-in for what the engine's
bevel shader would do.

## In use

- `site/plating`, eleven modules: `inspection-plate` (the reference plate),
  `riveted-panel`, `access-hatch`, `louvre-vent`, `number-plate`,
  `pressure-gauge`, `bolted-flange`, `tread-plate`, `ashlar-panel`,
  `copper-vault-panel` and `slate-field`. Each is a slab of 12 triangles (or a
  short cylinder) carrying 60 to 280 details.
- Bolts and rivets across the kits follow the reach rule above: 20 details on
  each splice plate, 9 rivets a face on the gantry's gussets, rivet fields on
  the trestle and truss gussets, washers and bolt ends round the ground-level
  nuts.
- `gh-column`: the sixteen ribs of the shaft are bead strokes (they were
  twelve modelled rods). `gh-vault-rib`: a row of rivets near each edge of
  both side faces, following the arch.
- `hull-on-stocks`: the shell plating (plate s104) is detail on each skin:
  strakes lapped fore and aft with a row of rivets on every lap, butts
  staggered from strake to strake with a row each side, portholes below the
  sheer and draught figures at the bow. About 4,000 details on a hull of
  2,800 triangles at the default stage.
- `site/rolling-stock`: the locomotive casing has panel joints with a row each
  side, a riveted crown seam (plate s173), rows either side of the brass
  line, a skirt row and its number, 12.004, in raised brass figures on each
  flank; the tender's side sheets are lapped and riveted; every wheel has a
  witness mark and an axle-end boss; the drivers carry a riveted balance
  weight (geometry) opposite the crank pin.

`DETAIL_VOCABULARY.md` lists what the references ask for and what is still
missing.

## Limits

- Details live in 3D mesh space, not in a UV chart. That is enough for a
  shader that works from position and normal; a baker needs the charts the
  modeler already writes for lathes, tubes, sweeps and patches.
- Projection uses a search tree (`docs/experiments/closest-point-index.md`):
  on the locomotive casing (2,100 points against 4,504 triangles) it fell from
  about 80 ms to about 3 ms including the build, and a part of 200,000
  triangles costs microseconds a point instead of milliseconds. The tree is
  rebuilt by every detail node; a document with many detail nodes on one large
  part would want it kept with the prototype.
- The clearance rule files joints and heads in buckets, so a new head asks
  one cell instead of every detail on the part.
- Lettering is one single-stroke face: figures and capitals only. Seams along
  the panel boundaries of a `patch`, hoops round a sleeve (bellows, fluting)
  and joints round an arch are not generated automatically; the builder
  passes the lines.
- A raised detail takes the height under it at one point (its centre, or the
  middle of each stroke segment), so a wide detail that straddles the edge of
  another is approximate.
- Heads left out by the clearance rule are not reported; the evaluator has no
  warning channel.

## Open questions for the Engine session

- Evaluate in the bevel shader or bake to a tile, and what the shader needs
  per detail beyond this row.
- Whether the section should be promoted out of the tool range, and whether a
  per-mesh grid should be written with it.
- Whether a detail's material should also carry a blend width (paint break at
  a seam) or stay a hard switch.
