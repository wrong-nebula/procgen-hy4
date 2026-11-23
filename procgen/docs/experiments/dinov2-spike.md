# DINOv2 spike: can visual features help review kits?

2026-11-16, Opus 5.5 (medium), on the owner's approval of the spike in the DINOv2 proposal (session notes,
`dinov2-proposal.md`, written by an Opus 5.5 subagent). Offline and optional: nothing in the modeler, the packs or
the gate uses it.

## Setup

- Model: `facebook/dinov2-with-registers-small` (ViT-S/14 with 4 registers, 84 MB), Hugging Face, Apache-2.0.
- Runtime: its own venv `procgen/.venv-features` (uv, Python 3.13, torch 2.15, transformers 5.23; 747 MB), PyTorch on
  MPS. Weights and caches in `procgen/.features/` (git-ignored). The global Python's broken `torchvision 0.1.6` is
  left alone.
- Script: `tools/dinov2_spike.py` (`HF_HOME=.features/hf .venv-features/bin/python tools/dinov2_spike.py`).
- Images, all letterboxed to 224 px:
  - 74 module renders: `pgm render --pass clay`, 448 px, one camera (yaw −130°, pitch 20°), at defaults;
  - 66 of those with one control raised 10%;
  - 239 atlas images (`References/assets`: the old engine's renders, flora renders, Eiffel plates);
  - 394 reference plates.
- Modes:
  - raw: cosine of the CLS token;
  - edges: both images reduced to dark lines on white first;
  - patch: mean best patch-to-patch cosine, both ways.
- Speed: 14.6 ms per image on the M1's GPU (MPS), batches of 16, measured. The whole run (about 770 images × 2 modes)
  takes under a minute.

## Calibration pairs

Only pairs where both images were looked at and show the same kind of object:

| Kind | Pairs |
|---|---|
| Atlas render | gantry-crane–gantry, deck-capstan–capstan, roller-fairlead–fairlead, quay-bollard–mooring, lock-gates–dock-gate, lock-cabin–survey-signal, rail-viaduct-bay–survey-viaduct, quay-wall-bay–quay, quay-stair–dock-access, rowboat–tender, rail-hall–steelworks-arrival, tree–FL13 and FL41 (13 pairs) |
| reference plate | tram-car–s114, s45, s50; steamer–s104, s182, s25; rowboat–s104; tree–s178, s114; hull-on-stocks–s25 (10 pairs) |

## Results

How each test was scored:
- **Rank:** where the true image lands among all candidates (1 is best).
- **AUC:** the mean over queries of 1 − (rank − 1)/(N − 1). 0.5 is chance; 1.0 means the true image is always first.

| Test | Mode | Median rank | Top 10 | AUC |
|---|---|---|---|---|
| Near duplicate: nudged render finds its own module (74 candidates, 66 queries) | raw / edges / patch | 1 / 1 / 1 | 66 / 66 / 66 | 1.0 / 0.995 / 1.0 (same vs nearest other) |
| Atlas render of the same piece (239 candidates, 13 queries) | raw | 9 | 7 | 0.886 |
| | edges | 10 | 8 | 0.873 |
| | patch | 19 | 4 | 0.863 |
| reference plate showing the object (394 candidates, 10 queries) | raw | 96.5 | 2 | 0.754 |
| | edges | 156 | 2 | 0.670 |
| | patch | 38 | 3 | 0.786 |

Ranks of the plate pairs, raw mode, in table order: 208, 16, 6, 1, 65, 223, 13, 136, 179, 128. The steamer finds
s104 (the Titanic) first. The yellow tram of s114 ranks 208th: the tram is a corner of a facade drawing.

## What this says

- **Near duplicates and regressions: works.** A module after a small change is always nearest to itself, with a clear
  margin to the next module. This test is easy (same camera, same clay pass), so it shows the tool can flag duplicate
  modules and unexpected visual changes. It does not yet show finer judgement.
- **Old engine's renders: real signal.** The matching atlas render lands in the top 10 of 239 for 7 or 8 of 13 modules,
  though the atlas images are lit, textured scenes and ours are clay. Useful to retrieve "the old model of this piece"
  and to cluster kits by family.
- **reference plates: weak.** Two modes clear the proposal's kill line (AUC 0.7), barely, on only 10 pairs. A median rank
  of 38 (patch) means the right plate is usually in the top 10 % but rarely in the top 10. Good enough to propose
  candidate plates for a reviewer to look at. Not good enough as a score of how on-style a module is. Edge maps
  make it worse, not better.
- **Limits:** one camera; small calibration sets; whole plates where the object is often a small part of the
  drawing. Cropping plates to the object would probably help the drawing test most.

## Recommendation for stage 2

Build the tool for the render-to-render uses only:
- `features extract` and `similar` over module renders, to find duplicates and the nearest module;
- `regression`, to compare a module's render before and after a change;
- `nearest atlas render`, to find the old model of a piece.

Offer plates only as a ranked list of candidates (raw or patch mode, top 20) for a person to check, never as a pass or
fail score. Before trusting drawings further, crop about 20 plates to their object and re-run this spike. The proposal's
interface (`tools/pgm_features.py`, MCP tools, a cache keyed by document, controls and camera, skipped cleanly when torch
or the weights are absent) stands.

Disk: the venv is 747 MB, the weights 84 MB, the renders and caches about 85 MB; 14 GB was free afterwards.

## Model sizes

2026-11-16, same images, pairs, modes and 224 px letterbox as above. `tools/dinov2_spike.py` now takes `--model`,
`--size` and `--dtype`, reads the register count from the model config and keys its cache by model, size and dtype.
It writes `.features/spike-<model>[-<size>].json`; the small run keeps `.features/spike.json`. Re-running the small
model with the new script gave identical ranks in every test and mode. Runs were under `nice -n 10`, on MPS, float32,
batches of 16, with the laptop shared.

### Cost (measured, except the giant row)

| Model | Params | Weights | ms per image | Peak MPS driver | Max RSS |
|---|---|---|---|---|---|
| small, 224 | 22 M | 84 MB | 14.7 | 1.2 GB | 0.5 GB |
| base, 224 | 87 M | 346 MB | 52 | 1.4 GB | 0.7 GB |
| large, 224 | 304 M | 1.22 GB | 178 | 2.1 GB | 0.8 GB |
| base, 448 | 87 M | (same) | 241 | 2.4 GB | 0.9 GB |
| giant, 224 (not run) | 1.1 B | 4.55 GB | about 600 to 700 (estimate) | about 5 GB in fp32, about 3 GB in fp16 (estimate) | |

- **Whole-run times:** about 1.5 minutes for small, about 3 minutes for base and about 6 minutes for large, patch
  scoring included. These are wall-clock, not exact.
- **Feature caches:** grow with model width. A full cache is 0.3 GB for small, 0.6 GB for base, 0.8 GB for large and
  2.3 GB for base at 448. They were deleted after each run to save disk; the JSON reports are kept.
- **Giant was skipped by the disk rule.** After the base-448 run the disk had 7.4 to 9.1 GB free; the figure moves
  with other work and swap on the shared laptop. The 4.55 GB download would leave under 8 GB. Swap was also nearly
  full (about 0.7 to 0.9 GB of 6 to 7 GB free), so a fp32 giant would have been risky either way. No fallback ids
  were needed: all three `facebook/dinov2-with-registers-*` repos exist.

### Results

Each cell is median rank / top 10 / AUC, where AUC = mean of 1 − (rank − 1)/(N − 1). Near duplicate has 66 queries
over 74 candidates. Atlas render has 13 queries over 239. reference plate has 10 queries over 394.

| Test | Mode | small 224 | base 224 | large 224 | base 448 |
|---|---|---|---|---|---|
| Near duplicate | raw | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 |
| | edges | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 |
| | patch | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 | 1 / 66 / 1.000 |
| Atlas render | raw | 9 / 7 / 0.886 | 8 / 7 / **0.912** | 8 / 7 / 0.838 | 4 / 8 / 0.892 |
| | edges | 10 / 8 / 0.873 | 22 / 5 / 0.848 | 22 / 5 / 0.810 | 7 / 7 / **0.912** |
| | patch | 19 / 4 / **0.863** | 25 / 4 / 0.783 | 42 / 4 / 0.712 | 39 / 3 / 0.756 |
| reference plate | raw | 96.5 / 2 / 0.754 | 18.5 / 4 / **0.883** | 8.5 / 5 / 0.875 | 35 / 4 / 0.870 |
| | edges | 156 / 2 / 0.670 | 99.5 / 3 / 0.678 | 23 / 5 / 0.701 | 18 / 5 / **0.806** |
| | patch | 38 / 3 / 0.786 | 51.5 / 4 / 0.769 | 32 / 3 / **0.802** | 76.5 / 3 / 0.703 |

reference ranks in raw mode, in pair order (tram-car s114, s45, s50; steamer s104, s182, s25; rowboat s104; tree
s178, s114; hull-on-stocks s25):

- small: 208, 16, 6, 1, 65, 223, 13, 136, 179, 128
- base: 26, 6, 1, 1, 4, 90, 11, 198, 78, 55
- large: 3, 1, 2, 1, 6, 11, 88, 320, 52, 19

### What this says

- **Small samples.** These are 10 and 13 query pairs. One pair moving 100 places moves the AUC by about 0.025, so
  differences under about 0.05 are noise. Only the near-duplicate test (66 queries) is a solid sample.
- **Near duplicates: no model helps.** All four runs are perfect; small is enough.
- **Atlas renders: bigger does not help.** Base is level with small in raw mode, and large is worse. Patch mode
  gets steadily worse as the model grows. Base at 448 lifts the median rank (4) and edges mode (0.91), but within
  the noise.
- **reference plates: the one clear gain, from small to base.** Raw mode goes from AUC 0.75 to 0.88, and the median
  rank from 96 to 18. Seven of ten plates land in the top 30, against three with small. The tram of s114, the
  steamer of s25 and the tree of s114 lift the most.
- **Large on plates: about the same as base.** Large has the best median (8.5): tram-car finds s45 first and s114
  third. It does not raise the AUC, and the rowboat (s104) and tree (s178) get worse. Large also lifts edges mode
  from base's 0.68 to 0.70, still the weakest mode.
- **448 px: not worth it at 4.6 times the cost.** It helps edges mode (0.81, perhaps because the line maps keep
  more detail) and hurts patch mode. Overall it is no better than base at 224.
- **Giant was not run.** With large adding nothing over base, giant is unlikely to pay for its 4.6 GB and roughly
  12 times base's time. This is an inference, not a measurement.

### Recommendation

- **base at 224 in raw mode for the drawing and atlas uses.** It costs about 52 ms per image, about 1.4 GB, and
  346 MB of weights. Even so, the plate test stays "a ranked list for a person to check": a median rank of 18 of 394
  on 10 pairs is a shortlist, not a score.
- **small for near-duplicate and regression checks,** where every model is perfect and small is 3.5 times faster.
- **Not large, giant or 448 px.** The stage 2 tool should default to base, with small as the cheap mode.
- **Next for the plate test:** crop plates to their object and add pairs before comparing models again.

Disk after these runs: weights for small, base and large in `.features/hf` (1.5 GB); caches cleared; 12 GB free at the end (it varied from 7.4 to 13 GB during the runs, with other work on the laptop). Even at 12 GB the giant download would leave under 8 GB.

## Owner's note for stage 2 (2026-11-16)

Compare patch tokens, not only the pooled embedding: a per-patch cosine between a module's render before and after a
change is a heatmap of where it changed. That makes the regression use show where to look, not just a score.
