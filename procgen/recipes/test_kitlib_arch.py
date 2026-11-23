#!/usr/bin/env python3
"""Tests for kitlib_arch.py: hipped_roof, sash_window, external_stair, box_on, clamp_to_wall,
corner_quoins, window_surround, louvred_lantern, corbelled_chimney, and panelled_door."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

RECIPES = Path(__file__).resolve().parent
PGM = RECIPES.parent / "build-release" / "pgm"
sys.path.insert(0, str(RECIPES))

from kitlib import Graph, C, control, _dump, clamp_to_wall  # noqa: E402

_BUTT = 0.0005


def _verify_doc(doc, label):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / f"{label}.json"
        p.write_text(_dump(doc, indent=1))
        chk = json.loads(subprocess.check_output([str(PGM), "check", str(p), "--intersections"], text=True))
        assert chk["passed"] and chk["clean"] and chk["openPrototypes"] == 0, (label, chk)
        assert chk.get("selfIntersectingPrototypes", 0) == 0, (label, chk)
        sw = json.loads(subprocess.check_output([str(PGM), "sweep", str(p), "--intersections"], text=True))
        assert sw["passed"] and sw["failed"] == 0, (label, sw)
        au = json.loads(subprocess.check_output([str(PGM), "audit", str(p), "--sweep", "--dangling"], text=True))
        assert au["passed"] and au["failed"] == 0, (label, au)


def test_full_parametric_pavilion():
    """All 9 Graph helpers composed on one parametric masonry-and-slate lodge with X controls."""
    g = Graph()
    W, D, H = C("width"), C("depth"), C("height")
    plinth_h = 0.36
    plinth = g.mat("plinth", g.box("plinth-b", [W + 0.22, D + 0.22, plinth_h], [0, 0, plinth_h * 0.5]),
                   "chipped-limestone")
    wall_h = H - plinth_h - _BUTT
    # Same-material stone test on rear/quoins/surrounds: walls use chipped-limestone to prove 0 self-intersections!
    walls = g.mat("walls", g.box("walls-b", [W, D, wall_h], [0, 0, plinth_h + _BUTT + wall_h * 0.5]),
                  "chipped-limestone")

    quoins = g.corner_quoins("quoins", W, D, z_base=plinth_h + _BUTT, height=wall_h, slot="chipped-limestone")
    sur_f = g.window_surround("sur-f", 1.28, 1.36, facing="-y", slot="chipped-limestone",
                              at=[-W * 0.22, -D * 0.5, 2.05])
    # Sash window nested cleanly inside window_surround clear opening (sill_h=0, lintel_h=0, z = z_sur + 0.161)
    win_f = g.sash_window("win-f", 1.28, 1.36, sill_h=0.0, lintel_h=0.0, raised=0.18,
                          frame_slot="wood", stone_slot="chipped-limestone", facing="-y",
                          at=[-W * 0.22, -D * 0.5, 2.05 + 0.161])
    # Side sash window with its own stone sill & lintel on -x wall (proves same-material stone sill/lintel vs wall!)
    win_w = g.sash_window("win-w", 1.05, 1.22, mullions=2, transoms=2, raised=0.0,
                          frame_slot="painted-wood", stone_slot="chipped-limestone", facing="-x",
                          at=[-W * 0.5, 0.0, 2.15])
    door_f = g.panelled_door("door-f", 0.96, 2.12, 0.056, at=[W * 0.22, -D * 0.5, plinth_h],
                             glazed_upper=True, panels_x=2, facing="-y")
    door_b = g.panelled_door("door-b", 0.92, 2.05, 0.054, at=[0, D * 0.5, plinth_h],
                             glazed_upper=False, panels_x=2, frame_slot="painted-wood", facing="+y")

    roof_rise = 1.28
    roof = g.hipped_roof("roof", W, D, rise=roof_rise, eaves=0.36, ridge_vent=False,
                         roof_slot="slate", fascia_slot="wood", trim_slot="weathered-iron", at=[0, 0, H])
    # Louvred lantern seated cleanly on top of the hipped roof crest
    lantern = g.louvred_lantern("lantern", 1.15, 0.54, 0.46, z_base=H + 0.14 + _BUTT + roof_rise + 0.08,
                                louvres=4, roof="hipped", finials=True)
    # Corbelled brick chimney seated on wall-plate shoulder near rear eaves
    chimney = g.corbelled_chimney("chimney", 0.68, 0.52, z_base=H + 0.14, height=1.95, pots=2,
                                  corbel_steps=3, at=[-W * 0.26, D * 0.28, 0])

    sign_brk = g.box_on("sign-brk", [0, 0, plinth_h + _BUTT + wall_h * 0.5], [W, D, wall_h],
                        "-y", [1.2, 0.07, 0.26], u=99.0, v=1.35, clamp_margin=0.45, slot="bronze")
    stair = g.external_stair("stair", rise=2.35, width=0.92, landing_len=1.12,
                             stringer_slot="weathered-iron", tread_slot="wood", rail_slot="iron",
                             facing="+x", at=[W * 0.5, 0, 0])

    out = g.bake("pavilion.baked", g.join(
        "all", plinth, walls, quoins, sur_f, win_f, win_w, door_f, door_b, roof, lantern, chimney, sign_brk, stair))
    doc = {
        "version": 1,
        "controls": {
            "width": control(6.4, 4.8, 9.0),
            "depth": control(4.0, 3.2, 5.2),
            "height": control(5.6, 4.8, 7.2),
        },
        "derived": g.derived,
        "nodes": g.nodes,
        "output": out,
    }
    _verify_doc(doc, "full_pavilion")


def test_individual_variants():
    """Verify standalone connectivity, clear-opening geometry, and variant options of the 5 helpers."""
    # 1. window_surround standalone + clear-opening verification with a snug test block inside opening
    g1 = Graph()
    W, H = C("w"), C("h")
    sur = g1.window_surround("sur", W, H, jamb_courses=5, keystone=True, hood=True, facing="-y", at=[0, 0, 0])
    # Place a glass pane filling 99% of the clear opening [ -W/2 .. +W/2, z = 0.161 .. 0.161 + H ]: must NOT intersect!
    z_op0 = 0.070 + _BUTT + 0.09 + _BUTT
    pane = g1.mat("pane", g1.box("pane-b", [W - 0.004, 0.02, H - 0.004], [0, -0.03, z_op0 + H * 0.5]), "glass")
    out1 = g1.bake("sur.baked", g1.join("u1", sur, pane))
    _verify_doc({
        "version": 1,
        "controls": {"w": control(1.4, 0.7, 2.4), "h": control(1.6, 0.9, 2.6)},
        "derived": g1.derived, "nodes": g1.nodes, "output": out1,
    }, "surround_clear")

    # 2. Gable louvred_lantern + 3-pot Y-oriented corbelled_chimney + hoodless surround + frameless door
    g2 = Graph()
    L = C("len")
    base = g2.mat("base", g2.box("base-b", [5.4, 2.6, 0.20], [0, 0, 0.10]), "stone")
    lan = g2.louvred_lantern("lan", L, 0.62, C("lh"), z_base=0.20, louvres=3, roof="gable", finials=False,
                             at=[-1.5, 0, 0])
    chm = g2.corbelled_chimney("chm", 0.50, C("cw"), z_base=0.20, height=C("ch"), pots=3, corbel_steps=2,
                               at=[0.0, 0, 0])
    sur2 = g2.window_surround("sur2", 0.85, 1.10, jamb_courses=3, keystone=True, hood=False,
                              facing="+y", at=[1.15, 0, 0.20 + _BUTT])
    dr = g2.panelled_door("dr", 0.90, C("dh"), 0.052, at=[2.15, 0, 0.20 + _BUTT],
                          glazed_upper=False, panels_x=1, outer_frame=False, threshold=False, facing="-y")
    out2 = g2.bake("variants.baked", g2.join("u2", base, lan, chm, sur2, dr))
    _verify_doc({
        "version": 1,
        "controls": {
            "len": control(1.3, 0.8, 1.9),
            "lh": control(0.50, 0.34, 0.72),
            "cw": control(0.84, 0.76, 1.05),
            "ch": control(1.7, 1.1, 2.6),
            "dh": control(2.0, 1.6, 2.4),
        },
        "derived": g2.derived, "nodes": g2.nodes, "output": out2,
    }, "helper_variants")

    # 3. Verify no Graph method collision with kitlib_glazing.ridge_lantern
    assert Graph.ridge_lantern.__module__ == "kitlib_glazing", Graph.ridge_lantern.__module__
    assert Graph.louvred_lantern.__module__ == "kitlib_arch", Graph.louvred_lantern.__module__


def main():
    test_full_parametric_pavilion()
    test_individual_variants()
    assert abs(float(clamp_to_wall(5.0, 3.0, 0.2, 0.1).v) - 2.7) < 1e-9
    print("ok   test_kitlib_arch (all 9 helpers: check --intersections + sweep --intersections + audit --sweep --dangling)")


if __name__ == "__main__":
    main()
