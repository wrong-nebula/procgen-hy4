#!/usr/bin/env python3
"""Live Run & Checkpoint-Diff Inspector HTTP server for model-bench.

Serves `bench/gallery/inspector.html` and JSON APIs over both completed
runs (`bench/runs/<date>/<task>/<slug>/`) and live in-flight sandboxes
(`/private/tmp/pgbench/<date>/<task>/<slug>/`), with on-demand checkpoint
rendering and 4-axis efficiency receipts.

Usage:
  PGBENCH_DATE=2026-11-17 python3 bench/harness/inspector.py [--port 8766] [--date 2026-11-17]
"""
from __future__ import annotations

import gzip
import http.server
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

os.environ.setdefault("PGBENCH_DATE", "2026-11-17")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import efficiency  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    gz = Path(str(path) + ".gz")
    raw = None
    if path.exists():
        raw = path.read_text(errors="replace")
    elif gz.exists():
        with gzip.open(gz, "rt", errors="replace") as f:
            raw = f.read()
    if not raw:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def parse_turns(run_dir: Path) -> list[dict]:
    rows = read_jsonl(run_dir / "transcript.jsonl")
    turns: list[dict] = []
    cur: dict | None = None
    t_idx = 0
    for r in rows:
        if "assistant" in r:
            t_idx += 1
            msg = r["assistant"] or {}
            reasoning = msg.get("reasoning") or ""
            if not reasoning and isinstance(msg.get("reasoning_details"), list):
                parts = []
                for d in msg["reasoning_details"]:
                    if isinstance(d, dict) and d.get("text"):
                        parts.append(d["text"])
                    elif isinstance(d, dict) and d.get("summary"):
                        parts.append(d["summary"])
                reasoning = "\n\n".join(parts)
            u = r.get("usage") or {}
            cur = {
                "turn": t_idx,
                "t": r.get("t"),
                "content": msg.get("content") or "",
                "reasoning": reasoning,
                "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cost": u.get("cost"),
                "tools": [],
            }
            turns.append(cur)
        elif "tool" in r and cur is not None:
            cur["tools"].append({
                "tool": r.get("tool"),
                "args": r.get("args", ""),
                "result": r.get("result", ""),
            })
        elif "score" in r and cur is not None:
            cur["inline_score"] = {"score": r.get("score"), "calls": r.get("calls"), "why": r.get("why", "")}
        elif r.get("type") == "assistant" and isinstance(r.get("message"), dict):
            # Claude Code CLI transcript fallback
            t_idx += 1
            blocks = r["message"].get("content") or []
            texts, tools_called = [], []
            for b in blocks:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        texts.append(b.get("text", ""))
                    elif b.get("type") == "thinking":
                        texts.append("> **Thinking:** " + b.get("thinking", ""))
                    elif b.get("type") == "tool_use":
                        tools_called.append({"tool": b.get("name"), "args": json.dumps(b.get("input", {}))[:1200], "result": ""})
            turns.append({
                "turn": t_idx,
                "t": None,
                "content": "\n\n".join(texts),
                "reasoning": "",
                "reasoning_tokens": None,
                "cost": None,
                "tools": tools_called,
            })
    return turns


def list_runs(date: str) -> dict:
    rroot = bench.BENCH / "runs" / date
    sroot = bench.SANDBOXES / date
    tasks_map: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()

    if rroot.exists():
        for tdir in sorted(p for p in rroot.iterdir() if p.is_dir()):
            task = tdir.name
            for sdir in sorted(p for p in tdir.iterdir() if p.is_dir()):
                slug = sdir.name
                seen.add((task, slug))
                rec_file = sdir / "receipt.json"
                rec = json.loads(rec_file.read_text()) if rec_file.exists() else {}
                ckpt_idx = sdir / "checkpoints" / "index.jsonl"
                ckpts = read_jsonl(ckpt_idx)
                hit45 = next((c["turn"] for c in ckpts if (c.get("funnel_score") or 0) >= 4.50 and not c.get("hard_misfits")), None)
                peak = max(((c.get("funnel_score") or 0) for c in ckpts), default=None)
                live_box = sroot / task / slug
                is_live = not rec_file.exists() and ((sdir / "transcript.jsonl").exists() or live_box.exists())
                tasks_map.setdefault(task, []).append({
                    "slug": slug,
                    "live": is_live,
                    "valid": rec.get("valid"),
                    "contract_ok": (rec.get("contract") or {}).get("ok"),
                    "turns": rec.get("num_turns"),
                    "cost_usd": rec.get("cost_usd_openrouter") or rec.get("cost_usd_token_estimate"),
                    "triangles": rec.get("triangles"),
                    "checkpoints": len(ckpts),
                    "turn_4_50": hit45,
                    "peak_funnel": peak,
                })

    if sroot.exists():
        for tdir in sorted(p for p in sroot.iterdir() if p.is_dir()):
            task = tdir.name
            for sdir in sorted(p for p in tdir.iterdir() if p.is_dir()):
                slug = sdir.name
                if (task, slug) in seen or slug.endswith("s99"):
                    continue
                tasks_map.setdefault(task, []).append({
                    "slug": slug, "live": True, "valid": None, "contract_ok": None,
                    "turns": None, "cost_usd": None, "triangles": None, "checkpoints": 0,
                    "turn_4_50": None, "peak_funnel": None,
                })

    dates = sorted([p.name for p in (bench.BENCH / "runs").iterdir() if p.is_dir() and p.name[:2] == "20"], reverse=True)
    return {"date": date, "dates": dates, "tasks": tasks_map}


def get_run_detail(date: str, task: str, slug: str, compute_eff: bool = False) -> dict:
    run_dir = bench.BENCH / "runs" / date / task / slug
    box_dir = bench.SANDBOXES / date / task / slug
    rec = json.loads((run_dir / "receipt.json").read_text()) if (run_dir / "receipt.json").exists() else {}
    brief_path = bench.BENCH / "tasks" / task / "brief.md"
    brief = brief_path.read_text() if brief_path.exists() else ""
    notes = ""
    if (run_dir / "NOTES.md").exists():
        notes = (run_dir / "NOTES.md").read_text(errors="replace")
    elif (box_dir / "NOTES.md").exists():
        notes = (box_dir / "NOTES.md").read_text(errors="replace")

    ckpts = read_jsonl(run_dir / "checkpoints" / "index.jsonl")
    turns = parse_turns(run_dir)
    is_live = not (run_dir / "receipt.json").exists() and (box_dir.exists() or (run_dir / "transcript.jsonl").exists())

    eff = rec.get("efficiency")
    if compute_eff and not eff and (run_dir / "receipt.json").exists() and (run_dir / "build.py").exists():
        try:
            eff = efficiency.compute_run_efficiency(task, slug, date=date, persist=True)
        except Exception as e:
            eff = {"error": str(e)}

    renders = []
    rdir = run_dir / "renders"
    if rdir.exists():
        renders = sorted(p.name for p in rdir.glob("*.png"))
    live_previews = []
    if box_dir.exists() and (box_dir / "renders").exists():
        live_previews = sorted(p.name for p in (box_dir / "renders").glob("*.png"))

    return {
        "date": date,
        "task": task,
        "slug": slug,
        "live": is_live,
        "brief": brief,
        "notes": notes,
        "receipt": rec,
        "efficiency": eff,
        "checkpoints": ckpts,
        "turns": turns,
        "renders": renders,
        "live_previews": live_previews,
        "has_final_build": (run_dir / "build.py").exists() or (box_dir / "recipes" / "build_bench_kit.py").exists(),
    }


def read_code_snapshot(date: str, task: str, slug: str, target: str) -> str:
    run_dir = bench.BENCH / "runs" / date / task / slug
    box_dir = bench.SANDBOXES / date / task / slug
    if target in ("", "empty", "none"):
        return ""
    if target == "final":
        if (run_dir / "build.py").exists():
            return (run_dir / "build.py").read_text(errors="replace")
        if (box_dir / "recipes" / "build_bench_kit.py").exists():
            return (box_dir / "recipes" / "build_bench_kit.py").read_text(errors="replace")
        return ""
    # Checkpoint filename inside checkpoints/
    safe = Path(target).name
    p = run_dir / "checkpoints" / safe
    return p.read_text(errors="replace") if p.exists() else ""


def ensure_checkpoint_renders(date: str, task: str, slug: str, ckpt_file: str) -> list[str]:
    run_dir = bench.BENCH / "runs" / date / task / slug
    safe = Path(ckpt_file).name
    src_py = run_dir / "checkpoints" / safe
    if not src_py.exists():
        return []
    stem = src_py.stem  # e.g. turn_013
    cache_dir = run_dir / "checkpoints" / f"renders_ui_{stem}"
    if (cache_dir / "iso-beauty.png").exists():
        return sorted(p.name for p in cache_dir.glob("*.png"))

    # Check if sha matches final build.py and final renders exist
    import hashlib
    if (run_dir / "build.py").exists() and (run_dir / "renders" / "iso-beauty.png").exists():
        if hashlib.sha256(src_py.read_bytes()).digest() == hashlib.sha256((run_dir / "build.py").read_bytes()).digest():
            cache_dir.mkdir(parents=True, exist_ok=True)
            for png in (run_dir / "renders").glob("*.png"):
                shutil.copy2(png, cache_dir / png.name)
            return sorted(p.name for p in cache_dir.glob("*.png"))

    spec = json.loads((bench.BENCH / "tasks" / task / "task.json").read_text())
    pgm = bench.PROCGEN / "build-release" / "pgm"
    tmp_root = Path("/private/var/folders/z5/6rm_mqdx6nz17tgp5kwpksb80000gn/T/opencode")
    if not tmp_root.exists():
        tmp_root = Path(tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="pgckpt_", dir=str(tmp_root)))
    try:
        shutil.copytree(bench.PROCGEN / "recipes", work / "recipes")
        (work / "recipes" / "site" / "bench").mkdir(parents=True, exist_ok=True)
        (work / "recipes" / "site" / "bench" / "modules.json").write_text(
            json.dumps({"name": "bench", "imports": spec.get("imports", ["../../arch"]), "modules": {}})
        )
        shutil.copy2(src_py, work / "recipes" / "build_bench_kit.py")
        code, _, _ = bench.sh(["python3", "recipes/build_bench_kit.py"], work)
        doc_path = work / "recipes" / "site" / "bench" / f"{spec['module']}.json"
        if code != 0 or not doc_path.exists():
            return []
        cache_dir.mkdir(parents=True, exist_ok=True)
        bench.render_all(spec, doc_path, cache_dir, pgm, work)
        return sorted(p.name for p in cache_dir.glob("*.png"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure_pace_renders(date: str, task: str, slug: str) -> list[str]:
    """Render pixel-locked Pace Layers 0..3 (Clay Massing, Structure, Enclosed Shell, Full Details)
    for any completed run using pgfix's --pace-layer + --frame."""
    run_dir = bench.BENCH / "runs" / date / task / slug
    build_py = run_dir / "build.py"
    if not build_py.exists():
        return []
    pdir = run_dir / "pace_layers"
    if (pdir / "pace-3.png").exists():
        return sorted(p.name for p in pdir.glob("*.png"))

    spec = json.loads((bench.BENCH / "tasks" / task / "task.json").read_text())
    pgfix_root = bench.BENCH.parent / "pgfix" / "procgen"
    pgm = pgfix_root / "build-release" / "pgm"
    recipes_src = pgfix_root / "recipes" if (pgfix_root / "recipes").exists() else bench.PROCGEN / "recipes"
    if not pgm.exists():
        pgm = bench.PROCGEN / "build-release" / "pgm"

    import diag
    frm = diag.envelope_frame(spec.get("contract"))
    frame_args = []
    if frm:
        (x0, y0, z0), (x1, y1, z1) = frm
        frame_args = ["--frame", f"{x0:.3f},{y0:.3f},{z0:.3f},{x1:.3f},{y1:.3f},{z1:.3f}"]

    tmp_root = Path("/private/var/folders/z5/6rm_mqdx6nz17tgp5kwpksb80000gn/T/opencode")
    if not tmp_root.exists():
        tmp_root = Path(tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="pgpace_", dir=str(tmp_root)))
    try:
        shutil.copytree(recipes_src, work / "recipes")
        (work / "recipes" / "site" / "bench").mkdir(parents=True, exist_ok=True)
        (work / "recipes" / "site" / "bench" / "modules.json").write_text(
            json.dumps({"name": "bench", "imports": spec.get("imports", ["../../arch"]), "modules": {}})
        )
        shutil.copy2(build_py, work / "recipes" / "build_bench_kit.py")
        code, _, _ = bench.sh(["python3", "recipes/build_bench_kit.py"], work)
        doc_path = work / "recipes" / "site" / "bench" / f"{spec['module']}.json"
        if code != 0 or not doc_path.exists():
            return []
        pdir.mkdir(parents=True, exist_ok=True)
        for layer in range(4):
            out_png = pdir / f"pace-{layer}.png"
            bench.sh([str(pgm), "render", str(doc_path), "--out", str(out_png),
                      "--view", "iso", "--size", "800x600", "--pace-layer", str(layer), *frame_args], work)
        return sorted(p.name for p in pdir.glob("*.png"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def serve(port: int = 8766, default_date: str | None = None):
    date0 = default_date or bench.TODAY
    gdir = bench.BENCH / "gallery"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(gdir), **kw)

        def log_message(self, *a):
            pass

        def send_json(self, obj: dict, status: int = 200):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_png(self, path: Path):
            if not path.exists() or not path.is_file():
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "max-age=30")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            p = parsed.path
            g = lambda k, d="": (qs.get(k) or [d])[0]

            if p in ("/", "/inspector", "/inspector.html"):
                self.path = "/inspector.html"
                return super().do_GET()
            if p == "/key.json":
                return self.send_json({"error": "use /api/reveal after scoring"}, 403)
            if p == "/api/manifest":
                mf = gdir / date0 / "manifest.json"
                return self.send_json(json.loads(mf.read_text()) if mf.exists() else {"tasks": []})
            if p == "/api/scores":
                sp = gdir / "scores.json"
                all_sc = json.loads(sp.read_text()) if sp.exists() else {}
                return self.send_json(all_sc.get(date0, {}))
            if p == "/api/runs":
                return self.send_json(list_runs(g("date", date0)))
            if p == "/api/run":
                return self.send_json(get_run_detail(
                    g("date", date0), g("task"), g("slug"), compute_eff=(g("eff") == "1")
                ))
            if p == "/api/code":
                a_txt = read_code_snapshot(g("date", date0), g("task"), g("slug"), g("a", "empty"))
                b_txt = read_code_snapshot(g("date", date0), g("task"), g("slug"), g("b", "final"))
                return self.send_json({"a": a_txt, "b": b_txt})
            if p == "/api/ckpt_render":
                views = ensure_checkpoint_renders(g("date", date0), g("task"), g("slug"), g("file"))
                return self.send_json({"views": views})
            if p == "/api/pace_render":
                views = ensure_pace_renders(g("date", date0), g("task"), g("slug"))
                return self.send_json({"views": views})
            if p == "/api/img":
                d, task, slug, name = g("date", date0), Path(g("task")).name, Path(g("slug")).name, Path(g("name")).name
                kind = g("kind", "final")
                run_dir = bench.BENCH / "runs" / d / task / slug
                box_dir = bench.SANDBOXES / d / task / slug
                if kind == "live":
                    return self.send_png(box_dir / "renders" / name)
                if kind == "pace":
                    return self.send_png(run_dir / "pace_layers" / name)
                if kind.startswith("ckpt:"):
                    stem = Path(kind.split(":", 1)[1]).stem
                    return self.send_png(run_dir / "checkpoints" / f"renders_ui_{stem}" / name)
                return self.send_png(run_dir / "renders" / name)
            return super().do_GET()

    print(f"Inspector -> http://127.0.0.1:{port}/  (date {date0})   Ctrl-C when done", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    args = sys.argv[1:]
    port = 8766
    date = bench.TODAY
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a == "--date" and i + 1 < len(args):
            date = args[i + 1]
    serve(port=port, default_date=date)
