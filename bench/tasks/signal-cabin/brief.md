## The object: a railway signal cabin

A small signal box of the survey railway (atlas SY32; SS01): a two-storey cabin beside the track from which the
signalman works the levers.

What it is:
- A **lower storey (the locking room)** in brick or stone, with small windows, a door, and the lever rods and
  point rods emerging at its foot on the track side through openings in a plinth.
- An **upper storey (the operating room)**, in timber or iron framing, **glazed nearly all round** in a continuous
  band of windows with glazing bars, so the signalman sees the line; a frame of levers (a row of iron levers in a
  frame) visible inside through the glass.
- A **hipped (or gabled) slate roof** with deep eaves on brackets, a finial or a small ventilator, and a stove chimney.
- An **external stair** (timber or iron, with a handrail) up one end to a door and a small landing at the upper floor.
- A **nameboard** on the track side, with the cabin's name in letters (detail text).
- Fire buckets, a lamp, a downpipe: welcome detail.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The track side is the front (-y): the nameboard and the lever-rod openings face -y.
- On the ground at z = 0, centred on x = y = 0.
- Controls: `width` 6 in [4, 10] (along the track, x); `depth` 3.6 in [3, 5]; `height` 6.5 in [5, 8] (ground to the
  eaves).
- Port: `base` at the origin facing -z, type `footing`, `{"width": "$width"}`.
- Materials: `brick` / `chipped-limestone` for the base, `wood` for the cabin, `glass`, `slate` for the roof,
  `iron` for levers and stair.

## How it is built
- The **lower storey** (locking room) is load-bearing masonry; it carries the **operating floor** and the timber or iron
  framed **upper storey**, which carries the **roof**. The lever frame stands on the operating floor along the track side;
  rods from the levers run down through the locking room and out through openings in the plinth towards the track.
- The external **stair** rises along one end to a landing at the upper door.

## Proportions (relative to `width`, `depth` and `height`; derive them with `G.let`)
- Locking room about 0.4 to 0.5 x `height` tall; the operating room about 0.35 x `height`; the roof the rest.
- The glazing band about 0.55 to 0.7 of the operating room's height, in panes about 0.4 to 0.6 m wide.
- Eaves projecting about 0.3 to 0.5 m on brackets; stair about 0.8 to 0.9 m wide.

## Must have
- Brick or stone lower storey with small windows and a door, the lever rods leaving through the plinth, the glazed
  operating room with glazing bars and a visible lever frame inside, a hipped or gabled slate roof on brackets with a
  chimney, the stair with its handrail and landing, the nameboard. At least four material slots.
