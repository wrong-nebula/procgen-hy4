#!/usr/bin/env python3
"""Critic calibration: a vision model scores every gallery entry the human scored, blind (renders and brief only,
the same rubric), and we measure agreement with the human.

  python3 harness/calibrate.py score <model> [--jobs 8] [--date D]   writes gallery/critic_scores/<model>.json
  python3 harness/calibrate.py report [--date D]                     agreement of every scored critic

Agreement: Spearman rank correlation of the mean score per task and over all entries, pairwise order agreement
(of entry pairs the human scored differently, the share the critic orders the same way), mean absolute error on
`overall`, and the critic's top pick per task."""
import base64
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

G = bench.BENCH / "gallery"
OUT = G / "critic_scores"
CRITERIA = ("reads", "fidelity", "craft", "overall")
RUBRIC = """Score this procedural 3D model against the brief, as an experienced modeller judging a competition, from 1
(poor) to 5 (excellent), on four criteria:
- reads: at a glance, from the iso views, does it read as the object in the brief?
- fidelity: does it have what the brief asks for (parts, proportions, ports and frame as visible)?
- craft: proportions, surface detail, sensible materials, no artefacts or broken geometry.
- overall: your overall judgement.
Use the whole scale; 3 is a competent but plain entry. Missing renders mean the entry failed: score it 1.
Reply with only a JSON object: {"reads": n, "fidelity": n, "craft": n, "overall": n, "why": "<one short sentence>"}"""


def arg(name, default):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def entries(date):
    man = json.loads((G / date / "manifest.json").read_text())
    scores = json.loads((G / "scores.json").read_text()).get(date, {})
    key = json.loads((G / "key.json").read_text())["tasks"]
    for t in man["tasks"]:
        for e in t["entries"]:
            human = scores.get(t["id"], {}).get(e["label"])
            if human and all(c in human for c in CRITERIA) and not stale(human, e):
                yield t, e, human, key[t["id"]][e["label"]]["slug"]


def brief_for(task_id, slug, current):
    """The brief an entry was built against: by the hash its receipt recorded, else (older runs) by condition."""
    rc = bench.BENCH / "runs" / "2026-11-17" / task_id / slug / "receipt.json"
    if rc.exists():
        sha = json.loads(rc.read_text()).get("brief_sha")
        f = bench.BENCH / "tasks" / task_id / "brief_versions" / f"{sha}.md"
        if sha and f.exists():
            return f.read_text()
    legacy = bench.BENCH / "tasks" / task_id / "brief_versions" / "legacy.md"
    if legacy.exists():                        # runs built before the brief gained its constructive sections
        return legacy.read_text()
    m = re.match(r".+?--([A-Z])-r\d+$", slug)
    cond = m.group(1) if m else None
    d = bench.BENCH / "tasks" / task_id / "brief_versions"
    if not d.exists():
        return current
    if task_id == "arched-spandrel":
        v = "v3" if cond == "I" else "v2" if cond in ("F", "G", "H", "M", "N") else "v1"
    elif task_id == "handwheel-valve":
        v = "v2" if cond in ("F", "G", "H", "M", "N") else "v1"
    else:
        return current
    return (d / f"{v}.md").read_text()


def stale(human, entry):
    """A score given on a different image than the one now in the gallery (e.g. re-rendered views) is stale.
    Scores saved before hashes were recorded have none and count as current."""
    import hashlib
    rel = entry["images"].get("iso-beauty")
    if not human.get("render_sha") or not rel or not (G / rel).exists():
        return False
    return hashlib.sha256((G / rel).read_bytes()).hexdigest()[:12] != human["render_sha"]


def score_one(model, task, entry, slug=None):
    import diag
    brief = brief_for(task["id"], slug, task["brief"]) if slug and "--by-version" in sys.argv else task["brief"]
    content = [{"type": "text", "text": "THE BRIEF\n\n" + brief + "\n\nTHE RENDERS:"}]
    for name in ("iso-beauty", "iso-clay", "opposite-beauty", "front-clay", "parts"):
        rel = entry["images"].get(name)
        if rel and (G / rel).exists():
            content.append({"type": "text", "text": name})
            content.append({"type": "image_url", "image_url": {"url": diag.encode_image_data_url(G / rel, max_w=1024)}})
    body = {"model": model, "max_tokens": 1500, "temperature": 0, "usage": {"include": True},
            "messages": [{"role": "system", "content": RUBRIC}, {"role": "user", "content": content}]}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + bench.api_key(), "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                resp = json.loads(r.read())
            text = resp["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", text, re.S)
            got = json.loads(m.group(0))
            return {c: float(got[c]) for c in CRITERIA} | {"why": got.get("why", ""), "cost": (resp.get("usage") or {}).get("cost")}
        except Exception as e:  # retry transient errors and unparsable replies
            err = repr(e)
            time.sleep(5 * (attempt + 1))
    return {"error": err}


def score(model, date, jobs):
    OUT.mkdir(exist_ok=True)
    path = OUT / (model.replace("/", "_") + ("--by-version" if "--by-version" in sys.argv else "") + ".json")
    done = json.loads(path.read_text()) if path.exists() else {}
    todo = [(t, e, slug) for t, e, _, slug in entries(date) if f"{t['id']}/{slug}" not in done or "error" in done[f"{t['id']}/{slug}"]]
    print(f"{model}: {len(todo)} entries to score")

    def one(item):
        t, e, slug = item
        return f"{t['id']}/{slug}", score_one(model, t, e, slug)

    with ThreadPoolExecutor(jobs) as ex:
        for k, v in ex.map(one, todo):
            done[k] = v
            path.write_text(json.dumps(done, indent=1))
    cost = sum(v.get("cost") or 0 for v in done.values())
    print(f"{model}: scored {sum(1 for v in done.values() if 'error' not in v)}, errors {sum(1 for v in done.values() if 'error' in v)}, cost ${cost:.2f}")


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else float("nan")


def pairwise(a, b):
    agree = total = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            if a[i] == a[j]:
                continue
            total += 1
            agree += (a[i] - a[j]) * (b[i] - b[j]) > 0
    return agree / total if total else float("nan")


def craft_receipt_term(meta_or_rec, med_tris):
    """Multi-axis anti-soup receipt term:
    1. Bounded richness: 0.40 * tanh(1.15 * log10(tris / med)), saturating smoothly instead of rewarding 50k-tri soup.
    2. Zero-triangle surface detail bonus (+0.06 when surfaceDetails >= 12).
    3. Anti-soup penalty (-0.18 when tris > max(28000, 4.5 * med) with zero analytical surfaceDetails)."""
    import math
    tris = max(int(meta_or_rec.get("triangles") or 1), 1)
    med = max(int(med_tris or 1), 1)
    rich = 0.40 * math.tanh(1.15 * math.log10(tris / med))
    q_ev = ((meta_or_rec.get("quality") or {}).get("evaluate") or {})
    eff = q_ev.get("efficiency") or meta_or_rec.get("efficiency") or {}
    s_det = eff.get("surfaceDetails")
    if s_det is None:
        s_det = (eff.get("wire") or {}).get("details")
    if s_det is None:
        s_det = q_ev.get("placedDetails", q_ev.get("details"))
    bonus = 0.06 if isinstance(s_det, int) and s_det >= 12 else 0.0
    soup_pen = -0.18 if (tris > max(28000, int(4.5 * med)) and s_det == 0) else 0.0
    return round(rich + bonus + soup_pen, 4)


def extract_brief_checklist(brief_text, max_items=6):
    """Deterministically extract concrete verifiable checklist items from a modelling brief."""
    items = []
    # 1. Quoted inscriptions / plaques
    for q in re.findall(r'[`"\u201c]([A-Z0-9][A-Z0-9 .\-/]{1,28})[`"\u201d]', brief_text):
        if len(items) < max_items and not q.islower():
            items.append((f"inscribed_{len(items)}", f"Visible legible inscription or plaque reading '{q}'."))
    # 2. Bullet lines or explicit dimensional/count sentences
    for ln in brief_text.splitlines():
        s = ln.strip()
        if s.startswith(("- ", "* ")) and len(s) > 18:
            clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", s[2:]).strip()
            if any(k in clean.lower() for k in ("port", "control", "json", "kitlib", "deliver")):
                continue
            items.append((f"item_{len(items)}", clean[:180]))
            if len(items) >= max_items:
                break
    if len(items) < max_items:
        for sent in re.split(r"(?<=[.!])\s+", brief_text):
            s = sent.strip()
            if len(s) > 25 and re.search(r"\b(\d+\s+(?:levers?|arches|bays|steps|treads|spokes|bolts|panes|lights)|clear|walk-through|open|through-bore|glazing)\b", s, re.I):
                if all(s[:40] not in ex[1] for ex in items):
                    items.append((f"req_{len(items)}", s[:180]))
                if len(items) >= max_items:
                    break
    return items[:max_items]


def pairwise_compare(model, brief, imgs_a, imgs_b, key=None):
    """Position-swapped head-to-head VLM comparison (`(A, B)` and `(B, A)`) to eliminate presentation-order bias."""
    import diag
    key = key or bench.api_key()
    sys_prompt = (
        "You are judging a procedural 3D modelling competition against a brief. Compare Entry 1 and Entry 2 on "
        "silhouette readability, faithful inclusion of every part/opening in the brief, architectural/mechanical craft, "
        "and absence of floating/obstructed geometry. Reply with ONLY JSON: "
        '{"winner": 1 | 2 | 0, "margin": 0.0..1.0, "why": "<short reason>"}'
    )

    def ask_once(first_imgs, second_imgs):
        content = [{"type": "text", "text": "THE BRIEF:\n" + brief.strip()}]
        for idx, im_map in ((1, first_imgs), (2, second_imgs)):
            content.append({"type": "text", "text": f"\n=== ENTRY {idx} ==="})
            for v in ("iso-beauty", "opposite-beauty", "front-clay"):
                p = im_map.get(v)
                if p and Path(p).exists():
                    content.append({"type": "text", "text": f"Entry {idx} - {v}"})
                    content.append({"type": "image_url", "image_url": {"url": diag.encode_image_data_url(Path(p), max_w=768)}})
        body = {"model": model, "max_tokens": 500, "temperature": 0, "usage": {"include": True},
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": content}]}
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
        txt = resp["choices"][0]["message"].get("content") or ""
        got = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        return int(got.get("winner", 0)), float(got.get("margin", 0.5)), got.get("why", ""), (resp.get("usage") or {}).get("cost") or 0.0

    w_ab, m_ab, why_ab, c1 = ask_once(imgs_a, imgs_b)
    w_ba, m_ba, why_ba, c2 = ask_once(imgs_b, imgs_a)
    # Convert both votes onto A (+1 for A, -1 for B, 0 for tie)
    v1 = 1.0 if w_ab == 1 else (-1.0 if w_ab == 2 else 0.0)
    v2 = 1.0 if w_ba == 2 else (-1.0 if w_ba == 1 else 0.0)
    net = 0.5 * (v1 + v2)
    winner = "A" if net > 0.1 else ("B" if net < -0.1 else "tie")
    return {"winner": winner, "win_prob_a": round(0.5 + 0.5 * net, 3), "cost": round(c1 + c2, 5),
            "ab_why": why_ab, "ba_why": why_ba}


def receipt_adjusted(rows_by_task, anti_soup=False):
    """Carbon's receipt grounding (H6): critic mean + richness/craft term, and 1.0 for invalid entries."""
    import math
    key = json.loads((G / "key.json").read_text())["tasks"]
    adj = {}
    for tid, slugs in rows_by_task.items():
        meta = {e["slug"]: e for e in key.get(tid, {}).values()}
        tris = sorted(t for t in (meta.get(s, {}).get("triangles") for s in slugs) if t)
        med = tris[len(tris) // 2] if tris else 1
        for s in slugs:
            m = meta.get(s, {})
            if not m.get("valid"):
                adj[(tid, s)] = None
            elif anti_soup:
                adj[(tid, s)] = craft_receipt_term(m, med)
            else:
                adj[(tid, s)] = 0.5 * math.log10(max(m.get("triangles") or 1, 1) / med)
    return adj


def report(date):
    mean = lambda d: sum(d[c] for c in CRITERIA) / 4
    rows = list(entries(date))
    by_task = {}
    for t, e, h, s in rows:
        by_task.setdefault(t["id"], []).append(s)
    adj = receipt_adjusted(by_task)
    adj_craft = receipt_adjusted(by_task, anti_soup=True)
    for path in sorted(OUT.glob("*.json")):
        crit = json.loads(path.read_text())
        print(f"\n== critic {path.stem}  (cost ${sum(v.get('cost') or 0 for v in crit.values()):.2f})")
        allh, allc = [], []
        for tid in sorted({t["id"] for t, *_ in rows}):
            pairs = [(mean(h), mean(crit[f"{tid}/{s}"]), h["overall"], crit[f"{tid}/{s}"]["overall"], s)
                     for t, e, h, s in rows if t["id"] == tid and f"{tid}/{s}" in crit and "error" not in crit[f"{tid}/{s}"]]
            if len(pairs) < 3:
                continue
            h, c = [p[0] for p in pairs], [p[1] for p in pairs]
            allh += h; allc += c
            mae = sum(abs(p[2] - p[3]) for p in pairs) / len(pairs)
            top_h = max(pairs, key=lambda p: p[0])[4]; top_c = max(pairs, key=lambda p: p[1])[4]
            print(f"  {tid:18} n={len(pairs):3}  spearman {spearman(h, c):+.2f}  pairwise {pairwise(h, c):.0%}  "
                  f"MAE(overall) {mae:.2f}  human top {top_h} | critic top {top_c}")
        print(f"  {'all':18} n={len(allh):3}  spearman {spearman(allh, allc):+.2f}  pairwise {pairwise(allh, allc):.0%}")
        for label, table in (("all, receipt-grounded", adj), ("all, anti-soup receipt", adj_craft)):
            ah, ac = [], []
            for t, e, h, s in rows:
                k = f"{t['id']}/{s}"
                if k in crit and "error" not in crit[k]:
                    a_ = table.get((t["id"], s))
                    ah.append(mean(h)); ac.append(1.0 if a_ is None else mean(crit[k]) + a_)
            print(f"  {label:22} n={len(ah):3}  spearman {spearman(ah, ac):+.2f}  pairwise {pairwise(ah, ac):.0%}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    date = arg("--date", "2026-11-17")
    if cmd == "score":
        score(sys.argv[2], date, int(arg("--jobs", "8")))
    elif cmd == "report":
        report(date)
    else:
        print(__doc__)
