# Closest-point index for detail projection

Experiment of 2026-11-15. Verdict: **adopt as is.**

## The question

Rivet rows, seams and stamps are projected onto the nearest point of a part's
surface (`docs/DETAILING.md`). `project()` in `src/doc/doc_detail.cpp` found
that point by testing every triangle of every part for every point. On the
locomotive casing (2,126 projections against 4,504 triangles) that was most of
the evaluation; on a hull or a subdivided organic part it would be seconds.

Would a search structure make it cheap, and can it be done without changing a
single bit of any document's output?

Two ideas were on the table. A paper on exact nearest-neighbour search for
points on a surface queried from the space around it (arXiv:2611.15320,
recorded in `docs/DP_NNS_SPEC.md`): a walk over the history of a Delaunay
triangulation, started from a grid of precomputed entry points, with query
points reordered for cache locality. And the finding of another session that a
surface-area-heuristic tree with cache-line-sized nodes was what won for
ambient occlusion. The paper searches nearest *points*; this query needs the
nearest point on a *triangle*, so its structure does not apply directly. Its
grid of entry points and its reordering were tried in transposed form.

## What was built

| File | What |
|---|---|
| `include/pg/closest.hpp`, `src/closest.cpp` | `closest_point_scan()`, the reference; `ClosestIndex`, a bounding-volume tree built once from a mesh. Both answer "nearest surface point within a distance": point, face normal, distance, face and triangle. |
| `src/doc/doc_detail.cpp` | `project()` uses one search per prototype: a scan for the first 16 points, then the index. Reach, the per-part box test and the error messages are unchanged. |
| `include/pg/doc.hpp` | `set_projection_search(Auto / Scan / Index)` and `profile_projection()`, for tests and benchmarks. `pgm` reads `PGM_PROJECTION_SEARCH`. |
| `tests/test_closest.cpp`, `tests/test_doc.cpp` | Index against scan on meshes and points chosen to break it; the rounding bound; documents under all three searches. |
| `bench/closest_bench.cpp`, `bench/closest_candidates.hpp` | `pg_bench_closest`: the tables below. The structures that lost live here, not in the library. |
| `bench/bench_main.cpp` | Two cases in `pg_bench` (build, 10,000 queries), each checked against the scan. |

The index is a binary tree over the triangles. Splits are chosen by the
surface-area heuristic over 16 bins along the axis on which the triangles'
centres spread most; leaves hold up to 4 triangles. A node is 64 bytes and
holds the boxes of *both* its children in single precision, so one cache line
decides on two subtrees. Triangles are copied in leaf order, 80 bytes each,
with exactly the mesh's coordinates. The walk goes into the nearer child first
and keeps the other on a stack with its distance.

## How it stays exact

The scan's rule is the definition: faces in order, each fanned from its first
vertex; a triangle whose normal cannot be taken is skipped; the first triangle
that attains the smallest `length(q - p) * scale` wins; the first part wins
among parts; a hit further than `reach` is an error.

1. **Same arithmetic.** Scan and index call one function for the nearest point
   of a triangle and form the distance with one expression, on the same
   operands. The normal is recomputed from the winning triangle by the scan's
   expression.
2. **The tie-break is carried, not hoped for.** Every triangle keeps its number
   in scan order. A candidate replaces the best when it is nearer, or exactly as
   near with a lower number.
3. **Nothing is skipped on trust.** A subtree is skipped only when every
   triangle in it is provably further than the best, in the arithmetic actually
   performed. That needs a bound on how far the *computed* nearest point can
   lie outside its triangle's box. On a vertex or an edge it is a few units in
   the last place. Inside the triangle the barycentric weights are quotients of
   differences of products and cancel; their error grows as
   `eps * kappa * D^2`, with `kappa = 1 / height^2` of the triangle and `D` the
   distance from the query to it. Each child box therefore stores the largest
   `kappa` below it. While `4096 * eps * kappa * D^2 < 1/4` the box distance is
   reduced by the corresponding stray before it is compared; otherwise the
   subtree is always visited. Slivers thinner than 1 in 32,768 are always
   visited. Boxes are rounded outwards when stored, box distances are shrunk by
   2^-40, and the comparison is strict.
4. **Fallbacks test everything.** A mesh with a non-finite coordinate, with
   more than 2^28 triangles, or with no more triangles than a leaf holds gets
   no tree, and a query with a non-positive scale ignores the tree.
5. **`project()` keeps its own rules.** Parts are visited in order with the
   same box test; a part's answer replaces the best only if strictly nearer.
   The index is asked for nothing beyond `min(reach, best so far)`, which
   cannot change which hit wins.

Point 3 is argued, and measured, but not proven line by line; see "Not
verified".

## Exactness checks

All of these pass in the release and the asan (ASan + UBSan + libc++
hardening) builds; `pg_tests` is 102 tests, 0 failed checks in both.

- **Randomized, index against scan** (`closest_index_matches_the_full_scan`):
  155,816 queries (126,352 hits, 29,464 misses) on 17 meshes, each with four
  tree variants, four reaches and three scales. Meshes: boxes, a subdivided
  sphere (also 2e7 m from the origin, 1e-6 its size, and a box 1e5 its size),
  a cylinder with 24-gon caps, an open grid, a 12 m sheet 12 mm thick beside a
  finely divided cube, a 12,288-triangle sphere, and six soups of 400 random
  triangles that include repeated corners, collinear corners, needles and
  slivers down to 1e-15 of their length. Points: in and around the bounds, on
  vertices, edge midpoints and face centres and 1e-9 off them, on the bounds'
  corners, and 100 diagonals away. 0 differ, compared bit for bit.
- **Ties**: 2,020 queries on a cube and a sphere, of which 1,012 points have
  several equally near triangles (the cube's centre, its diagonal planes, the
  space off its edges and corners, the sphere's centre). 0 differ.
- **The rounding bound**
  (`closest_point_strays_from_its_triangle_by_less_than_the_bound`): 400,000
  triangles of every shape, half of them axis-aligned so that any stray leaves
  the box, with the query up to 500,000 heights away above corners, edges and
  the boundaries between them. The computed point left its triangle's box in
  a few dozen cases, never by more than 0.2 of one unit of
  `eps * kappa * D^2 * edge`. The bound allows 4,096 units.
- **Documents** (`doc_details_are_the_same_however_the_surface_is_searched`):
  the casing, the tender, the driver pair and both plating modules, a turned and
  subdivided part placed twice at different scales with seams and rows, and a
  point exactly half way between two plates, each evaluated with Scan, Index
  and Auto: identical pack bytes and identical double-precision details.
- **Every recipe, before and after**: all 61 documents under
  `recipes/greenhouse`, `recipes/site` and `recipes/island-kit`, plus
  `riveted-panel` at two other control settings, compared between the commit
  before this work and after it: `pgm evaluate` receipts (timing removed) and
  the SHA-256 of `pgm export --emesh`. Identical with
  `PGM_PROJECTION_SEARCH` unset, `scan` and `index`. (The 15 `modules.json`
  files in those directories are module lists, not documents; they fail the
  same way before and after.)
- **Benchmarks**: every row below carries a count of answers that differ from
  the scan's. All are 0, also under the sanitizers.

Whether the tests would notice a broken index was checked by breaking it:

| Change made to `src/closest.cpp` | Tests |
|---|---|
| Latest triangle wins a tie instead of the earliest | fail (30,512 queries differ; the document test fails too) |
| Boxes rounded to nearest instead of outwards | fail (8,724) |
| Triangles without a normal kept | fail (184) |
| Box distance rounded up by 2^-40 instead of down, no stray allowance | fail (2,709) |
| Plain box distance, and subtrees exactly as far as the best skipped | fail (3,423) |
| Subtrees exactly as far as the best skipped (allowance kept) | **pass** |
| Plain box distance: no shrink, no stray allowance (strict comparison kept) | **pass** |

The last two rows say what the tests cannot show. With the allowance in place
a tied subtree is never *exactly* as far as the best, so the strict comparison
is a second guard. And no input was found on which the plain box distance
gives a wrong answer: the allowance for rounding rests on the argument above,
not on a failing case.

## Measurements

Apple M1, 8 cores, Apple clang 16, `-O3`. `pg_bench_closest`, at `nice 10`.
The laptop was shared and busy throughout (load averages between 9 and 28
during the runs), which costs a niced process both time and performance
cores. So each table gives the **fastest of 9 runs**, which is the steadier
figure there, and the medians of the same sitting are in the last section for
comparison. Ratios between rows are what to read; small differences between
tree variants are judged on counted work, which does not depend on the load.

The experiment was stopped by the project owner on 2026-11-15, before the
sub-agent had made its final timing runs and written the sections below. The
figures are from its last benchmark output (fastest of 9 runs, taken at the
commit the experiment started from); the sections after the tables were
written by the main session from that output and from a re-measurement after
merging.

**Documents** (whole evaluation, milliseconds; details identical in every row):

| Document | Projections | Scan | Tree | Auto (scan the first 16, then the tree) |
|---|---|---|---|---|
| Locomotive casing | 2,126 | 90.8 | 12.2 (7.4x) | 13.6 (6.7x) |
| Whole locomotive | 2,551 | 94.5 | 16.0 (5.9x) | 16.9 (5.6x) |
| Riveted panel 12 x 6 m, pitch 0.04 (12 triangles) | 4,569 | 30.1 | 29.4 (1.0x) | 29.8 (1.0x) |

On the casing the projection itself went from 81.4 ms to 1.35 ms of queries
plus 1.6 ms to build the tree.

**Stress case**: a subdivided sphere of 196,608 triangles, 20,000 queries,
reach 0.25 m (microseconds per query; `all` is the whole set):

| Structure | Build ms | Guide rows | On surface | Inside | All | Differ |
|---|---|---|---|---|---|---|
| Full scan (sampled) | 0 | 1,722 | 1,727 | 1,766 | 34,447 ms | 0 |
| Tree: SAH on the longest axis, leaf 4 (adopted) | 51 | 11.0 | 1.15 | 16.7 | 160 ms | 0 |
| Tree: SAH on three axes, leaf 4 | 100 | 11.3 | 1.15 | 15.9 | 159 ms | 0 |
| Tree: median split, leaf 4 | 63 | 12.7 | 1.54 | 19.8 | 187 ms | 0 |
| Tree: leaf 1 / leaf 2 / leaf 8 | 94 / 64 / 43 | 14.8 / 11.6 / 11.2 | | | 215 / 169 / 165 ms | 0 |
| Same tree, queries along a Morton curve | | 10.7 | 1.17 | 16.1 | 155 ms | 0 |
| Same tree, queries shuffled | | 13.7 | 1.42 | 17.6 | 186 ms | 0 |
| Same tree, one double-precision box per node | | 13.1 | 2.12 | 20.0 | 193 ms | 0 |
| Seeded from the previous hit | | 11.9 | 1.56 | 20.4 | 183 ms | 0 |
| Seeded from a grid of entry points, 16^3 / 48^3 | 89 / 1,853 | 11.9 / 11.4 | | | 170 / 169 ms | 0 |
| Uniform grid 19^3 / 37^3 / 74^3 | 6 / 7 / 10 | 51 / 38 / 66 | 5.8 / 2.6 / 1.3 | 93 / 78 / 395 | 802 / 623 / 2,112 ms | 0 |

**Break-even** (queries 0.1 diagonals off the surface):

| Mesh | Triangles | Build ms | Scan us/query | Tree us/query | Speed-up | Queries to pay for the build |
|---|---|---|---|---|---|---|
| Box, 1 segment | 12 | 0.001 | 0.16 | 0.17 | 0.9x | never |
| Box, 4 segments | 192 | 0.02 | 1.83 | 0.24 | 7.7x | 13 |
| Locomotive casing | 4,504 | 0.67 | 35.7 | 2.36 | 15x | 21 |
| Sphere, level 5 | 12,288 | 2.2 | 104.6 | 3.0 | 35x | 22 |
| Sphere, level 7 | 196,608 | 50.2 | 1,597 | 11.9 | 134x | 32 |

**After merging** onto the branch as it stood later that day (more detail
kinds, the clearance rule as a lookup), `pgm evaluate`, fastest of 7, scan
against auto, on a busy machine:

| Document | Details | Scan | Auto |
|---|---|---|---|
| `rail-locomotive` | 2,641 | 62.7 ms | 17.6 ms |
| `hull-on-stocks`, stage 2 | 5,269 | 134.7 ms | 115.6 ms |
| `hull-on-stocks`, 70 m | 7,543 | 137.0 ms | 130.9 ms |
| `gh-winter-garden` | 1,438 | 22.2 ms | 17.9 ms |

All 73 recipe packs are byte-identical before and after the merge, and the
hull's pack is the same under scan, index and auto.

## What it showed

- The tree removes projection as a cost wherever a part has thousands of
  triangles: about 15x per query on the locomotive casing, 134x on a part of
  200,000 triangles, with a build that pays for itself after 13 to 32 queries.
- Parts of a dozen triangles gain nothing, which is why `Auto` scans the first
  16 points on a part before building anything.
- The surface-area heuristic on the longest axis is as fast to query as the
  three-axis version and builds in half the time. Median splits are 17% slower
  to query. Leaves of 4 triangles are the best of 1, 2, 4 and 8.
- Two children's boxes in one 64-byte node are about 20% faster than one
  double-precision box per node.
- The hull on the stocks hardly gains (135 to 116 ms): its skins are small
  meshes, so its time is not in projection. Where it goes has not been
  profiled.

## What did not help

- **Uniform grids** over the triangles: 4 to 13 times slower than the tree on
  the large case, and badly so for points inside the part or far from it.
- **Seeding** the walk from the previous hit or from a grid of precomputed
  entry points (the paper's idea, transposed): no gain within the noise, and
  the fine grid costs 1.9 s to build.
- **Reordering the queries** along a Morton curve: about 3% over the order the
  guides arrive in, which is already coherent; not adopted.
- **Deeper leaves** (1 triangle): slower to build and to query.

## Not verified

- The rounding allowance (point 3 above) is argued and measured, not proven:
  no input was found on which a plain box distance gives a wrong answer, and
  the tests cannot tell the allowance from its absence.
- The timings were taken on a shared, busy laptop at `nice 10`; ratios are
  reliable, absolute figures are not. The sub-agent's final quiet timing run
  was not made.
- GCC 12 was covered only by the project gate after merging, not by the
  experiment itself.
- The tree is rebuilt by every detail node; whether keeping it with the
  prototype is worth it was not measured.
- Parallel construction and parallel queries (the other half of the paper)
  were not tried.

## Verdict

Adopt as is, at the speed-up measured. `Auto` is the default: results are
identical bit for bit to the scan on every recipe in the tree, and the
locomotive evaluates about 3.6 times faster. The next cost to look at for
detailed documents is not projection.
