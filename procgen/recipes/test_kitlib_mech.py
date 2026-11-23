#!/usr/bin/env python3
"""Tests for kitlib_mech.py: plummer_block, bushed_sheave, clevis_fork, stuffing_box,
arched_yoke, pierced_cheek, and mech_bore."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

RECIPES = Path(__file__).resolve().parent
PGM = RECIPES.parent / "build-release" / "pgm"
sys.path.insert(0, str(RECIPES))

from kitlib import Graph, C, PI, control, _dump, mech_bore  # noqa: E402
from kitparts import drafted_plinth, filleted_box  # noqa: E402

_BUTT = 0.0005


def _verify_doc(doc, label):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / f"{label}.json"
        p.write_text(_dump(doc, indent=1))
        chk = json.loads(subprocess.check_output([str(PGM), "check", str(p), "--intersections"], text=True))
        assert chk["passed"] and chk["clean"] and chk["openPrototypes"] == 0, (label, chk)
        assert chk.get("selfIntersectingPrototypes", 0) == 0, (label, chk)
        sds = chk.get("sds") or {}
        assert (sds.get("pinchedPoles") or 0) == 0, (label, sds)
        assert (sds.get("maxEvCurvature") or 0.0) <= 1.5, (label, sds)
        sw = json.loads(subprocess.check_output([str(PGM), "sweep", str(p), "--intersections"], text=True))
        assert sw["passed"] and sw["failed"] == 0, (label, sw)
        au = json.loads(subprocess.check_output([str(PGM), "audit", str(p), "--sweep", "--dangling"], text=True))
        assert au["passed"] and au["failed"] == 0, (label, au)


def _baked_doc(g, out_id, controls):
    baked = g.bake(out_id + ".baked", out_id)
    doc = {"version": 1, "controls": controls, "nodes": g.nodes, "output": baked}
    if g.derived:
        doc["derived"] = g.derived
    return doc


def test_individual_helpers():
    cases = [
        ("plummer", {"r": control(0.036, 0.028, 0.048)},
         lambda g: g.plummer_block("pb", mech_bore(C("r")), center_h=0.22, at=[0, 0, 0.22], axis="x")),
        ("sheave", {"r": control(0.32, 0.24, 0.42)},
         lambda g: g.bushed_sheave("sh", C("r"), 0.11, mech_bore(0.036), spokes=6, at=[0, 0, 0.35], axis="x")),
        ("clevis", {"g": control(0.044, 0.032, 0.064)},
         lambda g: g.clevis_fork("cf", C("g"), 0.018, 0.016, 0.18, at=[0, 0, 0.20], axis="z", with_pin=True)),
        ("stuffing", {"h": control(0.18, 0.14, 0.24)},
         lambda g: g.stuffing_box("sb", 0.028, height=C("h"), at=[0, 0, 0], axis="z")),
        ("yoke", {"sp": control(0.52, 0.42, 0.66)},
         lambda g: g.arched_yoke("yk", C("sp"), 0.62, mech_bore(0.028), at=[0, 0, 0], facing="x")),
        ("cheek", {"hz": control(0.84, 0.72, 0.98)},
         lambda g: g.pierced_cheek("ck", 0.56, C("hz"), 0.048, 0.42, mech_bore(0.045), at=[0.075, 0, 0], side=1)),
    ]
    for name, ctrls, fn in cases:
        g = Graph()
        out = fn(g)
        _verify_doc(_baked_doc(g, out, ctrls), name)


def test_full_parametric_mechanism():
    """All 6 Graph helpers composed on one parametric Victorian mechanism with rotating wheel & spindle."""
    g = Graph()
    ang, lift = C("angle"), C("lift")
    st = g.mat("m-st", drafted_plinth(g, "plinth", [1.72, 0.98, 0.22], at=[0, 0, 0.11], courses=2), "limestone")
    z_bed0 = 0.22 + _BUTT
    bed = g.mat("m-bed", filleted_box(g, "bed", [1.56, 0.82, 0.05], at=[0, 0, z_bed0 + 0.025], width=0.008, mode="fwn"), "iron")
    z_bed = z_bed0 + 0.05
    z_ax = 0.56
    r_sh = 0.034
    br = mech_bore(r_sh)

    # West pierced cheek + east plummer block supporting main shaft & bushed sheave
    ck_w = g.pierced_cheek("cheek-w", 0.48, 0.52, 0.042, z_ax - z_bed - _BUTT, br,
                           at=[-0.52, 0, z_bed + _BUTT], side=-1)
    pb_e = g.plummer_block("pb-e", br, center_h=z_ax - z_bed - _BUTT, at=[0.02, 0, z_ax], axis="x")
    g.n("shaft-raw", "cylinder", radius=r_sh, depth=0.76, sides=24, cap="quad")
    g.smooth("shaft-sm", "shaft-raw", angle=46, weighting="areaAngle")
    sh = g.mat("m-sh", g.xf("shaft", "shaft-sm", t=[-0.25, 0, z_ax], r=[0, PI * 0.5, 0]), "steel")
    sv0 = g.bushed_sheave("pul", 0.23, 0.095, br, groove_r=0.018, spokes=6, axis="x")
    sv = g.xf("sheave", sv0, t=[-0.25, 0, z_ax], r=[ang, 0, 0])

    # East spindle mechanism: stuffing box + arched yoke + spindle + clevis fork
    sb = g.stuffing_box("sbox", 0.022, height=0.16, at=[0.48, 0, z_bed + _BUTT], axis="z")
    yk = g.arched_yoke("yoke", 0.52, 0.52, mech_bore(0.022), foot_w=0.085, thickness=0.065,
                       at=[0.48, 0, z_bed + _BUTT], facing="y")
    sp_len = 0.64
    z_sp_c = z_bed + 0.34 + lift * 0.08
    g.n("spindle-raw", "cylinder", radius=0.022, depth=sp_len, sides=20, cap="quad")
    g.smooth("spindle-sm", "spindle-raw", angle=46, weighting="areaAngle")
    sp = g.mat("m-sp", g.xf("spindle", "spindle-sm", t=[0.48, 0, z_sp_c]), "steel")
    cf = g.clevis_fork("clevis", 0.036, 0.015, 0.012, 0.13,
                       at=[0.48, 0, z_sp_c + sp_len * 0.5 + 0.13 + _BUTT], axis="z")

    all_id = g.join("all", st, bed, ck_w, pb_e, sh, sv, sb, yk, sp, cf)
    _verify_doc(_baked_doc(g, all_id, {"angle": control(0.4, 0.0, 6.28), "lift": control(0.5, 0.0, 1.0)}), "full-mech")


if __name__ == "__main__":
    test_individual_helpers()
    test_full_parametric_mechanism()
    print("test_kitlib_mech: all 7 SDS/FWN open-bore mechanical suites passed")
