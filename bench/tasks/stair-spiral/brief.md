## The object: a cast-iron spiral stair

A late-19th-century cast-iron spiral stair (atlas IT02, IT07, IT08), as in a glasshouse gallery, a signal tower or a
library: open treads turning round a central column, with a balustrade, from a floor to a landing above.

What it is:
- A **central column** (a cast-iron tube with a base plate and a cap).
- **Treads**: open cast-iron plates, each a sector of a ring, with a pattern of openings or raised chequer (detail or real
  openings), each cast with a hub ring that stacks on the column.
- **Balustrade**: one baluster per tread at its outer end (turned or cast, with a collar), carrying a continuous helical
  **handrail** (timber or iron) that follows the stair's pitch.
- A **landing** plate at the top, with a short straight length of rail along its open edge.

## How it is built
- Each tread's hub ring slides over the column and sits on the one below, so the treads are carried by the column; the
  balusters stand on the treads' outer ends and carry the handrail; the landing is carried by the column and by the floor
  it serves (it ends at the top port).

## Proportions (derive them from the controls with `G.let`)
- Rise per tread about 0.18 to 0.22 m: the tread count is `rise` / that, rounded; the turn per tread follows from `turn`.
- Column about 0.10 to 0.14 m across; treads from the column to `diameter`/2; handrail about 0.9 m above the treads'
  nosings; balusters about 20 to 30 mm across.

## Must have
- The column with base and cap, every tread with its hub and its pattern, a baluster on every tread, a continuous helical
  handrail, the landing with its rail. At least three material slots.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank.
- The column's axis is x = y = 0; the stair starts on the floor at z = 0 with its first tread toward -y and climbs
  anticlockwise seen from above; the landing's top is at z = `rise`.
- Controls: `rise` 3.2 in [2.4, 5]; `diameter` 1.8 in [1.4, 2.4]; `turn` 360 in [270, 450] (degrees from the first tread
  to the landing).
- Ports: `foot` at [0, -`diameter`/4, 0] facing -z, type `deck`, `{"width": "$diameter"}`; `landing` at
  [0, 0, `rise`] facing +z, type `deck`, `{"width": 0.8}`.
- Materials: `iron` / `black` for the casting, `wood` or `bronze` for the handrail.
