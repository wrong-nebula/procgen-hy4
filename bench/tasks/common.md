# Modeling task: {TITLE}

You are working in a procedural modeling toolkit. Models are not drawn by hand: you write a Python
**kit builder** that emits geometry-graph documents (JSON), and the `pgm` command-line tool evaluates,
checks and renders them. Your job is to build one module, as well as you can, and to look at it while you work.

## Your working directory

- `recipes/kitlib.py`: the Python DSL every builder uses (`Graph`, `Kit`, `control`, `port`, `ref`, `review`,
  expressions such as `C("name")`, `T`, `sin`, `cos`, `PI`).
- `recipes/kitparts.py` and `recipes/kittype.py`: ready-made parts (filleted boxes, bored collars and nuts, cast
  housings, SDS cylinders) and lettering. `recipes/kitlib_*.py`: helper families (masonry, iron, glazing, orders,
  ornament, pipes, tracery, curves). Every helper is tested in the matching `recipes/test_kitlib_*.py`.
- `recipes/greenhouse/**/*.json`: a generated kit family you can open, check and render to see the expected level:
  dozens of named parts, several material slots, instanced repeats, secondary pieces (bosses, collars, gussets,
  mouldings, plinths) where parts meet, and surface detail as data. Do not copy their geometry.
- `docs/`: `DETAILING.md` and `DETAIL_VOCABULARY.md` describe surface detail (rivets, bolts, seams, strokes),
  which lies on a clean mesh as data, not geometry. `README.md` describes the node set and `pgm`.
- `pgm` is on your PATH (also at `bin/pgm`). Every command prints one JSON object:
  - `pgm nodes [type]`: every node type with its inputs and options
  - `pgm materials`: material slot names
  - `pgm check <doc.json>`: closed, manifold, oriented, plus `"sds"` stats (`quadRatio`, `sdsCages`, `multiLodPrototypes`, `highValencePoles`)
  - `pgm sweep <doc.json or dir>`: every control at both ends of its range
  - `pgm audit <doc.json>`: pieces that float apart (parts must touch within 3 mm)
  - `pgm render <doc.json> --out x.png [--view iso|opposite|front|side|top] [--pass beauty|clay|parts|matcap|zebra] [--yaw deg --pitch deg] [--wire-cage] [--lod 0..3]`
  - `pgm sheet <sheet.json> --root recipes/site --out x.png`: multi-panel sheet

### Hard-surface & SDS craft (`kitlib.py` / `kitparts.py`) — the 70 / 30 Rule & Anatomy-First Discipline
1. **Write an Anatomy & Clearance Datum Table first:** compute every mating coordinate up front (plinth top Z, bedplate Z, cheek inner/outer X, wheel axial clearance `>= 5 mm`, open bore radius `pin_r + 0.003`). Use `bored_nut`, `bored_collar`, `bolted_flange`, `spoked_sheave`, `pillow_block_bearing`, and `G.pierced_extrude` so pins and shafts pass through real open bores instead of clipping solid cylinders.
2. **70% Machined FWN vs. 30% Restrained Chaikin SDS:**
   - **Flat machined plates, bedplates, drafted plinths, frames, hex fasteners → FWN bevels** (dead-flat faces + crisp bevel catchlights): `filleted_box(..., mode="fwn")`, `drafted_plinth`, `bored_nut`, `bored_collar`, or `G.chamfer(..., width=0.012, segments=3, profile=0.5, clampOverlap=1)` → `G.smooth(..., angle=55, weighting="areaSq")`.
   - **Castings, domes, flared cheeks, square-to-round transitions → Chaikin SDS + `limit=1`**: `cast_housing`, `sds_cylinder(..., lod_levels=[2, 1, 0])`, `G.loft(sections=..., segments=16, rings=2, smooth=1, cap="quad")`, or `G.crease` (with optional `selection={"plane": ...}` / `{"radius": ...}` / `{"zRange": ...}`) → `G.subdivide(..., levels=2, creaseMethod="chaikin", limit=1, limit_normals=1, lodLevels=[2, 1, 0])`.
   - **Inspect surface continuity:** use `--pass matcap` / `--pass zebra` and `--wire-cage` to catch pole pinching, ripples, or overly pillowy creases.
- **Curved details:** use `G.shrinkwrap` (`kitparts.curved_boss_patch`) to conform nameplates/ribs onto curved SDS cylinders or hulls; inspect cages & creases with `pgm render --wire-cage` (or `preview(wire_cage=True)`).
- **3 Critical Pitfalls:**
  1. **Always pass `limit=1, limit_normals=1`** on `G.subdivide` — Halstead–Kass–DeRose exact limit snap prevents cage shrinkage so 3 mm `pgm audit` contacts and port positions stay exact.
  2. **Always use SDS-clean caps** (`cap="quad"` on `cylinder`/`revolve`/`sweep`/`tube`, `cap="inset"` on `extrude`) before `G.subdivide` to prevent cap pinching and high-valence poles.
  3. **Apply surface details AFTER `subdivide` / `chamfer`** — `G.fasteners`, `G.rivets`, `G.seam`, `G.stamp`, `G.text` are dropped by upstream mesh-rebuilding ops.

```python
# Copy-paste 4-line cast-iron SDS idiom (exact silhouette, zero shrinkage, automatic LODs):
G.n("cheek-raw", "extrude", profile=pts, depth=0.05, cap="inset")
G.loop_cut("cheek-cuts", "cheek-raw", margin=0.025, selection={"direction": [1, 0, 0], "dirAngle": 25})
G.crease("cheek-cr", "cheek-cuts", sharpness=2.2, min_angle=30)
G.subdivide("cheek", "cheek-cr", levels=2, creaseMethod="chaikin", limit=1, limit_normals=1, lodLevels=[2, 1, 0])

# Copy-paste 2-line FWN machined plate / stone block idiom (or kitparts.filleted_box(..., mode="fwn")):
G.chamfer("plate-bev", "plate-raw", width=0.012, segments=3, profile=0.5, clampOverlap=1)
G.smooth("plate", "plate-bev", angle=55, weighting="areaSq")
```

{BRIEF}

## What to deliver

1. Write **one** builder, `recipes/build_bench_kit.py`, in the style of the existing builders. It must
   create the kit with `Kit(ROOT, "bench", "site-bench", references=REFS)` where
   `ROOT = Path(__file__).resolve().parent / "site"`, and build exactly one module named `{MODULE}`.
   Give it a `ref(...)` record and a `review(...)` record (your honest view of what is weak).
   Running `python3 recipes/build_bench_kit.py` must regenerate `recipes/site/bench/` from scratch.
2. The module must pass `pgm check`, `pgm sweep` and `pgm audit` (run them on `recipes/site/bench/{MODULE}.json`).
3. **Look at your work.** Render it (beauty and clay, more than one view), open the PNGs and judge them
   against the brief. Iterate on form, proportions and detail until it reads as the object. Renders are
   cheap (well under a second).
4. Finish by writing `NOTES.md` in the working directory: what you built, how it maps to the brief, what
   you would improve next.

Rules:
- Only create or edit `recipes/build_bench_kit.py`, files under `recipes/site/bench/`, scratch renders
  under `renders/`, and `NOTES.md`. Do not modify kitlib, `pgm`, or other kits (importing helpers from
  `kitlib` is fine; copying small helpers from other builders into yours is fine).
- Work only inside this directory. No network access is needed.
- Your output is judged on renders made the same way for every entrant: iso beauty, iso clay,
  the opposite view, an orthographic front view, and a parts pass. It is judged on how well it reads as the
  object in the brief, fidelity to the brief, detail and craft, and whether it passes check, sweep and audit.
