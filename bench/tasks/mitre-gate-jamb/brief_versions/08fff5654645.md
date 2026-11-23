## The object: the hollow-quoin jamb of a mitre lock gate

A canal lock is closed by a pair of mitre gates (`lock_gates()` in `recipes/build_site_kits.py`: two
leaves that meet at a shallow V pointing upstream). Each leaf turns on a vertical **heel post** at the side
wall of the lock. This module is the masonry and ironwork of the wall at that point: the **jamb** a leaf hangs on.

What it is (dressed granite or limestone, wrought iron, late 19th century):
- A section of the lock's side wall, in large dressed blocks (coursing as detail), with a **coping** of larger
  stones at the quay edge.
- The **hollow quoin**: a vertical concave groove (a quarter to half cylinder) cut into the wall face, running
  the full height, in which the round heel post of the leaf turns. It is built of specially cut quoin stones,
  finer than the walling around it.
- At the bottom, a **pintle** (pivot) on a sill or heel stone at the floor of the lock, on which the heel post
  stands.
- At the top, an iron **anchor**: a collar strap that holds the top of the heel post against the quoin, tied
  back into the coping by two anchor bars (straps with bolts or wedges as detail).
- A shallow **gate recess** in the wall face beside the quoin, where the open leaf lies flush with the wall.
- **Ladder irons** in the recess (rungs set into the masonry, running from the floor to the coping), and an iron
  rubbing strip or a mooring ring.

Dimensions and frame (fixed to match `lock-gates`):
- Z-up, metres. The quay (top of the coping) is at z = 0 and the lock floor at z = -4.85. Expose the chamber
  depth as a control `depth` 4.85 in [2, 10] that moves the floor; everything must follow it.
- The lock chamber lies on the +y side of the wall face; the wall face is the plane y = 0 and the walling runs
  back (−y) at least 1.5 m. The jamb section is about 4 m long along x, with the hollow quoin's axis at
  x = 0, y = 0.25 (inside the chamber edge) so a heel post of radius about 0.25 m turns in it.
- Ports: `heel` at `[0, 0.25, -depth]` facing +z, type `hinge`, `{"radius": 0.25, "height": "$depth"}`
  (where the leaf's heel post stands); `quay` at `[0, -0.75, 0]` facing +z, type `deck`, `{"width": 1.5}`.
- Materials: use the existing slot names (`chipped-limestone`, `iron`, ...). `pgm materials` lists them.

## How it is built
- The leaf's **heel post** stands on the **pintle**, which is set in the **heel stone** at the lock floor, and turns in the
  **hollow quoin**. At the top, the **anchor collar** wraps the post and is tied back into the coping by **two anchor
  bars**, bolted or wedged into the stone: they take the leaf's pull.
- The quoin stones are cut to the curve of the post and are finer than the walling round them. The **coping** caps the
  wall and projects slightly over the face.

## Proportions (derive them from `depth` and the post radius with `G.let`)
- Hollow quoin radius: the heel post's 0.25 m plus a little clearance (0.26 to 0.28 m), a quarter to a half cylinder.
- Walling courses about 0.35 to 0.5 m high; quoin stones in the same courses, joints aligned.
- Coping about 0.4 to 0.6 m deep, projecting 0.05 to 0.1 m over the face.
- Anchor bars about 1.2 to 2 m long into the coping; the collar about 60 to 100 mm wide.

## Must have
- Coursing joints on the walling; quoin stones distinguished from the walling (a finer finish, a different slot or
  their own joints); the coping; the pintle and heel stone; the anchor collar with both bars and their bolts or wedges;
  the gate recess; the ladder irons; a rubbing strip or a mooring ring. At least three material slots.
