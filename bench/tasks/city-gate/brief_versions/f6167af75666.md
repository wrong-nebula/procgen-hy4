## The object: a neoclassical city toll gate (barrière)

One of the toll houses of a late-18th-century city wall, after Ledoux's barrières of Paris and Boullée's austere
geometry (atlas MN14-MN19, UT08, UT09): a massive, simple block of stone that the road passes through.

What it is:
- A **cubic or square block** of heavy, **banded (rusticated) masonry**: deep horizontal channels between courses.
- A **central passage** through it for the road, along y, under a **semicircular arch** with large plain voussoirs
  and a keystone; the passage is open from the front (-y) to the back (+y) so the road runs through.
- On both the front and the back face, a **portico**: four to six thick, unfluted (Tuscan or primitive Doric)
  **columns** with plain capitals, carrying an **entablature** (architrave, frieze, cornice) and a low **triangular
  pediment**.
- Above, a **low attic** or **drum** (cylindrical, with a ring of small windows or a colonnade) crowning the block.
- A few square windows on the sides, set deep in the rusticated wall; a plinth course at the foot.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The road runs along y through the passage.
- On the ground at z = 0, centred on x = y = 0.
- Controls: `width` 16 in [11, 22] (along x); `height` 14 in [10, 18] (ground to the top of the attic or drum);
  `columns` 4 in [4, 6] (integer, per portico).
- Ports: `road-front` at [0, -depth/2, 0] facing -y and `road-back` at [0, +depth/2, 0] facing +y (depth is the
  block's own depth along y), type `deck`, `{"width": 5}`.
- Materials: `chipped-limestone` / `pier-ashlar` / `stone` for the masonry, `slate` or `copper` for any roof.

## How it is built
- The **block** is solid masonry; the **passage** is a barrel vault through it from front to back, springing from its
  side walls; each **portico** stands on the plinth in front of a face, its columns carrying the entablature, which
  carries the pediment. The attic or drum stands on the block's top, set back from the faces.

## Proportions (relative to `width` and `height`; derive them with `G.let`)
- Passage about 0.3 to 0.35 x `width` wide, its crown about 0.45 to 0.55 x `height`.
- Columns about 1/7 of their height in diameter (Tuscan); intercolumniation about 2 to 2.5 diameters; the entablature
  about 1/4 of the column height; the pediment pitch about 13 to 17 degrees.
- Rustication courses about 0.45 to 0.6 m with channels about 50 to 80 mm deep.

## Must have
- Banded rustication, the arched passage open through the block with plain voussoirs and a keystone, a portico with
  columns, entablature and pediment on both faces, the attic or windowed drum, deep-set windows, a plinth course.
