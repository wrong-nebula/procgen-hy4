## The object: a quayside reeving block for a gate winch's haul line

A lock gate on this dock is hauled open and shut by a winch on the quay (`gate-winch` in
`recipes/build_machinery_kits.py`). Its wire rope cannot run straight to the gate leaf, so it is **reeved**
(passed) round a heavy iron block anchored to the quay that turns it through an angle and leads it
down toward the gate. This module is that block.

What it is (late-19th-century cast and wrought iron, on dressed limestone):
- A **stone bed block** set into the quay, with an iron **bedplate** bolted down onto it (bolt heads or nuts
  as detail).
- Two cast-iron **cheeks** standing on the bedplate, with stiffening ribs or a shaped outline, and a
  **sheave** (grooved pulley wheel) between them on a pin. The sheave is about 0.7 m across, with a
  visible groove for a 56 mm rope, a boss and either spokes or a pierced web. The pin ends show as nuts,
  collars or a cotter.
- Something that keeps the rope in the groove: a guard bar or a cover strap across the top of the cheeks.
- The rope itself: a short lead in, wrapped round part of the sheave in its groove, and a lead out.

Dimensions and frame:
- Z-up, metres. The bed block's top is flush with the quay at z = 0 (the block itself goes down below it);
  the bedplate and everything on it stand above z = 0. The sheave axle is horizontal, along x.
- Ports: `base` at the origin facing -z, type `footing`, `{"width": <bed block width>}`, with `up` = `(1, 0, 0)`
  (look at how `gate_winch()` declares its `base` port); `lead-in` and `lead-out`, type `cable`,
  `{"diameter": 0.056}`, where the rope arrives at and leaves the sheave, each facing along the rope's
  direction away from the block.
- Materials: use the existing slot names (`iron`, `steel`, `bronze`, `chipped-limestone`, ...).
  `pgm materials` lists them.
- A control for the angle the rope is turned through, if you model the rope, is welcome; keep every value in
  its range valid under `pgm sweep`.

## How it is built
- The rope's pull goes into the **sheave** through its groove; the sheave turns on a **pin** that passes through both
  **cheeks** and is held by nuts, collars or a cotter on the outside; the cheeks stand on the **bedplate** and are bolted
  to it; the bedplate is held down by bolts into the **bed stone**, which is set into the quay with its top flush at z = 0.
- The guard bar or cover strap spans the cheeks above the sheave so the rope cannot jump out of the groove.

## Proportions (relative to the sheave, about 0.7 m across; derive them with `G.let`)
- Cheeks: about 1.1 to 1.3 x the sheave diameter tall and about 1.2 x long; about 40 to 60 mm thick, with ribs or a shaped
  outline.
- Pin: about 0.12 to 0.15 x the sheave diameter across; boss about twice the pin.
- Groove: a little wider than the 56 mm rope; the rim about 0.1 x the sheave diameter deep.
- Bedplate: about 1.5 to 1.8 x the sheave diameter long; bed stone larger than the bedplate on every side.

## Must have
- The groove, the boss, spokes or a pierced web, the pin with its nuts or collars, the guard, the bedplate bolts, the bed
  stone, and the rope (lead in, wrap, lead out). At least three material slots.
