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
