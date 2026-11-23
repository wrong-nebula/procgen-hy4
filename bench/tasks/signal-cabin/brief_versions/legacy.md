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
