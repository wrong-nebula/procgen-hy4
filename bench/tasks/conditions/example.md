## Worked example

`examples/worked_example.py` is a kit builder written by a strong modeller for a *different* object (a
quayside reeving block for a winch rope), and `examples/NOTES.md` is its write-up. It was judged among the best
entries for its object. Read both before you plan. Notice:

- how finely it splits the object into parts (about 35 geometry calls and 90 graph calls in all), and that each part is named for what
  it is in the real object;
- how dimensions are named constants derived from the brief, so proportions follow the controls;
- how surface detail (bolts, seams, beads, strokes) lies on the parts as data, about 16 detail calls;
- its `ref(...)` and `review(...)` records.

Aim for that level of decomposition and detail for your own object. Do not copy its geometry, and do not run
it (read it only).
