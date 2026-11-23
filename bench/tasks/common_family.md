# Modeling task: a kit family, {TITLE}

You are working in a procedural modeling toolkit. Models are not drawn by hand: you write a Python
**kit builder** that emits geometry-graph documents (JSON), and the `pgm` command-line tool evaluates,
checks and renders them. Your job is to build a small **family** of related modules in one session ({MODULES}), as well as you can, and to look at
them while you work. They belong together: share proportions, materials, construction and detail conventions
(named constants and helper functions in your builder), so that they read as one kit when placed side by side.

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
  - `pgm render <doc.json> --out x.png [--view iso|opposite|front|side|top] [--pass beauty|clay|parts] [--yaw deg --pitch deg] [--wire-cage] [--lod 0..3]`
  - `pgm sheet <sheet.json> --root recipes/site --out x.png`: multi-panel sheet

### Hard-surface & SDS craft (`kitlib.py` / `kitparts.py`) — avoid raw faceted boxes/cylinders!
Choose the right workflow per part:
- **Flat machined plates, frames, stone blocks → FWN bevels** (`filleted_box(..., mode="fwn")` or `G.chamfer(..., segments=3, profile=0.5, clampOverlap=1)` → `G.smooth(..., angle=55, weighting="areaSq")`).
- **Castings & industrial housings (cheeks, valve bodies, bosses, drums, corbels, ironwork) → Chaikin SDS + `limit=1`**: `cast_housing(...)`, `sds_cylinder(..., lod_levels=[2, 1, 0])`, or sparse all-quad cage (`cap="quad"` on `cylinder`/`revolve`/`sweep`, `cap="inset"` on `extrude`) → `G.crease` / `G.support_loops` / `G.loop_cut` → `G.subdivide(..., levels=2, creaseMethod="chaikin", limit=1, limit_normals=1, lodLevels=[2, 1, 0])`.
- **Curved details:** `G.shrinkwrap` (`kitparts.curved_boss_patch`); inspect cages with `--wire-cage` (`preview(wire_cage=True)`).
- **3 Critical Pitfalls:** (1) always keep `limit=1, limit_normals=1` on `G.subdivide` (zero shrinkage → 3 mm `pgm audit` contacts hold); (2) always use `cap="quad"` / `cap="inset"` before `subdivide`; (3) apply `G.fasteners` / `G.rivets` / `G.seam` / `G.stamp` / `G.text` **AFTER** `subdivide` / `chamfer`.

```python
# Copy-paste 4-line cast-iron SDS idiom (exact silhouette, zero shrinkage, automatic LODs):
G.n("rib-raw", "extrude", profile=pts, depth=0.028, cap="inset")
G.loop_cut("rib-cuts", "rib-raw", margin=0.025, selection={"direction": [1, 0, 0], "dirAngle": 25})
G.crease("rib-cr", "rib-cuts", sharpness=2.2, min_angle=30)
G.subdivide("rib", "rib-cr", levels=2, creaseMethod="chaikin", limit=1, limit_normals=1, lodLevels=[2, 1, 0])

# Copy-paste 2-line FWN machined plate / stone block idiom (or kitparts.filleted_box(..., mode="fwn")):
G.chamfer("plate-bev", "plate-raw", width=0.012, segments=3, profile=0.5, clampOverlap=1)
G.smooth("plate", "plate-bev", angle=55, weighting="areaSq")
```

{BRIEF}

## What to deliver

1. Write **one** builder, `recipes/build_bench_kit.py`, in the style of the existing builders. It must
   create the kit with `Kit(ROOT, "bench", "site-bench", references=REFS)` where
   `ROOT = Path(__file__).resolve().parent / "site"`, and build exactly these modules: {MODULES}.
   Give each module a `ref(...)` record and a `review(...)` record (your honest view of what is weak).
   Running `python3 recipes/build_bench_kit.py` must regenerate `recipes/site/bench/` from scratch.
2. Every module must pass `pgm check`, `pgm sweep` and `pgm audit` (run them on each `recipes/site/bench/<module>.json`).
3. **Look at your work.** Render each module (beauty and clay, more than one view), open the PNGs and judge them
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
