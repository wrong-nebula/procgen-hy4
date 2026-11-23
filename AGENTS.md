# AGENTS.md: building kits in procgen-hy4

Written for coding agents (Hy4 Flash first, but any agent). Read this file, then work. For a new kit, also read
[BUILDING_KITS.md](BUILDING_KITS.md) (briefs, model choice, what to do when a gate fails). Keep context small:
do not read whole `kitlib*.py` files; search them (`grep -n "def name" procgen/recipes/kitlib*.py`).

## What you are doing

You write a **Python kit builder**. It emits a geometry-graph document (JSON). The `pgm` binary evaluates,
checks and renders that document. You never edit meshes by hand and you never edit `pgm` or `kitlib`.

## Setup (once per checkout)

```bash
cd procgen && cmake --preset release && cmake --build --preset release -j 8 && cd ..
procgen/build-release/pgm nodes | head -c 400      # prints JSON: the build works
```

Needs CMake 3.24+, a C++20 compiler, Python 3.10+. The MuJoCo gate runs through `uv`
(`uv run --with mujoco --with numpy --with coacd --with pillow`), so install `uv`.

## The loop (repeat until it passes)

1. **Start from the starter.** `cp examples/starter/bollard.py examples/<you>/<kit>.py`, rename the module,
   change the `Kit(ROOT, "out/<kit>", "<kit>")` line. Running the file writes `out/<kit>/<kit>.json`.
2. **Datum table first.** Before any geometry, write every mating coordinate as a constant or `g.let(...)`:
   ground z, plinth top, shaft radius, bore radius, port origins. Parts are placed from the table, never by eye.
3. **Build in five functions** (base, primary mass, openings/mechanism, secondary craft, details). Get a
   scaffold for a bench task with `python3 bench/harness/mini_mode.py scaffold <task>`.
4. **Verify with ONE command** (run from `procgen/`):

```bash
cd procgen
python3 tools/kit_harness/verify_round.py \
  --script ../examples/<you>/<kit>.py \
  --doc ../examples/<you>/out/<kit>/<kit>.json \
  --render /tmp/<kit>.png --stamp /tmp/<kit>.ok
```

   It runs build → `pgm check --intersections` → `pgm audit` → `pgm sweep` → MuJoCo CoACD collision audit →
   render, and prints at most 8 lines. On success: `ALL GATES PASS | tris=… | MuJoCo=100/100 …` and the stamp
   file exists. Add `--check-spin` for parts that must turn (wheels, valves, turntables).
5. **Look at the render** (`/tmp/<kit>.png`). Fix the one worst problem. Go to 4.

Do not finish until the stamp file exists. Do not claim a pass you did not see printed.

## Hard rules (the gates enforce these)

- **Frame:** Z-up, metres, ground at z = 0, the front faces **-y**. Back and sides must be finished.
- **Touching, not overlapping:** parts that meet share a face or sit within `BUTT = 0.0005` m. `pgm audit`
  fails any piece further than 3 mm from the rest. Overlapping solids of one material fail
  `check --intersections` (self-intersections after the material leaf is merged).
- **Open bores:** a shaft through a hub uses `r_bore = r_shaft + 0.0018`; use `bored_collar`, `bored_nut`,
  `pierced_extrude` from `kitparts`/`kitlib` so pins pass through real holes.
- **Glazing:** keep `>= 0.004` m between glass and anything in front of it.
- **Controls:** every control is swept at both ends of its range (`pgm sweep`). If the model breaks at the
  minimum or maximum, narrow the range or fix the geometry, do not remove the control.
- **Subdivision:** always `limit=1, limit_normals=1` on `G.subdivide`; use `cap="quad"` / `cap="inset"`
  before subdividing; add rivets, bolts, seams and lettering **after** subdivide/chamfer (those ops drop them).
- **Materials:** use slot names from `procgen/build-release/pgm materials`; use at least three.

## Where things are

| Path | What |
|---|---|
| `procgen/recipes/kitlib.py` | The builder DSL: `Graph`, `Kit`, `control`, `port`, `ref`, `review`, `C("name")`, `sin`, `cos`, `PI` |
| `procgen/recipes/kitparts.py`, `kittype.py` | Ready parts (filleted boxes, bored collars/nuts, cast housings, SDS cylinders) and lettering |
| `procgen/recipes/kitlib_*.py` | Helper families: masonry, iron, glazing, orders, ornament, pipes, tracery, curves, mech |
| `procgen/docs/DETAILING.md`, `DETAIL_VOCABULARY.md` | Rivets, bolts, seams and strokes as surface data |
| `procgen/build-release/pgm` | `nodes`, `materials`, `check`, `sweep`, `audit`, `render`, `export`, `sheet`; each prints one JSON object |
| `procgen/tools/kit_harness/` | `verify_round.py` (the gate), `mujoco_audit.py`, `diag.py`, `critique_v2.py`, depth/normal sheets |
| `examples/starter/bollard.py` | Smallest builder that passes every gate. Copy it |
| `examples/hy4/*.py` | Four kits Hy4 Flash built (turntable, city gate, metro entrance, iron shopfront). Read one for the level expected |
| `bench/tasks/<task>/brief.md` | 28 modelling briefs with controls, ports and materials |
| `bench/harness/` | The evolution loop that produced the Hy4 examples (needs an OpenRouter key) |

## Useful single commands

```bash
P=procgen/build-release/pgm
$P inspect  <doc.json>                         # controls, ports, node types, material slots
$P render   <doc.json> --out /tmp/a.png --view iso --pass clay   # views: iso opposite front side top
$P render   <doc.json> --out /tmp/b.png --pass parts              # one colour per part
$P check    <doc.json> --set height=1.4        # one control value
python3 procgen/tools/kit_harness/diag.py <doc.json> /tmp/d.png $P   # 2x2 sheet: 1 m axes, port arrows, 1.75 m figure
```

## Running the Hy4 Flash loop (optional, costs a few cents per kit)

```bash
export PGBENCH_OPENROUTER_KEY=...              # or put it in ~/.config/pgbench/openrouter.key (chmod 600)
python3 bench/harness/kit_evolve.py evolve turntable --model hy4-flash-xhigh --out /tmp/evolve-turntable
```

Seeds are written by Hy4 in a sandbox (`/tmp/pgbench`, override with `PGBENCH_SANDBOXES`), refined,
proportion-tuned with CMA-ES (no LLM calls) and ranked. The key is never written into a run directory.

## Etiquette

- Edit only your own builder and its `out/` folder. Do not modify `kitlib`, `kitparts`, `pgm` or other kits.
- Surgical edits: change the function that holds the defect, not the whole file.
- When done, report: what you built, the exact `ALL GATES PASS` line, and the one weakest point left.
