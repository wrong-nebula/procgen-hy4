#!/usr/bin/env python3
"""ByteAsk: procgen's implementation and verification gate (stdlib only).

  byteask.py verify [--quick | --auto] [--no-gcc] [--no-kits]
      Debug, release and ASan+UBSan builds with the unit tests; GCC 12 in the
      Thermite harness image; both judge-kit self-tests; gallery render with
      per-scene quality. Gentle on a shared machine (nice, -j2). Exit 0 only
      when every stage is green. Report: procgen/.byteask/verify-<ts>.json
      --quick: release build, one judge kit, every recipe stage.
      --auto: the full gate, unless everything changed since this checkout's
      last green full verify (commits, edits, untracked files) is recipe, doc
      or script work; then the C++-only stages (debug, ASan, GCC 12, judge
      kits, engine packs) are skipped and listed in the report. Use it after
      each step; run the plain full gate before a commit or merge.
  byteask.py integrate <patch-file | thermite-run>
      Apply a Thermite champion on a scratch branch, run verify, commit on
      green (message cites the run and the judge's numbers), else discard.
  byteask.py bench [filter]
      Micro-benchmarks with quality verdicts (release build).
  byteask.py status
      Branch, last verify report, Thermite run states for procgen campaigns.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # procgen/
REPO = ROOT.parent                                      # git repository root
REPORTS = ROOT / ".byteask"
KITS = Path.home() / "thermite-kits"
TMCP = KITS / "live" / "tmcp.py"
HARNESS = ("ghcr.io/iammrduncan/thermite-harness@sha256:"
           "8f2fa2ee6ea93e9f9d005c2a708976f8e46c7f1c53e15791de8da3cdae324f5d")
NICE = ["nice", "-n", "10"]


def run(cmd, cwd=ROOT, timeout=1800, env=None):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout, time.time() - t0
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") + f"\n[byteask] timeout after {timeout}s", time.time() - t0


class Gate:
    def __init__(self):
        self.stages = []

    def stage(self, name, ok, detail, seconds):
        self.stages.append({"stage": name, "ok": ok, "detail": detail, "seconds": round(seconds, 1)})
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<26} {seconds:6.1f}s  {detail}", flush=True)
        return ok

    @property
    def ok(self):
        return all(s["ok"] for s in self.stages)


def tests_line(out):
    m = re.findall(r"(\d+) tests, (\d+) failed checks", out)
    return (int(m[-1][0]), int(m[-1][1])) if m else (0, -1)


def build_and_test(g, preset):
    code, out, t1 = run(["cmake", "--preset", preset])
    if code:
        return g.stage(f"{preset}: configure", False, out.strip().splitlines()[-1][:120], t1)
    code, out, t2 = run(NICE + ["cmake", "--build", "--preset", preset, "-j", "2"], timeout=3600)
    warnings = [l for l in out.splitlines() if "warning:" in l or "error:" in l]
    if code or warnings:
        return g.stage(f"{preset}: build", False, (warnings or out.strip().splitlines())[-1][:120], t1 + t2)
    code, out, t3 = run([f"./build-{preset}/pg_tests"], timeout=1800)
    n, failed = tests_line(out)
    return g.stage(f"{preset}: build + tests", code == 0 and failed == 0, f"{n} tests, {failed} failed checks",
                   t1 + t2 + t3)


def gcc_stage(g):
    script = ("cmake -S /src -B /tmp/b -DCMAKE_BUILD_TYPE=Release -G Ninja >/dev/null && "
              "cmake --build /tmp/b -j 2 2>&1 | grep -E 'warning|error' | head -5; /tmp/b/pg_tests | tail -1")
    code, out, t = run(NICE + ["docker", "run", "--rm", "--cpus", "2", "--network", "none", "-v", f"{ROOT}:/src:ro",
                               "--entrypoint", "sh", HARNESS, "-c", script], timeout=3600)
    n, failed = tests_line(out)
    noisy = any(("warning" in l or "error" in l) and "failed checks" not in l for l in out.splitlines())
    return g.stage("gcc12 (harness image)", code == 0 and failed == 0 and n > 0 and not noisy,
                   f"{n} tests, {failed} failed checks" + (", warnings" if noisy else ""), t)


def kit_stage(g, kit):
    code, out, t = run(NICE + [str(KITS / kit / "selftest.sh"), str(ROOT)], timeout=3600)
    rows = [l for l in out.splitlines() if "check=" in l]
    fails = [l.split()[0] for l in rows if '"pass": true' not in l]
    return g.stage(f"kit {kit}", code == 0 and rows and not fails,
                   f"{len(rows) - len(fails)}/{len(rows)} cells pass" + (f"; failing {fails}" if fails else ""), t)


def gallery_stage(g):
    code, out, t = run(["cmake", "--build", "--preset", "release", "--target", "pg_gallery", "-j", "2"])
    if code:
        return g.stage("gallery", False, "build failed", t)
    code, out, t2 = run(NICE + ["./build-release/pg_gallery", "gallery"], timeout=300)
    rows = [l for l in out.splitlines() if "watertight" in l]
    bad = [l.split("faces")[0].strip() for l in rows
           if "watertight 1" not in l or not re.search(r"SI 0\b", l) or re.search(r"volume -", l)]
    return g.stage("gallery (visual QA)", code == 0 and rows and not bad,
                   f"{len(rows) - len(bad)}/{len(rows)} scenes clean" + (f"; bad: {bad}" if bad else ""), t + t2)


MAIN_RECIPES = REPO.parents[2] / "Recipes" / "Geometry" if len(REPO.parents) > 2 else None
ENGINE = REPO.parent / "cpp-fps-engine-scaffold-6653d3" / "engine" / "build" / "native-release"


def pgm_json(args, timeout=600):
    code, out, t = run(["./build-release/pgm"] + args, timeout=timeout)
    try:
        return json.loads(out.strip().splitlines()[-1]), t
    except (json.JSONDecodeError, IndexError):
        return {"passed": False, "error": out[-200:]}, t


def conformance_stage(g):
    """Every reference document must match the reference evaluator's receipt."""
    receipts = ROOT / "tests" / "data" / "reference-receipts.json"
    recipes = Path(os.environ.get("PGM_RECIPES", str(MAIN_RECIPES) if MAIN_RECIPES else ""))
    if not recipes.is_dir():
        print("  SKIP  doc conformance            (recipe directory not found; set PGM_RECIPES)", flush=True)
        return True
    d, t = pgm_json(["conformance", str(recipes), str(receipts)])
    detail = f"{d.get('matched', 0)}/{d.get('documents', 0)} documents match the reference"
    if d.get("issues"):
        detail += f"; first: {d['issues'][0]['document']}: {d['issues'][0]['differences'][0]}"
    return g.stage("doc conformance", bool(d.get("passed")), detail, t)


def island_kit_stage(g):
    code, out, t = run([sys.executable, "recipes/build_island_kit.py"])
    if code:
        return g.stage("island kit", False, out.strip().splitlines()[-1][:120], t)
    bad, total = [], 0
    for doc in sorted((ROOT / "recipes" / "island-kit").glob("*.json")):
        if doc.name == "modules.json":
            continue
        total += 1
        d, dt = pgm_json(["check", str(doc)])
        t += dt
        if not (d.get("passed") and d.get("clean") and d.get("openPrototypes") == 0):
            bad.append(doc.stem)
    return g.stage("island kit", not bad, f"{total - len(bad)}/{total} documents are clean closed solids" + (f"; bad: {bad}" if bad else ""), t)


def sweep_stage(g):
    """Every recipe document evaluates and stays a clean solid at its defaults, at both ends of every control and at every variant."""
    d, t = pgm_json(["sweep", str(ROOT / "recipes")])
    detail = f"{d.get('documents', 0)} documents, {d.get('cases', 0)} settings, {d.get('failed', '?')} failed"
    if d.get("failures"):
        f = d["failures"][0]
        detail += f"; first: {Path(f['document']).name} {f['set']}: {f['error'][-80:]}"
    return g.stage("control sweep", bool(d.get("passed")), detail, t)


def audit_stage(g):
    """Contact audit over every recipe document and its control sweep: parts hold together within 3 mm, apart from
    the known findings in tests/data/audit-baseline.json (which must be tightened when a module improves)."""
    d, t = pgm_json(["audit", str(ROOT / "recipes"), "--sweep", "--baseline", str(ROOT / "tests" / "data" / "audit-baseline.json")])
    detail = f"{d.get('documents', 0)} documents, {d.get('cases', 0)} settings, {d.get('failed', '?')} detached"
    if d.get("failures"):
        f = d["failures"][0]
        detail += f"; first: {f['document']} {f['set']}: {f.get('components')} pieces > {f.get('allowed')}"
    if d.get("baselineToTighten"):
        detail += f"; tighten the baseline: {[x['document'] for x in d['baselineToTighten']]}"
    return g.stage("contact audit", bool(d.get("passed")), detail, t)


def kit_family_stage(g, label, builder, family, min_kits):
    """A kit family is a set of kits that import each other; every module and example must be a clean closed solid."""
    code, out, t = run([sys.executable, builder])
    if code:
        return g.stage(label, False, out.strip().splitlines()[-1][:120], t)
    bad, total, kits = [], 0, 0
    for manifest in sorted((ROOT / "recipes" / family).glob("*/modules.json")):
        kits += 1
        for doc in sorted(manifest.parent.glob("*.json")):
            if doc.name == "modules.json":
                continue
            total += 1
            d, dt = pgm_json(["check", str(doc)])
            t += dt
            if not (d.get("passed") and d.get("clean") and d.get("openPrototypes") == 0):
                bad.append(doc.stem)
    detail = f"{kits} kits, {total - len(bad)}/{total} modules are clean closed solids" + (f"; bad: {bad}" if bad else "")
    return g.stage(label, not bad and kits >= min_kits, detail, t)


def references_stage(g):
    """Every kit module names the atlas recipes, atlas sources and plate images it is derived from, and the ids resolve."""
    start = time.time()
    main = REPO.parents[2] if len(REPO.parents) > 2 else None
    atlas = main / "References" / "data.json" if main else None
    library = main.parent / "references" / "plates" if main else None
    details = sources = images = None
    if atlas and atlas.exists():
        data = json.loads(atlas.read_text())
        details, sources = {d["id"] for d in data["details"]}, {d["id"] for d in data["sources"]}
    if library and library.is_dir():
        images = {f.name.split("_")[0] for f in library.rglob("s[0-9]*_*.jpg")}
    modules, ids, bad = 0, 0, []
    for family in ("greenhouse", "site", "isle"):
        for doc in sorted((ROOT / "recipes" / family).glob("*/*.json")):
            if doc.name == "modules.json":
                continue
            modules += 1
            r = json.loads(doc.read_text()).get("references") or {}
            if not r.get("notes") or not (r.get("atlas") or r.get("sources") or r.get("plates") or r.get("other")):
                bad.append(f"{doc.stem}: no references")
                continue
            for key, known in (("atlas", details), ("sources", sources), ("plates", images)):
                for ref_id in r.get(key, []):
                    ids += 1
                    if known is not None and ref_id not in known:
                        bad.append(f"{doc.stem}: unknown {key} id {ref_id}")
    code, out, _ = run([sys.executable, "recipes/list_references.py"])
    if code:
        bad.append("list_references.py failed")
    checked = "ids resolve in the atlas and the plate library" if details is not None and images is not None else "atlas or library not reachable, ids unchecked"
    detail = f"{modules} modules cite {ids} reference ids; {checked}" + (f"; {bad[:3]}" if bad else "")
    return g.stage("kit references", not bad, detail, time.time() - start)


def mcp_stage(g):
    code, out, t = run([sys.executable, "tools/test_pgm_mcp.py"], timeout=300)
    last = out.strip().splitlines()[-1] if out.strip() else "no output"
    return g.stage("mcp protocol", code == 0, last, t)


def engine_pack_stage(g):
    """The engine's own tools must accept the modeler's packs."""
    meshc, sandbox = ENGINE / "tools" / "meshbuild" / "meshc", ENGINE / "sandbox" / "sandbox"
    if not (meshc.exists() and sandbox.exists()):
        print("  SKIP  engine pack check          (engine binaries not built)", flush=True)
        return True
    REPORTS.mkdir(exist_ok=True)
    ok, parts, total = True, [], 0.0
    # The flat material pack binds every modeler slot name; the engine's matc must read it.
    emat = REPORTS / "engine-check-modeler.emat"
    d, t = pgm_json(["materials", "--emat", str(emat)])
    matc = ENGINE / "tools" / "matbuild" / "matc"
    material_args = []
    if d.get("passed") and matc.exists():
        code, out, t1 = run([str(matc), "--info", str(emat)], timeout=120)
        good = code == 0 and "EMAT v1" in out
        ok = ok and good
        total += t + t1
        parts.append(f"material pack: {'OK' if good else 'FAILED'} ({len(d.get('materials', []))} materials, {len(d.get('slots', []))} slot names)")
        material_args = ["--material-pack", str(emat)] if good else []
    for name, document, detailed in (("island", "recipes/island-kit/lock-island-assembly.json", False),
                                     ("winter garden", "recipes/greenhouse/assemblies/gh-winter-garden.json", False),
                                     ("locomotive", "recipes/site/rolling-stock/rail-locomotive.json", True)):
        pack = REPORTS / f"engine-check-{name.replace(' ', '-')}.emesh"
        d, t = pgm_json(["export", document, "--emesh", str(pack)])
        if not d.get("passed"):
            return g.stage("engine pack check", False, d.get("error", "export failed")[:120], total + t)
        code1, out1, t1 = run([str(meshc), "--info", str(pack)], timeout=120)
        code2, out2, t2 = run([str(sandbox), "--mesh-pack", str(pack)] + material_args + ["--verify", "--frames", "10"], cwd=REPORTS, timeout=300)
        details = d["emesh"].get("details", 0)
        good = code1 == 0 and "EMSH v1" in out1 and code2 == 0 and "verify: OK" in out2 and (details > 0 or not detailed)
        ok = ok and good
        total += t + t1 + t2
        parts.append(f"{name}: {'OK' if good else 'FAILED'} ({d['emesh']['meshes']} meshes, {d['emesh']['instances']} instances"
                     + (f", {details} details)" if detailed else ")"))
    return g.stage("engine pack check", ok, "meshc --info + sandbox --verify; " + "; ".join(parts), total)


CODE_DIRS = ("src/", "include/", "tests/", "bench/", "kits/")
CODE_SUFFIXES = (".cpp", ".hpp", ".h", ".cc", ".txt", ".cmake")   # .txt: CMakeLists.txt


def is_code(path):
    """A procgen-relative path whose change can break what only the full gate checks (builds, sanitizers, GCC, judge
    kits, engine packs). Test data counts: the receipts and baselines are read by the C++ tests too."""
    return (path.startswith(CODE_DIRS) or path.endswith(CODE_SUFFIXES) or path == "CMakePresets.json"
            or path == "tools/byteask.py")


def last_green_full():
    """The newest green full verify report in this checkout whose commit git still knows, else None."""
    for rep in sorted(REPORTS.glob("verify-*.json"), reverse=True) if REPORTS.exists() else []:
        try:
            r = json.loads(rep.read_text())
        except json.JSONDecodeError:
            continue
        if r.get("ok") and r.get("mode", "quick" if r.get("quick") else "full") == "full" \
                and subprocess.run(["git", "cat-file", "-e", r["commit"] + "^{commit}"], cwd=REPO, stderr=subprocess.DEVNULL).returncode == 0:
            return r["commit"]
    return None


def changed_since(base):
    """procgen-relative paths that differ from base: commits since base, uncommitted edits and untracked files."""
    prefix = ROOT.relative_to(REPO).as_posix() + "/"
    tracked = git("diff", "--name-only", base, "--", str(ROOT)).splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", str(ROOT)).splitlines()
    return sorted({p[len(prefix):] for p in tracked + untracked if p.startswith(prefix)})


def auto_mode():
    """'full' unless every change since the last green full verify is recipe, docs or script work."""
    base = last_green_full()
    if base is None:
        return "full", None, "no green full verify in this checkout"
    code = [p for p in changed_since(base) if is_code(p)]
    if code:
        return "full", base, f"code changed since {base[:8]}: {code[:3]}{' …' if len(code) > 3 else ''}"
    return "recipes", base, f"only recipes/docs/scripts changed since {base[:8]}"


def verify(args):
    mode, base, why = ("quick", None, "--quick") if "--quick" in args else ("full", None, "")
    if "--auto" in args and mode != "quick":
        mode, base, why = auto_mode()
    quick = mode == "quick"
    light = mode != "full"
    print(f"[byteask] verify ({mode}) at {git('rev-parse', '--short', 'HEAD')}" + (f": {why}" if why else ""),
          flush=True)
    skipped = []
    dirty = bool(git("status", "--porcelain", "--", str(ROOT)))   # before the stages regenerate tracked files

    def skip(name):
        skipped.append(name)
        print(f"  SKIP  {name:<26}         ({why})", flush=True)

    g = Gate()
    for preset in ("debug", "release", "asan"):
        if preset == "release" or not light:
            build_and_test(g, preset)
        elif mode == "recipes":
            skip(f"{preset}: build + tests")
    if not light and "--no-gcc" not in args:
        gcc_stage(g)
    elif mode == "recipes":
        skip("gcc12 (harness image)")
    if "--no-kits" not in args:
        for kit in ("procgen-ops", "procgen-weld"):
            if mode == "recipes" or (quick and kit != "procgen-ops"):
                if mode == "recipes":
                    skip(f"kit {kit}")
            elif (KITS / kit / "selftest.sh").exists():
                kit_stage(g, kit)
    gallery_stage(g)
    conformance_stage(g)
    island_kit_stage(g)
    kit_family_stage(g, "greenhouse kits", "recipes/build_greenhouse_kits.py", "greenhouse", 6)
    kit_family_stage(g, "site kits", "recipes/build_site_kits.py", "site", 3)
    kit_family_stage(g, "isle kits", "recipes/build_isle_kits.py", "isle", 2)
    sweep_stage(g)
    audit_stage(g)
    references_stage(g)
    mcp_stage(g)
    if not light:
        engine_pack_stage(g)
    elif mode == "recipes":
        skip("engine pack check")
    REPORTS.mkdir(exist_ok=True)
    # With uncommitted edits HEAD is not what was verified, so a later --auto run must not use it as its base.
    report = {"commit": git("rev-parse", "HEAD"), "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "quick": quick,
              "mode": mode if not (mode == "full" and dirty) else "full-dirty", "base": base, "reason": why,
              "skipped": skipped, "ok": g.ok, "stages": g.stages}
    path = REPORTS / f"verify-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"[byteask] {'GREEN' if g.ok else 'RED'} -> {path.relative_to(REPO)}")
    return 0 if g.ok else 1


def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, text=True, capture_output=True).stdout.strip()


def tmcp(tool, args):
    out = subprocess.run([sys.executable, str(TMCP), tool, json.dumps(args)], text=True, capture_output=True,
                         env={**os.environ, "TMCP_MAX": "400000"}).stdout
    return json.loads(out)


def find_patch(run_name):
    """A champion patch for a Thermite run: from the rig outbox, else from the run record."""
    outbox = Path.home() / "thermite" / ".dev" / "outbox"
    hits = sorted(outbox.rglob(f"*{run_name}*.patch"), key=lambda p: p.stat().st_mtime) if outbox.exists() else []
    if hits:
        return hits[-1], None
    d = tmcp("get_run", {"run": run_name})
    return None, d


def integrate(args):
    if not args:
        print(__doc__)
        return 2
    target = args[0]
    patch = Path(target).resolve() if Path(target).exists() else None  # git runs from the repo root
    note = ""
    if patch is None:
        patch, rec = find_patch(target)
        if patch is None:
            champs = (rec or {}).get("champion", [])
            print(f"[byteask] no exported patch for {target} yet; champion history: "
                  f"{[(c['seq'], c['commit'][:8], c.get('delta')) for c in champs]}")
            print("          Export it first (rig Export, or issue export_now {run}), then re-run.")
            return 2
        note = f"Thermite run {target}"
    if git("status", "--porcelain", "--untracked-files=no"):
        print("[byteask] working tree has uncommitted changes; commit or stash first")
        return 2
    base = git("rev-parse", "--abbrev-ref", "HEAD")
    scratch = f"byteask/integrate-{int(time.time())}"
    git("switch", "-c", scratch)
    r = subprocess.run(["git", "apply", "--3way", "--directory=procgen", str(patch)], cwd=REPO, text=True,
                       capture_output=True)
    if r.returncode:
        r = subprocess.run(["git", "apply", "--3way", str(patch)], cwd=REPO, text=True, capture_output=True)
    if r.returncode:
        print("[byteask] patch does not apply:\n" + r.stderr[-1500:])
        git("switch", base)
        git("branch", "-D", scratch)
        return 1
    if verify([]) != 0:
        print("[byteask] verification RED: discarding the integration")
        git("reset", "--hard", "-q")
        git("switch", base)
        git("branch", "-D", scratch)
        return 1
    git("add", "-A", "procgen")
    msg = (f"Integrate Thermite champion ({note or patch.name})\n\nVerified by byteask verify "
           f"(debug, release, ASan+UBSan, GCC 12, judge-kit self-tests, gallery).\n\n" +
           os.environ.get("BYTEASK_COAUTHOR", "Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"))
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=REPO, check=True)
    commit = git("rev-parse", "--short", "HEAD")
    git("switch", base)
    r = subprocess.run(["git", "merge", "--ff-only", scratch], cwd=REPO, text=True, capture_output=True)
    git("branch", "-D", scratch) if r.returncode == 0 else None
    print(f"[byteask] integrated {commit} into {base}" if r.returncode == 0
          else f"[byteask] verified commit {commit} is on {scratch}; fast-forward refused: {r.stderr.strip()}")
    return 0 if r.returncode == 0 else 1


def bench(args):
    code, out, _ = run(NICE + ["cmake", "--build", "--preset", "release", "-j", "2"])
    if code:
        print(out[-2000:])
        return code
    cmd = ["./build-release/pg_bench", "--reps", "9"] + (["--filter", args[0]] if args else [])
    code, out, _ = run(NICE + cmd, timeout=1800)
    print(out)
    return code


def status(_args):
    print(f"[byteask] branch {git('rev-parse', '--abbrev-ref', 'HEAD')} @ {git('log', '-1', '--format=%h %s')}")
    reps = sorted(REPORTS.glob("verify-*.json")) if REPORTS.exists() else []
    if reps:
        r = json.loads(reps[-1].read_text())
        print(f"  last verify: {'GREEN' if r['ok'] else 'RED'} at {r['commit'][:8]} ({r['time']})")
    if TMCP.exists():
        for line in subprocess.run([sys.executable, str(TMCP), "leaderboard", "{}"], text=True,
                                   capture_output=True).stdout.splitlines():
            if "procgen" in line:
                print("  " + line.strip()[:160])
    return 0


if __name__ == "__main__":
    cmds = {"verify": verify, "integrate": integrate, "bench": bench, "status": status}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(2)
    sys.exit(cmds[sys.argv[1]](sys.argv[2:]))
