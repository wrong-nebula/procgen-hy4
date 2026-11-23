#!/usr/bin/env python3
"""Model benchmark for the procgen kit builder.

  bench.py sandbox  <task> <slug>             make a scrubbed sandbox (no model call)
  bench.py run      <task> <slug>... [--pilot] [--all]   sandbox + agent run + collect, one after another
  bench.py collect  <task> <slug>             re-run the builder, check, render, write receipt.json
  bench.py baseline <task>                    render the repo's own module as a hidden anchor entry
  bench.py gallery  [--date D]                build the blind gallery for a date
  bench.py judge    [--port 8765]             serve the judging page

Sandboxes live in /tmp/pgbench (outside the repos, so the agent's Read is denied on the real checkouts).
Results land in bench/runs/<date>/<task>/<slug>/. The OpenRouter key is read from
$PGBENCH_OPENROUTER_KEY or ~/.config/pgbench/openrouter.key; it is never written into a run.
"""
import ast
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
PROCGEN = Path(os.environ.get("PGBENCH_PROCGEN", BENCH.parent / "procgen"))
SANDBOXES = Path(os.environ.get("PGBENCH_SANDBOXES", "/tmp/pgbench")).resolve()
TODAY = os.environ.get("PGBENCH_DATE") or time.strftime("%Y-%m-%d")  # a round keeps its start date
MAX_TURNS = 150
TIMEOUT_S = 45 * 60

COPY = ["recipes", "docs", "README.md"]
COPY_EXCLUDE = ["__pycache__", "*.png", "*.emesh"]


# Conditions (scaffolding experiments): a run slug "<model>--<C>-r<n>" (best-of-N samples: "...-r<n>s<k>")
# runs <model> with condition C's add-ons. See tasks/conditions/*.md.
CONDITIONS = {"A": [], "B": ["plan"], "C": ["plan", "example"], "D": ["plan", "critic"], "E": ["plan", "example", "critic"],
              # round 2: the critic that remembers its lists (no plan or checklist), alone and with the parts library
              "F": ["critic2"], "G": ["critic2", "parts"], "H": [],
              # round 3: the lessons memory (hints on pgm failures, query tool, agents add lessons)
              "M": ["memory"], "N": ["memory", "critic2", "parts"],
              "I": [],  # I: round-4 baseline (arch brief also asks for the back of the walk)
              "L": [],  # L: the lean harness (harness/lean.py) instead of Claude Code; same sandbox, prompt, collection
              "P": [],  # P: lean harness + plateau stop (critic-scored builds, keep the best, stop when it stops improving)
              "Q": ["progressive"]}  # Q: pixel-locked camera + retained last-valid mesh + pace-layered progressive disclosure
MEMORY_SEED = BENCH / "memory"   # lessons.jsonl; a sequence run sets PGBENCH_MEMORY to its own store  # H: round-2 baseline (new toolkit and briefs)
EXAMPLE = {"builder": "runs/2026-11-17/reeving-block/fable-5.2/build.py", "notes": "runs/2026-11-17/reeving-block/fable-5.2/NOTES.md"}
SLUG_RE = re.compile(r"^(?P<base>.+?)--(?P<cond>[A-Z]+)-r(?P<rep>\d+)(?:s(?P<sample>\d+))?$")
HARNESS_LETTERS = {"L": "lean", "P": "lean-plateau"}   # combine with any condition letters, e.g. "--LH-r1"


def feats_of(cond):
    """Features of a condition string: the union over its letters (harness letters add none)."""
    out = []
    for ch in cond or "":
        for f in CONDITIONS.get(ch, []):
            if f not in out:
                out.append(f)
    return out


def harness_of(cond):
    return "lean-plateau" if cond and "P" in cond else "lean" if cond and "L" in cond else "claude"


def split_slug(slug):
    m = SLUG_RE.match(slug)
    return (m["base"], m["cond"], int(m["rep"]), int(m["sample"]) if m["sample"] else None) if m else (slug, None, None, None)


def model_for(slug):
    return models()["models"][split_slug(slug)[0]]


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_task(task, slug=None):
    d = BENCH / "tasks" / task
    spec = json.loads((d / "task.json").read_text())
    prompt = (BENCH / "tasks" / ("common_family.md" if spec.get("family") else "common.md")).read_text()
    if spec.get("family"):
        spec.setdefault("module", spec["modules"][0])
        prompt = prompt.replace("{MODULES}", ", ".join(f"`{m}`" for m in spec["modules"]))
    prompt = prompt.replace("{TITLE}", spec["title"]).replace("{MODULE}", spec["module"])
    prompt = prompt.replace("{BRIEF}", (d / "brief.md").read_text().strip())
    cond = split_slug(slug)[1] if slug else None
    if cond:
        extra = "\n\n".join((BENCH / "tasks" / "conditions" / f"{f}.md").read_text().strip() for f in feats_of(cond))
        if extra:
            prompt = prompt.replace("## What to deliver", extra + "\n\n## What to deliver", 1)
    return spec, prompt


def models():
    return json.loads((BENCH / "harness" / "models.json").read_text())


def sh(cmd, cwd, env=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def pgm_json(args, cwd, pgm):
    code, out, err = sh([str(pgm)] + args, cwd)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return {"passed": False, "error": (out + err)[-2000:], "exit": code}


# ---------------------------------------------------------------- sandbox
def drop_python(path, functions, dict_keys):
    src = path.read_text()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    spans = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append((start, node.end_lineno))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else None
            for k, v in zip(node.value.keys, node.value.values):
                if name in dict_keys and isinstance(k, ast.Constant) and k.value in dict_keys[name]:
                    spans.append((k.lineno, v.end_lineno))
    found = len(spans)
    if found != len(functions) + sum(len(v) for v in dict_keys.values()):
        die(f"scrub: {path.name}: expected to drop {functions} {dict_keys}, found {found} spans")
    for a, b in sorted(spans, reverse=True):
        del lines[a - 1:b]
    path.write_text("".join(lines))
    ast.parse(path.read_text())  # still valid Python


def make_sandbox(task, slug):
    spec, prompt = load_task(task, slug)
    box = SANDBOXES / TODAY / task / slug
    if box.exists():
        shutil.rmtree(box)
    box.mkdir(parents=True)
    for item in COPY:
        cmd = ["rsync", "-a"] + [f"--exclude={e}" for e in COPY_EXCLUDE] + [str(PROCGEN / item), str(box)]
        subprocess.run(cmd, check=True)
    (box / "bin").mkdir()
    shutil.copy2(PROCGEN / "build-release" / "pgm", box / "bin" / "pgm")
    mj_src = BENCH / "harness" / "mujoco_audit.py"
    if mj_src.exists():
        (box / ".bench").mkdir(exist_ok=True)
        shutil.copy2(mj_src, box / ".bench" / "mujoco_audit.py")
        phys_bin = box / "bin" / "physics"
        phys_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "from pathlib import Path\n"
            "box = Path(__file__).resolve().parent.parent\n"
            "cmd = ['uv', 'run', '--with', 'mujoco', '--with', 'numpy', '--with', 'coacd', '--with', 'pillow',\n"
            "       'python3', str(box / '.bench' / 'mujoco_audit.py')] + sys.argv[1:]\n"
            "env = {**os.environ, 'PGBENCH_PGM': str(box / 'bin' / 'pgm')}\n"
            "raise SystemExit(subprocess.call(cmd, env=env))\n"
        )
        phys_bin.chmod(0o755)
    cond0 = split_slug(slug)[1]
    if "no-parts" in feats_of(cond0):   # kitparts.py + kitlib_*.py are part of the base toolkit unless explicitly ablated
        for f in ("recipes/kitparts.py", "docs/KITPARTS.md"):
            (box / f).unlink(missing_ok=True)
        readme = box / "README.md"
        readme.write_text(re.sub(r"; ready-made parts built on them are in `recipes/kitparts.py`,\s*see `docs/KITPARTS.md`", "",
                                 readme.read_text()))
    s = spec["scrub"]
    for op in s.get("python_drop", []):
        drop_python(box / op["file"], op["functions"], op["dict_keys"])
    for op in s.get("replace", []):
        p = box / op["file"]
        t = p.read_text()
        if op["old"] not in t:
            die(f"scrub: '{op['old']}' not found in {op['file']}")
        p.write_text(t.replace(op["old"], op["new"]))
    for op in s.get("drop_lines", []):
        p = box / op["file"]
        kept = [l for l in p.read_text().splitlines(keepends=True) if not re.search(op["pattern"], l)]
        text = "".join(kept)
        if p.suffix == ".json":
            text = re.sub(r",(\s*[\]}])", r"\1", text)
            json.loads(text)
        p.write_text(text)
    for cmd in s.get("rebuild", []):
        code, out, err = sh(cmd, box)
        if code:
            die(f"scrub: rebuild {cmd} failed:\n{out}{err}")
    (box / "renders").mkdir()
    cond = split_slug(slug)[1]
    feats = feats_of(cond)
    tdir = BENCH / "tasks" / task
    if "plan" in feats:
        shutil.copy2(tdir / "checklist.md", box / "CHECKLIST.md")
        shutil.copy2(tdir / "briefcheck.py", box / "briefcheck.py")
    if "example" in feats:
        (box / "examples").mkdir()
        src = (BENCH / EXAMPLE["builder"]).read_text()
        if 'Kit(ROOT, "bench", "site-bench"' not in src:
            die("example builder no longer declares the bench kit; update the rename")
        src = src.replace('Kit(ROOT, "bench", "site-bench"', 'Kit(ROOT, "example", "site-example"')
        (box / "examples" / "worked_example.py").write_text(src)
        shutil.copy2(BENCH / EXAMPLE["notes"], box / "examples" / "NOTES.md")
    if True:  # every run keeps last-good / last-valid snapshots of the builder (see harness/snapshot.py)
        (box / ".bench").mkdir(exist_ok=True)
        shutil.copy2(BENCH / "harness" / "snapshot.py", box / ".bench" / "snapshot.py")
        import hashlib
        brief_text = (BENCH / "tasks" / task / "brief.md").read_text()
        brief_sha = hashlib.sha256(brief_text.encode()).hexdigest()[:12]
        vdir = BENCH / "tasks" / task / "brief_versions"
        vdir.mkdir(exist_ok=True)
        if not (vdir / f"{brief_sha}.md").exists():
            (vdir / f"{brief_sha}.md").write_text(brief_text)   # every brief a run was given is kept, by hash
        (box / ".bench.json").write_text(json.dumps({"module": spec["module"], "brief_sha": brief_sha}))
    if "critic" in feats or "critic2" in feats:
        src = "critique_v2.py" if "critic2" in feats else "critique.py"
        shutil.copy2(BENCH / "harness" / src, box / "critique.py")
        (box / "BRIEF.md").write_text((tdir / "brief.md").read_text())
    if "memory" in feats:
        store = Path(os.environ.get("PGBENCH_MEMORY") or MEMORY_SEED)
        (box / "memory").mkdir()
        shutil.copy2(store / "lessons.jsonl", box / "memory" / "lessons.jsonl")
        shutil.copy2(BENCH / "harness" / "lessons.py", box / "memory" / "lessons.py")
        subprocess.run(["python3", str(box / "memory" / "lessons.py"), "--index"], check=True, capture_output=True)
        (box / "bin" / "pgm").rename(box / "bin" / "pgm.real")
        shutil.copy2(BENCH / "harness" / "pgm_wrapper.py", box / "bin" / "pgm")
        (box / "bin" / "pgm").chmod(0o755)
    if "parts" in feats:
        if not (box / "recipes" / "kitparts.py").exists():
            die("condition needs recipes/kitparts.py in the toolkit (merge the kitlib-parts branch first)")
        (box / "examples").mkdir(exist_ok=True)
        shutil.copy2(PROCGEN / "tools" / "test_kitparts.py", box / "examples" / "parts_demo.py")
    # leak check after every condition file is staged (lessons, examples, parts demo)
    terms = [t.lower() for t in s.get("leak_terms", [])]
    leaks = []
    for f in box.rglob("*"):
        if f.relative_to(box).parts[0] in (".bench", ".bench.json"):
            continue   # the harness's own files (they name the module the agent is asked to build)
        if f.is_file() and f.suffix in (".py", ".json", ".md", ".txt", ".js"):
            low = f.read_text(errors="ignore").lower()
            leaks += [f"{f.relative_to(box)}: {t}" for t in terms if t in low]
    if leaks:
        die("scrub left traces of the answer:\n  " + "\n  ".join(leaks))
    (box / "PROMPT.md").write_text(prompt)
    print(f"sandbox ready: {box}")
    return box, spec, prompt


# ---------------------------------------------------------------- agent run
def api_key():
    k = os.environ.get("PGBENCH_OPENROUTER_KEY")
    p = Path.home() / ".config" / "pgbench" / "openrouter.key"
    if not k and p.exists():
        k = p.read_text().strip()
    if not k:
        die("no key: set PGBENCH_OPENROUTER_KEY or create ~/.config/pgbench/openrouter.key")
    return k


def key_remaining(key):
    """What can actually be spent: the key's remaining limit or the account's balance, whichever is lower
    (a key with room left still fails with 402 when the account runs dry)."""
    vals = []
    for url, f in (("https://openrouter.ai/api/v1/key", lambda d: d.get("limit_remaining")),
                   ("https://openrouter.ai/api/v1/credits", lambda d: d["total_credits"] - d["total_usage"])):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                v = f(json.loads(r.read())["data"])
            if v is not None:
                vals.append(float(v))
        except Exception:
            pass
    return min(vals) if vals else None


def key_usage(key):
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return float(json.load(r)["data"]["usage"])
    except Exception as e:
        print(f"  (key usage lookup failed: {e})")
        return None


def settled_usage(key, before, poll_s=15, stable=3, max_s=300):
    """OpenRouter's key usage lags the requests; wait until it stops changing so the next run is not billed for this one."""
    last, same, t0 = key_usage(key), 0, time.time()
    while last is not None and same < stable and time.time() - t0 < max_s:
        time.sleep(poll_s)
        now = key_usage(key)
        same = same + 1 if now == last else 0
        last = now
    return last


_PRICES = {}


def token_cost(model_id, usage):
    """Cost from Claude Code's token counts and OpenRouter list prices (a cross-check; provider routing can differ)."""
    if not usage:
        return None
    if not _PRICES:
        try:
            with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
                for m in json.load(r)["data"]:
                    _PRICES[m["id"]] = m["pricing"]
        except Exception:
            return None
    p = _PRICES.get(model_id)
    if not p:
        return None
    f = lambda k: float(p.get(k) or 0)
    cost = (usage.get("input_tokens", 0) * f("prompt") + usage.get("output_tokens", 0) * f("completion")
            + usage.get("cache_read_input_tokens", 0) * (f("input_cache_read") or f("prompt"))
            + usage.get("cache_creation_input_tokens", 0) * (f("input_cache_write") or f("prompt")))
    return round(cost, 4)


def claude_args(prompt, box, max_turns=None):
    allowed = ["Read", "Write", "Edit", "Glob", "Grep", "TodoWrite",
               "Bash(python3 *)", "Bash(pgm *)", "Bash(bin/pgm *)", "Bash(./bin/pgm *)",
               "Bash(ls *)", "Bash(ls)", "Bash(mkdir *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
               "Bash(grep *)", "Bash(wc *)", "Bash(cd *)"]
    home = str(Path.home())
    settings = {"permissions": {"deny": [f"Read(/{home}/**)", f"Edit(/{home}/**)", f"Write(/{home}/**)",
                                          f"Grep(/{home}/**)", f"Glob(/{home}/**)"]}}
    snap = box / ".bench" / "snapshot.py"
    if snap.exists():
        settings["hooks"] = {"PostToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": f"python3 '{snap}'", "timeout": 60}]}]}
    return ["-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns or MAX_TURNS),
            "--allowedTools", *allowed,
            "--disallowedTools", "WebFetch", "WebSearch", "Agent", "Task",
            "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {}}),
            "--settings", json.dumps(settings)]


def run_agent(task, slug, box, prompt, parallel=False):
    m = model_for(slug)
    out = BENCH / "runs" / TODAY / task / slug
    out.mkdir(parents=True, exist_ok=True)
    key = api_key()
    cfg = box.parent / f".claude-config-{slug}"
    shutil.rmtree(cfg, ignore_errors=True)
    cfg.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}
    env.update(OPENROUTER_API_KEY=key, CLAUDE_CONFIG_DIR=str(cfg), PATH=f"{box / 'bin'}:{env['PATH']}",
               ORI_TELEMETRY="0")
    before = None if parallel else key_usage(key)
    effort = m.get("effort")
    base, cond, rep_, sample = split_slug(slug)
    if harness_of(cond) != "claude":
        import lean
        spec, _ = load_task(task)
        record = {"task": task, "slug": slug, "base": base, "condition": cond, "rep": rep_, "model": m["id"],
                  "effort": m.get("effort"), "vision": m.get("vision"), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
        record.update(lean.run(box, spec["module"], m["id"], m.get("effort"), key, prompt, out, plateau=harness_of(cond) == "lean-plateau",
                               brief=(BENCH / "tasks" / task / "brief.md").read_text(), contract=spec.get("contract"),
                               progressive=bool(cond and "Q" in cond) or ("progressive" in feats_of(cond))))
        record["cost_usd_total_estimate"] = round(record.get("cost_usd_openrouter", 0) + record.get("critic_cost_usd", 0), 4)
        (out / "run.json").write_text(json.dumps(record, indent=2))
        print(f"  {slug}: exit={record['exit']} turns={record.get('num_turns')} calls={record.get('tool_calls')} "
              f"wall={record['wall_s']}s cost=${record['cost_usd_openrouter']}")
        return record
    record = {"task": task, "slug": slug, "base": base, "condition": cond, "rep": rep_, "sample": sample,
              "model": m["id"], "effort": effort, "vision": m.get("vision"),
              "max_turns": MAX_TURNS, "timeout_s": TIMEOUT_S, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for attempt in ("with-effort", "no-effort"):
        cmd = ["ori", "claude", "--model", m["id"]]
        if attempt == "with-effort" and effort:
            cmd += ["--reasoning-effort", effort]
        spec_ = load_task(task)[0]
        cmd += ["--"] + claude_args(prompt, box, spec_.get("max_turns"))
        t0 = time.time()
        with open(out / "transcript.jsonl", "w") as so, open(out / "stderr.log", "w") as se:
            try:
                p = subprocess.run(cmd, cwd=box, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se,
                                   timeout=spec_.get("timeout_min", TIMEOUT_S // 60) * 60)
                record["exit"] = p.returncode
            except subprocess.TimeoutExpired:
                record["exit"] = "timeout"
        record["wall_s"] = round(time.time() - t0, 1)
        err = (out / "stderr.log").read_text()
        if attempt == "with-effort" and record["exit"] not in (0, "timeout") and re.search(r"effort|reasoning", err, re.I) \
                and record["wall_s"] < 30:
            print(f"  {slug}: effort '{effort}' rejected, retrying without it")
            record["effort"] = None
            continue
        break
    after = None if parallel else settled_usage(key, before)
    record["cost_usd_openrouter"] = round(after - before, 4) if before is not None and after is not None else None
    record.update(transcript_stats(out / "transcript.jsonl", box))
    record["cost_usd_token_estimate"] = token_cost(m["id"], record.get("usage"))
    (out / "run.json").write_text(json.dumps(record, indent=2))
    print(f"  {slug}: exit={record['exit']} turns={record.get('num_turns')} wall={record['wall_s']}s "
          f"cost=${record['cost_usd_openrouter']}")
    return record


def transcript_stats(path, box):
    stats = {"num_turns": None, "usage": None, "result": None, "tool_calls": 0, "renders_viewed": 0, "outside_paths": []}
    home = str(Path.home())
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            stats["init"] = {k: ev.get(k) for k in ("model", "tools", "mcp_servers", "permissionMode")}
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    stats["tool_calls"] += 1
                    blob = json.dumps(c.get("input"))
                    if c.get("name") == "Read" and ".png" in blob:
                        stats["renders_viewed"] += 1
                    if home in blob:
                        stats["outside_paths"].append(blob[:300])
        if ev.get("type") == "result":
            stats["num_turns"] = ev.get("num_turns")
            stats["usage"] = ev.get("usage")
            stats["result"] = ev.get("subtype")
            stats["claude_reported_cost_usd"] = ev.get("total_cost_usd")
    return stats


# ---------------------------------------------------------------- collect + render
def set_args(sets):
    return [a for k, v in (sets or {}).items() for a in ("--set", f"{k}={v}")]


def render_all(spec, doc, dest, pgm, cwd, sets=None):
    dest.mkdir(parents=True, exist_ok=True)
    results = {}
    for r in spec["renders"]:
        cam = ["--yaw", str(r["yaw"]), "--pitch", str(r.get("pitch", 0))] if "yaw" in r else ["--view", r["view"]]
        args = ["render", str(doc), "--out", str(dest / f"{r['name']}.png"), *cam, "--pass", r["pass"], "--size", r["size"]]
        if r.get("projection"):
            args += ["--projection", r["projection"]]
        args += set_args(sets)
        res = pgm_json(args, cwd, pgm)
        results[r["name"]] = res.get("passed", False)
    return results


def evaluate(doc, cwd, pgm, sets=None):
    ev = pgm_json(["evaluate", "--efficiency", str(doc)] + set_args(sets), cwd, pgm)
    if not ev.get("passed"):   # fallback if an older pgm binary does not know --efficiency
        ev = pgm_json(["evaluate", str(doc)] + set_args(sets), cwd, pgm)
    chk = pgm_json(["check", str(doc)] + set_args(sets), cwd, pgm)
    swp = pgm_json(["sweep", str(doc)], cwd, pgm)
    aud = pgm_json(["audit", str(doc)] + set_args(sets), cwd, pgm)
    return {"evaluate": ev, "check": {"passed": chk.get("passed"), "sds": chk.get("sds"), "detail": chk if not chk.get("passed") else None},
            "sweep": {"passed": swp.get("passed"), "detail": swp if not swp.get("passed") else None},
            "audit": {"passed": aud.get("passed"), "detail": aud if not aud.get("passed") else None}}


def collect_family(task, slug, spec):
    """A family task: rerun the builder, then check, contract and render every module; a Becher sheet of all of them."""
    box = SANDBOXES / TODAY / task / slug
    out = BENCH / "runs" / TODAY / task / slug
    out.mkdir(parents=True, exist_ok=True)
    pgm = box / "bin" / "pgm"
    receipt = json.loads((out / "run.json").read_text()) if (out / "run.json").exists() else {"task": task, "slug": slug}
    builder = box / "recipes" / "build_bench_kit.py"
    for f in ("NOTES.md",):
        if (box / f).exists():
            shutil.copy2(box / f, out / f)
    receipt["modules"] = {}
    if builder.exists():
        shutil.copy2(builder, out / "build.py")
        shutil.rmtree(box / "recipes" / "site" / "bench", ignore_errors=True)
        code, o, e = sh(["python3", str(builder)], box)
        receipt["builder_rerun"] = {"exit": code, "output": (o + e)[-1500:]}
        if code and (box / ".bench" / "last_good.py").exists():
            shutil.copy2(box / ".bench" / "last_good.py", builder)
            shutil.copy2(builder, out / "build.py")
            sh(["python3", str(builder)], box)
            receipt["fallback_last_good"] = "last_good.py"
    shutil.rmtree(out / "out", ignore_errors=True)
    if (box / "recipes" / "site" / "bench").exists():
        shutil.copytree(box / "recipes" / "site" / "bench", out / "out")
    tiles = []
    for m in spec["modules"]:
        doc = box / "recipes" / "site" / "bench" / f"{m}.json"
        r = {"delivered": doc.exists()}
        if doc.exists():
            q = evaluate(doc, box, pgm)
            r.update(valid=all(q[k]["passed"] for k in ("check", "sweep", "audit")), triangles=q["evaluate"].get("placedTriangles"))
            r["renders"] = render_all(spec, doc, out / "renders" / m, pgm, box)
            if spec.get("contracts", {}).get(m):
                import contract
                r["contract"] = contract.check(spec["contracts"][m], doc, pgm, box)
            png = out / "renders" / m / "iso-beauty.png"
            if png.exists():
                tiles.append((m, png))
        else:
            r["valid"] = False
        receipt["modules"][m] = r
    receipt["valid"] = all(r.get("valid") for r in receipt["modules"].values())
    receipt["triangles"] = sum(r.get("triangles") or 0 for r in receipt["modules"].values())
    if tiles:   # the family on one sheet, side by side, as it is judged
        from PIL import Image, ImageDraw
        sheet = Image.new("RGB", (640 * len(tiles), 520), (24, 24, 24))
        dr = ImageDraw.Draw(sheet)
        for i, (m, png) in enumerate(tiles):
            sheet.paste(Image.open(png).convert("RGB").resize((640, 480)), (640 * i, 0))
            dr.text((640 * i + 10, 492), m, fill=(230, 230, 230))
        sheet.save(out / "renders" / "family-sheet.png")
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(f"  {slug}: valid={receipt['valid']} modules=" + ", ".join(f"{m}:{'ok' if r.get('valid') else 'FAIL'}" for m, r in receipt["modules"].items()))
    if not os.environ.get("PGBENCH_KEEP_SANDBOX"):
        shutil.rmtree(box, ignore_errors=True)
        shutil.rmtree(box.parent / f".claude-config-{slug}", ignore_errors=True)


def collect(task, slug):
    spec, _ = load_task(task)
    if spec.get("family"):
        return collect_family(task, slug, spec)
    box = SANDBOXES / TODAY / task / slug
    out = BENCH / "runs" / TODAY / task / slug
    out.mkdir(parents=True, exist_ok=True)
    pgm = box / "bin" / "pgm"
    receipt = json.loads((out / "run.json").read_text()) if (out / "run.json").exists() else {"task": task, "slug": slug}
    builder = box / "recipes" / "build_bench_kit.py"
    doc = box / "recipes" / "site" / "bench" / f"{spec['module']}.json"
    receipt["delivered"] = {"builder": builder.exists(), "notes": (box / "NOTES.md").exists()}
    if (box / ".bench.json").exists():
        receipt["brief_sha"] = json.loads((box / ".bench.json").read_text()).get("brief_sha")
    if builder.exists():
        shutil.copy2(builder, out / "build.py")
        bench_dir = box / "recipes" / "site" / "bench"
        shutil.rmtree(bench_dir, ignore_errors=True)     # the builder must regenerate it from scratch
        code, o, e = sh(["python3", str(builder)], box)
        receipt["builder_rerun"] = {"exit": code, "output": (o + e)[-1500:]}
        last_good, last_valid = box / ".bench" / "last_good.py", box / ".bench" / "last_valid.py"
        ok = lambda *a: pgm_json(list(a), box, pgm).get("passed")
        broken = code != 0 or not doc.exists() or not ok("check", str(doc))
        invalid = broken or not (ok("sweep", str(doc)) and ok("audit", str(doc)))
        contract_spec = spec.get("contract")
        contract_ok = True
        if not broken and contract_spec and (BENCH / "harness" / "contract.py").exists():
            import contract as ct_mod
            contract_ok = ct_mod.check(contract_spec, doc, pgm, box).get("ok", True)
        if not broken and (invalid or not contract_ok) and (BENCH / "harness" / "local_repair.py").exists():
            import local_repair
            lr_res = local_repair.try_local_repair(box, spec["module"], contract_spec, pgm, max_evals=12)
            if lr_res.get("patched"):
                receipt["local_repair"] = lr_res
                shutil.copy2(builder, out / "build.py")
                code, o, e = sh(["python3", str(builder)], box)
                receipt["builder_rerun"] = {"exit": code, "output": (o + e)[-1500:]}
                broken = code != 0 or not doc.exists() or not ok("check", str(doc))
                invalid = broken or not (ok("sweep", str(doc)) and ok("audit", str(doc)))
        fallback = last_valid if invalid and last_valid.exists() else last_good if broken and last_good.exists() else None
        if fallback and fallback.read_bytes() != builder.read_bytes():
            shutil.copy2(builder, out / "build_final_broken.py")
            shutil.copy2(fallback, builder)
            shutil.copy2(builder, out / "build.py")
            shutil.rmtree(bench_dir, ignore_errors=True)
            code, o, e = sh(["python3", str(builder)], box)
            receipt["fallback_last_good"] = fallback.name
            receipt["builder_rerun"] = {"exit": code, "output": (o + e)[-1500:]}
            broken = code != 0 or not doc.exists() or not ok("check", str(doc))
            invalid = broken or not (ok("sweep", str(doc)) and ok("audit", str(doc)))
            if not broken and invalid and (BENCH / "harness" / "local_repair.py").exists():
                import local_repair
                lr_res = local_repair.try_local_repair(box, spec["module"], contract_spec, pgm, max_evals=12)
                if lr_res.get("patched"):
                    receipt["local_repair"] = lr_res
                    shutil.copy2(builder, out / "build.py")
                    code, o, e = sh(["python3", str(builder)], box)
                    receipt["builder_rerun"] = {"exit": code, "output": (o + e)[-1500:]}
    if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") and doc.exists() \
            and (BENCH / "harness" / "local_repair.py").exists():
        import local_repair
        plr = local_repair.try_physics_local_repair(box, spec["module"], spec.get("contract"), pgm, max_evals=8)
        if plr.get("patched"):
            receipt["physics_local_repair"] = plr
            shutil.copy2(builder, out / "build.py")
    if (box / "NOTES.md").exists():
        shutil.copy2(box / "NOTES.md", out / "NOTES.md")
    if (box / "PLAN.md").exists():
        shutil.copy2(box / "PLAN.md", out / "PLAN.md")
    logs = [f for f in (box / "critic_log.jsonl", box / "renders" / "critic_log.jsonl") if f.exists()]
    if logs:
        calls = [json.loads(l) for f in logs for l in f.read_text().splitlines() if l.strip()]
        (out / "critic_log.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
        receipt["critic_calls"] = sum(1 for c in calls if c.get("ok"))
        receipt["critic_cost_usd"] = round(sum(c.get("cost") or 0 for c in calls), 4)
    receipt["plan_written"] = (box / "PLAN.md").exists()
    mem = box / "memory"
    if (mem / "lessons.jsonl").exists():
        (out / "memory").mkdir(exist_ok=True)
        for f in ("lessons.jsonl", "hint_log.jsonl"):
            if (mem / f).exists():
                shutil.copy2(mem / f, out / "memory" / f)
        hints = [json.loads(l) for l in (mem / "hint_log.jsonl").read_text().splitlines()] if (mem / "hint_log.jsonl").exists() else []
        lessons = [json.loads(l) for l in (mem / "lessons.jsonl").read_text().splitlines() if l.strip()]
        receipt["memory"] = {"hints_shown": len(hints), "lessons": len(lessons),
                             "added": [l["id"] for l in lessons if l.get("added_by") == "agent"],
                             "hit_ids": sorted({h for x in hints for h in x["hints"]})}
        store = os.environ.get("PGBENCH_MEMORY")
        if store:  # a sequence carries the lessons forward to its next task
            shutil.copy2(mem / "lessons.jsonl", Path(store) / "lessons.jsonl")
    receipt["cost_usd_total_estimate"] = round((receipt.get("cost_usd_token_estimate") or 0) + (receipt.get("critic_cost_usd") or 0), 4)
    shutil.rmtree(out / "out", ignore_errors=True)
    if (box / "recipes" / "site" / "bench").exists():
        shutil.copytree(box / "recipes" / "site" / "bench", out / "out")
    if (box / "renders").exists():
        shutil.rmtree(out / "renders" / "agent", ignore_errors=True)
        shutil.copytree(box / "renders", out / "renders" / "agent")
    if doc.exists():
        receipt["quality"] = evaluate(doc, box, pgm)
        receipt["renders"] = render_all(spec, doc, out / "renders", pgm, box)
        q = receipt["quality"]
        receipt["valid"] = all(q[k]["passed"] for k in ("check", "sweep", "audit"))
        receipt["triangles"] = q["evaluate"].get("placedTriangles")
    else:
        receipt["valid"] = False
        receipt["renders"] = {}
    if (BENCH / "harness" / "contract.py").exists() and spec.get("contract") and doc.exists():
        import contract
        receipt["contract"] = contract.check(spec["contract"], doc, pgm, box)
    if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") and doc.exists():
        mj_script = BENCH / "harness" / "mujoco_audit.py"
        if mj_script.exists():
            try:
                r = subprocess.run(
                    ["uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
                     "python3", str(mj_script), str(doc), "--sweep-controls",
                     "--render-collisions", str(out / "renders" / "mujoco-xray.png"),
                     "--export-mjcf", str(out / "out" / f"{spec['module']}.xml")],
                    env={**os.environ, "PGBENCH_PGM": str(pgm)}, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and r.stdout.strip():
                    receipt["physics"] = json.loads(r.stdout)
            except Exception as e:
                receipt["physics"] = {"error": repr(e)}
    # Mechanical craft gates: SDS pole fairness + material slot coverage
    gates = {}
    sds = ((receipt.get("quality") or {}).get("check") or {}).get("sds") or {}
    if sds:
        pinched = sds.get("pinchedPoles") or 0
        max_ev = sds.get("maxEvCurvature")
        gates["pole_gate"] = {
            "passed": (pinched == 0) and (max_ev is None or max_ev <= 1.5),
            "pinchedPoles": pinched,
            "maxEvCurvature": max_ev,
        }
    phys = receipt.get("physics") or {}
    mats_present = sorted((phys.get("massByMaterialKg") or {}).keys())
    if mats_present:
        aspec_path = out / "ANATOMY_SPEC.json"
        expected_mats = []
        if aspec_path.exists():
            try:
                aspec_obj = json.loads(aspec_path.read_text())
                sp = aspec_obj.get("spec") or aspec_obj
                expected_mats = sorted({p.get("material") for p in (sp.get("parts") or []) if p.get("material")})
            except Exception:
                pass
        gates["material_gate"] = {
            "passed": len(mats_present) >= min(3, len(expected_mats) or 3),
            "present": mats_present,
            "expected": expected_mats,
        }
    if gates:
        receipt["gates"] = gates
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    phys_s = f" physics={phys['physicsScore']}/100" if "physicsScore" in phys else ""
    gate_s = ""
    if gates:
        g_ok = all(g.get("passed") for g in gates.values())
        gate_s = f" gates={'ok' if g_ok else 'WARN'}"
    print(f"  {slug}: valid={receipt['valid']} renders={sum(receipt['renders'].values())}/{len(spec['renders'])}"
          + (f" contract={'ok' if receipt['contract']['ok'] else 'FAIL'}" if receipt.get("contract") else "") + phys_s + gate_s)
    if not os.environ.get("PGBENCH_KEEP_SANDBOX"):   # outputs are archived in runs/; the sandbox is disposable
        shutil.rmtree(box, ignore_errors=True)
        shutil.rmtree(box.parent / f".claude-config-{slug}", ignore_errors=True)


def baseline(task):
    spec, _ = load_task(task)
    if not spec.get("baseline"):
        print(f"baseline: {task} has none (new module)")
        return
    sets = spec["baseline"].get("set")
    out = BENCH / "runs" / TODAY / task / "baseline-repo"
    out.mkdir(parents=True, exist_ok=True)
    src = PROCGEN / spec["baseline"]["document"]
    pgm = PROCGEN / "build-release" / "pgm"
    (out / "out").mkdir(exist_ok=True)
    shutil.copy2(src, out / "out" / src.name)
    receipt = {"task": task, "slug": "baseline-repo", "model": spec["baseline"]["label"], "baseline": True}
    receipt["quality"] = evaluate(src, PROCGEN, pgm, sets)
    receipt["renders"] = render_all(spec, src, out / "renders", pgm, PROCGEN, sets)
    receipt["valid"] = all(receipt["quality"][k]["passed"] for k in ("check", "sweep", "audit"))
    receipt["triangles"] = receipt["quality"]["evaluate"].get("placedTriangles")
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(f"baseline: valid={receipt['valid']} -> {out}")


PICK_SYSTEM = """You are a senior 3D modeller choosing which of several candidate models best fulfils a brief.
You see only renders. Judge as a client would: does it read as the object at a glance, does it have what the
brief asks for, are the proportions and the craft right, is anything broken. Reply with exactly one line
'PICK: <number>' followed by one line giving the reason."""


def pick(task, prefix, model="anthropic/claude-opus-5.5"):
    """Best-of-N: a critic picks one of <prefix>s1..sN by its renders; the pick is copied to <prefix>."""
    import base64
    runs = BENCH / "runs" / TODAY / task
    samples = sorted(d for d in runs.iterdir() if d.name.startswith(prefix + "s") and (d / "receipt.json").exists())
    if not samples:
        die(f"pick: no samples for {prefix}")
    recs = {d: json.loads((d / "receipt.json").read_text()) for d in samples}
    pool = [d for d in samples if recs[d].get("valid")] or samples
    random.shuffle(pool)
    content = [{"type": "text", "text": "THE BRIEF\n\n" + (BENCH / "tasks" / task / "brief.md").read_text()}]
    for i, d in enumerate(pool, 1):
        content.append({"type": "text", "text": f"CANDIDATE {i}"})
        for view in ("iso-beauty", "opposite-beauty", "front-clay"):
            f = d / "renders" / f"{view}.png"
            if f.exists():
                content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode()}})
    body = {"model": model, "max_tokens": 2000, "usage": {"include": True},
            "messages": [{"role": "system", "content": PICK_SYSTEM}, {"role": "user", "content": content}]}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    text = (resp["choices"][0]["message"].get("content") or "").strip()
    if not text:
        die(f"pick: empty answer from the critic ({resp['choices'][0].get('finish_reason')})")
    m = re.search(r"PICK:\s*(\d+)", text)
    n = int(m.group(1)) if m and 1 <= int(m.group(1)) <= len(pool) else 1
    chosen = pool[n - 1]
    dest = runs / prefix
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(chosen, dest)
    rec = dict(recs[chosen])
    pick_cost = (resp.get("usage") or {}).get("cost") or 0
    rec.update(slug=prefix, sample=None, picked_from=chosen.name, pick_reason=text, pick_cost_usd=pick_cost,
               samples=[{"slug": d.name, "valid": recs[d].get("valid"), "cost": recs[d].get("cost_usd_total_estimate")} for d in samples],
               cost_usd_total_estimate=round(sum(recs[d].get("cost_usd_total_estimate") or 0 for d in samples) + pick_cost, 4))
    (dest / "receipt.json").write_text(json.dumps(rec, indent=2))
    print(f"[{task}] {prefix}: picked {chosen.name} of {len(samples)} ({text.splitlines()[-1][:100]}); total ${rec['cost_usd_total_estimate']}")


# ---------------------------------------------------------------- gallery + judge
def gallery(date):
    runs = BENCH / "runs" / date
    g = BENCH / "gallery" / date
    if g.exists():
        shutil.rmtree(g)
    g.mkdir(parents=True)
    manifest, key = {"date": date, "tasks": []}, {"date": date, "tasks": {}}
    rng = random.Random()
    for tdir in sorted(p for p in runs.iterdir() if p.is_dir() and (BENCH / "tasks" / p.name / "task.json").exists()):
        spec, _ = load_task(tdir.name)
        if spec.get("family"):
            continue   # families are judged on their own sheet, not in the blind gallery
        entries = [e for e in sorted(tdir.iterdir()) if (e / "receipt.json").exists() and split_slug(e.name)[3] is None]
        rng.shuffle(entries)
        task_entry = {"id": tdir.name, "title": spec["title"], "brief": (BENCH / "tasks" / tdir.name / "brief.md").read_text(),
                      "renders": [r["name"] for r in spec["renders"]], "entries": []}
        key["tasks"][tdir.name] = {}
        for i, e in enumerate(entries):
            label = chr(ord("A") + i) if i < 26 else chr(ord("A") + i // 26 - 1) + chr(ord("A") + i % 26)
            rec = json.loads((e / "receipt.json").read_text())
            dest = g / tdir.name / label
            dest.mkdir(parents=True)
            imgs = {}
            for r in spec["renders"]:
                f = e / "renders" / f"{r['name']}.png"
                if f.exists():
                    try:
                        os.link(f, dest / f.name)
                    except OSError:
                        shutil.copy2(f, dest / f.name)
                    imgs[r["name"]] = f"{date}/{tdir.name}/{label}/{f.name}"
            task_entry["entries"].append({"label": label, "images": imgs})
            key["tasks"][tdir.name][label] = {
                "slug": e.name, "model": rec.get("model"), "effort": rec.get("effort"), "vision": rec.get("vision"),
                "valid": rec.get("valid"), "check": rec.get("quality", {}).get("check", {}).get("passed"),
                "sweep": rec.get("quality", {}).get("sweep", {}).get("passed"),
                "audit": rec.get("quality", {}).get("audit", {}).get("passed"), "triangles": rec.get("triangles"),
                "cost_usd": rec.get("cost_usd_openrouter"), "cost_usd_estimate": rec.get("cost_usd_token_estimate"), "wall_s": rec.get("wall_s"), "turns": rec.get("num_turns"),
                "renders_viewed": rec.get("renders_viewed"), "exit": rec.get("exit"),
                "outside_paths": len(rec.get("outside_paths") or []),
                "condition": rec.get("condition"), "base": rec.get("base"), "critic_calls": rec.get("critic_calls"),
                "cost_total_estimate": rec.get("cost_usd_total_estimate"), "picked_from": rec.get("picked_from")}
        manifest["tasks"].append(task_entry)
    (g / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (BENCH / "gallery" / "key.json").write_text(json.dumps(key, indent=1))
    print(f"gallery: {g} ({sum(len(t['entries']) for t in manifest['tasks'])} entries)")


def judge(port, date):
    import http.server
    gdir = BENCH / "gallery"
    scores_path = gdir / "scores.json"

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(gdir), **kw)

        def log_message(self, *a):
            pass

        def send_json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def scores(self):
            return json.loads(scores_path.read_text()) if scores_path.exists() else {}

        def do_GET(self):
            if self.path.startswith("/key.json"):
                return self.send_json({"error": "use /api/reveal after scoring"}, 403)
            if self.path == "/api/manifest":
                return self.send_json(json.loads((gdir / date / "manifest.json").read_text()))
            if self.path == "/api/scores":
                return self.send_json(self.scores().get(date, {}))
            if self.path.startswith("/api/reveal/"):
                task = self.path.rsplit("/", 1)[1]
                key = json.loads((gdir / "key.json").read_text())
                labels = key["tasks"].get(task, {})
                done = self.scores().get(date, {}).get(task, {})
                if any(l not in done for l in labels):
                    return self.send_json({"error": "score every entry of this task first"}, 403)
                return self.send_json(labels)
            if self.path in ("/", ""):
                self.path = "/judge.html"
            return super().do_GET()

        def do_POST(self):
            if self.path != "/api/scores":
                return self.send_json({"error": "not found"}, 404)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            all_scores = self.scores()
            import hashlib
            img = gdir / date / body["task"] / body["label"] / "iso-beauty.png"
            render_hash = hashlib.sha256(img.read_bytes()).hexdigest()[:12] if img.exists() else None
            all_scores.setdefault(date, {}).setdefault(body["task"], {})[body["label"]] = {
                **body["score"], "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "render_sha": render_hash}
            scores_path.write_text(json.dumps(all_scores, indent=1))
            self.send_json({"ok": True})

    print(f"judging {date}: http://127.0.0.1:{port}/")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


# ---------------------------------------------------------------- main
def main(argv):
    if not argv:
        die(__doc__)
    cmd, rest = argv[0], argv[1:]
    opt = lambda name, default: rest[rest.index(name) + 1] if name in rest else default
    if cmd == "sandbox":
        make_sandbox(rest[0], rest[1])
    elif cmd == "run":
        task = rest[0]
        valued = {"--jobs", "--min-credit"}
        slugs = [s for i, s in enumerate(rest[1:], 1)
                 if not s.startswith("--") and rest[i - 1] not in valued]
        if "--pilot" in rest:
            slugs = models()["pilot"]
        if "--all" in rest:
            slugs = list(models()["models"])
        for s in slugs:
            if split_slug(s)[0] not in models()["models"]:
                die(f"unknown model slug {s}")
        jobs = int(opt("--jobs", "1"))
        force = "--force" in rest
        if "--mujoco" in rest:
            os.environ["PGBENCH_MUJOCO"] = "1"
        if "--anatomy" in rest:
            os.environ["PGBENCH_ANATOMY"] = "1"
        todo = [s for s in slugs if force or not (BENCH / "runs" / TODAY / task / s / "receipt.json").exists()]
        for s in set(slugs) - set(todo):
            print(f"[{task}] {s}: already done today, skipped (--force to redo)")
        key = api_key()
        min_left = float(opt("--min-credit", "8"))

        def one(s):
            left = key_remaining(key)
            if left is not None and left < min_left:
                print(f"[{task}] {s}: skipped, key has ${left:.2f} left (< ${min_left})")
                return
            print(f"[{task}] {s}: start (key ${left})")
            try:
                box, spec, prompt = make_sandbox(task, s)
                if os.environ.get("PGBENCH_ANATOMY", "0").lower() in ("1", "true", "yes"):
                    import anatomy_spec as aspec
                    brief_txt = (BENCH / "tasks" / task / "brief.md").read_text()
                    ref_imgs = sorted((BENCH / "tasks" / task).glob("ref*.png")) + sorted((BENCH / "tasks" / task).glob("ref*.jpg"))
                    ar = aspec.generate_anatomy_spec(brief_txt, spec["module"], contract=spec.get("contract"), images=ref_imgs)
                    (box / "ANATOMY_SPEC.json").write_text(json.dumps(ar["spec"], indent=2) + "\n")
                    out_dir = BENCH / "runs" / TODAY / task / s
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / "ANATOMY_SPEC.json").write_text(json.dumps(ar, indent=2) + "\n")
                    prompt = aspec.format_for_prompt(ar["spec"], ar["warnings"]) + "\n" + prompt
                    print(f"  {s}: Step 0 ANATOMY_SPEC ready (model={ar['model']} cost=${ar.get('cost_usd') or 0:.4f} warnings={len(ar['warnings'])})")
                run_agent(task, s, box, prompt, parallel=jobs > 1)
                collect(task, s)
            except SystemExit as e:
                print(f"[{task}] {s}: failed: {e}")
            except Exception as e:
                print(f"[{task}] {s}: crashed: {e!r}")

        if jobs > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(jobs) as ex:
                list(ex.map(one, todo))
        else:
            for s in todo:
                one(s)
    elif cmd == "rerender":   # re-render a task's standard views from each run's saved module (no model call)
        task = rest[0]
        spec, _ = load_task(task)
        pgm = PROCGEN / "build-release" / "pgm"
        import gzip, tempfile
        for d in sorted((BENCH / "runs" / TODAY / task).iterdir()):
            src = d / "out" / f"{spec['module']}.json"
            if not (d / "receipt.json").exists():
                continue
            with tempfile.TemporaryDirectory() as tmp:
                if not src.exists() and src.with_suffix(".json.gz").exists():
                    shutil.copytree(d / "out", Path(tmp) / "out")
                    t = Path(tmp) / "out" / src.name
                    t.write_bytes(gzip.decompress((Path(tmp) / "out" / (src.name + ".gz")).read_bytes()))
                    src = t
                if not src.exists():
                    print(f"  {d.name}: no module saved"); continue
                res = render_all(spec, src, d / "renders", pgm, src.parent)
                print(f"  {d.name}: {sum(res.values())}/{len(res)} renders")
    elif cmd == "pick":
        pick(rest[0], rest[1])
    elif cmd == "collect":
        collect(rest[0], rest[1])
    elif cmd == "baseline":
        baseline(rest[0])
    elif cmd == "gallery":
        gallery(opt("--date", TODAY))
    elif cmd == "judge":
        judge(int(opt("--port", "8799")), opt("--date", TODAY))
    elif cmd == "inspect":
        import inspector
        inspector.serve(int(opt("--port", "8766")), opt("--date", TODAY))
    else:
        die(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
