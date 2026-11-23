## The object: an Art Nouveau metro entrance (a Guimard-style edicule)

A cast-iron and glass metro entrance in the manner of Hector Guimard (atlas IW04, IW14, N04, N05; plate s14): the
stair down to the platforms framed by an iron balustrade, two lamp standards and, over the stair, a glazed canopy.

What it is:
- A **stair opening** in the pavement: the first flight of steps going down (along +y) between stone or iron curbs.
- A cast-iron **balustrade** round three sides of the opening, open at the entrance end (-y), where people walk in
  and go down toward +y: panels of stems and leaves between posts, painted green, with a moulded top rail.
- Two **lamp standards** at the entrance (-y end), cast stems that branch at the top into a curved arm carrying an
  orange-red glass lamp, and between them an arched **sign** (an enamel plate on a cast frame) reading METROPOLITAIN.
- A **canopy** over the stair: a light iron frame carrying glass in an inverted, fan or "dragonfly" shape, supported on
  posts that rise from the balustrade.

## How it is built
- The balustrade posts stand on the curb and carry the panels and the rail; the canopy frame is carried by taller posts
  at the corners; the lamp standards stand at the entrance corners and carry the sign between them; the steps descend
  from the pavement level (z = 0) into the ground.

## Proportions (derive them from the controls with `G.let`)
- Opening `width` by `length`; balustrade about 0.9 to 1.0 m high; lamp standards about 3 to 3.5 m; the sign's top about
  0.3 m above the lamps' arms; canopy eaves about 2.6 to 3.2 m above the pavement.
- Steps about 0.17 m rise and 0.3 m going.

## Must have
- The descending steps, the balustrade with organic stem-and-leaf panels (geometry or strokes on panels), both lamp
  standards with their lamps, the arched sign with lettering (detail text), the glazed canopy with its frame. At least
  four material slots (iron or patina green, glass, opal or terracotta for the lamps, stone).

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The entrance faces -y.
- The opening is centred on x = 0, from y = -`length`/2 to +`length`/2; the pavement is at z = 0.
- Controls: `width` 2.2 in [1.6, 3.2]; `length` 5 in [3.5, 7].
- Ports: `pavement` at [0, -`length`/2, 0] facing -y, type `deck`, `{"width": "$width"}`; `stair` at
  [0, `length`/2, -`length`/2 * 0.55] facing +y, type `deck`, `{"width": "$width"}` (where the flight continues down).
- Materials: existing slots (`patina`, `iron`, `glass`, `opal`, `terracotta`, `chipped-limestone`...).
