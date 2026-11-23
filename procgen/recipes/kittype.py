"""Lettering, inscriptions and architectural signage for procgen kit builders.

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
"""
import math
import unicodedata
from kitlib import PI, absx, atan2, gt, mn, mx

# Every glyph is authored on a 0..6 cap-height grid (y = 0 baseline, y = 6 cap top,
# y = 3 vertical midpoint). Widths are in the same 1/6-cap-height grid units.
GRID_H = 6.0
DEFAULT_SPACING = {"plain": 1.4, "roman": 1.15, "nouveau": 0.95}

# Right-handed face frames viewed from OUTSIDE the face: right x up == normal.
FACES = {
    "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),   # street / south face
    "+y": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),   # rear / north face
    "+x": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),    # east face
    "-x": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0)),  # west face
    "+z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),    # top / floor face
    "-z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),  # soffit / underside
}

# Exact widths of C++ `detailText` (`src/doc/doc_detail.cpp` glyph_strokes).
_PLAIN_WIDTHS = {
    " ": 2.4, "1": 2.0, "I": 2.0, "-": 3.0, ".": 0.5, ":": 0.5, "/": 4.0,
}

# Accent decomposition for French / Brussels inscriptions.
_ACCENTS = {
    "É": ("E", "acute"), "È": ("E", "grave"), "Ê": ("E", "circum"), "Ë": ("E", "diaeresis"),
    "À": ("A", "grave"), "Â": ("A", "circum"), "Ä": ("A", "diaeresis"),
    "Î": ("I", "circum"), "Ï": ("I", "diaeresis"),
    "Ô": ("O", "circum"), "Ö": ("O", "diaeresis"),
    "Ù": ("U", "grave"), "Û": ("U", "circum"), "Ü": ("U", "diaeresis"),
    "Ç": ("C", "cedilla"),
}


def _arc(cx, cy, rx, ry, deg0, deg1, steps=10):
    return [(cx + rx * math.cos(math.radians(deg0 + (deg1 - deg0) * k / steps)),
             cy + ry * math.sin(math.radians(deg0 + (deg1 - deg0) * k / steps)))
            for k in range(steps + 1)]


def _dedupe(pts):
    out = []
    for x, y in pts:
        if not out or math.hypot(x - out[-1][0], y - out[-1][1]) > 1e-4:
            out.append((round(x, 5), round(y, 5)))
    return out


def _build_roman():
    """Classical monumental capitals: proportioned widths, bracketed serifs, round bowls."""
    s = 0.42
    g = {}

    def stem(x, serifs="tb"):
        st = [[(x, 0.0), (x, 6.0)]]
        if "b" in serifs:
            st.append([(x - s, 0.0), (x + s, 0.0)])
        if "t" in serifs:
            st.append([(x - s, 6.0), (x + s, 6.0)])
        return st

    g[" "] = (2.2, [])
    g["I"] = (1.5, stem(0.75))
    g["J"] = (2.9, [[(2.35 - s, 6.0), (2.35 + s, 6.0)],
                    [(2.35, 6.0), (2.35, 1.4)] + _arc(1.25, 1.4, 1.1, 1.4, 0, -155, 8)])
    g["L"] = (3.6, [[(0.55 - s, 6.0), (0.55 + s, 6.0)],
                    [(0.15, 0.0), (3.45, 0.0), (3.55, 0.95)],
                    [(0.55, 6.0), (0.55, 0.0)]])
    g["T"] = (4.2, [[(2.1, 6.0), (2.1, 0.0)],
                    [(2.1 - s, 0.0), (2.1 + s, 0.0)],
                    [(0.05, 5.15), (0.15, 6.0), (4.05, 6.0), (4.15, 5.15)]])
    g["F"] = (3.6, [[(0.55, 0.0), (0.55, 6.0)],
                    [(0.55 - s, 0.0), (0.55 + s * 1.2, 0.0)],
                    [(0.15, 6.0), (3.45, 6.0), (3.55, 5.2)],
                    [(0.55, 3.15), (2.85, 3.15)], [(2.85, 2.7), (2.85, 3.6)]])
    g["E"] = (3.7, g["F"][1] + [[(0.15, 0.0), (3.55, 0.0), (3.65, 0.85)]])
    g["H"] = (4.4, stem(0.55) + stem(3.85) + [[(0.55, 3.1), (3.85, 3.1)]])
    g["N"] = (4.4, [[(0.5, 0.0), (0.5, 6.0), (3.9, 0.0), (3.9, 6.0)],
                    [(0.5 - s, 0.0), (0.5 + s, 0.0)],
                    [(0.5 - s, 6.0), (0.5 + 0.15, 6.0)],
                    [(3.9 - s, 6.0), (3.9 + s, 6.0)]])
    g["M"] = (5.2, [[(0.45, 0.0), (0.65, 6.0), (2.6, 0.65), (4.55, 6.0), (4.75, 0.0)],
                    [(0.05, 0.0), (0.9, 0.0)], [(4.3, 0.0), (5.15, 0.0)],
                    [(0.2, 6.0), (0.9, 6.0)], [(4.3, 6.0), (5.0, 6.0)]])
    g["A"] = (4.5, [[(0.35, 0.0), (2.25, 6.0), (4.15, 0.0)],
                    [(1.05, 2.15), (3.45, 2.15)],
                    [(0.0, 0.0), (0.85, 0.0)], [(3.65, 0.0), (4.5, 0.0)]])
    g["V"] = (4.4, [[(0.35, 6.0), (2.2, 0.0), (4.05, 6.0)],
                    [(0.0, 6.0), (0.85, 6.0)], [(3.55, 6.0), (4.4, 6.0)]])
    g["W"] = (5.8, [[(0.35, 6.0), (1.55, 0.0), (2.9, 4.5), (4.25, 0.0), (5.45, 6.0)],
                    [(0.0, 6.0), (0.8, 6.0)], [(5.0, 6.0), (5.8, 6.0)]])
    g["X"] = (4.2, [[(0.45, 0.0), (3.75, 6.0)], [(0.45, 6.0), (3.75, 0.0)],
                    [(0.05, 0.0), (0.9, 0.0)], [(3.3, 0.0), (4.15, 0.0)],
                    [(0.05, 6.0), (0.9, 6.0)], [(3.3, 6.0), (4.15, 6.0)]])
    g["Y"] = (4.2, [[(0.4, 6.0), (2.1, 2.85), (3.8, 6.0)],
                    [(2.1, 2.85), (2.1, 0.0)], [(2.1 - s, 0.0), (2.1 + s, 0.0)],
                    [(0.0, 6.0), (0.85, 6.0)], [(3.35, 6.0), (4.2, 6.0)]])
    g["K"] = (4.2, stem(0.55) + [[(3.7, 6.0), (0.75, 2.95), (3.8, 0.0)],
                                 [(3.25, 6.0), (4.05, 6.0)], [(3.35, 0.0), (4.2, 0.0)]])
    g["Z"] = (4.0, [[(0.25, 5.2), (0.35, 6.0), (3.75, 6.0), (0.25, 0.0), (3.75, 0.0), (3.85, 0.85)]])
    g["O"] = (4.6, [_arc(2.3, 3.0, 2.1, 3.0, 0, 360, 16)])
    g["Q"] = (4.7, [_arc(2.3, 3.0, 2.1, 3.0, 0, 360, 16),
                    [(2.45, 1.35), (3.85, 0.0), (4.65, -0.15)]])
    g["C"] = (4.1, [_arc(2.25, 3.0, 2.05, 3.0, 42, 318, 14),
                    [(3.82, 4.95), (3.92, 5.75)]])
    g["G"] = (4.4, [_arc(2.25, 3.0, 2.05, 3.0, 42, 326, 14),
                    [(3.82, 4.95), (3.92, 5.75)],
                    [(3.95, 0.35), (3.95, 2.55)], [(2.55, 2.55), (4.35, 2.55)]])
    g["D"] = (4.4, [[(0.55, 0.0), (0.55, 6.0)],
                    [(0.15, 6.0), (2.05, 6.0)] + _arc(2.05, 3.0, 2.15, 3.0, 90, -90, 12) + [(0.15, 0.0)]])
    g["P"] = (3.8, [[(0.55, 0.0), (0.55, 6.0)], [(0.15, 0.0), (1.15, 0.0)],
                    [(0.15, 6.0), (1.95, 6.0)] + _arc(1.95, 4.5, 1.65, 1.5, 90, -90, 10) + [(0.55, 3.0)]])
    g["R"] = (4.1, g["P"][1] + [[(1.95, 3.0), (3.65, 0.0), (4.1, 0.0)]])
    g["B"] = (4.0, [[(0.55, 0.0), (0.55, 6.0)],
                    [(0.15, 6.0), (1.9, 6.0)] + _arc(1.9, 4.6, 1.45, 1.4, 90, -90, 9) + [(0.55, 3.2)],
                    [(1.9, 3.2)] + _arc(2.0, 1.6, 1.75, 1.6, 90, -90, 9) + [(0.15, 0.0)]])
    g["S"] = (3.6, [_arc(1.8, 4.5, 1.45, 1.5, 32, 252, 9) + _arc(1.85, 1.5, 1.55, 1.5, 72, -148, 9),
                    [(3.05, 5.25), (3.12, 5.88)], [(0.52, 0.72), (0.45, 0.1)]])
    g["U"] = (4.3, [[(0.55, 6.0), (0.55, 1.85)] + _arc(2.15, 1.85, 1.6, 1.85, 180, 360, 10) + [(3.75, 6.0)],
                    [(0.15, 6.0), (0.95, 6.0)], [(3.35, 6.0), (4.15, 6.0)]])
    # Figures & punctuation
    g["0"] = (3.9, [_arc(1.95, 3.0, 1.75, 3.0, 0, 360, 16)])
    g["1"] = (2.1, [[(0.35, 5.15), (1.15, 6.0), (1.15, 0.0)], [(0.45, 0.0), (1.85, 0.0)]])
    g["2"] = (3.7, [_arc(1.85, 4.45, 1.55, 1.55, 155, -35, 9) + [(0.25, 0.0), (3.55, 0.0), (3.65, 0.7)]])
    g["3"] = (3.6, [_arc(1.75, 4.55, 1.4, 1.45, 145, -75, 9),
                    _arc(1.8, 1.6, 1.55, 1.6, 75, -145, 9)])
    g["4"] = (3.9, [[(2.75, 0.0), (2.75, 6.0), (0.2, 1.85), (3.75, 1.85)], [(2.25, 0.0), (3.25, 0.0)]])
    g["5"] = (3.6, [[(3.35, 6.0), (0.55, 6.0), (0.45, 3.45)] + _arc(1.8, 1.8, 1.6, 1.8, 120, -145, 10)])
    g["6"] = (3.8, [_arc(2.0, 3.0, 1.7, 3.0, 55, 270, 10) + _arc(1.95, 1.85, 1.65, 1.85, -90, 172, 10)])
    g["7"] = (3.6, [[(0.2, 5.2), (0.3, 6.0), (3.45, 6.0), (1.35, 0.0)]])
    g["8"] = (3.8, [_arc(1.9, 4.55, 1.4, 1.45, 0, 360, 12), _arc(1.9, 1.6, 1.65, 1.6, 0, 360, 12)])
    g["9"] = (3.8, [[(3.8 - x, 6.0 - y) for x, y in g["6"][1][0]]])
    g["."] = (1.0, [_arc(0.5, 0.35, 0.25, 0.25, 0, 360, 6)])
    g[":"] = (1.0, [_arc(0.5, 1.2, 0.24, 0.24, 0, 360, 6), _arc(0.5, 4.2, 0.24, 0.24, 0, 360, 6)])
    g["-"] = (2.6, [[(0.3, 3.0), (2.3, 3.0)]])
    g["/"] = (3.0, [[(0.25, 0.0), (2.75, 6.0)]])
    g["'"] = (1.1, [[(0.65, 6.1), (0.4, 4.65)]])
    g["°"] = (1.8, [_arc(0.9, 4.9, 0.55, 0.55, 0, 360, 8)])
    g["&"] = (4.4, [[(3.95, 2.3), (1.35, 0.25)] + _arc(1.5, 1.5, 1.25, 1.5, -125, -245, 7)
                    + _arc(1.85, 4.65, 1.05, 1.35, 225, -45, 9) + [(0.65, 2.2), (3.95, 0.0)]])
    return {k: (w, [_dedupe(p) for p in st]) for k, (w, st) in g.items()}


def _build_nouveau():
    """Condensed Brussels / Paris 1900 sign-painter capitals: tall proportions, high waist (y = 3.85), curved legs."""
    hw = 3.85
    g = {}
    g[" "] = (1.85, [])
    g["I"] = (1.05, [[(0.525, 0.0), (0.525, 6.0)], [(0.15, 0.0), (0.9, 0.0)], [(0.15, 6.0), (0.9, 6.0)]])
    g["J"] = (2.35, [[(1.95, 6.0), (1.95, 1.35)] + _arc(1.05, 1.35, 0.9, 1.35, 0, -160, 8)])
    g["L"] = (2.75, [[(0.35, 6.0), (0.35, 0.0), (2.6, 0.0), (2.7, 0.35)]])
    g["T"] = (3.1, [[(1.55, 6.0), (1.55, 0.0)],
                    [(0.1, 5.75), (0.8, 6.0), (2.3, 6.0), (3.0, 5.75)]])
    g["F"] = (2.8, [[(0.35, 0.0), (0.35, 6.0), (2.65, 6.0), (2.75, 5.7)],
                    [(0.35, hw), (2.25, hw)]])
    g["E"] = (2.85, g["F"][1] + [[(0.35, 0.0), (2.7, 0.0), (2.8, 0.3)]])
    g["H"] = (3.15, [[(0.35, 0.0), (0.35, 6.0)], [(2.8, 0.0), (2.8, 6.0)],
                     [(0.35, hw - 0.2), (1.575, hw + 0.15), (2.8, hw - 0.2)]])
    g["N"] = (3.2, [[(0.35, 0.0), (0.35, 6.0), (2.85, 0.0), (2.85, 6.0)]])
    g["M"] = (3.85, [[(0.25, 0.0), (0.45, 6.0), (1.925, 2.35), (3.4, 6.0), (3.6, 0.0)]])
    g["A"] = (3.3, [[(0.15, 0.0), (0.55, 2.2), (1.65, 6.0), (2.75, 2.2), (3.15, 0.0)],
                    [(0.48, hw - 1.15), (1.65, hw - 0.8), (2.82, hw - 1.15)]])
    g["V"] = (3.2, [[(0.2, 6.0), (1.6, 0.0), (3.0, 6.0)]])
    g["W"] = (4.35, [[(0.2, 6.0), (1.15, 0.0), (2.175, 4.1), (3.2, 0.0), (4.15, 6.0)]])
    g["X"] = (3.1, [[(0.25, 0.0), (2.85, 6.0)], [(0.25, 6.0), (2.85, 0.0)]])
    g["Y"] = (3.1, [[(0.2, 6.0), (1.55, hw - 0.35), (2.9, 6.0)], [(1.55, hw - 0.35), (1.55, 0.0)]])
    g["K"] = (3.1, [[(0.35, 0.0), (0.35, 6.0)],
                    [(2.85, 6.0), (0.45, hw - 0.25)],
                    [(0.85, hw - 0.05), (2.15, 1.8), (2.95, 0.0)]])
    g["Z"] = (2.95, [[(0.2, 5.75), (0.5, 6.0), (2.75, 6.0), (0.2, 0.0), (2.75, 0.0), (2.9, 0.3)]])
    g["O"] = (3.25, [_arc(1.625, 3.0, 1.425, 3.0, 0, 360, 16)])
    g["Q"] = (3.35, [_arc(1.625, 3.0, 1.425, 3.0, 0, 360, 16),
                     [(1.75, 1.4), (2.65, 0.15), (3.3, -0.2)]])
    g["C"] = (2.95, [_arc(1.65, 3.0, 1.45, 3.0, 48, 312, 14)])
    g["G"] = (3.15, [_arc(1.65, 3.0, 1.45, 3.0, 48, 330, 14),
                     [(2.9, 0.45), (2.9, hw - 0.85), (1.7, hw - 0.85)]])
    g["D"] = (3.2, [[(0.35, 0.0), (0.35, 6.0), (1.45, 6.0)]
                    + _arc(1.45, 3.0, 1.55, 3.0, 90, -90, 12) + [(0.35, 0.0)]])
    g["P"] = (2.95, [[(0.35, 0.0), (0.35, 6.0), (1.55, 6.0)]
                     + _arc(1.55, (6.0 + hw) * 0.5, 1.2, (6.0 - hw) * 0.5, 90, -90, 9) + [(0.35, hw)]])
    g["R"] = (3.1, g["P"][1] + [[(1.45, hw), (2.35, 2.0), (2.95, 0.0)]])
    g["B"] = (3.05, [[(0.35, 0.0), (0.35, 6.0), (1.5, 6.0)]
                     + _arc(1.5, (6.0 + hw) * 0.5, 1.15, (6.0 - hw) * 0.5, 90, -90, 9) + [(0.35, hw)],
                     [(1.5, hw)] + _arc(1.55, hw * 0.5, 1.35, hw * 0.5, 90, -90, 10) + [(0.35, 0.0)]])
    g["S"] = (2.85, [_arc(1.425, 4.65, 1.15, 1.35, 35, 248, 9)
                     + _arc(1.45, 1.75, 1.25, 1.75, 68, -148, 10)])
    g["U"] = (3.15, [[(0.35, 6.0), (0.35, 1.75)]
                     + _arc(1.575, 1.75, 1.225, 1.75, 180, 360, 10) + [(2.8, 6.0)]])
    # Figures & punctuation
    g["0"] = (3.0, [_arc(1.5, 3.0, 1.3, 3.0, 0, 360, 16)])
    g["1"] = (1.65, [[(0.25, 5.1), (0.95, 6.0), (0.95, 0.0)], [(0.35, 0.0), (1.5, 0.0)]])
    g["2"] = (2.95, [_arc(1.475, 4.5, 1.25, 1.5, 155, -35, 9) + [(0.2, 0.0), (2.8, 0.0)]])
    g["3"] = (2.9, [_arc(1.4, 4.65, 1.15, 1.35, 145, -75, 9),
                    _arc(1.45, 1.7, 1.25, 1.7, 75, -145, 9)])
    g["4"] = (3.05, [[(2.15, 0.0), (2.15, 6.0), (0.2, 2.05), (2.9, 2.05)]])
    g["5"] = (2.9, [[(2.65, 6.0), (0.4, 6.0), (0.35, 3.65)] + _arc(1.45, 1.85, 1.3, 1.85, 120, -145, 10)])
    g["6"] = (3.0, [_arc(1.55, 3.0, 1.35, 3.0, 55, 270, 10) + _arc(1.5, 1.85, 1.3, 1.85, -90, 172, 10)])
    g["7"] = (2.85, [[(0.2, 5.75), (0.45, 6.0), (2.7, 6.0), (1.05, 0.0)]])
    g["8"] = (3.0, [_arc(1.5, 4.65, 1.1, 1.35, 0, 360, 12), _arc(1.5, 1.7, 1.3, 1.7, 0, 360, 12)])
    g["9"] = (3.0, [[(3.0 - x, 6.0 - y) for x, y in g["6"][1][0]]])
    g["."] = (0.85, [_arc(0.425, 0.35, 0.22, 0.22, 0, 360, 6)])
    g[":"] = (0.85, [_arc(0.425, 1.25, 0.22, 0.22, 0, 360, 6), _arc(0.425, 4.25, 0.22, 0.22, 0, 360, 6)])
    g["-"] = (2.1, [[(0.25, hw - 0.35), (1.85, hw - 0.35)]])
    g["/"] = (2.4, [[(0.2, 0.0), (2.2, 6.0)]])
    g["'"] = (0.95, [[(0.55, 6.1), (0.35, 4.7)]])
    g["°"] = (1.5, [_arc(0.75, 4.9, 0.48, 0.48, 0, 360, 8)])
    g["&"] = (3.4, [[(3.1, 2.2), (1.1, 0.25)] + _arc(1.2, 1.5, 1.0, 1.5, -125, -245, 7)
                    + _arc(1.45, 4.65, 0.85, 1.35, 225, -45, 9) + [(0.5, 2.2), (3.15, 0.0)]])
    return {k: (w, [_dedupe(p) for p in st]) for k, (w, st) in g.items()}


FONTS = {
    "roman": _build_roman(),
    "nouveau": _build_nouveau(),
}


def _accent_strokes(kind, w):
    cx = w * 0.5
    if kind == "acute":
        return [[(cx - 0.35, 6.55), (cx + 0.45, 7.35)]]
    if kind == "grave":
        return [[(cx - 0.45, 7.35), (cx + 0.35, 6.55)]]
    if kind == "circum":
        return [[(cx - 0.65, 6.55), (cx, 7.35), (cx + 0.65, 6.55)]]
    if kind == "diaeresis":
        return [_arc(cx - 0.45, 6.95, 0.16, 0.16, 0, 360, 6),
                _arc(cx + 0.45, 6.95, 0.16, 0.16, 0, 360, 6)]
    if kind == "cedilla":
        return [[(cx, 0.0), (cx - 0.15, -0.55)] + _arc(cx - 0.35, -0.85, 0.35, 0.3, 60, -135, 6)]
    return []


def plain_safe(text):
    """Normalise arbitrary Unicode text into the ASCII subset accepted by C++ `detailText`."""
    out = []
    for raw in str(text).upper():
        if raw in _ACCENTS:
            raw = _ACCENTS[raw][0]
        else:
            decomp = unicodedata.normalize("NFD", raw)
            raw = "".join(ch for ch in decomp if unicodedata.category(ch) != "Mn")
        if ("A" <= raw <= "Z") or ("0" <= raw <= "9") or raw in " .:-/":
            out.append(raw)
        elif raw in ("'", "\u2019", "`", "_", ","):
            out.append(" ")
    return "".join(out) or " "


def glyph_spec(ch, font="plain"):
    """Return `(width_units, strokes)` on the 0..6 grid for character `ch` in `font`."""
    u = ch.upper()
    if font == "plain":
        c = plain_safe(u)[0]
        return (_PLAIN_WIDTHS.get(c, 4.0), [])
    table = FONTS[font]
    accent = None
    if u in _ACCENTS:
        u, accent = _ACCENTS[u]
    elif u not in table:
        u = plain_safe(u)[0]
    w, strokes = table.get(u, table[" "])
    if accent:
        strokes = list(strokes) + [_dedupe(p) for p in _accent_strokes(accent, w)]
    return w, strokes


def glyph_width(ch, font="plain"):
    """Advance width of one character in cap-height units (width / cap_height)."""
    return glyph_spec(ch, font)[0] / GRID_H


def text_aspect(text, font="plain", spacing=None):
    """Rendered line width divided by cap-height (for the widest line if `text` has `\\n`)."""
    if font not in DEFAULT_SPACING:
        raise ValueError(f"unknown font {font!r}; expected one of {tuple(DEFAULT_SPACING)}")
    gap = DEFAULT_SPACING[font] if spacing is None else spacing
    best = 0.25
    for line in str(text).splitlines() or [str(text)]:
        if not line:
            continue
        chars = plain_safe(line) if font == "plain" else line
        widths = [glyph_spec(c, font)[0] for c in chars]
        total = sum(widths) + gap * max(0, len(widths) - 1)
        best = max(best, total / GRID_H)
    return best


def fit_text_size(G, id_, text, inner_width, inner_height, *, font="plain", max_size=None,
                  spacing=None, line_gap=0.42):
    """Derived cap-height expression that fits `text` (one or more `\\n` lines) inside `(inner_width, inner_height)`."""
    lines = str(text).splitlines() or [str(text)]
    n_lines = max(1, len(lines))
    block_h = n_lines + (n_lines - 1) * line_gap
    aspect = text_aspect(text, font=font, spacing=spacing)
    cap_by_h = inner_height / block_h
    if max_size is not None:
        cap_by_h = mn(cap_by_h, max_size)
    cap_by_w = inner_width / mx(aspect, 0.25)
    return G.let(f"{id_}-cap", mx(0.004, mn(cap_by_h, cap_by_w)))


def _resolve_frame(face, right, up, normal):
    if right is not None and up is not None:
        if normal is None:
            normal = (
                right[1] * up[2] - right[2] * up[1],
                right[2] * up[0] - right[0] * up[2],
                right[0] * up[1] - right[1] * up[0],
            )
        return tuple(right), tuple(up), tuple(normal)
    if face not in FACES:
        raise ValueError(f"unknown face {face!r}; expected one of {tuple(FACES)} or explicit right=/up=")
    return FACES[face]


def _join_curves(G, id_, curve_ids):
    """Join any number of curve nodes while respecting `join`'s 128-input ceiling."""
    if len(curve_ids) == 1:
        return curve_ids[0]
    if len(curve_ids) <= 96:
        return G.join(id_, *curve_ids)
    chunks = []
    for k in range(0, len(curve_ids), 96):
        chunks.append(G.join(f"{id_}-b{k // 96}", *curve_ids[k:k + 96]))
    return G.join(id_, *chunks)


def _stroke_opts(profile, size, stroke_ratio, relief, slot, reach, step=None):
    w = size * stroke_ratio
    opts = {"width": w, "slot": slot, "reach": reach}
    if step is not None:
        opts["step"] = step
    if profile == "groove":
        opts["depth"] = absx(relief) if relief is not None else size * 0.045
    elif profile in ("bead", "band", "lap"):
        opts["height"] = relief if relief is not None else size * 0.035
    elif profile == "mark":
        if not slot:
            raise ValueError("profile='mark' requires a material slot")
    else:
        raise ValueError(f"unknown text profile {profile!r}")
    return opts


def _emit_stroke_block(G, id_, surface, lines, anchor, size, right, up, normal, *,
                       font, spacing, line_gap, profile, stroke_ratio, relief, slot, reach):
    gap = DEFAULT_SPACING[font] if spacing is None else spacing
    n_lines = len(lines)
    curves = []
    idx = 0
    for li, line in enumerate(lines):
        if not line.strip():
            continue
        glyphs = [glyph_spec(ch, font) for ch in line]
        total_u = sum(w for w, _ in glyphs) + gap * max(0, len(glyphs) - 1)
        cursor_u = -0.5 * total_u
        line_v = ((n_lines - 1) * 0.5 - li) * (1.0 + line_gap)
        for w, strokes in glyphs:
            for poly in strokes:
                if len(poly) < 2:
                    continue
                pts = []
                for gx, gy in poly:
                    u = (cursor_u + gx) / GRID_H
                    v = (gy - GRID_H * 0.5) / GRID_H + line_v
                    pts.append([round(u * right[i] + v * up[i], 6) for i in range(3)])
                cid = f"{id_}-s{idx}"
                idx += 1
                G.n(cid, "spline", points=pts, interpolation="polyline")
                curves.append(cid)
            cursor_u += w + gap
    if not curves:
        return surface
    joined = _join_curves(G, f"{id_}-unit", curves)
    placed = G.xf(f"{id_}-guides", joined, t=anchor, s=[size, size, size])
    opts = _stroke_opts(profile, size, stroke_ratio, relief, slot, reach, step=mx(0.003, size * 0.18))
    return G.stroke(id_, surface, placed, profile=profile, **opts)


def _inscribe_single(G, id_, surface, text, centre, panel_size, *,
                     face, font, pad, max_size, profile, slot, stroke_ratio,
                     relief, spacing, line_gap, lift, reach, right, up, normal):
    right_v, up_v, norm_v = _resolve_frame(face, right, up, normal)
    pw, ph = panel_size
    px, py = (pad, pad) if isinstance(pad, (int, float)) else pad
    inner_w = G.let(f"{id_}-iw", mx(0.01, pw * (1.0 - 2.0 * px)))
    inner_h = G.let(f"{id_}-ih", mx(0.01, ph * (1.0 - 2.0 * py)))
    size = fit_text_size(G, id_, text, inner_w, inner_h, font=font,
                         max_size=max_size, spacing=spacing, line_gap=line_gap)
    anchor = [centre[i] + lift * norm_v[i] if norm_v[i] else centre[i] for i in range(3)]
    lines = [ln for ln in str(text).splitlines()] or [str(text)]

    if font == "plain":
        cur = surface
        n_lines = len(lines)
        for li, ln in enumerate(lines):
            safe = plain_safe(ln)
            if not safe.strip():
                continue
            v_off = ((n_lines - 1) * 0.5 - li) * (1.0 + line_gap) * size
            at = [anchor[i] + (v_off * up_v[i] if up_v[i] else 0.0) for i in range(3)]
            opts = _stroke_opts(profile, size, stroke_ratio, relief, slot, reach)
            if spacing is not None:
                opts["spacing"] = spacing
            lid = id_ if n_lines == 1 else f"{id_}-l{li}"
            cur = G.text(lid, cur, at, safe, size=size, direction=list(right_v),
                         align="centre", profile=profile, **opts)
        return cur

    return _emit_stroke_block(
        G, id_, surface, lines, anchor, size, right_v, up_v, norm_v,
        font=font, spacing=spacing, line_gap=line_gap, profile=profile,
        stroke_ratio=stroke_ratio, relief=relief, slot=slot, reach=reach,
    )


def inscribe(G, id_, surface, text, centre, panel_size, face="-y", *,
             font="plain", variant=None, pad=(0.08, 0.24), max_size=None,
             profile="bead", slot="bronze", stroke_ratio=0.11, relief=None,
             spacing=None, line_gap=0.42, lift=0.015, reach=0.18,
             right=None, up=None, normal=None):
    """Inscribe `text` dead-centre inside a rectangular panel of `panel_size = [width, height]`
    whose face midpoint is `centre`.

    - Never pass a baseline coordinate: `centre` is the geometric midpoint of the panel face.
    - `panel_size` accepts numbers or `kitlib` expressions (`[W - 0.2, FH]`), so cap-height
      shrinks automatically when controls sweep narrow.
    - `text` may be a single string (`"MAISON IRIS"`, or multi-line `"ANNO\\n1898"`) OR a
      tuple/list of strings paired with an integer control `variant=C("name")`.
    - `face` (`"-y"`, `"+y"`, `"+x"`, `"-x"`, `"+z"`, `"-z"`) sets reading direction and
      outward projection so rear and side faces are never mirrored.
    - `font`: `"plain"` (`detailText`), `"roman"` (serif architectural capitals) or
      `"nouveau"` (condensed Brussels 1900 capitals).
    """
    kw = dict(face=face, font=font, pad=pad, max_size=max_size, profile=profile, slot=slot,
              stroke_ratio=stroke_ratio, relief=relief, spacing=spacing, line_gap=line_gap,
              lift=lift, reach=reach, right=right, up=up, normal=normal)
    if isinstance(text, (list, tuple)):
        choices = list(text)
        if not choices:
            return surface
        if len(choices) == 1 or variant is None:
            return _inscribe_single(G, id_, surface, choices[0], centre, panel_size, **kw)
        nodes = [_inscribe_single(G, f"{id_}-v{i}", surface, t, centre, panel_size, **kw)
                 for i, t in enumerate(choices)]
        cur = nodes[0]
        for i in range(1, len(nodes)):
            step_id = id_ if i == len(nodes) - 1 else f"{id_}-sel{i}"
            cur = G.n(step_id, "select", [nodes[i], cur], condition=gt(variant, i - 0.5))
        return cur
    return _inscribe_single(G, id_, surface, text, centre, panel_size, **kw)


def _arch_z_fn(z_at):
    if callable(z_at):
        return z_at
    z_ends, rise = z_at
    return lambda u: z_ends + rise * (1.0 - u * u)


def _arch_single(G, id_, surface, text, span, band_height, z_at, y, face, *,
                 font, pad_x, pad_y, spread, max_size, profile, slot,
                 stroke_ratio, relief, spacing, follow_slope, lift, reach):
    if face not in ("-y", "+y"):
        raise ValueError("arch_inscribe operates on x-z arches facing '-y' or '+y'")
    sx = 1.0 if face == "-y" else -1.0
    ny = -1.0 if face == "-y" else 1.0
    z_fn = _arch_z_fn(z_at)
    gap = DEFAULT_SPACING[font] if spacing is None else spacing

    word = plain_safe(text) if font == "plain" else str(text)
    specs = [glyph_spec(ch, font) for ch in word]
    if not specs:
        return surface
    total_units = sum(w for w, _ in specs) + gap * max(0, len(specs) - 1)
    aspect = max(0.25, total_units / GRID_H)

    usable_w = G.let(f"{id_}-uw", mx(0.02, span * (1.0 - 2.0 * pad_x)))
    usable_h = G.let(f"{id_}-uh", mx(0.01, band_height * (1.0 - 2.0 * pad_y)))
    cap_h = usable_h if max_size is None else mn(usable_h, max_size)
    size = G.let(f"{id_}-cap", mx(0.004, mn(cap_h, usable_w / aspect)))
    half_span = G.let(f"{id_}-hs", mx(0.02, span * 0.5))

    # Character centres in ems relative to word midpoint; optional `spread` (0..1) blends
    # between natural tracking (`size * aspect`) and filling `usable_w` end-to-end.
    natural_w = size * aspect
    line_w = G.let(f"{id_}-lw", natural_w + (usable_w - natural_w) * max(0.0, min(1.0, spread)))

    centers_frac = []
    cursor = -0.5 * total_units
    for w, st in specs:
        centers_frac.append((cursor + 0.5 * w) / max(total_units, 1e-6))
        cursor += w + gap

    y_pos = y + lift * ny
    du = 0.02

    if font == "plain":
        cur = surface
        opts_base = _stroke_opts(profile, size, stroke_ratio, relief, slot, reach)
        for k, (ch, frac) in enumerate(zip(word, centers_frac)):
            if ch == " ":
                continue
            xk = sx * frac * line_w
            uk = G.let(f"{id_}-u{k}", xk / half_span)
            zk = z_fn(uk)
            if follow_slope:
                dz = z_fn(uk + sx * du) - z_fn(uk - sx * du)
                dx = 2.0 * du * half_span
                direction = [sx * dx, 0.0, dz]
            else:
                direction = [sx, 0.0, 0.0]
            cur = G.text(f"{id_}-c{k}", cur, [xk, y_pos, zk], ch, size=size,
                         direction=direction, align="centre", profile=profile, **opts_base)
        return G.join(id_, cur) if cur != surface else surface

    char_guides = []
    for k, ((w, strokes), frac) in enumerate(zip(specs, centers_frac)):
        if not strokes:
            continue
        sub = []
        for j, poly in enumerate(strokes):
            if len(poly) < 2:
                continue
            pts = [[round(sx * (gx - 0.5 * w) / GRID_H, 6), 0.0, round((gy - 0.5 * GRID_H) / GRID_H, 6)]
                   for gx, gy in poly]
            cid = f"{id_}-g{k}-{j}"
            G.n(cid, "spline", points=pts, interpolation="polyline")
            sub.append(cid)
        if not sub:
            continue
        glyph_node = _join_curves(G, f"{id_}-g{k}", sub)
        xk = sx * frac * line_w
        uk = G.let(f"{id_}-u{k}", xk / half_span)
        zk = z_fn(uk)
        if follow_slope:
            dz = z_fn(uk + sx * du) - z_fn(uk - sx * du)
            dx = 2.0 * du * half_span
            ry = atan2(dz, dx) * -sx
            rot = [0.0, ry, 0.0]
        else:
            rot = None
        char_guides.append(G.xf(f"{id_}-p{k}", glyph_node, t=[xk, y_pos, zk], r=rot, s=[size, size, size]))

    if not char_guides:
        return surface
    all_guides = _join_curves(G, f"{id_}-guides", char_guides)
    opts = _stroke_opts(profile, size, stroke_ratio, relief, slot, reach, step=mx(0.003, size * 0.18))
    return G.stroke(id_, surface, all_guides, profile=profile, **opts)


def arch_inscribe(G, id_, surface, text, span, band_height, z_at, y, face="-y", *,
                  font="nouveau", variant=None, pad_x=0.11, pad_y=0.23, spread=0.0,
                  max_size=None, profile="bead", slot="marking", stroke_ratio=0.12,
                  relief=None, spacing=None, follow_slope=True, lift=0.018, reach=0.18):
    """Set `text` along an arched band spanning `[-span/2, +span/2]` in X at face plane `y`.

    `z_at` is EITHER:
    - a callable `z_centre(u)` (`u = x / (span / 2)` in `[-1, 1]`) returning the **vertical
      midpoint Z** of the arch band at `u` (not its bottom rim!), OR
    - a `(z_ends, rise)` tuple for a smooth parabolic arch `z_ends + rise * (1 - u**2)`.

    Characters are proportionally kerned by true glyph width, kept inside `pad_x`/`pad_y`
    margins across control sweeps, centred on the band midline, and rotated tangent to the arch.
    """
    kw = dict(font=font, pad_x=pad_x, pad_y=pad_y, spread=spread, max_size=max_size,
              profile=profile, slot=slot, stroke_ratio=stroke_ratio, relief=relief,
              spacing=spacing, follow_slope=follow_slope, lift=lift, reach=reach)
    if isinstance(text, (list, tuple)):
        choices = list(text)
        if not choices:
            return surface
        if len(choices) == 1 or variant is None:
            return _arch_single(G, id_, surface, choices[0], span, band_height, z_at, y, face, **kw)
        nodes = [_arch_single(G, f"{id_}-v{i}", surface, t, span, band_height, z_at, y, face, **kw)
                 for i, t in enumerate(choices)]
        cur = nodes[0]
        for i in range(1, len(nodes)):
            step_id = id_ if i == len(nodes) - 1 else f"{id_}-sel{i}"
            cur = G.n(step_id, "select", [nodes[i], cur], condition=gt(variant, i - 0.5))
        return cur
    return _arch_single(G, id_, surface, text, span, band_height, z_at, y, face, **kw)


# ---------------------------------------------------------------- ready-made signage parts
_OPPOSITE_FACE = {"-y": "+y", "+y": "-y", "+x": "-x", "-x": "+x", "+z": "-z", "-z": "+z"}
_OUTLINE_ROT = {
    "-y": [PI * 0.5, 0.0, 0.0], "+y": [PI * -0.5, 0.0, 0.0],
    "+x": [PI * 0.5, 0.0, PI * 0.5], "-x": [PI * 0.5, 0.0, PI * -0.5],
    "+z": None, "-z": [PI, 0.0, 0.0],
}


def _bevel_box(G, id_, size, at, bevel=0.006):
    raw = G.box(id_ + "-raw", size, at)
    if not bevel:
        return raw
    b = mn(bevel, mn(size[0], mn(size[1], size[2])) * 0.35)
    return G.n(id_, "boxBevel", [raw], width=b)


def sign_board(G, id_, text, at, size, *, face="-y", both_sides=False,
               font="nouveau", variant=None, board_slot="black", frame_slot="iron",
               border_slot="bronze", text_slot="bronze", profile="bead",
               pad=(0.09, 0.24), max_size=None, bevel=0.007):
    """A complete framed fascia signboard centred at `at` with `size = [width, depth, height]`
    (for `face="-y"`/`"+y"`, or `[depth, width, height]` for `"+x"`/`"-x"`):
    bevelled board, top & bottom moulded rails, end stiles, inset fillet border, and
    vertically centred, width-clamped lettering (optionally on `both_sides=True`).
    Returns one material-assigned, audit-connected geometry node id.
    """
    cx, cy, cz = at
    along_x = face in ("-y", "+y")
    span = G.let(f"{id_}-w", size[0] if along_x else size[1])
    depth = G.let(f"{id_}-d", size[1] if along_x else size[0])
    height = G.let(f"{id_}-h", size[2])
    rail_h = G.let(f"{id_}-rh", mn(0.038, height * 0.13))
    stile_w = G.let(f"{id_}-sw", mn(0.045, span * 0.04))

    board_dims = [span, depth, height] if along_x else [depth, span, height]
    board = _bevel_box(G, f"{id_}-board", board_dims, [cx, cy, cz], bevel=bevel)

    faces = [face, _OPPOSITE_FACE[face]] if both_sides else [face]
    inner_panel = [mx(0.04, span - stile_w * 2.4), mx(0.03, height - rail_h * 2.4)]
    cur = board
    for fi, fc in enumerate(faces):
        _, _, nv = FACES[fc]
        f_at = [cx + nv[0] * depth * 0.5, cy + nv[1] * depth * 0.5, cz + nv[2] * depth * 0.5]
        if border_slot:
            b_at = [f_at[i] + nv[i] * 0.012 for i in range(3)]
            guide = G.outline(f"{id_}-bdr-g{fi}", at=b_at, r=_OUTLINE_ROT[fc], shape="rect",
                              size=[mx(0.02, span - stile_w * 2.1), mx(0.02, height - rail_h * 2.1)],
                              corner=mn(0.02, height * 0.08))
            cur = G.stroke(f"{id_}-bdr{fi}", cur, guide, profile="bead",
                           width=mn(0.009, height * 0.028), height=0.0025, slot=border_slot, reach=0.12)
        cur = inscribe(G, f"{id_}-txt{fi}", cur, text, f_at, inner_panel, face=fc,
                       font=font, variant=variant, pad=pad, max_size=max_size,
                       profile=profile, slot=text_slot, lift=0.015, reach=0.14)
    board_mat = G.mat(f"{id_}-board-mat", cur, board_slot)

    if not frame_slot:
        return G.join(id_, board_mat)

    frame_parts = []
    rail_d = depth + 0.028
    for tag, sgn in (("bot", -1.0), ("top", 1.0)):
        rz = cz + sgn * (height * 0.5 - rail_h * 0.5)
        r_dims = [span + 0.024, rail_d, rail_h] if along_x else [rail_d, span + 0.024, rail_h]
        frame_parts.append(_bevel_box(G, f"{id_}-rail-{tag}", r_dims, [cx, cy, rz], bevel=bevel * 0.8))
    stile_h = mx(0.01, height - rail_h * 2.0)
    for tag, sgn in (("l", -1.0), ("r", 1.0)):
        off = sgn * (span * 0.5 - stile_w * 0.5)
        s_at = [cx + off, cy, cz] if along_x else [cx, cy + off, cz]
        s_dims = [stile_w, depth + 0.02, stile_h] if along_x else [depth + 0.02, stile_w, stile_h]
        frame_parts.append(_bevel_box(G, f"{id_}-stile-{tag}", s_dims, s_at, bevel=bevel * 0.8))
    G.join(f"{id_}-frame-raw", *frame_parts)
    frame_mat = G.mat(f"{id_}-frame-mat", f"{id_}-frame-raw", frame_slot)
    return G.join(id_, board_mat, frame_mat)


def number_plaque(G, id_, text, at, size=(0.15, 0.022, 0.18), *, face="-y",
                  shape="rect", font="nouveau", variant=None, plaque_slot="black",
                  rim_slot="bronze", text_slot="marking", profile="mark",
                  screws=True, pad=(0.16, 0.25)):
    """Enamel or cast house-number / notice plaque centred at `at` (`size = [w, d, h]` on `±y` faces):
    bevelled plate (`rect` or `oval`), inset rim bead, optional slotted corner/side screws, and
    dead-centre numerals or short notice (`"25"`, `"OUVERT"`, `"NO\\n25"`).
    """
    cx, cy, cz = at
    along_x = face in ("-y", "+y")
    pw = G.let(f"{id_}-w", size[0] if along_x else size[1])
    pd = G.let(f"{id_}-d", size[1] if along_x else size[0])
    ph = G.let(f"{id_}-h", size[2])
    _, _, nv = FACES[face]

    if shape == "oval":
        segs = 28
        prof = [[math.cos(2 * math.pi * k / segs) * 0.5, math.sin(2 * math.pi * k / segs) * 0.5]
                for k in range(segs)]
        G.n(f"{id_}-oval-unit", "profileSolid", profile=prof, depth=1.0, bevel=0.06)
        rot = [PI * 0.5, 0.0, 0.0] if along_x else [PI * 0.5, 0.0, PI * 0.5]
        # profileSolid extrudes 0..depth along local +Z; centre its thickness first
        G.xf(f"{id_}-oval-c", f"{id_}-oval-unit", t=[0.0, 0.0, -0.5])
        plate = G.xf(f"{id_}-plate", f"{id_}-oval-c", t=[cx, cy, cz], r=rot, s=[pw, ph, pd])
    else:
        dims = [pw, pd, ph] if along_x else [pd, pw, ph]
        plate = _bevel_box(G, f"{id_}-plate", dims, [cx, cy, cz], bevel=0.006)

    cur = plate
    f_at = [cx + nv[0] * pd * 0.5, cy + nv[1] * pd * 0.5, cz + nv[2] * pd * 0.5]
    if rim_slot:
        b_at = [f_at[i] + nv[i] * 0.01 for i in range(3)]
        if shape == "oval":
            G.outline(f"{id_}-rim-u", shape="circle", radius=0.43, segments=32)
            guide = G.xf(f"{id_}-rim-g", f"{id_}-rim-u", t=b_at, r=_OUTLINE_ROT[face], s=[pw, ph, 1.0])
        else:
            guide = G.outline(f"{id_}-rim-g", at=b_at, r=_OUTLINE_ROT[face], shape="rect",
                              size=[mx(0.02, pw * 0.84), mx(0.02, ph * 0.84)], corner=mn(0.015, pw * 0.12))
        cur = G.stroke(f"{id_}-rim", cur, guide, profile="bead",
                       width=mn(0.006, pw * 0.04), height=0.0018, slot=rim_slot, reach=0.08)

    if screws:
        rv, uv, _ = FACES[face]
        s_pts = []
        if shape == "oval":
            for sgn in (-1.0, 1.0):
                off = sgn * pw * 0.37
                s_pts.append([f_at[i] + nv[i] * 0.012 + rv[i] * off for i in range(3)])
        else:
            for sx in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    s_pts.append([f_at[i] + nv[i] * 0.012 + rv[i] * sx * pw * 0.36 + uv[i] * sz * ph * 0.36
                                  for i in range(3)])
        G.points(f"{id_}-scr-at", s_pts)
        cur = G.fasteners(f"{id_}-scr", cur, f"{id_}-scr-at", head="slotted",
                          radius=mn(0.006, pw * 0.038), height=0.0018, slot=rim_slot or "bronze", reach=0.08)

    eff_pad = (max(pad[0], 0.22), max(pad[1], 0.27)) if shape == "oval" else pad
    cur = inscribe(G, f"{id_}-txt", cur, text, f_at, [pw, ph], face=face,
                   font=font, variant=variant, pad=eff_pad, profile=profile,
                   slot=text_slot, stroke_ratio=0.125, lift=0.012, reach=0.09)
    return G.mat(id_, cur, plaque_slot)


def arch_sign_board(G, id_, text, span, band_height, z_ends, rise, y=0.0, thickness=0.055, *,
                    face="-y", both_sides=False, font="nouveau", variant=None, spread=0.35,
                    board_slot="terracotta", rim_slot="iron", text_slot="marking",
                    profile="bead", segments=24):
    """Curved arched sign plate (station entrance, market portal, footbridge arch) spanning
    `[-span/2, +span/2]` in X at `y`: closed extruded arch band from `z_ends + rise*(1 - u^2)`
    with moulded top & bottom swept rims, end bosses, and tangent-tilted `arch_inscribe` lettering
    centred strictly along the arch midline.
    """
    sp = G.let(f"{id_}-span", span)
    bh = G.let(f"{id_}-bh", band_height)
    ze = G.let(f"{id_}-ze", z_ends)
    rs = G.let(f"{id_}-rise", rise)
    hs = G.let(f"{id_}-hs", sp * 0.5)
    z_mid = lambda u: ze + rs * (1.0 - u * u)

    us = [-1.0 + 2.0 * k / segments for k in range(segments + 1)]
    bot_pts = [[hs * u, z_mid(u) - bh * 0.5] for u in us]
    top_pts = [[hs * u, z_mid(u) + bh * 0.5] for u in reversed(us)]
    G.n(f"{id_}-plate-xz", "extrude", profile=bot_pts + top_pts, depth=thickness)
    plate = G.xf(f"{id_}-plate", f"{id_}-plate-xz", t=[0.0, y + thickness * 0.5, 0.0], r=[PI * 0.5, 0.0, 0.0])

    faces = [face, _OPPOSITE_FACE[face]] if both_sides else [face]
    cur = plate
    for fi, fc in enumerate(faces):
        fy = y - thickness * 0.5 if fc == "-y" else y + thickness * 0.5
        cur = arch_inscribe(G, f"{id_}-txt{fi}", cur, text, sp, bh, z_mid, fy, face=fc,
                            font=font, variant=variant, spread=spread, profile=profile,
                            slot=text_slot, lift=0.018, reach=0.14)
    plate_mat = G.mat(f"{id_}-plate-mat", cur, board_slot)

    rim_r = G.let(f"{id_}-rr", mn(0.024, bh * 0.085))
    boss_w = G.let(f"{id_}-bw", mn(0.08, sp * 0.04))
    u_lim = G.let(f"{id_}-ul", mx(0.2, 1.0 - boss_w * 0.5 / mx(hs, 0.05)))
    iron_parts = []
    for tag, off_z in (("top", bh * 0.5), ("bot", bh * -0.5)):
        pts3 = [[hs * u * u_lim, y, z_mid(u * u_lim) + off_z] for u in us]
        G.n(f"{id_}-rim-{tag}-p", "spline", points=pts3, interpolation="catmullRom", resolution=4)
        # tube depth spans across thickness so rim clasps both faces cleanly
        iron_parts.append(G.n(f"{id_}-rim-{tag}", "tube", [f"{id_}-rim-{tag}-p"], radius=rim_r + thickness * 0.35, sides=10))
    for tag, sgn in (("l", -1.0), ("r", 1.0)):
        iron_parts.append(_bevel_box(G, f"{id_}-boss-{tag}",
                                     [boss_w, thickness + rim_r * 2.4, bh + rim_r * 2.6],
                                     [sgn * hs, y, ze], bevel=0.008))
    G.join(f"{id_}-iron-raw", *iron_parts)
    iron_mat = G.mat(f"{id_}-iron-mat", f"{id_}-iron-raw", rim_slot)
    return G.join(id_, plate_mat, iron_mat)
