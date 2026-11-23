# kittype: panel-bound lettering and signage for kit builders

Generated from `recipes/kittype.py` by `tools/kittype_doc.py`; do not edit by hand.

Lettering, inscriptions and architectural signage for procgen kit builders.

Why this module exists:
`detailText` (`G.text`) anchors at the **vertical midpoint** (mid-cap-height) of a
line, NOT the baseline, and builders routinely guess string widths by hand. That
combination causes two recurring bugs across kits:
1. Text sits half a cap-height too low (clipping bottom rails and plaque rims)
   when an author subtracts `0.5 * size` from a board's centre.
2. Long names overrun plate ends or clip borders when `width` sweeps down.

`kittype` makes signage **panel-bound** instead of baseline-guessed:

    from kittype import (inscribe, arch_inscribe, sign_board, number_plaque,
                         arch_sign_board, text_aspect, fit_text_size)

- `inscribe(...)`: centres one or more lines (or a control-selected tuple of
  variants) inside `panel_size = [width, height]` on any face (`"-y"`, `"+y"`,
  `"+x"`, `"-x"`, `"+z"`, `"-z"`), clamping cap-height automatically so `pgm sweep`
  never overflows, and setting `direction` from `face` so rear text is never mirrored.
- `arch_inscribe(...)`: sets proportionally spaced characters along an arched
  centreline `z_at(u)`, centred on the band height and tilted tangent to the arch.
- `sign_board(...)`, `number_plaque(...)`, `arch_sign_board(...)`: complete,
  audit-clean framed signboards and bevelled plaques with inset borders, optional
  fixing screws and centred lettering in one call.
- Three faces (`font="plain" | "roman" | "nouveau"`):
  - `"plain"`: the engine's fast single-node `detailText` face (ASCII-normalised so
    French accents never fail evaluation).
  - `"roman"`: proportioned classical architectural capitals with bracketed serifs
    and curved bowls (`detailStroke`), plus French accents (`É È Ê À Â Î Ô Û Ç`).
  - `"nouveau"`: condensed Brussels / Paris 1900 sign-painter & ironwork capitals
    with high crossbars (`y = 3.85`) and curved stems (`detailStroke`), plus accents.

## Functions

### `arch_inscribe(G, id_, surface, text, span, band_height, z_at, y, face='-y', *, font='nouveau', variant=None, pad_x=0.11, pad_y=0.23, spread=0.0, max_size=None, profile='bead', slot='marking', stroke_ratio=0.12, relief=None, spacing=None, follow_slope=True, lift=0.018, reach=0.18)`

Set `text` along an arched band spanning `[-span/2, +span/2]` in X at face plane `y`.

`z_at` is EITHER:
- a callable `z_centre(u)` (`u = x / (span / 2)` in `[-1, 1]`) returning the **vertical
  midpoint Z** of the arch band at `u` (not its bottom rim!), OR
- a `(z_ends, rise)` tuple for a smooth parabolic arch `z_ends + rise * (1 - u**2)`.

Characters are proportionally kerned by true glyph width, kept inside `pad_x`/`pad_y`
margins across control sweeps, centred on the band midline, and rotated tangent to the arch.

### `arch_sign_board(G, id_, text, span, band_height, z_ends, rise, y=0.0, thickness=0.055, *, face='-y', both_sides=False, font='nouveau', variant=None, spread=0.35, board_slot='terracotta', rim_slot='iron', text_slot='marking', profile='bead', segments=24)`

Curved arched sign plate (station entrance, market portal, footbridge arch) spanning
`[-span/2, +span/2]` in X at `y`: closed extruded arch band from `z_ends + rise*(1 - u^2)`
with moulded top & bottom swept rims, end bosses, and tangent-tilted `arch_inscribe` lettering
centred strictly along the arch midline.

### `fit_text_size(G, id_, text, inner_width, inner_height, *, font='plain', max_size=None, spacing=None, line_gap=0.42)`

Derived cap-height expression that fits `text` (one or more `\n` lines) inside `(inner_width, inner_height)`.

### `glyph_spec(ch, font='plain')`

Return `(width_units, strokes)` on the 0..6 grid for character `ch` in `font`.

### `glyph_width(ch, font='plain')`

Advance width of one character in cap-height units (width / cap_height).

### `inscribe(G, id_, surface, text, centre, panel_size, face='-y', *, font='plain', variant=None, pad=(0.08, 0.24), max_size=None, profile='bead', slot='bronze', stroke_ratio=0.11, relief=None, spacing=None, line_gap=0.42, lift=0.015, reach=0.18, right=None, up=None, normal=None)`

Inscribe `text` dead-centre inside a rectangular panel of `panel_size = [width, height]`
whose face midpoint is `centre`.

- Never pass a baseline coordinate: `centre` is the geometric midpoint of the panel face.
- `panel_size` accepts numbers or `kitlib` expressions (`[W - 0.2, FH]`), so cap-height
  shrinks automatically when controls sweep narrow.
- `text` may be a single string (`"MAISON IRIS"`, or multi-line `"ANNO\n1898"`) OR a
  tuple/list of strings paired with an integer control `variant=C("name")`.
- `face` (`"-y"`, `"+y"`, `"+x"`, `"-x"`, `"+z"`, `"-z"`) sets reading direction and
  outward projection so rear and side faces are never mirrored.
- `font`: `"plain"` (`detailText`), `"roman"` (serif architectural capitals) or
  `"nouveau"` (condensed Brussels 1900 capitals).

### `number_plaque(G, id_, text, at, size=(0.15, 0.022, 0.18), *, face='-y', shape='rect', font='nouveau', variant=None, plaque_slot='black', rim_slot='bronze', text_slot='marking', profile='mark', screws=True, pad=(0.16, 0.25))`

Enamel or cast house-number / notice plaque centred at `at` (`size = [w, d, h]` on `±y` faces):
bevelled plate (`rect` or `oval`), inset rim bead, optional slotted corner/side screws, and
dead-centre numerals or short notice (`"25"`, `"OUVERT"`, `"NO\n25"`).

### `plain_safe(text)`

Normalise arbitrary Unicode text into the ASCII subset accepted by C++ `detailText`.

### `sign_board(G, id_, text, at, size, *, face='-y', both_sides=False, font='nouveau', variant=None, board_slot='black', frame_slot='iron', border_slot='bronze', text_slot='bronze', profile='bead', pad=(0.09, 0.24), max_size=None, bevel=0.007)`

A complete framed fascia signboard centred at `at` with `size = [width, depth, height]`
(for `face="-y"`/`"+y"`, or `[depth, width, height]` for `"+x"`/`"-x"`):
bevelled board, top & bottom moulded rails, end stiles, inset fillet border, and
vertically centred, width-clamped lettering (optionally on `both_sides=True`).
Returns one material-assigned, audit-connected geometry node id.

### `text_aspect(text, font='plain', spacing=None)`

Rendered line width divided by cap-height (for the widest line if `text` has `\n`).

## Faces

- `plain`: native `detailText` single-stroke capitals and figures (1 node, ASCII-normalised).
- `roman`: classical architectural capitals with bracketed serifs, curved bowls and French accents (`detailStroke`).
- `nouveau`: condensed Brussels / Paris 1900 sign-painter capitals with high crossbars (`y = 3.85`) and accents (`detailStroke`).

Standalone kit: `recipes/build_signage_kits.py` builds `recipes/site/signage/` (`fascia-sign`, `house-plaque`, `arch-sign`).
Verification: `python3 tools/test_kittype.py`.
