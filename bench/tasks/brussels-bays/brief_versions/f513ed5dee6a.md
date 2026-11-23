## The objects: a four-piece Brussels Art Nouveau facade family

Build four coordinated upper-facade modules for a late-19th-century Brussels town house (after Horta, Hankar and
Cauchie; atlas FD03, FD07, FD09, FD10, FD12, FD20, BB04, BB11), authored together in one builder so they share one
architectural grammar: dressed `chipped-limestone` walling and carved consoles, painted green `iron` mullions and
whiplash scroll bars, `bronze` collars and tie rosettes, `wood` casement sashes, `slate` / `copper` weathering caps, and
`glass` lights with leaded transoms.

### Common coordinate frame & construction rules
- **Frame:** Z-up, metres. Each module mounts against a house facade plane at **`y = 0`**, projecting streetward into
  **`-y`** (with up to `0.25 m` of wall-seat / reveal behind `y = 0` so the rear view in `opposite` is cleanly finished
  with stone jambs, interior sash frames and trim, never hollow or unfinished).
- Centred horizontally on `x = 0`, bottom at `z = 0`, top at `z = height`.
- **Port (all four):** `"wall"` at `[0, 0, height / 2]`, normal `[0, 1, 0]`, type `"wall"`,
  `{"width": "$width", "height": "$height"}`.
- Share helper functions for moulded limestone consoles, fluted iron mullions, leaded diamond transom lights, and
  swept Art Nouveau whiplash ironwork so all four modules read as siblings from the same Brussels facade.
- For inscriptions and dates, use `from kittype import inscribe` (`font="nouveau"` or `"roman"`) so lettering is
  vertically centred and width-clamped across control sweeps.

---

### 1. `oriel-corbelled`: corbelled painted-iron and limestone oriel bay
- **How it is built:** Twin stepped dressed-limestone corbel consoles (`z = 0 .. 0.24 * height`) spring from a wall
  backplate at `y = 0` out to `-projection`, carrying a moulded stone sill apron, a three-sided canted or rectangular
  painted-iron bay cage (`z = 0.24 * height .. 0.86 * height`) with slender fluted corner posts, timber-framed lower
  casements, a leaded small-pane upper transom band with `bronze` rosettes, and a flared `copper` or `slate` hipped
  weathering hood (`z = 0.86 * height .. height`) with an iron finial crest.
- **Controls:** `width` `2.4` in `[1.6, 3.8]`, `height` `3.8` in `[2.8, 5.2]`, `projection` `0.85` in `[0.5, 1.3]`.
- **Must have:** paired stone corbels with side flutes, panelled Apron spandrel under the sill, glazed front + angled/side
  return lights, leaded transom row, pitched metal hood, finished interior wall-frame opening on `+y`.

### 2. `bowed-bay`: curved bow-front cast-iron window bay
- **How it is built:** A segmental curved bow plan (`y(x) = -bow * cos(...)` or faceted curved arc of 5–7 lights) seated
  into dressed limestone side pilasters at `y = 0`. Below: a curved moulded stone/iron sill and curved panelled timber/iron
  apron (`z = 0 .. 0.22 * height`). Middle: tall curved glazing bays divided by slender round iron colonnettes with
  `bronze` neck rings and swept whiplash corner brackets. Top: curved leaded transom band, riveted iron cornice band and
  low domed `copper` / `slate` cap with delicate iron cresting.
- **Controls:** `width` `2.8` in `[1.8, 4.4]`, `height` `3.4` in `[2.5, 4.6]`, `bow` `0.7` in `[0.35, 1.1]`.
- **Must have:** genuine curved plan bow, slender iron colonnettes with bronze collars, curved apron & cornice, leaded
  upper lights, finished rear reveal frame on `+y`.

### 3. `balcony-tier`: Brussels whiplash iron balcony on stone consoles with French casement
- **How it is built:** At the foot (`z = 0 .. 0.20 * height`), two or three sculpted limestone scroll consoles carry a
  moulded stone balcony slab (`y = -projection .. 0.20`) with drained edge drip. Around the slab perimeter rises a
  `0.95 m` wrought-iron Art Nouveau balustrade: stout corner standards with `bronze` finials, top timber/bronze handrail,
  and symmetrical S-curved whiplash scroll panels. Set into the limestone wall frame at `y = 0` is a tall double-leaf
  glazed timber French casement door with transom fanlight and moulded stone architrave/lintel (`z = 0.20 * height .. height`),
  keeping a clear unobstructed threshold onto the balcony deck.
- **Controls:** `width` `3.0` in `[1.8, 4.8]`, `height` `3.2` in `[2.5, 4.2]`, `projection` `0.75` in `[0.45, 1.15]`.
- **Must have:** stone consoles + slab, three-sided wrought-iron whiplash railing with bronze accents, recessed French
  casement double doors + fanlight in a coursed stone surround, finished rear face.

### 4. `dated-frieze-panel`: Brussels sgraffito & carved stone lintel panel
- **How it is built:** A coursed `chipped-limestone` spandrel/frieze block (`width` × `height`, `~0.24 m` thick) with
  projecting moulded stone sill and cornice hood, framing a recessed `terracotta` / cream `tile` sgraffito field bordered
  by `bronze` / `iron` tie-plate rosettes and stylized botanical scroll relief (`detailStroke` curves and discs), with a
  central stone or enamel cartouche inscribed via `kittype.inscribe` (`variant=C("variant")`, integer `0..2`, selecting
  `("ANNO 1898", "MAISON HANKAR", "ATELIER NORD")` in `font="nouveau"` or `"roman"`).
- **Controls:** `width` `3.2` in `[1.6, 5.6]`, `height` `0.95` in `[0.6, 1.5]`, `variant` `0` in `[0, 2]` (integer).
- **Must have:** stone surround + sill + hood, recessed sgraffito panel with botanical/geometric scroll strokes, pair of
  iron/bronze wall-anchor rosettes, and centred `kittype.inscribe` cartouche text across all `width`/`variant` sweeps.
