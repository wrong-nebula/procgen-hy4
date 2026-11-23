## The object: a timber cradle for a ship's tender on the dock floor

The cradle that holds a small boat (a ship's tender or launch) upright on the floor of a dry dock or a slipway (atlas
SY11, SY12; dock service): heavy timber baulks shaped to the hull, braced and bolted, with the boat sitting in it.

What it is:
- Two or three **cradle frames** across the boat, each a heavy timber sill on the floor with two **arms** rising to each
  side, their tops cut to the hull's curve (with felt or leather pads as detail).
- **Longitudinal timbers** tying the frames together, with diagonal **braces** and iron **straps and bolts** at the joints.
- **Wedges** under the sills and **shores** (props) from the floor to the boat's sides at the frames.
- The **boat**: a clinker-built tender (planks overlapping, as detail), with its keel, stem, transom, gunwale, thwarts
  (seats) and a rudder, sitting with its keel on keel blocks along the cradle's centre.

## How it is built
- The keel rests on keel blocks on the floor; the hull bears on the cradle arms' padded tops; the arms are tenoned into
  the sills and braced; shores take the boat's sides; wedges level the sills on the floor.

## Proportions (derive them from `length` with `G.let`)
- Boat beam about 0.28 to 0.33 x `length`, depth about 0.4 x beam; cradle frames at about 0.25 and 0.75 of the length
  (three frames above 7 m); baulks about 0.2 to 0.3 m square.

## Must have
- The hull with keel, stem, transom, gunwale, clinker planking (detail) and thwarts; the cradle frames with shaped arms
  and pads; longitudinal ties and braces; straps and bolts; keel blocks, wedges and shores. At least three material slots.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The boat lies along x (bow toward +x).
- On the dock floor at z = 0, centred on x = y = 0.
- Controls: `length` 6 in [4, 9] (the boat's length).
- Port: `floor` at the origin facing -z, type `footing`, `{"width": 2.5}`.
- Materials: `wood` for timber and boat, `iron` / `weathered-iron` for straps and bolts, a paint colour slot for the hull
  if you like.
