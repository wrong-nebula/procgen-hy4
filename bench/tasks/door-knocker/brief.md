## The object: a cast bronze door knocker on its back plate

A late-19th-century door knocker (atlas IW03, IW12, IW13; door family D05): a shaped bronze ring or hammer hanging from
a decorative mount on a back plate, with a strike plate below it, screwed to a door.

What it is:
- A **back plate**: a shaped cast plate (a cartouche, a scrolled shield or a leaf) with a moulded rim, screwed to the door.
- A **mount**: a boss or a cast head (a lion, a mask or a stylised flower) at the top of the plate, with a **pivot pin**
  across it.
- The **knocker**: a heavy ring or a hammer-shaped bar hanging from the pin, with a thickened striking end.
- A **strike plate** (a stud or a small plate) below, where the knocker hits.
- Screw heads, the casting's chased detail: surface detail.

## How it is built
- The plate is fixed to the door face (y = 0); the mount is cast with the plate or bolted through it; the knocker
  hangs on the pin and rests against the strike plate. With `lift`, the knocker swings about the pin's axis (along x)
  away from the door.

## Proportions (derive them with `G.let`)
- Back plate about 0.12 to 0.2 m wide and 0.2 to 0.3 m tall; knocker ring about 0.1 to 0.15 m across, section 12 to 18 mm.
- Pin about 0.04 to 0.06 m in front of the plate; strike plate about 0.1 to 0.15 m below the pin.

## Must have
- The shaped back plate with its rim, the decorative mount, the pin, the knocker, the strike plate, screws (detail).
  At least two material slots (bronze, and iron or black for pins and screws).

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The door face is the plane y = 0 and the knocker stands in front of it (-y).
- The pin's centre at [0, -`reach`, 0], with z = 0 at the pin.
- Controls: `scale` 1 in [0.7, 1.4] (scales the whole knocker); `lift` 0 in [0, 1] (0 resting, 1 lifted 60 degrees).
- Port: `mount` at [0, 0, 0] facing -y, type `fitting`, `{"width": 0.16}`.
- Materials: `bronze`, `patina`, `iron`, `black`.
