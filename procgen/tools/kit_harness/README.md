# `procgen/tools/kit_harness` — Multi-Modal Kit Verification & Depth/Normal Detail Harness

Fail-fast verification and diagnostics for kits built with `procgen/build-release/pgm`. The Hy4 Flash evolution loop that uses these gates lives in `bench/harness`.

## Ported Tools (4 Layers)

### 1. Immediate High-ROI Gates & Visual QA
- **`verify_round.py`** — Single fail-fast 5-gate CLI (`build -> pgm check --intersections -> pgm audit -> pgm sweep -> MuJoCo CoACD collision audit -> render -> stamp`).
- **`mujoco_audit.py`** — Exact concave CoACD overlap & collision X-ray auditor (`buried_or_duplicate`, `cross_material_clip`, `same_material_clip`) + `--render-collisions xray.png`.
  ```bash
  uv run --with mujoco --with numpy --with coacd --with pillow python3 \
    tools/kit_harness/mujoco_audit.py <doc.json> --fast --render-collisions xray.png
  ```
- **`mujoco_support_audit.py`** — Exact GJK/EPA surface-to-surface support graph & gravity-drop auditor for interior/prop assemblies.
- **`diag.py`** — 2×2 diagnostic contact sheet injecting 1 m XYZ axes, 0.6 m copper port-normal arrows, and a 1.75 m human scale figure.
- **`critique_v2.py`** — Stateful senior-modeller vision critic reading 4 `pgm render` passes (`iso-beauty`, `opposite-beauty`, `front-clay-ortho`, `iso-clay`) and emitting `<= 7` ranked `MISFITS` with anti-churn tracking.

### 2. DepthPro2-S Metric Depth & Analytical Normal Passes
- **`depth_normal_sheet.py`** — Zero-WebGL one-command Apple Silicon MPS `apple/DepthPro2-S` diagnostic sheet (`[Beauty | Clay | DepthPro2 Metric | DepthPro2 Normals]` across 4 views + normal-gradient / flat-face metrics):
  ```bash
  python3 tools/kit_harness/depth_normal_sheet.py <doc.json|pack.emesh> --out depth_normal.png --size 448
  ```
- **`local_depthpro2.py`** — Full calibrated multi-view `DepthPro2-S` metric depth + analytical camera-space normals + zoomed 4-panel `[Clay | Painted | DepthPro2 Metric | DepthPro2 Normals]` guide strips.
- **`discover_proposals.py`** — Normal-residual (`Δθ`) + Sobel gradient + silhouette overhang detector with 3D Z-up back-projection, `0.22 m` voxel clustering, and Tier-1 (`0x8007` SDF) vs Tier-2 (3D geometry) routing.

### 3. Overhang / Silhouette Carving
- **`visual_hull_3d.py`** — `88³` Z-up voxel carving `(Painted Visual Hull) \ (Existing Geometry)` to extract exact world bboxes & 2D ortho profiles for missing projecting silhouettes (`VH01..VH06`).

### 4. Hy4-Flash Prompt & Context Discipline
- **`hy4_prompt_builder.py`** — Compact prompt generator for `opencode/hy4-flash-free` (`anchors.json` + surgical edits + `BUTT = 0.0005` / `r_bore` tolerances + single `verify_round.py --stamp` command).
