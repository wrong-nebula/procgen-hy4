## The object: one bay of a stone street arcade with an arch and spandrels

One bay of a covered street arcade in a late-19th-century city of dressed limestone and painted iron
(think the arcaded street fronts of Paris and the Brussels galleries). Bays repeat along a street, so each bay
carries **half a pier at each end** and neighbours share them.

What it is:
- Two half piers of coursed stone, with a base course and an **impost** (a moulded block or band) where the
  arch springs.
- A **stone arch** between the piers, built of **voussoirs** (wedge-shaped stones radiating from the arch
  centre) with a projecting **keystone** at the crown. Semicircular or segmental, your choice; the clear opening
  should read as a walkway people pass through.
- **The walk** runs along the street (along x), under one bay after another: the arches face the street, and each bay's
  sides at x = ±pitch/2 stay open between the street piers and the building wall, so neighbouring bays make one
  continuous covered walk.
- **Spandrels**: the walling that fills the triangles between the arch's extrados, the piers and the level above.
  It should read as masonry (coursed, or panelled) and not as a plain slab.
- **The back of the walk** is part of the bay too and is seen from the street through the arch: the building wall at
  y = 0 with its own detail (a shopfront, doors or windows, pilasters, a plinth), the ceiling or vault over the walk,
  and the returns where the walk meets the piers. It should be finished, not a blank panel or an open void.
- A string course or cornice across the top of the bay, and a roof or vault over the walk back to the building
  wall behind it.
- An iron tie rod across the walk at springing level, anchored with a plate at each end, and the detail that helps
  it read (moulding on the arch ring, joint lines).

Dimensions and frame (fixed so bays chain with each other):
- Z-up, metres, standing on the pavement at z = 0. x runs along the street front, centred on the bay.
  The **street face** is at y = -depth and the **building wall** at y = 0.
- Controls: `pitch` (bay width, pier centre to pier centre) 3.6 in [2.4, 6]; `height` (pavement to the top of
  the bay) 4.2 in [3, 5.4]; `depth` (street face to wall) 2.2 in [1.2, 4]. The arch should follow `pitch` and
  `height` across their whole ranges.
- Ports: `west` at `[-pitch/2, -depth/2, 0]` facing -x and `east` at `[pitch/2, -depth/2, 0]` facing +x, both of
  type `deck` with `{"width": "$depth"}`.
- Materials: use the existing slot names (`chipped-limestone`, `iron`, ...). `pgm materials` lists them.

## How it is built
- The **street wall** (the two half piers, the spandrels and the arch, all in one plane at the street face) carries the
  string course, the cornice and the street edge of the roof or vault. The **building wall** at y = 0 carries the other
  edge. The ceiling or vault spans the walk between them.
- The arch ring **bears on the imposts**: its voussoirs radiate from the arch centre, the keystone sits at the crown, and
  the ring's ends sit on the impost tops, not in the air beside them.
- The tie rod runs across the walk (along y) at springing level, from a pier to the building wall, and holds the arch's
  thrust; its anchor plates sit on the faces it passes through.

## Proportions (derive them from the controls with `G.let`, so every bay in the range keeps them)
- Half pier, along x: about 0.09 to 0.12 x `pitch`, so the clear opening is about 0.75 to 0.82 x `pitch`.
- Arch ring depth (intrados to extrados): about 0.10 x the clear span, at least 0.3 m. Keystone projects 0.05 to 0.10 m.
- Springing (impost top): about 0.45 to 0.55 x `height`; the impost block about 0.06 x `height` deep.
- String course plus cornice: about 0.10 to 0.14 x `height`; the cornice projects about 0.06 x `height`.
- Street wall thickness: about 0.25 x `depth`, between 0.35 and 0.6 m.

## Must have
- Channel joints or coursing on the piers and spandrels; a moulded impost; separate voussoirs with a projecting keystone;
  a moulded cornice; the finished back of the walk (above); a ceiling or vault seen through the arch; the pavement under
  the walk; the tie rod. At least four material slots (for example dressed stone, rougher walling, iron, and a roof or
  pavement material).
