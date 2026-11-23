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
