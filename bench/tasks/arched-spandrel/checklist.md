# Checklist: arched arcade bay (do-confirm)

Confirm each item against your renders and `briefcheck.py`. Mark pass or fail in `PLAN.md` under `## Checks`.

1. **Half piers** at both ends (x = ±pitch/2), coursed stone with a base course at the pavement; neighbours
   would share them.
2. **Impost** on each pier: a moulded block or band where the arch springs.
3. **Arch ring of separate voussoirs** radiating from the arch centre, with a **projecting keystone** at the
   crown. Not a single curved band.
4. **Clear opening** reads as a walkway people pass through (head height under the crown, not a slot).
5. **Spandrels** between the extrados, the piers and the level above read as masonry (coursed or panelled),
   not a plain slab.
6. **String course or cornice** across the top, and a **roof or vault** over the walk from the street face
   (y = -depth) back to the wall (y = 0).
7. The arch **follows pitch and height** across their ranges: look at renders with `--set pitch=2.4`,
   `--set pitch=6`, `--set height=3` and `--set height=5.4`.
8. `python3 briefcheck.py` reports no misfits, and `pgm check`, `pgm sweep` and `pgm audit` pass.
