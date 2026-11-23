## The objects: a desert-flora family for the glasshouse beds

Four plants from the atlas's desert flora (FL51-FL56), built together as one kit so they read as one planting when set
side by side in a dry bed of the glasshouse: the same greens and soil, the same way of making ribs, spines, teeth and
areoles (as surface detail on clean meshes), the same scale conventions. Share constants and helpers in your builder.

Common to all four:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side. Each module stands on the ground at z = 0, centred on x = y = 0, with a port `base` at the origin facing -z, type `footing`.
- Materials: a green slot (`leaf`, or `grass` for a lighter green) for the plants, `earth` or `sand` for the ground
  patch at the foot, `bronze` or a pale slot for spines and teeth.
- Must have: each plant's characteristic form (below), its surface detail, and a ground patch at its foot.

### `cactus-column`: a columnar desert cactus (saguaro type)

A tall ribbed cactus of the kind in the atlas's desert flora (FL51-FL56): one main column rising from the ground,
with a few upturned arms, as it grows in a dry garden or a desert bed in the glasshouse.

What it is:
- A **main column**, round in plan but **ribbed**: 12 to 24 vertical ribs with grooves between them, the ribs rounded
  and the column tapering slightly to a domed, slightly woolly crown.
- **Arms**: each leaves the column at a shoulder, runs out a short way, bends and rises parallel to the column,
  ribbed like it and ending in its own domed tip. Arms differ a little in height and length.
- **Areoles and spines** along each rib crest at regular spacing: small felted spots with clusters of short spines.
  These are surface detail (fasteners, stamps or ticks on the ribs), not geometry.
- At the foot: the column flaring slightly where it enters the ground, a little bare earth or gravel round it.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank.
- Standing on the ground at z = 0, the column's axis at x = y = 0.
- Controls: `height` 4 in [1.5, 8] (ground to crown); `arms` 2 in [0, 4] (integer); `ribs` 16 in [10, 24] (integer).
  Arms scale with the height; every value in every range must pass `pgm sweep`.
- Port: `base` at the origin facing -z, type `footing`, `{"width": 0.8}`.
- Materials: `leaf` (or another green slot) for the plant, `earth` or `sand` for the ground; spines can use `bronze`
  or a pale slot. `pgm materials` lists the slots.


### `agave-rosette`: an agave rosette

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


### `cactus-barrel`: a barrel cactus (Ferocactus / Echinocactus type)
- A squat **globe or short barrel**, 0.3 to 1.2 m tall, with 13 to 30 deep, sharp **ribs**, a slightly sunken woolly
  **crown** with a ring of yellow flowers or fruit at the top, and stout curved **central spines** on areoles along every
  rib crest (detail), the spines longest near the crown.
- Controls: `height` 0.7 in [0.3, 1.2]; `ribs` 20 in [13, 30] (integer). Port `base`, `{"width": 0.6}`.

### `aloe`: an aloe (Aloe ferox / arborescens type)
- A **rosette of fleshy, lance-shaped leaves** on a short trunk clothed in dead leaves, the leaves curving out and down,
  toothed on their edges, with **one to three flower spikes** (candelabra racemes of red-orange tubular flowers) rising
  above the rosette.
- Controls: `height` 1.6 in [0.6, 3] (ground to the top of the rosette); `spikes` 2 in [0, 3] (integer). Port `base`, `{"width": 0.7}`.
