## The object: a glasshouse roof vent sash with its rack-and-pinion opening gear

A ventilating sash in a glasshouse roof (atlas G04, G10; the greenhouse service plant): a hinged glazed light in the
roof slope, opened by a rack-and-pinion gear worked from below by a crank or a long rod.

What it is:
- A short length of **roof**: two glazing bars (rafters) at the sides and a ridge or purlin at the top, with fixed glass
  panes in the bays to either side of the vent (part of the module).
- The **sash**: an iron or timber frame with glass, hinged along its top edge to the purlin.
- The **gear**: a curved or straight **rack** fixed to the sash's bottom rail, engaging a **pinion** on a horizontal
  **shaft** that runs along under the purlin in bearings; a crank wheel or a rod with a crank at the end of the shaft.

## How it is built
- The sash hangs on hinges on the purlin and rests on the frame below when closed; turning the shaft turns the pinion,
  which drives the rack and pushes the sash open about its hinge line. With `open`, the sash and its rack rotate about the
  hinge; the shaft, bearings, pinion and roof stay (the pinion may turn).

## Proportions (derive them with `G.let`)
- Sash about 0.9 to 1.2 m along the slope and as wide as the bay; frame sections about 40 to 60 mm.
- Rack about 0.3 to 0.45 m long, pinion about 60 to 90 mm across; shaft about 25 mm with bearings every bay.

## Must have
- Fixed glazing and bars to both sides, the sash with its glass and hinges, the rack engaged on the pinion, the shaft and
  its bearings, the crank. At least three material slots (iron, glass, and bronze or steel for the gear).

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The roof slope faces -y (down-slope toward -y) and rises toward +y; the hinge line runs along x.
- The hinge line is at [x, 0, 0] (the purlin's top), the bay centred on x = 0.
- Controls: `width` 1.2 in [0.8, 1.8] (the vent bay); `pitch` 26 in [20, 40] (roof slope, degrees); `open` 0 in [0, 1].
- Ports: `roof-west` at [-`width`/2 - 0.6, 0, 0] facing -x and `roof-east` at [`width`/2 + 0.6, 0, 0] facing +x, type
  `deck`, `{"width": 1.2}` (where the roof continues).
- Materials: `iron`, `glass`, `bronze`.
