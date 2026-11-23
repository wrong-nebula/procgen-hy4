#!/usr/bin/env python3
"""Build two demo modules from recipes/kitparts.py into a scratch kit, then pgm check, sweep and audit them and
render an iso view of each. Usage: python3 tools/test_kitparts.py [out_dir]"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "recipes"))
from kitlib import C, PI, Graph, Kit, control, mn, port  # noqa: E402
from kitparts import (arched_wall, fit_ring_depth, channel_joints, bolted_flange, fit_arch, moulding, moulding_height,  # noqa: E402
                      spoked_wheel, tie_rod, voussoirs, cast_housing, sds_cylinder, filleted_box, curved_boss_patch)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/private/tmp/kitparts-test")
PGM = str(HERE / "build-release" / "pgm")
kit = Kit(OUT, "parts-demo", "parts-demo")


def demo_arch():
    """A street-arcade bay in about 25 lines of parts."""
    G = Graph()
    P, H, D = C("pitch"), C("height"), C("depth")
    hp, tw = P * 0.5, mn(0.55, D * 0.3)
    yb = D * -1 + tw                                   # back of the street wall
    k = H / 4.2
    corn = moulding_height("cornice", k)
    R = hp - (0.3 + P * 0.035)
    arch = fit_arch(G, "arch", R, H - corn - 0.45 * k, mn(1.95, H * 0.47))
    rd = fit_ring_depth(arch, hp, H - corn)
    arched_wall(G, "wall", arch, rd, hp, H - 0.02, yb, tw)
    channel_joints(G, "wall-coursed", "wall", arch, rd, hp, 0.42, H - corn, D * -1)
    voussoirs(G, "ring", arch, rd, yb, tw)
    moulding(G, "impost-w", "impost", hp - R + 0.06, D * -1, arch["spring"] - 0.3 * k, k, x0=hp * -1)
    moulding(G, "impost-e", "impost", hp - R + 0.06, D * -1, arch["spring"] - 0.3 * k, k, x0=R - 0.06)
    moulding(G, "plinth-w", "plinth", hp - R, D * -1, 0, x0=hp * -1)     # on the piers only, not across the walk
    moulding(G, "plinth-e", "plinth", hp - R, D * -1, 0, x0=R)
    moulding(G, "cornice", "cornice", P, D * -1, H - corn, k)
    G.box("roof", [P, D - 0.05, 0.08], [0, D * -0.5, H + 0.0])
    tie_rod(G, "tie", "y", yb - 0.05, -0.05, [(hp - (hp - R) * 0.5) * -1, 0, arch["spring"] - 0.5])
    G.box("wall-pilaster", [hp - R, 0.1, H], [(hp - (hp - R) * 0.5) * -1, -0.05, H * 0.5])  # what the tie anchors to
    G.join("stone", "wall-pilaster", "wall-coursed", "ring", "impost-w", "impost-e", "plinth-w", "plinth-e", "cornice", "roof")
    G.mat("limestone", "stone", "chipped-limestone")
    G.mat("iron", "tie", "iron")
    G.join("bay", "limestone", "iron")
    deck = {"width": "$depth"}
    kit.module("demo-arch", {"pitch": control(3.6, 2.4, 6), "height": control(4.2, 3, 5.4), "depth": control(2.2, 1.2, 4)},
               G, "bay", {"west": port([hp * -1, D * -0.5, 0], [-1, 0, 0], "deck", deck),
                          "east": port([hp, D * -0.5, 0], [1, 0, 0], "deck", deck)})


def demo_valve_bits():
    """A flanged pipe run with a handwheel above it."""
    G = Graph()
    L = C("length")
    G.pipe("barrel", "x", L * -0.5 + 0.2, L * 0.5 - 0.2, 0.38, 0.33, sides=32)
    bolted_flange(G, "flange-w", "x", [L * -0.5 + 0.1, 0, 0], 0.53, 0.33, 0.2, face=-1)
    bolted_flange(G, "flange-e", "x", [L * 0.5 - 0.1, 0, 0], 0.53, 0.33, 0.2, face=1)
    G.rod("stem", [0, 0, 0.37], [0, 0, 1.3], 0.03, 10)
    spoked_wheel(G, "wheel", [0, 0, 1.2], 0.32, spokes=5)
    G.join("iron-parts", "barrel", "flange-w", "flange-e")
    G.mat("iron", "iron-parts", "iron")
    G.join("bronze-parts", "stem", "wheel")
    G.mat("bronze", "bronze-parts", "bronze")
    G.join("parts", "iron", "bronze")
    pipe = {"diameter": 0.76}
    kit.module("demo-pipe-wheel", {"length": control(2, 1.2, 6)}, G, "parts",
               {"west": port([L * -0.5, 0, 0], [-1, 0, 0], "pipe", pipe), "east": port([L * 0.5, 0, 0], [1, 0, 0], "pipe", pipe)})


def demo_cast_housing():
    """An SDS + FWN cast-iron junction housing: FWN baseplate, Chaikin SDS body, quad-capped SDS drum, shrinkwrapped nameplate."""
    G = Graph()
    W = C("width")
    # 1. Flat machined baseplate: FWN circular bevel (sharp panels, specular bevels)
    filleted_box(G, "base", [W, 0.44, 0.06], at=[0, 0, 0.03], width=0.010, segments=3, mode="fwn")
    G.outline("base-bolts", at=[0, 0, 0.10], shape="rect", size=[W - 0.10, 0.32])
    G.fasteners("base-bolted", "base", "base-bolts", head="hex", radius=0.014, height=0.010, washer=0.024, count=8)
    # 2. Cast-iron SDS body with raised top boss (Chaikin creases + limit=1 zero shrinkage + multi-LOD)
    cast_housing(G, "body", [W - 0.14, 0.32, 0.34], at=[0, 0, 0.22], boss_inset=0.04, boss_height=0.06,
                 wall_crease=1.8, rim_crease=3.2, levels=2, lod_levels=[2, 1, 0])
    # 3. SDS cylindrical drum on top using all-quad cap (zero rim pinching)
    sds_cylinder(G, "drum", 0.11, 0.26, at=[0, 0, 0.54], axis="z", sides=12, rings=2, rim_crease=2.6,
                 levels=2, lod_levels=[2, 1, 0])
    # 4. Shrinkwrapped bronze nameplate conforming to the curved drum front (-y)
    curved_boss_patch(G, "badge", "drum", [0.12, 0.018, 0.07], at=[0, -0.11, 0.54], offset=0.002, thickness=0.008)
    G.join("iron-raw", "base-bolted", "body", "drum")
    G.mat("iron", "iron-raw", "iron")
    G.mat("bronze", "badge", "bronze")
    G.join("housing", "iron", "bronze")
    kit.module("demo-cast-housing", {"width": control(0.60, 0.48, 0.84)}, G, "housing",
               {"base": port([0, 0, 0], [0, 0, -1], "footing", {"width": "$width"})})


def pgm(*args):
    out = subprocess.run([PGM, *args], capture_output=True, text=True).stdout.strip().splitlines()
    return json.loads(out[-1]) if out else {"passed": False}


demo_arch()
demo_valve_bits()
demo_cast_housing()
kit.close()
ok = True
for name in ("demo-arch", "demo-pipe-wheel", "demo-cast-housing"):
    doc = OUT / "parts-demo" / f"{name}.json"
    chk, swp, aud = pgm("check", str(doc)), pgm("sweep", str(doc)), pgm("audit", str(doc))
    closed = chk.get("closedPrototypes"), chk.get("openPrototypes")
    sds = chk.get("sds") or {}
    pgm("render", str(doc), "--out", str(OUT / f"{name}.png"), "--view", "iso", "--pass", "beauty", "--size", "900x700")
    print(f"{name}: check={chk.get('passed')} closed/open={closed} sds_quads={sds.get('quadRatio', 0):.2f} "
          f"cages={sds.get('sdsCages', 0)} lods={sds.get('multiLodPrototypes', 0)} "
          f"sweep={swp.get('passed')} audit={aud.get('passed')} "
          f"{'' if aud.get('passed') else aud.get('detached')}{'' if swp.get('passed') else str(swp.get('failures'))[:300]}")
    ok &= bool(chk.get("passed") and swp.get("passed") and aud.get("passed"))
sys.exit(0 if ok else 1)
