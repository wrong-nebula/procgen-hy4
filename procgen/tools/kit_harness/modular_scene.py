#!/usr/bin/env python3
"""Fix #1: Modular Per-Round Plugin Runner (keeps subagent context <35K even at Round 10!).

Instead of copying & growing a single 147 KB monolithic script every round, a scene stores:
  - `<scene_dir>/seed.py`       -> defines `build_seed(g)` returning `(controls, ctx, parts, ports)`
  - `<scene_dir>/r01_*.py`      -> small ~10 KB plugin defining `apply(g, ctx, parts)`
  - `<scene_dir>/r02_*.py`      -> small ~10 KB plugin defining `apply(g, ctx, parts)`
  ...

Each round agent ONLY writes a single small plugin file `rKK_<slug>.py` without ever reading
or copying earlier rounds!

Usage:
  python3 coevolution/modular_scene.py --scene-dir coevolution/scenes/my_scene --module my-scene --out-root coevolution/workspace_my_scene --upto 3
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

PROCGEN_RECIPES = Path(__file__).resolve().parents[2] / "recipes"
if str(PROCGEN_RECIPES) not in sys.path:
    sys.path.insert(0, str(PROCGEN_RECIPES))

from kitlib import Kit, Graph


def load_module(py_path: Path):
    spec = importlib.util.spec_from_file_location(py_path.stem, str(py_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {py_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-dir", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--upto", type=int, default=99)
    args = ap.parse_args()

    sdir = Path(args.scene_dir).resolve()
    seed_mod = load_module(sdir / "seed.py")
    g = Graph()
    controls, ctx, parts, ports = seed_mod.build_seed(g)

    plugins = sorted(sdir.glob("r[0-9][0-9]_*.py"))
    applied = []
    for p in plugins:
        rnum = int(p.name[1:3])
        if rnum > args.upto:
            continue
        mod = load_module(p)
        mod.apply(g, ctx, parts)
        applied.append(p.name)

    g.join("root_assembly", *parts)
    kit = Kit(Path(args.out_root).resolve(), "recipes/site/coevolution", "site-coevolution")
    kit.module(args.module, controls, g, "root_assembly", ports)
    kit.close()
    print(f"Built modular {args.module} (seed + {len(applied)} plugins: {applied})")


if __name__ == "__main__":
    main()
