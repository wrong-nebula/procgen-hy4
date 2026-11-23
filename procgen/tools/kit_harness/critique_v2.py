#!/usr/bin/env python3
"""Independent critic, version 2 (remembers its earlier lists): for a bench module: renders the standard views, sends them with the brief to a strong
vision model on OpenRouter, prints a short list of misfits. At most MAX_CALLS calls per sandbox; every call is
logged (usage and cost) to critic_log.jsonl. Copied into a sandbox as critique.py next to .bench.json."""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / ".bench.json").read_text())
LOG = HERE / "renders" / "critic_log.jsonl"  # renders/ is a place the rules let the agent write
MAX_CALLS = 3
MODEL = os.environ.get("PGBENCH_CRITIC", CFG.get("critic", "anthropic/claude-opus-5.5"))
VIEWS = [("iso", "beauty", None), ("opposite", "beauty", None), ("front", "clay", "ortho"), ("iso", "clay", None)]

SYSTEM = """You are a senior 3D modeller and draughtsman reviewing a junior's procedural model against a brief.
You see only renders, never code. Judge what the renders show, as a client would at a glance and then on a
closer look.

Return a list of MISFITS: places where the model does not yet read as the object in the brief, misses
something the brief asks for, has wrong proportions, or looks crude or broken. Most important first. At most 7.

Each misfit on its own line, in this form:
N. <what is wrong, as seen, naming the view> -> <the concrete change: which part, roughly what size or where>
Keep each misfit to at most three short sentences, plain text, no bold or headings.

Rules: no praise and no summary. Do not ask for anything the brief does not want. Do not mention code or tools.
If nothing blocks it from reading as the object and meeting the brief, start your reply with the line
NO BLOCKING MISFITS
and then give at most 3 optional refinements (or none). Do not invent problems to fill a list.

If you are shown your EARLIER LISTS for this model, keep your advice consistent with them. Say which earlier
misfits are now fixed (one short line, "Fixed: 1, 3"), then list what remains or is new. Do not reverse earlier
advice unless the renders show it was wrong; if you do, say so explicitly in that misfit ("Reversing my earlier
point N because ...")."""


def render(doc, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for view, pas, proj in VIEWS:
        png = out_dir / f"critic-{view}-{pas}.png"
        cmd = [str(HERE / "bin" / "pgm"), "render", str(doc), "--out", str(png), "--view", view, "--pass", pas,
               "--size", "768x576"]
        if proj:
            cmd += ["--projection", proj]
        subprocess.run(cmd, capture_output=True, text=True)
        if png.exists():
            images.append((f"{view} view, {pas} pass" + (" (orthographic)" if proj else ""), png))
    return images


def main():
    calls = [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
    done = [c for c in calls if c.get("ok")]
    if len(done) >= MAX_CALLS:
        print(f"critique: you have used all {MAX_CALLS} critiques. Work from the lists you have.")
        return 1
    doc = HERE / "recipes" / "site" / "bench" / f"{CFG['module']}.json"
    if not doc.exists():
        print(f"critique: {doc.relative_to(HERE)} does not exist; run your builder first.")
        return 1
    images = render(doc, HERE / "renders" / "critic" / f"call-{len(done) + 1}")
    if not images:
        print("critique: the module did not render; fix pgm render errors first.")
        return 1
    earlier = "".join(f"\n\nEARLIER LIST {c['call']}:\n{c['text']}" for c in done)
    content = [{"type": "text", "text": "THE BRIEF\n\n" + (HERE / "BRIEF.md").read_text() + earlier +
                "\n\nTHE RENDERS (standard views at the default controls, as the model is now):"}]
    for caption, png in images:
        content.append({"type": "text", "text": caption})
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                            base64.b64encode(png.read_bytes()).decode()}})
    body = {"model": MODEL, "max_tokens": 2500, "usage": {"include": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                                          "Content-Type": "application/json"})
    t0 = time.time()
    entry = {"call": len(done) + 1, "model": MODEL, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
        text = resp["choices"][0]["message"]["content"].strip()
        entry.update(ok=True, usage=resp.get("usage"), cost=(resp.get("usage") or {}).get("cost"), text=text)
    except Exception as e:  # network or API error: not counted against the limit
        entry.update(ok=False, error=repr(e)[:400])
        text = None
    entry["seconds"] = round(time.time() - t0, 1)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    if text is None:
        print(f"critique: the critic could not be reached ({entry['error'][:120]}). Try again in a minute.")
        return 1
    left = MAX_CALLS - len(done) - 1
    print(f"MISFITS (critique {len(done) + 1} of {MAX_CALLS}; {left} left)\n\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
