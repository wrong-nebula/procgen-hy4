#!/usr/bin/env python3
"""Starter kit: a cast-iron bollard on a stone plinth.

The smallest builder that passes every gate of `procgen/tools/kit_harness/verify_round.py`.
Copy it to start a new kit: rename the module, change the controls, replace the parts.

    python3 examples/starter/bollard.py          # writes examples/starter/out/bollard/bollard.json

Frame: Z-up, metres, ground at z = 0, the front faces -y.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "procgen" / "recipes"))

from kitlib import C, Graph, Kit, control, port, ref, review  # noqa: E402

ROOT = Path(__file__).resolve().parent

# One datum table up front: every mating height is computed here, never guessed later.
PLINTH = [0.50, 0.50, 0.12]   # stone plinth, x y z
SHAFT_R = 0.11                # iron shaft radius
CAP = [0.30, 0.30, 0.06]      # iron cap plate


def build():
    kit = Kit(ROOT, "out/bollard", "bollard", references={
        "bollard": ref(notes="Starter example: plinth, shaft, cap.",
                       review=review("Plain boxes and a rod; no casting detail.", by="example")),
    })
    controls = {"height": control(0.9, 0.6, 1.4)}   # shaft height in metres: default, min, max
    g = Graph()
    g.let("top", PLINTH[2] + C("height"))           # z of the shaft top, follows the control

    # Parts sit exactly on each other (shared faces): touching within 3 mm, no overlap.
    g.box("plinth_raw", PLINTH, at=[0, 0, PLINTH[2] / 2])
    g.mat("plinth", "plinth_raw", "chipped-limestone")

    g.rod("shaft_raw", [0, 0, PLINTH[2]], [0, 0, C("top")], SHAFT_R, sides=24, cap="quad")
    g.mat("shaft", "shaft_raw", "iron")

    g.box("cap_raw", CAP, at=[0, 0, C("top") + CAP[2] / 2])
    g.mat("cap", "cap_raw", "weathered-iron")

    g.join("root", "plinth", "shaft", "cap")
    ports = {"foot": port([0, 0, 0], [0, 0, -1], "ground")}
    kit.module("bollard", controls, g, "root", ports)
    kit.close()


if __name__ == "__main__":
    build()
