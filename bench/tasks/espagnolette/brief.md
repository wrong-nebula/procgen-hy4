## The object: an espagnolette (cremone bolt) on a casement window stile

The long iron locking bar of a tall French casement window (atlas D06, D07): a rod running the height of the meeting
stile, turned by a central handle so that hooks at its ends engage keepers in the frame head and sill.

What it is:
- A **round iron rod** (about 16 mm) running up the front of a short length of **timber stile** (the casement's
  meeting stile, part of the module), held by **guide brackets** (small plates screwed to the stile with a loop the
  rod turns in) at regular spacing.
- At each end, a **hook or claw** turned off the rod, and the **keeper** it engages on a short piece of frame head and
  sill (both part of the module).
- In the middle, the **handle**: a lever (often a shaped knob-ended lever) on a boss, turning the rod, resting on a
  decorative **handle plate**; a small catch or rest on the stile that holds the lever down.
- Decorative turned collars or knops on the rod. Screws as detail.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The stile's front face is at y = 0; the rod and handle stand in front of it (-y).
- The stile runs vertically, x = 0, from the sill piece at z = 0 to the head piece at z = `length`.
- Controls: `length` 1.6 in [0.8, 2.6]; `turn` 0 in [0, 1] (0 locked: handle down, hooks engaged; 1 open: handle
  turned 90 degrees, hooks rotated out of the keepers).
- Port: `mount` at [0, 0, `length`/2] facing -y, type `fitting`, `{"width": 0.08}`.
- Materials: `iron` or `black` for the ironwork, `bronze` for the handle if you like, `wood` for the stile.

## How it is built
- The **rod** runs in **guide brackets** screwed to the stile; the **handle** on its boss turns the rod; turning the rod
  turns the **hooks** at its ends into or out of their **keepers** on the head and sill pieces. The handle rests in a
  catch on the stile when locked.
- With `turn`, the rod, its hooks and the handle rotate together about the rod's axis; brackets, keepers and stile stay.

## Proportions (relative to `length`; derive them with `G.let`)
- Rod about 14 to 18 mm across; guide brackets every 0.3 to 0.45 m; the handle about 0.12 to 0.18 m long.
- Stile about 0.06 to 0.08 m wide and 0.05 m deep, moulded on its face, with a glazing rebate on one side.
- Hooks project about 25 to 40 mm and turn through 90 degrees into keepers about 40 to 60 mm long.

## Must have
- A rod that reads at a glance (not lost against the stile: a contrasting slot or a little more diameter), brackets,
  the handle with its boss and plate, both hooks engaged in keepers, the catch, screws (detail), a moulded stile.
