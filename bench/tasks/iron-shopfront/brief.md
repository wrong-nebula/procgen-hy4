## The object: a painted-iron and glass shopfront of a Brussels town house

The ground-floor shopfront of a late-19th-century Brussels house (atlas FD01, FD25, B05, BB01, FD26): a cast and
wrought iron frame with large plate-glass windows, a recessed entrance door, a stall riser below and a fascia with the
shop's name above.

What it is:
- An **iron frame** of slender cast-iron columns or pilasters with capitals, carrying a **lintel beam** across the
  opening, with Art Nouveau ornament in the spandrels or brackets (whiplash curves, stems).
- Two **display windows** in large panes with slender glazing bars and a transom row of small panes (leaded or
  coloured glass) above them.
- A **recessed entrance** in the middle: a glazed door set back about 0.6 to 1 m, with returns (side windows) and a
  tiled floor in the recess.
- A **stall riser** under the windows (panelled timber or stone), and a **fascia** board above the transom with the
  shop's name in raised or painted letters (detail text) and a cornice over it.
- A rolled-up blind box under the fascia and a house number on the frame.

## How it is built
- The columns stand on the stall riser's plinth (or the pavement) and carry the lintel, which carries the facade above;
  the windows sit in the frame between columns; the door is hung in a frame at the back of the recess.

## Proportions (derive them with `G.let`)
- Columns about 0.12 to 0.18 m wide; the stall riser about 0.45 to 0.6 m tall; the transom row about 0.12 to 0.15 x
  `height`; the fascia about 0.08 to 0.12 x `height`.

## Must have
- The iron frame with columns and capitals and ornament, plate glass with glazing bars, the leaded transom row, the
  recessed glazed door with returns and tiled floor, the stall riser, the lettered fascia with its cornice. At least
  four material slots.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The shopfront's face is the plane y = 0 and the street is on its -y side; the house wall behind is at y > 0.
- Standing on the pavement at z = 0, centred on x = 0.
- Controls: `width` 4.5 in [3, 7]; `height` 3.6 in [3, 4.6]; `name` 0 in [0, 2] (integer: which of three shop names to
  letter on the fascia).
- Port: `wall` at [0, 0, 0] facing -y, type `wall`, `{"width": "$width", "height": "$height"}`.
- Materials: `iron`, `black`, `glass`, `wood`, `tile`, `bronze`, `marking`.
