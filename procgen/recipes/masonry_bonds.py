"""Masonry bond layout: the one source for the modeler's masonry geometry and the engine's bond maps.
Built by Opus 5.5 (medium).

Pure Python, no dependencies (kitlib_masonry imports this module; it imports nothing of the kit library).

  bond_pattern(bond, ...)   the bond's natural repeat cell: rows of head joints and the unit faces.
                            brick_wall / ashlar_wall lay their head joints on it (stone by stone, detail
                            grooves) and joints="material" names the slot whose maps are baked from it.
  cell(slot)                the square cell of a bond-map slot (procgen/tools/bond_maps.py bakes it): the
                            natural cell repeated n x m times, plus the map-only data (arris, recess,
                            relief). The material's `tile` is one value, so the cell must be square.

  from masonry_bonds import bond_pattern, cell, BONDS
  bond_pattern("flemish")["cell"]   # (0.3375, 0.15)
  c = cell("brick-flemish")         # c.size == 1.35, c.repeat == (4, 9), c.units: [Unit(x, y, w, h, kind, course)]

Frame (both): x to the right, z (y in a Unit) up, metres; the cell's origin is a head-joint and a bed-joint
centre line and sits at UV (0, 0) = the wall frame's x = 0, z = 0 (joints="material" UVs are x, z in
metres), so a wall's geometry joints and its map's joints coincide. A unit is its visible face, joints
excluded, and may run past the cell's right edge (the cell is periodic in x and z).

Slots, the natural cell and the repeats that make it square (SLOTS):
  brick-stretcher    0.225 x 0.15  m  x 2 x 3  ->  0.45 m tile
  brick-english      0.225 x 0.15  m  x 2 x 3  ->  0.45 m
  brick-flemish      0.3375 x 0.15 m  x 4 x 9  ->  1.35 m
  brick-header       0.1125 x 0.15 m  x 4 x 3  ->  0.45 m
  ashlar-coursed     0.7 x 0.7 m      x 2 x 2  ->  1.4 m  (1 x 1 is square too; doubled so the map shows
                                                           eight blocks rather than two)
  ashlar-rusticated  1.05 x 0.7 m     x 2 x 3  ->  2.1 m
Brick 215 x 102.5 x 65 mm, 10 mm joints. Ashlar: 0.35 m courses (joint included), half bond, 0.7 m blocks
with 5 mm joints (coursed) or 1.05 m blocks with 8 mm joints (rusticated); those are bond_pattern's
defaults for the two ashlar slots, and ashlar_wall(joints="material") takes them when `block` is not given.
"""
from dataclasses import dataclass, field

BOND_SLOTS = ("brick-stretcher", "brick-english", "brick-flemish", "brick-header", "ashlar-coursed", "ashlar-rusticated")
BRICK = (0.215, 0.1025, 0.065)   # length, width (header face), height, metres
BRICK_JOINT = 0.010
ASHLAR = {"ashlar-coursed": dict(block=0.7, joint=0.005), "ashlar-rusticated": dict(block=1.05, joint=0.008)}


def bond_pattern(bond, brick=BRICK, joint=None, course=0.35, block=None, shift=0.5):
    """One natural repeat cell of a bond: the single source of truth for brick_wall / ashlar_wall head joints
    and for the engine's bond maps (cell() repeats it to a square).

    bond: "stretcher", "english", "flemish", "header" (or "brick-..."), "ashlar" / "ashlar-coursed" /
    "ashlar-rusticated" (`course` high, `block` long, each course shifted `shift` of a block). brick
    (length, width, height) and joint in metres; joint defaults to 0.01 (brick), 0.005 (coursed ashlar) or
    0.008 (rusticated), block to 0.7 (coursed) or 1.05 (rusticated): with the defaults the pattern is the
    one the slot's maps are baked from. The cell is anchored at UV (0, 0) = the wall frame's x = 0, z = 0.
    Returns {"bond", "slot", "cell": (width, height), "joint", "rows": [{"z0", "z1", "cuts"}] (courses from
    the bottom: joint centre lines, head-joint centre x positions in [0, width)), "units": [(x0, z0, x1, z1,
    kind)]} with units the faces of the bricks or blocks between the joints (kind stretcher, header or block);
    a unit may run past the cell's right edge (wrap it by the cell width).
    Example: bond_pattern("flemish")["cell"] -> (0.3375, 0.15)"""
    b = bond[6:] if bond.startswith("brick-") else bond
    if b.startswith("ashlar"):
        slot = "ashlar-rusticated" if b == "ashlar-rusticated" else "ashlar-coursed"
        j = ASHLAR[slot]["joint"] if joint is None else joint
        block = ASHLAR[slot]["block"] if block is None else block
        n = next((k for k in range(1, 9) if abs((k * shift) % 1.0) < 1e-9 or abs((k * shift) % 1.0 - 1) < 1e-9), 8)
        rows = [{"z0": k * course, "z1": (k + 1) * course, "cuts": [((k * shift) % 1.0) * block]} for k in range(n)]
        w = block
    else:
        assert b in ("stretcher", "english", "flemish", "header"), f"bond {bond!r}: stretcher, english, flemish, header or ashlar"
        slot = "brick-" + b
        Lb, Wb, Hb = brick
        j = BRICK_JOINT if joint is None else joint
        c, P, Ph = Hb + j, Lb + j, Wb + j
        w, cuts = {"stretcher": (P, [[0.0], [P * 0.5]]), "header": (Ph, [[0.0], [Ph * 0.5]]),
                   "english": (P, [[0.0], [P * 0.25, P * 0.75]]),
                   "flemish": (Ph + P, [[0.0, Ph], [(Ph + P) * 0.5, (Ph + P) * 0.5 + Ph]])}[b]
        rows = [{"z0": k * c, "z1": (k + 1) * c, "cuts": cs} for k, cs in enumerate(cuts)]
    units = []
    for r in rows:
        cs = sorted(r["cuts"])
        for i, x in enumerate(cs):
            nx = cs[i + 1] if i + 1 < len(cs) else cs[0] + w
            kind = "block" if b.startswith("ashlar") else ("header" if abs(nx - x - (brick[1] + j)) < 1e-6 else "stretcher")
            units.append((x + j * 0.5, r["z0"] + j * 0.5, nx - j * 0.5, r["z1"] - j * 0.5, kind))
    return {"bond": b, "slot": slot, "cell": (w, rows[-1]["z1"]), "joint": j, "rows": rows, "units": units}


# Map-only data per slot: repeats (n across, m up) of the natural cell, arris profile ("round": radius,
# "chamfer": 45-degree width), mortar recess below the face and the depth the 0..1 height map spans (metres).
SLOTS = {
    "brick-stretcher": dict(repeat=(2, 3), edge="round", edge_size=0.003, recess=0.006, relief=0.010),
    "brick-english": dict(repeat=(2, 3), edge="round", edge_size=0.003, recess=0.006, relief=0.010),
    "brick-flemish": dict(repeat=(4, 9), edge="round", edge_size=0.003, recess=0.006, relief=0.010),
    "brick-header": dict(repeat=(4, 3), edge="round", edge_size=0.003, recess=0.006, relief=0.010),
    "ashlar-coursed": dict(repeat=(2, 2), edge="round", edge_size=0.002, recess=0.004, relief=0.007),
    # banded, V-jointed: an 8 mm joint between 20 mm chamfers
    "ashlar-rusticated": dict(repeat=(2, 3), edge="chamfer", edge_size=0.020, recess=0.024, relief=0.030),
}


@dataclass(frozen=True)
class Unit:
    x: float        # left edge of the face, metres (in [0, size); may run past the cell's right edge)
    y: float        # bottom edge of the face
    w: float
    h: float
    kind: str       # "stretcher", "header" or "block"
    course: int


@dataclass
class Bond:
    name: str
    size: float                 # the square repeat cell, metres (= the material's tile)
    joint: float                # joint width between faces, metres
    edge: str                   # arris profile: "round" (radius) or "chamfer" (45 degrees, width)
    edge_size: float            # arris radius or chamfer width, metres
    recess: float               # mortar surface below the face, metres (deeper than the arris)
    relief: float               # depth range the 0..1 height map spans, metres
    repeat: tuple = (1, 1)      # natural cells across and up
    natural: tuple = (0.0, 0.0)  # bond_pattern's cell (width, height)
    rows: list = field(default_factory=list)    # [{"z0", "z1", "cuts"}] over the square cell (joint centre lines)
    units: list = field(default_factory=list)


def _square(name):
    s = SLOTS[name]
    p = bond_pattern(name)
    (w, h), (n, m) = p["cell"], s["repeat"]
    size = round(n * w, 9)
    assert abs(n * w - m * h) < 1e-9, f"{name}: {n} x {w:g} != {m} x {h:g}, the cell is not square"
    b = Bond(name, size, p["joint"], s["edge"], s["edge_size"], s["recess"], s["relief"], (n, m), (w, h))
    rk = len(p["rows"])
    for jm in range(m):
        for k, r in enumerate(p["rows"]):
            b.rows.append({"z0": round(r["z0"] + jm * h, 9), "z1": round(r["z1"] + jm * h, 9),
                           "cuts": sorted(round(c + i * w, 9) for i in range(n) for c in r["cuts"])})
    for jm in range(m):
        for x0, z0, x1, z1, kind in p["units"]:
            for i in range(n):
                z = z0 + jm * h
                b.units.append(Unit(round(x0 + i * w, 9), round(z, 9), round(x1 - x0, 9), round(z1 - z0, 9), kind,
                                    jm * rk + int(round((z0 - p["joint"] * 0.5) / (h / rk)))))
    b.units.sort(key=lambda u: (u.course, u.x))
    return b


_CACHE = {n: _square(n) for n in BOND_SLOTS}
BONDS = BOND_SLOTS


def cell(name):
    """The square repeat cell of a bond-map slot (see the module docstring). Raises KeyError for unknown slots."""
    if name not in _CACHE:
        raise KeyError(f"bond {name!r}: one of {', '.join(BONDS)}")
    return _CACHE[name]
