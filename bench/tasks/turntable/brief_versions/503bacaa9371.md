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

## How it is built
- The **bridge** (two girders with cross girders and a deck carrying the track) rests at its middle on the **king-pin**
  and bearing on the centre pier, and at its two ends on **carriage wheels** running on the **ring rail** at the pit
  bottom. With `angle`, the whole bridge (girders, deck, track, carriage wheels and turning gear) turns about the pivot;
  the pit, its wall, the ring rail and the approach tracks stay.
- The track on the bridge lines up with an approach track when `angle` is 0, 90, 180 or 270.

## Proportions (relative to `diameter`; derive them with `G.let`)
- Girders about 0.08 to 0.1 x `diameter` deep at the middle, tapering to about half at the ends.
- Pit depth about 0.06 to 0.1 x `diameter` (at least 0.9 m); ring rail about 0.3 to 0.5 m inside the wall.
- Track gauge 1.435 m (given); deck slightly narrower than the girders' spacing.

## Must have
- Riveted girders (rivets as detail), cross girders, a planked deck with sleepers and rails, the king-pin and centre
  pier, carriage wheels on the ring rail, the turning gear and locking catch, the coped pit wall, four approach tracks.
