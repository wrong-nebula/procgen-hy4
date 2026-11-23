## The object: a catch basin (gully) in a glasshouse floor

The cast-iron gully that takes rainwater from a column's downpipe and the floor's run-off in a 19th-century
glasshouse (atlas G05, G10; the greenhouse service plant): a grated pit set into a stone floor.

What it is:
- A square of the **stone floor** (flags, joints as detail) with a **raised kerb or frame** round the gully.
- A **cast-iron grating** in a frame, a pattern of bars or a pierced decorative plate (geometry, with real openings),
  sitting flush with the floor.
- Below it, the **sump**: a brick- or stone-lined pit you can see into through the grating, with water standing in it.
- A **downpipe shoe**: a cast-iron downpipe coming down beside the gully (from a column above, cut off at the top of
  the module) and turning into the pit through a shoe, and an **outlet pipe** leaving the pit low on one side.
- Bolts, the maker's mark on the grating, lifting slots: detail.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The floor's top is at z = 0; the gully goes down below it.
- Centred on x = y = 0; the downpipe stands at the back (+y side) of the gully.
- Controls: `size` 0.6 in [0.4, 0.9] (grating width); `depth` 0.8 in [0.5, 1.4] (floor to the sump bottom).
- Ports: `floor` at the origin facing +z, type `deck`, `{"width": 1.2}`; `downpipe` at the top of the downpipe
  facing +z, type `pipe`, `{"diameter": 0.1}`; `outlet` at the outlet's end facing outward, type `pipe`, `{"diameter": 0.15}`.
- Materials: `iron` / `black` for the ironwork, `chipped-limestone` / `pier-ashlar` for the floor, `brick` for the
  sump, `water` for the water.
