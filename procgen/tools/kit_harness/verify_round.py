#!/usr/bin/env python3
"""Fix #2 + #6: Single Fail-Fast Compact 5-Gate Verifier + Anti-Hallucination Stamp.

Replaces 5-6 verbose shell commands (which bloat KV cache across multi-turn Hy4 loops)
with ONE compact, fail-fast CLI that prints <= 8 lines of actionable diagnostics and
writes a verified JSON stamp only when EVERY gate passes.

Usage:
  python3 tools/kit_harness/verify_round.py \
    --script ../examples/hy4/turntable.py \
    --doc ../examples/hy4/out/turntable/turntable.json \
    --render /tmp/turntable.png \
    --stamp /tmp/turntable.ok
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
PROCGEN_DIR = HARNESS_DIR.parent.parent
ROOT = PROCGEN_DIR
PGM = PROCGEN_DIR / "build-release/pgm"
MJ_AUDIT = HARNESS_DIR / "mujoco_audit.py"
BELL_TEST = HARNESS_DIR / "mujoco_bell_test.py"


def run_cmd(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def fail(gate: str, lines: list[str]) -> None:
    print(f"FAIL [{gate}]")
    for ln in lines[:10]:
        print(f"  {ln}")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="", help="Monolithic round build script to execute")
    ap.add_argument("--modular", default="", help="Optional modular scene dir (runs modular_scene.py instead of --script)")
    ap.add_argument("--module", default="", help="Module name when using --modular")
    ap.add_argument("--out-root", default="", help="Workspace out-root when using --modular")
    ap.add_argument("--upto", type=int, default=99, help="Max round plugin number when using --modular")
    ap.add_argument("--doc", required=True, help="Compiled recipe JSON path")
    ap.add_argument("--render", default="", help="Optional 768x768 iso beauty preview output path")
    ap.add_argument("--backend", choices=("software", "engine"), default="software", help="Preview render backend for --render (software or native headless Dawn engine)")
    ap.add_argument("--stamp", default="", help="Optional verification stamp JSON path (written ONLY on full pass)")
    ap.add_argument("--check-spin", action="store_true", help="Also require MuJoCo CoACD free-spin pass")
    ap.add_argument("--support-shell", default="", help="Optional exterior shell doc -> runs MuJoCo exact support-graph + gravity-drop audit!")
    ap.add_argument("--min-spin-deg", type=float, default=120.0)
    args = ap.parse_args()

    doc_p = (ROOT / args.doc) if not Path(args.doc).is_absolute() else Path(args.doc)
    stamp_p = Path(args.stamp) if args.stamp else None
    if stamp_p and stamp_p.exists():
        stamp_p.unlink()

    if args.modular:
        script_p = (ROOT / args.modular) if not Path(args.modular).is_absolute() else Path(args.modular)
        out_root_p = (ROOT / args.out_root) if not Path(args.out_root).is_absolute() else Path(args.out_root)
        build_cmd = [
            "python3", str(HARNESS_DIR / "modular_scene.py"),
            "--scene-dir", str(script_p), "--module", args.module,
            "--out-root", str(out_root_p), "--upto", str(args.upto),
        ]
    else:
        script_p = (ROOT / args.script) if not Path(args.script).is_absolute() else Path(args.script)
        if not script_p.exists():
            fail("SCRIPT_MISSING", [f"File does not exist on disk: {script_p}"])
        build_cmd = ["python3", str(script_p)]

    # 1. Build
    rc, out, err = run_cmd(build_cmd)
    if rc != 0:
        tail = (err or out).splitlines()[-8:]
        fail("BUILD", tail)

    # 2. pgm check --intersections
    rc, out, err = run_cmd([str(PGM), "check", str(doc_p), "--intersections"])
    try:
        chk = json.loads(out)
    except Exception:
        fail("PGM_CHECK", (err or out).splitlines()[-6:])
    if not chk.get("passed"):
        lines = []
        for iss in (chk.get("issues") or [])[:6]:
            lines.append(
                f"mat={iss.get('material')} selfX={iss.get('selfIntersections')} "
                f"nonMan={iss.get('nonManifoldEdges')} open={iss.get('boundaryEdges')} src={iss.get('source')}"
            )
        fail("PGM_CHECK", lines or [out[:300]])

    # 3. pgm audit --dangling (skip single-component requirement for multi-furniture interiors when --support-shell is active)
    rc, out, err = run_cmd([str(PGM), "audit", str(doc_p), "--dangling"])
    try:
        aud = json.loads(out)
    except Exception:
        fail("PGM_AUDIT", (err or out).splitlines()[-6:])
    if not aud.get("passed") and not args.support_shell:
        lines = [f"components={aud.get('components')} dangling={aud.get('danglingCount')}"]
        for d in (aud.get("detached") or [])[:5]:
            gap_mm = round(float(d.get("gap", 0)) * 1000, 2)
            srcs = list((d.get("sources") or {}).keys())[:3]
            lines.append(
                f"DETACHED {srcs} gap={gap_mm}mm nearest={d.get('nearestMainSource')} "
                f"delta={[round(x*1000, 1) for x in d.get('deltaToMain', [])]}mm"
            )
        for d in (aud.get("dangling") or [])[:4]:
            lines.append(f"DANGLING {d}")
        fail("PGM_AUDIT", lines)

    # 4. pgm sweep
    rc, out, err = run_cmd([str(PGM), "sweep", str(doc_p)])
    try:
        swp = json.loads(out)
    except Exception:
        fail("PGM_SWEEP", (err or out).splitlines()[-6:])
    if not swp.get("passed"):
        fail("PGM_SWEEP", [str(f) for f in (swp.get("failures") or [])[:6]])

    # 5. MuJoCo 4.0 + CoACD static physics (--fast)
    rc, out, err = run_cmd([
        "uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
        "python3", str(MJ_AUDIT), str(doc_p), "--fast",
    ])
    try:
        mj = json.loads(out)
    except Exception:
        fail("MUJOCO_AUDIT", (err or out).splitlines()[-6:])
    clips = int(mj.get("crossMaterialClips", 0)) + int(mj.get("sameMaterialClips", 0))
    buried = int(mj.get("buriedOrDuplicate", 0))
    if not mj.get("passed") or float(mj.get("physicsScore", 0)) < 100.0 or clips > 0 or buried > 0:
        lines = [
            f"score={mj.get('physicsScore')} crossClips={mj.get('crossMaterialClips')} "
            f"sameClips={mj.get('sameMaterialClips')} buried={buried} SF={mj.get('overturningSafetyFactor')}"
        ]
        for ix in (mj.get("topIntersections") or mj.get("intersections") or [])[:6]:
            lines.append(
                f"CLIP [{ix.get('severity')}] {ix.get('partA')}({ix.get('matA')}) <-> "
                f"{ix.get('partB')}({ix.get('matB')}) depth={ix.get('exactDepthMM')}mm overlap={ix.get('overlapPctSmaller')}%"
            )
        fail("MUJOCO_AUDIT", lines)

    # 6. Optional Articulated Free-Spin Test
    spin = None
    if args.check_spin:
        rc, out, err = run_cmd([
            "uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
            "python3", str(BELL_TEST), str(doc_p),
        ])
        try:
            spin = json.loads(out.splitlines()[-1])
        except Exception:
            fail("MUJOCO_SPIN", (err or out).splitlines()[-6:])
        if not spin.get("freeSpin") or int(spin.get("maxContacts", 1)) > 0 or float(spin.get("rotationDeg", 0)) < args.min_spin_deg:
            fail("MUJOCO_SPIN", [json.dumps(spin)])

    # 6B. Optional MuJoCo Exact Support-Graph + Multi-Body Gravity Drop Audit (0 floating beams/props!)
    if args.support_shell:
        sh_p = (ROOT / args.support_shell) if not Path(args.support_shell).is_absolute() else Path(args.support_shell)
        rc, out, err = run_cmd([
            "uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
            "python3", str(HARNESS_DIR / "mujoco_support_audit.py"),
            "--shell", str(sh_p), "--interior", str(doc_p),
        ])
        try:
            sup = json.loads(out)
        except Exception:
            fail("MUJOCO_SUPPORT", (err or out).splitlines()[-6:])
        if not sup.get("passed"):
            lines = [
                f"floating={sup.get('floating_count')} drop_failures={sup.get('drop_failure_count')} "
                f"ring_fit_issues={sup.get('ring_fit_issue_count', 0)}"
            ]
            for fp in (sup.get("floating_parts") or [])[:6]:
                lines.append(f"FLOAT {fp['part']} gap={fp['gap_mm']}mm nearest={fp['nearest']}")
            for df in (sup.get("drop_failures") or [])[:4]:
                lines.append(f"DROP {df.get('cluster')} drop={df.get('drop_mm')}mm tilt={df.get('tilt_deg')}deg")
            for rf in (sup.get("ring_fit_issues") or [])[:6]:
                lines.append(f"RING_FIT {rf['ring']} on {rf['host']}: {rf['reason']}")
            fail("MUJOCO_SUPPORT", lines)

    # 7. Render preview if requested
    tris = None
    render_backend = None
    if args.render:
        rc, out, err = run_cmd([
            str(PGM), "render", str(doc_p), "--out", args.render,
            "--view", "iso", "--pass", "beauty", "--size", "768x768",
            "--backend", args.backend,
        ])
        if rc != 0:
            fail("PGM_RENDER", (err or out).splitlines()[-4:])
        try:
            rj = json.loads(out)
            tris = rj.get("triangles")
            render_backend = rj.get("backend")
        except Exception:
            pass

    sha = (
        hashlib.sha256(script_p.read_bytes()).hexdigest()[:16]
        if script_p.is_file()
        else hashlib.sha256(b"".join(p.read_bytes() for p in sorted(script_p.glob("*.py")))).hexdigest()[:16]
    )
    summary = {
        "passed": True,
        "script": str(script_p),
        "sha256": sha,
        "bytes": script_p.stat().st_size if script_p.is_file() else sum(p.stat().st_size for p in script_p.glob("*.py")),
        "triangles": tris,
        "prototypes": chk.get("prototypes"),
        "sdsCages": (chk.get("sds") or {}).get("sdsCages", 0),
        "quadRatio": round(float((chk.get("sds") or {}).get("quadRatio", 0)), 3),
        "islands": aud.get("islands"),
        "sweepCases": swp.get("cases"),
        "physicsScore": mj.get("physicsScore"),
        "solidParts": mj.get("solidParts"),
        "safetyFactor": mj.get("overturningSafetyFactor"),
        "spin": (
            {"part": spin.get("part"), "rotationDeg": spin.get("rotationDeg"), "maxContacts": spin.get("maxContacts")}
            if spin else None
        ),
        "render": args.render or None,
        "renderBackend": render_backend,
    }
    if stamp_p:
        stamp_p.parent.mkdir(parents=True, exist_ok=True)
        stamp_p.write_text(json.dumps(summary, indent=2))

    spin_str = f" | SPIN={spin['part']} {spin['rotationDeg']}° (0 contacts)" if spin else ""
    print(
        f"ALL GATES PASS | tris={tris} | proto={summary['prototypes']} | sds={summary['sdsCages']} "
        f"| quad={summary['quadRatio']} | MuJoCo=100/100 (0 clips, SF={summary['safetyFactor']}x){spin_str}"
    )


if __name__ == "__main__":
    main()
