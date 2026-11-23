# Detail vocabulary: what the references ask for

A scan made on 2026-11-15, at the project owner's request, to find what the
detail engine could not yet make. `DETAILING.md` describes the engine itself.

## What was scanned

| Source | Where | What was read |
|---|---|---|
| plate library | `experiments/references/plates` (396 images) | every folder index; in full: s70, s77, s79, s104, s173, s174, s208, s278, s345, s380; two contact sheets |
| Detail atlas | `References/data.json` in the main checkout (150 recipes) | every recipe searched for relief vocabulary; 30 read in full |
| Atlas expansion | the Atlas session's worktree (282 recipes, not merged) | families Façade detail, Ironwork, Riveted structure, Art Deco, Art Nouveau, Industrial, Monumental, Interior, Glass and iron |
| Codex atlas | `~/Documents/Codex/2026-11-13/loo/outputs` | mechanical enemies (Caisson Tick, Rivet Hound, Walking Plate Yard), bestiary detail plates 01 to 06, civic maintenance crew detail sheet, and a word count over 96 briefs |

Not read: the remaining 380 plate images at full size, the character kits,
the armory, and the other Codex sets (shipyard, printworks, frost court). They
were sampled through their indexes only.

## What they show, and where it stands

"Detail" means data on the surface; "geometry" means the part is modelled.

| Feature | Seen in | Kind | How it is made | Status |
|---|---|---|---|---|
| Dome rivets in rows, edge distance kept | s77, s174, W04, E03, SY06, every riveted creature | detail | `rivetLine` | built |
| Panel seams, sheet joints | s77, s104, LL03, Walking Plate Yard | detail | `detailSeam` | built |
| Hexagon and square heads, washers | SY14, SY13, E03, A01, Caisson Tick legs, Last Crossing flanges | detail | `fastener` head hex, square; `washer` | built |
| Cap nuts | SY14 | detail | `fastener` head cap | built |
| Slotted and cruciform screws | LL03, s278 door, Section Keeper bezel | detail | `fastener` head slotted, cross; clocked at random | built |
| Pyramid studs | Rivet Hound carapace | detail | `fastener` head stud | built |
| Bolt circles (flanges, bezels, pivots, anchor rings) | Last Crossing, SY16, lenses on every robot | detail | `fastener` with `count` on an `outline` circle | built |
| Hoop bands, straps, trim along a panel edge | s77 hoops, Slag Bailiff bands, Section Keeper helmet strip, Walking Plate Yard brass trim | detail | `detailStroke` band, with its own slot; heads sit on it | built |
| Raised beads: stiffeners, mouldings, gaskets, weld beads | s278 door, RD01 retaining beads, RA01 | detail | `detailStroke` bead | built |
| Lapped plates, strakes, clinker planking, shell segments | s104 hull strakes, Mortar Mite | detail | `detailStroke` lap; rivets sit on the lap | built: the hull on the stocks is plated this way |
| Paint lines and pinstripes, witness marks | s278 gold lining, s77 platform edge | detail | `detailStroke` mark, `detailStamp` with height 0 | built |
| Stitched seams, broken lines | Landing Strider boot, Section Keeper apron | detail | `detailStroke` with `dash` | built |
| Hatches, doors, plates with rounded corners | s278, Slag Bailiff, IW10 | detail on a slab | `detailStamp` box with `corner` | built |
| Nested and stepped frames, plaques | s380, RA02, Walking Plate Yard muzzle | detail | stacked `detailStamp` boxes or polygons | built |
| Louvre vents, slatted grilles | Rivet Hound belly, Shutter Reader, Slag Bailiff radiator, G06, N05 | detail | `detailStamp` box with profile ramp | built |
| Bored and perforated fields | owner's reference plate, SY16 | detail | `pointField` and `detailStamp` | built |
| Lenses, portholes, bezels with glass | s77, s104, every robot's eye | detail | ring stamp, glass slot on a disc, screws on a circle | built |
| Graduations and dials | s278 gauge, s208 rings, Gauge Bearer, SY16, A02 | detail | `detailTicks`, `detailText` | built |
| Raised, engraved and painted lettering | s173 "12.004", s104 ship's name, s77 station plates, FD12 date panel, ID07 warehouse lettering | detail | `detailText`: figures and capitals, single stroke | built; no lower case, accents or serif faces |
| Coursed stone joints, banded rustication | s70, s380, s345, FD16 | detail | `courses` and `detailStroke` | built for a plain rectangle; not round openings or arches |
| Slates, scales, shingles, lapped carapace | s345, s208, MN08, Mortar Mite | detail | hex `pointField`, stamp with profile ramp | built |
| Chequer plate | footplates, SY04 treads | detail | grid `pointField`, stamp with `angle` per point | built |
| Counterweights, bosses, brackets, handles | owner's note, D05, IW12 | geometry | modelled, then detailed | counterweight built |
| Ribs, fluting and reeding along a shaft | greenhouse column, LL01, D03 | detail | one guide repeated round the axis, `detailStroke` bead or groove | built: `gh-column` has sixteen ribs as detail |
| Bellows, ribbed sleeves, coils | Section Keeper elbows, Rivet Hound gaiters | detail | hoops of bead strokes round a part | no helper yet: the builder lays the hoops by hand |
| Voussoir joints round an arch, quoins | s70, FD16 | detail | radial strokes from an arc | not built |
| Leaded and stained glass, glazing bars | s345, FD06, FD17, FD26, GI09 | detail on glass | bead strokes with a lead slot; quarry patterns | strokes work; no pattern generator |
| Sgraffito friezes, enamel panels, stencilled ornament | FD09, FD12, FD19, FD01 | flat marks | repeating units of marks in several slots | not built |
| Whiplash ironwork, scroll grilles, cresting | s70, IW01, IW07, N01 | geometry | swept curves | existing railing panel only |
| Brick bond, wood grain, rust, brushed metal | s345, Slag Bailiff, every plate | material | the engine's tiling materials | engine side |
| Skin folds, growth lines, ribbed fans | Lock Sleeper, Mortar Mite | detail | strokes along flow lines | strokes work; no organic generator |
| Wall anchors, small cast ornaments | s345 | geometry or detail | to decide per piece | not built |

## What this changed in the engine

Shapes polygon and cross, rounded corners on boxes, profiles cone and ramp,
strokes with five profiles and dashes, fasteners with seven heads, graduations,
lettering, outlines, masonry courses, a rotation per stamp, and the rule that a
raised detail stands on what is under it. Eleven modules in `site/plating`
exercise them; `recipes/sheets/detailing-vocabulary.json` draws nine.
