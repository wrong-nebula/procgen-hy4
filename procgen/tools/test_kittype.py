#!/usr/bin/env python3
"""Verify `recipes/kittype.py` and `site/signage`: build a type specimen wall testing all three
faces (`plain`, `roman`, `nouveau`), French accents, multi-line plaques, oval/rect house numbers,
`G.text(..., valign=...)`, rear-face non-mirroring, and curved `arch_sign_board`, then run
`pgm check`, `pgm sweep`, `pgm audit --sweep` and render specimen views.

Usage: python3 tools/test_kittype.py [out_dir]
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "recipes"))
from kitlib import C, Graph, Kit, control, port  # noqa: E402
from kittype import arch_sign_board, inscribe, number_plaque, sign_board  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/private/var/folders/z5/6rm_mqdx6nz17tgp5kwpksb80000gn/T/opencode/kittype-test")
OUT.mkdir(parents=True, exist_ok=True)
PGM = str(HERE / "build-release" / "pgm")


def pgm(*args):
    lines = subprocess.run([PGM, *args], capture_output=True, text=True, check=True).stdout.strip().splitlines()
    return json.loads(lines[-1])


def build_specimen():
    kit = Kit(OUT, "specimen", "kittype-specimen")
    G = Graph()
    W = C("width")
    N = C("name")

    # Stone backing wall so everything forms one connected architectural specimen.
    wall_raw = G.box("wall-raw", [W, 0.28, 3.3], [0.0, 0.14, 1.65])
    # Top frieze: incised classical Roman capitals + accents directly on limestone.
    wall_frieze = inscribe(
        G, "roman-frieze", wall_raw,
        ("AUX GRANDS HOMMES", "ÉCOLE POLYTECHNIQUE"),
        centre=[0.0, 0.0, 2.95], panel_size=[W - 0.3, 0.44],
        face="-y", font="roman", variant=N, profile="groove", slot="black",
    )
    # Rear wall carries a rear-facing Roman inscription (verifies +y is never mirrored).
    wall_both = inscribe(
        G, "roman-rear", wall_frieze, "SORTIE NORD 1898",
        centre=[0.0, 0.28, 2.95], panel_size=[W - 0.3, 0.40],
        face="+y", font="roman", profile="groove", slot="black",
    )
    # Alphabet specimen band (A-Z, 0-9) in Roman and Nouveau on lower wall.
    wall_alpha_r = inscribe(
        G, "alpha-roman", wall_both, "ABCDEFGHIJKLM NOPQRSTUVWXYZ 0123456789",
        centre=[0.0, 0.0, 0.55], panel_size=[W - 0.25, 0.26],
        face="-y", font="roman", profile="mark", slot="black",
    )
    wall_alpha_n = inscribe(
        G, "alpha-nouveau", wall_alpha_r, "ABCDEFGHIJKLM NOPQRSTUVWXYZ 0123456789",
        centre=[0.0, 0.0, 0.24], panel_size=[W - 0.25, 0.26],
        face="-y", font="nouveau", profile="mark", slot="iron",
    )
    wall = G.mat("wall", wall_alpha_n, "chipped-limestone")

    # Arched station sign mounted flush on upper-middle wall (y_back = 0).
    arch = arch_sign_board(
        G, "metro-arch", ("MÉTROPOLITAIN", "GALERIE DU NORD"),
        span=W * 0.78, band_height=0.38, z_ends=2.12, rise=0.26, y=-0.03, thickness=0.06,
        face="-y", font="nouveau", variant=N, spread=0.32,
    )

    # Shopfront fascia board flush on middle wall.
    fascia = sign_board(
        G, "shop-fascia", ("MAISON IRIS & CIE", "CAFÉ DE L'OPÉRA"),
        at=[0.0, -0.045, 1.52], size=[W * 0.86, 0.09, 0.38],
        face="-y", font="nouveau", variant=N,
    )

    # Three plaques flush on lower-middle wall: rectangular number, oval number, multi-line plain notice.
    p_rect = number_plaque(
        G, "num-rect", "25", at=[W * -0.28, -0.012, 1.0], size=[0.18, 0.024, 0.20],
        face="-y", shape="rect", font="nouveau",
    )
    p_oval = number_plaque(
        G, "num-oval", "N° 104", at=[0.0, -0.012, 1.0], size=[0.28, 0.024, 0.19],
        face="-y", shape="oval", font="roman", profile="bead", text_slot="bronze",
    )
    p_multi = number_plaque(
        G, "notice", "OUVERT\n8:00 - 19:30", at=[W * 0.28, -0.012, 1.0], size=[0.38, 0.024, 0.20],
        face="-y", shape="rect", font="plain",
    )

    G.join("all", wall, arch, fascia, p_rect, p_oval, p_multi)
    kit.module(
        "type-specimen",
        {"width": control(4.2, 2.4, 6.4), "name": control(0, 0, 1, True)},
        G, "all",
        {"base": port([0, 0.14, 0], [0, 0, -1], "footing", {"width": "$width"}, up=(0, -1, 0))},
    )
    kit.close()
    return OUT / "specimen" / "type-specimen.json"


if __name__ == "__main__":
    doc = build_specimen()
    chk = pgm("check", str(doc))
    swp = pgm("sweep", str(doc))
    aud = pgm("audit", str(doc), "--sweep")
    pgm("render", str(doc), "--out", str(OUT / "specimen-front.png"),
        "--view", "front", "--projection", "ortho", "--pass", "beauty", "--size", "1400x1050")
    pgm("render", str(doc), "--out", str(OUT / "specimen-iso.png"),
        "--view", "iso", "--pass", "beauty", "--size", "1400x1050")
    pgm("render", str(doc), "--out", str(OUT / "specimen-opposite.png"),
        "--view", "opposite", "--pass", "beauty", "--size", "1400x1050")
    pgm("render", str(doc), "--out", str(OUT / "specimen-narrow.png"),
        "--set", "width=2.4", "--set", "name=1",
        "--view", "front", "--projection", "ortho", "--pass", "beauty", "--size", "1200x950")
    print(f"specimen: check={chk['passed']} clean={chk['clean']} sweep={swp['passed']} ({swp['cases']} cases) audit={aud['passed']}")
    sys.exit(0 if (chk["passed"] and chk["clean"] and swp["passed"] and aud["passed"]) else 1)
