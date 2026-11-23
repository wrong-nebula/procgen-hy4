## The object: a columnar desert cactus (saguaro type)

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

## How it is built
- One **main column** grows from the ground; each **arm** branches from the column at a shoulder, runs out roughly
  horizontally a short way, then bends and rises parallel to the column. The arm's bend is one smooth elbow, joined into
  the column without a crease or a visible seam (blend the shoulder, do not butt a cylinder against it).
- Ribs run the full length of column and arms, continuous round the elbow; areoles sit on the rib crests.

## Proportions (relative to `height`; derive them with `G.let`)
- Column diameter about 0.12 to 0.16 x `height` (at least 0.3 m); arms about 0.65 to 0.8 of the column's diameter.
- Arms branch between 0.3 and 0.6 x `height` and rise to 0.6 to 0.85 x `height`.
- Rib depth about 0.06 to 0.1 of the column's radius; areoles every 15 to 25 mm along each rib.

## Must have
- Ribs on column and arms, smooth elbows, domed crowns, areoles with spine clusters (detail), a ground patch.
