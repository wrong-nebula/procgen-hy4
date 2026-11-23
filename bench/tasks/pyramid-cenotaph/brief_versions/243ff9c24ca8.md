## The object: a visionary pyramid cenotaph (after Boullée)

A monument of Étienne-Louis Boullée's visionary architecture (atlas MN14-MN19, UT08, UT24-UT26): a vast, severe stone
pyramid on a stepped base, with a deep entrance portico cut into its foot, as an object in the city's monumental quarter.

What it is:
- A **pyramid** of large dressed stone, its faces stepped (courses that step back) or smooth with coursing as detail.
- A **stepped plinth** (two or three broad steps) round its base.
- An **entrance** on the front face: a deep, dark rectangular or arched recess with a severe Doric or Egyptian portico of
  thick columns carrying a plain entablature, and steps up to it.
- On the axis of each side face, a smaller blind niche or a sarcophagus-like block on the plinth.
- Lettering cut over the entrance (detail text, e.g. AUX GRANDS HOMMES).

## How it is built
- The pyramid stands on the plinth; the entrance recess is cut into the front face at the plinth level, and the portico
  stands in it carrying the entablature, which carries the masonry above the opening.

## Proportions (derive them from the controls with `G.let`)
- Height about 0.6 to 0.7 x `base` (a steep Egyptian pyramid); plinth steps about 0.4 to 0.6 m each.
- Entrance about 0.12 to 0.18 x `base` wide and 0.15 to 0.22 x `base` high, about 0.1 x `base` deep.
- Columns about 1/6 of their height in diameter.

## Must have
- The pyramid with stepped or coursed faces, the stepped plinth, the deep entrance with its portico and steps, the
  lettering, the side niches or blocks. At least two material slots.

Frame and controls:
- Z-up, metres. The **front faces -y**: the standard iso and front views look at it from that side, so put the side people see first there. The back and sides are seen too (the opposite view) and must be finished, not left blank. The entrance faces -y.
- On the ground at z = 0, centred on x = y = 0.
- Controls: `base` 30 in [18, 50] (side of the plinth's top); `height` 20 in [12, 34].
- Ports: `approach` at [0, -`base`/2 - 2, 0] facing -y, type `deck`, `{"width": 8}`.
- Materials: `chipped-limestone`, `pier-ashlar`, `stone`, `black` for the recess.
