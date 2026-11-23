## The object: a locomotive turntable in its pit

A late-19th-century railway turntable (atlas SS13-SS16 hydraulic lift and yard group): a girder bridge carrying one
track, pivoting on a central king-pin in a circular stone-lined pit, turned by hand.

What it is:
- A **circular pit**: a stone or brick retaining wall round its edge with a coping at rail level, a paved or
  cinder floor below, and a **ring rail** at the bottom near the wall on which the bridge's end wheels run.
- The **bridge**: two deep riveted plate girders (or a lattice pair) spanning the pit, deeper at the middle, with
  cross girders between them and a timber deck carrying **one standard-gauge track** (rails on sleepers or a
  continuous timber bearer). At each end, **carriage wheels** on the ring rail.
- The **centre pivot**: a cast-iron king-pin and bearing on a masonry pier at the middle.
- **Turning gear**: a hand capstan or long push levers at one end, and a catch (locking bolt) that lines the bridge
  up with the approach tracks.
- Around the coping: the ends of **approach tracks** meeting the pit at the four compass points.
- Rivets, bolts and plank joints as detail.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The ground and the coping/rail top are at z = 0; the pit goes down below it.
- The pivot at x = y = 0.
- Controls: `diameter` 15 in [10, 20] (pit diameter); `angle` 0 in [0, 360] (the bridge's heading, 0 along x).
- Ports: `track-a` and `track-b` at the bridge's two ends, at rail level, facing outward along the bridge (they turn
  with `angle`), type `track`, `{"gauge": 1.435}`; `approach-e`, `approach-n`, `approach-w`, `approach-s` at the pit
  edge on the +x, +y, -x, -y axes, facing outward, type `track`, `{"gauge": 1.435}`.
- Materials: `iron` / `weathered-iron` for the bridge, `wood` for the deck and sleepers, `steel` for the rails,
  `chipped-limestone` or `brick` for the pit, `ballast` or `concrete` for the floor.
