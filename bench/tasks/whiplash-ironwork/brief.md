## The objects: a four-piece Brussels Art Nouveau wrought-iron family

Build four coordinated wrought- and cast-iron architectural ironwork modules (after Horta, Hankar, Brandt and atlas
D01, N01, N02, IW01, IW02, IW05, IW07, IW08; plate s70), authored together in one builder so they share one forged
grammar: painted dark-green `iron` scrolls and bars, `black` cast shoes and lock boxes, `bronze` forged collars,
tendril buds and rosettes, and `chipped-limestone` plinth/curb seats where rooted in masonry.

### Common coordinate frame & construction rules
- **Frame:** Z-up, metres, street/front side on **`-y`**, finished cleanly on both `-y` and `+y` (ironwork is seen in
  silhouette from both sides!).
- Every curved stem and C-/S-scroll must physically meet its neighbouring bar, rail or collar within `3 mm` so
  `pgm audit --sweep` passes as a single connected assembly across all control extremes.
- Share Python curve/scroll helpers (`catmullRom` splines, forged `bronze` binding collars where tangent scrolls kiss,
  and tapered/layered bar sections) so all four pieces unmistakably belong to one foundry catalogue.

---

### 1. `crest-iron`: roof ridge & cornice whiplash cresting bay
- **How it is built:** One repeatable `pitch`-wide bay (`x in [-pitch/2, +pitch/2]`, `y = 0`, `z = 0 .. height`) of
  rooftop ridge cresting: a flanged `black`/`iron` base shoe channel along `z = 0`, stout end standards at `±pitch/2`
  with turned `bronze` finials, a mid-height horizontal tie bar, and an alternating rhythm of tall lancet stems and
  symmetrical S-curved whiplash volutes bound by `bronze` forged collars and crowned with stylized spear/bud tips.
- **Controls:** `pitch` `2.4` in `[1.2, 4.8]`, `height` `0.75` in `[0.45, 1.25]`.
- **Ports:** `"west"` at `[-pitch/2, 0, 0]` normal `[-1, 0, 0]` and `"east"` at `[pitch/2, 0, 0]` normal `[1, 0, 0]`,
  type `"railing"`, `{"height": "$height"}`.

### 2. `scroll-gate`: double-leaf Brussels courtyard entrance gate
- **How it is built:** Standing on `z = 0`, centred on `x = 0` (`width` × `height`): twin dressed `chipped-limestone`
  jamb plinths / cast `black` hinge piers at `±width/2` carrying heavy `bronze` pintle hinges, a pair of symmetric
  meeting wrought-iron gate leaves (`z = 0.08 .. height`) with sheeted lower kick panels (riveted `iron` with beaten
  bosses), vertical stiles, a lock rail at `~1.05 m` with a cast `black` & `bronze` lock case, handle and house-number /
  monogram cartouche (`kittype.inscribe`), open upper bars filled with sweeping asymmetrical whiplash scrolls, and a
  rising arched overthrow crest meeting at a central `bronze` lantern/finial boss.
- **Controls:** `width` `2.2` in `[1.4, 3.4]`, `height` `2.6` in `[2.0, 3.4]`.
- **Port:** `"base"` at `[0, 0, 0]` normal `[0, -1, 0]`, type `"deck"`, `{"width": "$width"}`.

### 3. `balcony-bracket`: forged whiplash cantilever console bracket
- **How it is built:** Wall-mounted at `y = 0`, projecting streetward to `y = -reach` over `z = 0 .. drop`: a vertical
  riveted `iron` wall backplate along `y = 0` with `bronze` anchor bolts (`z = 0 .. drop`), a horizontal top bearing
  table (`z = drop`, `y = -reach .. 0`) to carry a balcony slab or canopy beam, a primary curved compression strut
  sweeping smoothly from the foot `[0, 0, 0.08]` up and out to `[0, -reach * 0.92, drop - 0.04]`, and nested whiplash
  S-scrolls + `bronze` binding collars and forged leaf/rosette bosses filling the spandrel triangle.
- **Controls:** `reach` `0.85` in `[0.45, 1.4]`, `drop` `1.05` in `[0.6, 1.7]`.
- **Port:** `"wall"` at `[0, 0, drop / 2]` normal `[0, 1, 0]`, type `"wall"`, `{"height": "$drop"}`.

### 4. `railing-fountain`: cast-and-wrought basin & terrace parapet bay (atlas D01 / N01)
- **How it is built:** One repeatable `pitch`-wide bay (`x in [-pitch/2, +pitch/2]`, `z = 0 .. height`): a low moulded
  `chipped-limestone` curb plinth (`z = 0 .. 0.18`), half-newel cast `iron` end posts at `±pitch/2` with `bronze` collar
  rings, a broad moulded `bronze`-capped top handrail at `z = height`, a lower rail above the stone curb, and `3–5`
  openwork leaf-ribbon / whiplash balusters whose stems split into paired recurved scrolls clasping central `bronze`
  medallions.
- **Controls:** `pitch` `2.2` in `[1.2, 3.8]`, `height` `1.05` in `[0.8, 1.35]`.
- **Ports:** `"west"` at `[-pitch/2, 0, 0]` normal `[-1, 0, 0]` and `"east"` at `[pitch/2, 0, 0]` normal `[1, 0, 0]`,
  type `"railing"`, `{"height": "$height"}`.
