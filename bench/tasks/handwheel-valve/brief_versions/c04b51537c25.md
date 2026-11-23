## The object: a rising-stem gate valve on a cast main

A gate valve set into a straight run of cast-iron water main in a late-19th-century dock pumping station
(the same world as the rest of the site kits: riveted iron, bronze fittings, dressed limestone).

What it is:
- A cast-iron valve body round the pipe: the main passes straight through it, and the body swells out round
  the gate inside it. It joins the main at a flange on each side.
- A bonnet on top of the body, bolted on, with a stuffing-box gland where the stem comes out.
- A **rising stem**: a bronze screwed spindle rising vertically out of the bonnet. On a rising-stem valve a yoke
  (an arch or two pillars) stands on the bonnet and carries the handwheel's nut, so the wheel stays at one
  height and the stem slides up through it.
- A bronze **handwheel**, horizontal, at about hand height above the quay: rim about 640 mm across, a hub and
  spokes (four or more).

Dimensions and frame (these are fixed so it mates with the existing `pump-main` module):
- Z-up, metres. The pipe axis lies along x through the origin.
- The main is 760 mm outside diameter. Its flanges are about 1.06 m across.
- The module has two ports of type `pipe` with `{"diameter": 0.76}`: `west` facing -x and `east` facing +x, at
  the ends of the valve body (look at how `pump_main()` in `recipes/build_machinery_kits.py` declares its ports).
  The pipe ends are open: the bore shows through the flanges, as it does on `pump-main`.
- Materials: use the slot names the site kits already use (`iron`, `patina`, `bronze`, `steel`, ...).
  `pgm materials` lists them.

Optional controls are welcome (for example, how far the gate is open, which sets how far the stem stands
above the wheel), as long as every value in their ranges still passes `pgm sweep`.

## How it is built
- The **body** carries everything: it is cast with the two flanged ends of the main and swells round the gate. The
  **bonnet** is bolted to the body's top flange; the **stuffing box and gland** sit on top of the bonnet, where the
  stem comes out.
- The **yoke** (an arch or two pillars) stands on the bonnet and carries, at its head, the **wheel nut**. The handwheel is
  fixed on that nut, **above the yoke head**. The screwed **stem** runs through the nut: turning the wheel turns the nut
  and the stem rises through it, so the stem stands above the wheel when the valve is open. The wheel never sits between
  the bonnet and the yoke head.

## Proportions (relative to the main, 760 mm outside diameter; derive them with `G.let`)
- Flange: about 1.06 m across (given), about 0.07 to 0.09 m thick, with 12 to 16 bolts on a circle about 0.9 m across.
- Body: swelling to about 1.3 to 1.5 x the main's diameter round the gate; bonnet about 0.6 to 0.8 x the main's diameter tall.
- Handwheel: rim about 640 mm across (given), rim section about 25 to 35 mm, at least four spokes, hub about 1/5 of the rim.
- Stem: about 50 to 65 mm across, with a visible thread.

## Must have
- An open bore at both ends (the main is hollow); bolts on both main flanges and on the bonnet joint; studs and nuts on
  the gland; the yoke; the wheel nut on the yoke head with the wheel on it; a threaded stem rising through it. At least
  three material slots (cast iron, bronze for wheel and stem, and steel or iron for bolts).
