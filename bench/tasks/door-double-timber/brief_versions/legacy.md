## The object: a pair of panelled timber doors in a dressed-stone surround

A late-19th-century double front door (atlas RD01, RD03, FD05, FD13): two timber leaves in a stone doorway, as on a
Brussels town house or a public building.

What it is:
- A **stone surround**: jambs and a lintel (or a flat arch with a keystone), with a moulded architrave round the
  opening and a plinth block at the foot of each jamb; a stone threshold step.
- **Two door leaves** meeting in the middle, each a **framed and panelled** timber door: stiles, rails and raised or
  fielded panels (two or three per leaf), an **upper glazed light** with glazing bars, a moulded meeting stile on one
  leaf covering the joint.
- A **fanlight or transom** above the leaves (glazed, with bars), under the lintel.
- **Ironmongery**: butt hinges on the outer stiles (three per leaf), a knob or ring pull on each leaf, a letter plate
  and an escutcheon. Screws and nails as detail.
- The back (inside) face of the leaves is panelled too.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The doorway is in the wall plane y = 0; the leaves sit in it, closed.
- Standing on the ground at z = 0, centred on x = 0.
- Controls: `width` 1.8 in [1.2, 2.6] (clear opening); `height` 3.0 in [2.4, 3.8] (clear opening, to the transom's top);
  `open` 0 in [0, 1] (0 closed, 1 both leaves swung 90 degrees inward, to +y, on their hinges).
- Port: `wall` at the origin facing -y, type `wall`, `{"width": "$width", "height": "$height"}`.
- Materials: `wood` for the leaves, `pier-ashlar` or `chipped-limestone` for the stone, `glass`, `iron` or `bronze`
  for the ironmongery.
