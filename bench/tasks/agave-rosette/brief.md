## The object: an agave rosette

A large agave (century plant) as in the atlas's desert flora (FL51-FL56): a stemless rosette of thick, stiff,
sword-shaped leaves radiating from the centre at the ground.

What it is:
- **Leaves** in a spiral rosette, the outer ones spreading low and curving slightly up and out, the inner ones more
  upright, the innermost a tight cone of unopened leaves at the heart.
- Each leaf is **thick and fleshy**: wide at the base, tapering to a sharp **terminal spine**, channelled (concave)
  on the upper face, with **marginal teeth** along both edges (teeth as detail or small geometry), and faint
  imprints of neighbouring leaves (optional detail).
- A low mound of soil or gravel under it.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank.
- On the ground at z = 0, centred on x = y = 0.
- Controls: `diameter` 1.8 in [0.6, 3] (spread of the outer leaves); `leaves` 30 in [12, 48] (integer).
  The rosette must stay closed and valid across both ranges.
- Port: `base` at the origin facing -z, type `footing`, `{"width": 0.6}`.
- Materials: a green slot (`leaf`) for the leaves, `earth` / `sand` for the ground.

## How it is built
- The leaves grow from a short buried stem at the centre in a **spiral** (a golden-angle step between leaves), outer
  ones oldest and lowest, inner ones younger and more upright, the innermost folded into a tight cone.
- Each leaf is a thick tapering blade: concave on top, convex below, with marginal teeth and a terminal spine.

## Proportions (relative to `diameter`; derive them with `G.let`)
- Outer leaves about 0.45 to 0.55 x `diameter` long and about 0.08 to 0.12 x `diameter` wide at the base.
- Rosette height about 0.5 to 0.7 x `diameter`; the central cone about 0.15 x `diameter` across.
- Teeth every 20 to 40 mm along the margins; the terminal spine 30 to 60 mm.

## Must have
- A spiral rosette of thick channelled leaves, marginal teeth and terminal spines, the central cone, a ground mound.
  Keep it light for the engine: about 20k triangles or fewer (teeth and spines as detail, not geometry).
