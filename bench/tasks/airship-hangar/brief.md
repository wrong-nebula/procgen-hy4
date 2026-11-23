## The object: `airship-hangar` — a monumental 1900 dirigible shed (two structural variants)

A grand early-aviation airship shed after Chalais-Meudon *Hangar Y* and imagined aerostat halls (s18; atlas SY25-SY27,
AE01-AE04), standing on `z = 0`, centred on `(0, 0)` in plan, running along `x in [-length/2, +length/2]` and across
`y in [-span/2, +span/2]`, with its main openable dirigible portal at **`x = +length / 2`** facing **`+x`**.

### Frame, controls & port (must match existing callers exactly)
- **Module name:** `airship-hangar`
- **Controls:** `length` `36.0` in `[24.0, 48.0]`; `span` `22.0` in `[16.0, 28.0]`; `height` `16.6` in `[12.0, 21.0]`;
  `variant` `0` in `[0, 1]` (integer).
- **Port:** `"door"` at `[length / 2, 0, 0]`, normal `[1, 0, 0]`, type `"deck"`, `{"width": S - 2}`.

### How it is built
Both variants sit on a paved `concrete` / `chipped-limestone` hangar floor apron (`z = 0 .. 0.14 m`) with inset steel
handling rails running down the nave to `x = +length/2`, keep a genuinely open high clear interior nave visible through
the parted `+x` portal doors, and have a fully detailed rear elevation at `-x` (seen prominently in the standard `iso`
view, which looks from `-x, -y`! Make sure BOTH `-x` and `+x` ends look architectural and monumental!).

1. **Variant `0` — Chalais-Meudon buttressed brick & iron-truss shed:**
   - **Side walls (`y = ±span/2`, `z = 0 .. 0.62 * height`):** modular bays (`mx(4, rnd(length / 6))`) separated by
     stepped `brick` buttress piers with `chipped-limestone` plinths, band courses and pier caps; between buttresses,
     tall arched multi-pane `glass` clerestory windows in `iron` sashes over coursed `brick` spandrels.
   - **Roof & monitor lantern (`z = 0.62 * height .. height`):** internal riveted `iron` Polonceau / Fink roof trusses at
     every bay, pitched `slate` roof slopes with overhanging eaves brackets, and a continuous raised **glazed ridge
     monitor lantern** (`glass` clerestory + `copper`/`iron` ridge ventilator cap) along the crown.
   - **Portal (`+x`) & Rear Gable (`-x`):** at `+x`, an overhead riveted `iron` box-girder door gantry carrying two huge
     parted sliding lattice-framed hangar door leaves (`iron` + upper `glass` lights) leaving a tall central dirigible
     berth opening, topped by a framed `kittype.sign_board` / `kittype.inscribe` fascia (`"HANGAR Y"`, `font="nouveau"`).
     At `-x`, a dressed `brick` & `chipped-limestone` gable wall with a large circular rose/ wheel window, stepped
     pilasters, and a glazed crew airlock annex door.

2. **Variant `1` — Parabolic riveted-arch vault hall:**
   - **Springing buttresses & arched vault (`z = 0 .. height`):** along both flanks, sloped `chipped-limestone` /
     `concrete` thrust piers carry external/exposed parabolic riveted `iron` arch ribs at every bay, spanning a closed
     thickened parabolic `steel` & `slate` shell punctuated by continuous longitudinal bands of `glass` skylight panels
     and a ridge ventilation catwalk with `iron` railings and `bronze` beacon finials.
   - **Portal (`+x`) & Rear Wind-Screen (`-x`):** at `+x`, massive arched riveted `iron` portal ring and parted tall
     vertical-ribbed rolling hangar doors on short outrigger tracks (staying strictly inside `|y| <= span/2 + 2.0`).
     At `-x`, a ribbed apsidal or radially glazed parabolic gable wind-screen with vertical iron mullions, riveted
     transom rings, and ground-level workshop/engine bay doors.
