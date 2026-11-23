## Working method: plan, then do-confirm

1. **Plan before you code.** Write `PLAN.md` first: the parts the object is made of, as a draughtsman would
   list them, not a symbol of the object. For each part: what it is, its size and position in metres (taken
   from the brief where the brief gives them), its material slot, and how you will build it in the DSL.
   Expect a few dozen parts and a list of the surface detail that lies on them. Then build from the plan.
2. **Do-confirm at two pause points.** `CHECKLIST.md` lists what the object must have. At each pause point
   run `python3 briefcheck.py` (it checks the brief's dimensions, controls and ports at the defaults and at
   the ends of every range, plus check, sweep and audit), then go through `CHECKLIST.md` item by item
   against your renders. Write each item as pass or fail under a `## Checks` heading in `PLAN.md`, and fix
   every fail before you go on.
   - Pause point 1: after your first build that passes `pgm check`.
   - Pause point 2: before you write `NOTES.md`.

You may create and edit `PLAN.md` as well as the files listed under the rules below.
