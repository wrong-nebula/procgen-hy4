# DP-NNS spatial index — planned work (owner's spec, recorded 2026-11-15)

Status: **implemented (2026-11-18, branch `geogram-exact`).**
- Core index: `include/pg/dp_nns.hpp` + `src/dp_nns.cpp` (`pg::DpNnsIndex`), backed by pure BSD-3 Geogram `Delaunay.psm` (`third_party/geogram_psm/`, compiled privately with `-ffp-contract=off`), handling 1D collinear, 2D coplanar, 3D manifold and volumetric point sets with exact predicates + Simulation of Simplicity, bitwise duplicate site chaining, a flood-filled 3D uniform seed grid (`O(1)` walk seeding), 2-hop cocircular tie escape, exact `query`, `query_knn`, Morton-reordered `query_batch`, `find_close_pairs_dpnns` and `sample_unsigned_distance_field`.
- Verified: `tests/test_dp_nns.cpp` (100% exactness vs brute-force oracle across empty, 1-point, collinear, tilted coplanar + duplicates, manifold surface + far volumetric queries, k-NN and 4-thread concurrent reads; ASan + UBSan clean).
- Measured on Apple Silicon (`pg_bench` `sphere_L6`, 24,578 manifold vertices, 10,000 volumetric queries): build median **91.1 ms**, 10k Morton batch queries median **1.857 ms** (**186 ns / query**, ~5.4M exact 1-NN queries/s, 0 oracle mismatches).

**Integrate DP-NNS (Delaunay-based Dynamic Programming Nearest Neighbor Search) as a high-performance exact spatial query primitive into the procedural modeling system.**

### Background
DP-NNS builds a successor DAG from the history of incremental 3D Delaunay triangulation. Queries walk this DAG to find exact nearest neighbors. It is especially strong when points lie on or near a surface manifold and queries are volumetric (or when queries can be far from the data), often outperforming k-d trees / R-trees in those regimes. Recent accelerations (grid-based query initialization + level-consistent parallel construction) give roughly 2× query speedup and ~10× build speedup while preserving exactness and near-logarithmic expected query cost under the stated assumptions. Reference: arXiv:2611.15320 ("Delaunay-Based Nearest Neighbor Search with Grid-Accelerated Queries and Parallel Construction").

### Goals
Add a production-quality, data-oriented C++20/23 spatial index based on accelerated DP-NNS. It must be usable as a drop-in exact nearest-neighbor / closest-point backend for the rest of the procedural system, with strong numerical stability, clear ownership, and measurable performance.

### Required deliverables

1. **Core implementation**
   - Successor-DAG construction from a point set (support optional surface/manifold hints if beneficial).
   - Exact 1-NN query.
   - Grid-accelerated query initialization (precompute valid starting points per cell).
   - Parallel construction that preserves query quality (level-consistent / BRIO-style insertion).
   - Optional but useful: k-NN, batch queries, and query reordering for cache locality (e.g. Morton).
   - Clean, minimal interface that can later be swapped or hybridized with a k-d tree / BVH for cases where build cost dominates.

2. **Integration points** (document and implement at least the first two)
   - Scattering (minimum-distance / density-controlled / collision-aware placement on surfaces or in volumes).
   - Mesh cleanup (vertex welding, near-duplicate detection, local density estimation).
   - Distance-field / SDF generation from surface samples (high-value use case).
   - Closest-point queries for projection, trimming, or quality evaluation in boolean/CSG or deformation nodes.
   - Quality metrics that need close-pair or spatial checks.

3. **Numerical & reliability requirements**
   - Exact under the algorithm's model; use robust predicates or carefully controlled floating-point where topology or nearest-neighbor decisions are made.
   - No silent failures; clear error reporting on degenerate inputs.
   - Memory ownership must be explicit (no hidden leaks; compatible with the system's arena / ownership model).
   - Thread-safety story for construction and queries must be documented.

4. **Testing & verification** (must be included)
   - Unit tests against a brute-force oracle for exactness on small and medium sets.
   - Tests on manifold-aligned surfaces, hard-surface samples, volumetric points, and degenerate cases (coplanar, near-duplicates, empty, single point).
   - Sanitizer builds (ASan + UBSan) clean.
   - Micro-benchmarks measuring build time, query throughput (single and batch, surface + volumetric queries) and memory use.
   - Quality / correctness checks: exactness rate, worst-case query length, behavior under floating-point noise.
   - Representative inputs drawn from the same hard-surface and organic cases used by the rest of the system.

5. **Performance harness**
   - As soon as a correct implementation exists, expose a measurable interface (build + query + exactness) that can be used by an external optimizer (Thermite-style hill-climbing) without changing the public API.
   - Include at least one realistic end-to-end micro-benchmark (e.g. scatter N points with minimum distance, or generate a coarse SDF from a mesh).

### Implementation guidance
- Prefer a clean, data-oriented design (SoA-friendly where it helps, minimal virtual calls in hot paths).
- Start with a correct sequential version + tests, then add grid acceleration and parallel construction.
- Keep the public interface stable so later operations can depend on it.
- Document assumptions (manifold preference, build vs query trade-off, when to prefer a simpler index).
- If you port or adapt algorithms from the paper, cite the relevant sections and keep the implementation self-contained and well-commented.

### Workflow
1. Scaffold the spatial-index interface and a correct (even if slower) DP-NNS core with full tests and sanitizer builds.
2. Add grid acceleration and parallel construction, re-verify exactness.
3. Integrate into at least one real operation (scattering or cleanup recommended first).
4. Deliver benchmarks and a short note on expected speedups and remaining limitations.

Report any numerical or topological edge cases encountered and how they were handled. Prioritize correctness and exactness over raw speed until the test suite is solid.

### Where it plugs in today
`pg::scatter_points` (hash grid for the Poisson test), `pg::weld_vertices` (hash grid), the planned SDF node, and closest-point checks in `analyze()`.
