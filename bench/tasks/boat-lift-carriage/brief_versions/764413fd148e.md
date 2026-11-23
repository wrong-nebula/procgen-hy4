## The object: the carriage of a hydraulic boat lift

The moving part of a 19th-century hydraulic boat lift (atlas SS10, SS13-SS16; after the Anderton and the Belgian
Canal du Centre lifts): a riveted iron **trough** full of water, carried on a single large **hydraulic ram**, with
**guide frames** at its ends; boats float into it, and it rises and falls between two canal levels.

What it is:
- The **trough** (caisson): a long box girder of riveted iron plate with stiffening ribs outside, open at the top,
  filled with water, closed at each end by a vertical **lifting gate** in a frame.
- Under its middle, the **ram**: a large vertical cylinder (piston) on a cross girder under the trough, rising out of a
  **press cylinder** with a gland at its top.
- At each end, **guide shoes** running on vertical guides (part of the module: short lengths of the guide columns).
- Walkways with railings along both sides of the trough; capstans or bollards at the ends.

## How it is built
- The ram pushes up the cross girder, which carries the trough; the guide shoes keep it plumb on the guides; the gates
  slide up in their frames to let a boat in or out. With `level`, the trough, ram and everything on it move up the guides;
  the press cylinder and the guide columns stay.

## Proportions (derive them with `G.let`)
- Trough `length` by about 0.25 x `length` wide and 0.1 x `length` deep; ram about 0.06 to 0.08 x `length` across;
  stiffeners every 1 to 1.5 m; rivets as detail.

## Must have
- The riveted trough with external stiffeners and water inside, both end gates in their frames, the ram and press
  cylinder with gland, the cross girder, guide shoes on guide columns, side walkways with railings. At least four
  material slots (iron, water, steel or bronze for the ram, wood or iron plate for walkways).

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The trough runs along x.
- The press cylinder stands on the ground at z = 0 at x = y = 0.
- Controls: `length` 40 in [25, 70]; `level` 0.5 in [0, 1] (the trough's position between its low and high stops, a
  travel of about 0.4 x `length`).
- Ports: `canal-west` at the trough's west end facing -x and `canal-east` at its east end facing +x, type `water`,
  `{"width": 6, "height": 2.5}` (they move with `level`).
- Materials: `iron`, `weathered-iron`, `water`, `steel`, `wood`.
