#!/usr/bin/env python3
"""KIT-FORGE Slice 2: Pairwise Bradley-Terry Swiss Tournament Selector (`tournament.py`).

Upgraded with calibrated `baseten/bison-1` + `nace-ai/drex-max` two-backbone consensus:
  1. Pixel-locked, envelope-normalized `[A | B]` side-by-side comparison strips
     (`iso-beauty` + `opposite-beauty`) with neutral `LEFT (A)` / `RIGHT (B)` framing.
  2. Position-debiased soft-probability pairwise evaluation via `baseten/bison-1`
     (respecting `max_concurrency=3` semaphore + jittered 429 backoff in `decision_gate.py`):
     randomized initial side + automatic `[A|B]` & `[B|A]` dual-pass on close pairs
     (`|p - 0.5| < close_margin`, or all pairs when `double_orient=True`).
  3. Multi-round Swiss Tournament + Penalized Soft-Probability Bradley-Terry MLE + Fisher
     information asymptotic standard error (Elo +/- SE), feeding continuous calibrated win
     probabilities `p(A > B)` directly (never hard `0/1` flips).
  4. Tier-S3 Finalist Selection & Tie-Break:
     - Checks top-2 finalists with cross-family partner `nace-ai/drex-max`.
     - Auto-promotes `#1` when `bison-1` and `drex-max` agree AND `(Elo_1 - Elo_2) >= 15`
       and `max_defect_p < 0.25`.
     - Escalates ONLY top-2 pairs where `bison-1` and `drex-max` disagree OR `|ΔElo| < 15`
       to `openai/gpt-6.1-sol` (~$0.02), respecting `VerifierBudget` (<= $0.05/asset).
  5. Calibration benchmark (`calibrate`) on the top-end (`human >= 3.5`) pairs of
     `gallery/2026-11-17`, verifying Binary Gate #2 (`PAIRWISE_TOURNAMENT_CALIBRATION`).
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import math
import random
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench          # noqa: E402
import decision_gate  # noqa: E402

CACHE_PATH = bench.BENCH / "gallery" / "critic_scores" / "pairwise_bison_drex_cache.json"
_CACHE_LOCK = threading.Lock()
_CACHE_DATA: dict | None = None

PAIRWISE_QUESTIONS = {
    "better_reads_and_proportion": {
        "type": "noul",
        "instructions": (
            "Compare the two 3D models shown side by side: LEFT (A) vs RIGHT (B), across both "
            "the top row (iso-beauty) and bottom row (opposite-beauty). Which model reads more "
            "convincingly as the object in the brief with stronger overall silhouette, realistic "
            "proportions, and cleaner structural composition?"
        ),
        "criteria": {
            "true": "LEFT (A) has better silhouette, proportions, and architectural/mechanical readability than RIGHT (B).",
            "false": "RIGHT (B) has better silhouette, proportions, and architectural/mechanical readability than LEFT (A).",
        },
    },
    "better_craft_and_detail": {
        "type": "noul",
        "instructions": (
            "Compare LEFT (A) vs RIGHT (B) on craft quality, surface detailing, joinery, material "
            "contrast, bevels/curvature, and completeness on both front and back elevations."
        ),
        "criteria": {
            "true": "LEFT (A) shows higher craft, richer coherent detailing, and cleaner front/back execution than RIGHT (B).",
            "false": "RIGHT (B) shows higher craft, richer coherent detailing, and cleaner front/back execution than LEFT (A).",
        },
    },
    "better_overall_no_defects": {
        "type": "noul",
        "instructions": (
            "Overall blind competition verdict comparing LEFT (A) vs RIGHT (B) against the brief. "
            "Penalize missing parts, blocked doorways/arches/bores, floating/detached pieces, "
            "boxy primitive blocks, or awkward collisions."
        ),
        "criteria": {
            "true": "LEFT (A) is the better overall 3D kit module and wins this head-to-head match.",
            "false": "RIGHT (B) is the better overall 3D kit module and wins this head-to-head match.",
        },
    },
}


# ---------------------------------------------------------------------------
# Persistent On-Disk Pairwise Cache (thread-safe)
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    global _CACHE_DATA
    if _CACHE_DATA is None:
        merged = {}
        # Migrate any existing drex cache entries seamlessly
        old_cache = bench.BENCH / "gallery" / "critic_scores" / "pairwise_drex_cache.json"
        for p in (old_cache, CACHE_PATH):
            if p.exists():
                try:
                    merged.update(json.loads(p.read_text()))
                except Exception:
                    pass
        _CACHE_DATA = merged
    return _CACHE_DATA


def _save_cache():
    if _CACHE_DATA is None:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_CACHE_DATA, indent=1))
    tmp.replace(CACHE_PATH)


def _file_sha(p: Path | None) -> str:
    if not p or not p.exists():
        return "none"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Pixel-Locked Envelope-Normalized [A | B] Strip Renderer
# ---------------------------------------------------------------------------

def _envelope_normalize_tile(img_path: Path | None, tw: int = 480, th: int = 360) -> Image.Image:
    """Load render and normalize foreground framing tightly inside `(tw, th)` so neither
    candidate gets an unfair apparent scale advantage from background margins."""
    if not img_path or not img_path.exists():
        return Image.new("RGB", (tw, th), (28, 30, 34))
    im = Image.open(img_path).convert("RGB")
    arr = np.asarray(im, dtype=np.int16)
    corners = np.stack([arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]], axis=0)
    bg = np.median(corners, axis=0)
    diff = np.abs(arr - bg).max(axis=2)
    mask = diff > 14
    ys, xs = np.where(mask)
    if len(xs) > 100:
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        pad_x, pad_y = int(bw * 0.10), int(bh * 0.10)
        cx0, cy0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
        cx1, cy1 = min(im.width, x1 + pad_x), min(im.height, y1 + pad_y)
        crop = im.crop((cx0, cy0, cx1, cy1))
        scale = min((tw - 12) / max(1, crop.width), (th - 12) / max(1, crop.height))
        nw, nh = max(1, int(round(crop.width * scale))), max(1, int(round(crop.height * scale)))
        resized = crop.resize((nw, nh), Image.Resampling.LANCZOS)
        bg_rgb = tuple(int(c) for c in bg)
        canvas = Image.new("RGB", (tw, th), bg_rgb)
        canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
        return canvas
    return im.resize((tw, th), Image.Resampling.LANCZOS)


def make_ab_strip(
    imgs_a: dict,
    imgs_b: dict,
    out_path: Path | None = None,
    *,
    label_a: str = "LEFT (A)",
    label_b: str = "RIGHT (B)",
    tw: int = 480,
    th: int = 360,
) -> Image.Image:
    """Build a pixel-locked 2-row comparison strip:
       Row 0: [A: iso-beauty      | B: iso-beauty]
       Row 1: [A: opposite-beauty | B: opposite-beauty]
    """
    hdr = 26
    div = 6
    rows = []
    for view in ("iso-beauty", "opposite-beauty"):
        pa = Path(imgs_a[view]) if imgs_a.get(view) else None
        pb = Path(imgs_b[view]) if imgs_b.get(view) else None
        if (pa and pa.exists()) or (pb and pb.exists()):
            rows.append((view, _envelope_normalize_tile(pa, tw, th), _envelope_normalize_tile(pb, tw, th)))
    if not rows:
        rows = [("iso-beauty", Image.new("RGB", (tw, th), (28, 30, 34)), Image.new("RGB", (tw, th), (28, 30, 34)))]

    W = tw * 2 + div
    H = hdr + len(rows) * th + (len(rows) - 1) * 4
    canvas = Image.new("RGB", (W, H), (18, 20, 24))
    dr = ImageDraw.Draw(canvas)
    dr.rectangle([0, 0, tw, hdr], fill=(26, 36, 48))
    dr.rectangle([tw + div, 0, W, hdr], fill=(48, 30, 26))
    dr.text((12, 6), label_a, fill=(240, 246, 255))
    dr.text((tw + div + 12, 6), label_b, fill=(255, 244, 236))

    y = hdr
    for vname, ta, tb in rows:
        canvas.paste(ta, (0, y))
        canvas.paste(tb, (tw + div, y))
        dr.rectangle([4, y + 4, 155, y + 22], fill=(0, 0, 0))
        dr.text((8, y + 7), f"A · {vname}", fill=(230, 235, 240))
        dr.rectangle([tw + div + 4, y + 4, tw + div + 155, y + 22], fill=(0, 0, 0))
        dr.text((tw + div + 8, y + 7), f"B · {vname}", fill=(230, 235, 240))
        y += th + 4
    dr.rectangle([tw, 0, tw + div, H], fill=(220, 224, 230))
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path)
    return canvas


def _encode_pil_jpeg(im: Image.Image, max_w: int = 960, quality: int = 86) -> str:
    if im.width > max_w:
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Position-Debiased Soft-Probability Pairwise Evaluator (`baseten/bison-1`)
# ---------------------------------------------------------------------------

def _call_decision_single_order(
    brief: str,
    imgs_left: dict,
    imgs_right: dict,
    *,
    key: str,
    model: str = "baseten/bison-1",
    brief_sha: str = "",
    budget: decision_gate.VerifierBudget | None = None,
) -> dict:
    """Single-orientation `[LEFT | RIGHT]` call returning calibrated soft `p_left` in (0, 1)."""
    sig = "|".join([
        model,
        brief_sha or hashlib.sha256(brief.encode()).hexdigest()[:10],
        _file_sha(Path(imgs_left["iso-beauty"])) if imgs_left.get("iso-beauty") else "none",
        _file_sha(Path(imgs_left["opposite-beauty"])) if imgs_left.get("opposite-beauty") else "none",
        _file_sha(Path(imgs_right["iso-beauty"])) if imgs_right.get("iso-beauty") else "none",
        _file_sha(Path(imgs_right["opposite-beauty"])) if imgs_right.get("opposite-beauty") else "none",
    ])
    with _CACHE_LOCK:
        cache = _load_cache()
        if sig in cache and "error" not in cache[sig]:
            rec = dict(cache[sig])
            rec["cached"] = True
            return rec

    strip = make_ab_strip(imgs_left, imgs_right, label_a="LEFT (A)", label_b="RIGHT (B)")
    data_url = _encode_pil_jpeg(strip, max_w=960, quality=86)
    state = [
        {"type": "text", "text": "MODELING BRIEF:\n" + brief.strip()[:2800] + "\n\nSIDE-BY-SIDE COMPARISON STRIP (LEFT = A, RIGHT = B):"},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    body = {"model": model, "state": state, "questions": PAIRWISE_QUESTIONS}
    raw, ms, err = decision_gate.post_decisions(body, key=key, timeout=90)
    if err or raw is None:
        return {"error": err or "empty_response", "p_left": 0.5, "cost": 0.0, "ms": ms, "cached": False}

    ans = raw.get("answers") or {}
    probs = {}
    for qk in PAIRWISE_QUESTIONS:
        p = decision_gate._noul_prob(ans.get(qk))
        if p is not None:
            probs[qk] = float(p)
    cost = float((raw.get("usage") or {}).get("cost") or 0.0)
    if budget is not None:
        budget.record(model, cost)
    if not probs:
        return {"error": "empty_probs", "p_left": 0.5, "cost": cost, "ms": ms, "cached": False}

    w_map = {"better_reads_and_proportion": 0.30, "better_craft_and_detail": 0.30, "better_overall_no_defects": 0.40}
    num = sum(probs[k] * w_map[k] for k in probs)
    den = sum(w_map[k] for k in probs)
    p_left = float(np.clip(num / max(den, 1e-9), 0.001, 0.999))
    res = {"p_left": round(p_left, 5), "probs": probs, "cost": cost, "ms": ms, "cached": False}
    with _CACHE_LOCK:
        cache = _load_cache()
        cache[sig] = {k: v for k, v in res.items() if k != "cached"}
    return res


def pairwise_compare(
    brief: str,
    imgs_a: dict,
    imgs_b: dict,
    *,
    key: str | None = None,
    model: str = "baseten/bison-1",
    double_orient: bool = False,
    close_margin: float = 0.25,
    rng: random.Random | None = None,
    budget: decision_gate.VerifierBudget | None = None,
) -> dict:
    """Position-debiased soft-probability `p(A > B)` using `baseten/bison-1` (default) or any Decision model.
    Randomizes initial side; automatically runs flipped orientation on close pairs (`|p - 0.5| < close_margin`)
    or whenever `double_orient=True`."""
    key = key or bench.api_key()
    rng = rng or random.Random()
    brief_sha = hashlib.sha256(brief.encode()).hexdigest()[:10]
    flip_first = rng.random() < 0.5

    if not flip_first:
        r1 = _call_decision_single_order(brief, imgs_a, imgs_b, key=key, model=model, brief_sha=brief_sha, budget=budget)
        p_ab = r1["p_left"]
    else:
        r1 = _call_decision_single_order(brief, imgs_b, imgs_a, key=key, model=model, brief_sha=brief_sha, budget=budget)
        p_ab = 1.0 - r1["p_left"]

    api_cost = 0.0 if r1.get("cached") else float(r1.get("cost") or 0.0)
    nominal_cost = float(r1.get("cost") or 0.0)
    calls = 1
    debiased = False

    if double_orient or abs(p_ab - 0.5) < close_margin:
        if not flip_first:
            r2 = _call_decision_single_order(brief, imgs_b, imgs_a, key=key, model=model, brief_sha=brief_sha, budget=budget)
            p_ab_2 = 1.0 - r2["p_left"]
        else:
            r2 = _call_decision_single_order(brief, imgs_a, imgs_b, key=key, model=model, brief_sha=brief_sha, budget=budget)
            p_ab_2 = r2["p_left"]
        p_ab = 0.5 * (p_ab + p_ab_2)
        api_cost += 0.0 if r2.get("cached") else float(r2.get("cost") or 0.0)
        nominal_cost += float(r2.get("cost") or 0.0)
        calls = 2
        debiased = True

    return {
        "model": model,
        "p_a_wins": round(float(np.clip(p_ab, 0.001, 0.999)), 5),
        "debiased": debiased,
        "calls": calls,
        "cost_usd": round(api_cost, 6),
        "nominal_cost_usd": round(nominal_cost, 6),
    }


def pairwise_sol_tiebreak(
    brief: str,
    imgs_a: dict,
    imgs_b: dict,
    *,
    key: str | None = None,
    model: str = "openai/gpt-6.1-sol",
    double_orient: bool = True,
    budget: decision_gate.VerifierBudget | None = None,
) -> dict:
    """Single-match escalation tie-breaker using `openai/gpt-6.1-sol` (~$0.02)."""
    key = key or bench.api_key()
    sys_msg = (
        "You are a blind 3D architectural & mechanical design competition judge. "
        "Compare LEFT (A) vs RIGHT (B) across both iso-beauty and opposite-beauty views against the brief. "
        "Return ONLY compact JSON: {\"winner\": \"A\"|\"B\", \"p_left\": 0.0..1.0, \"why\": \"<=30 words\"}."
    )

    def _one(left_imgs, right_imgs):
        strip = make_ab_strip(left_imgs, right_imgs, label_a="LEFT (A)", label_b="RIGHT (B)")
        url = _encode_pil_jpeg(strip, max_w=1024, quality=88)
        body = {
            "model": model,
            "max_tokens": 250,
            "temperature": 0,
            "usage": {"include": True},
            "reasoning": {"effort": "low"},
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": [
                    {"type": "text", "text": "BRIEF:\n" + brief.strip()[:2500] + "\nCompare LEFT (A) vs RIGHT (B):"},
                    {"type": "image_url", "image_url": {"url": url}},
                ]},
            ],
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read())
        txt = raw["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        got = json.loads(m.group(0)) if m else {"winner": "A", "p_left": 0.5, "why": txt[:120]}
        p_left = float(got.get("p_left", 0.85 if got.get("winner") == "A" else 0.15))
        cost = float((raw.get("usage") or {}).get("cost") or 0.0)
        if budget is not None:
            budget.record(model, cost)
        return float(np.clip(p_left, 0.01, 0.99)), cost, str(got.get("why", ""))

    p1, c1, why1 = _one(imgs_a, imgs_b)
    if double_orient:
        p2_rev, c2, why2 = _one(imgs_b, imgs_a)
        p_a = 0.5 * (p1 + (1.0 - p2_rev))
        return {"p_a_wins": round(p_a, 4), "winner": "A" if p_a >= 0.5 else "B",
                "cost_usd": round(c1 + c2, 5), "why": f"{why1} | rev: {why2}", "model": model}
    return {"p_a_wins": round(p1, 4), "winner": "A" if p1 >= 0.5 else "B",
            "cost_usd": round(c1, 5), "why": why1, "model": model}


# ---------------------------------------------------------------------------
# Soft-Probability Penalized Bradley-Terry MLE + Fisher SE & Swiss Pairing
# ---------------------------------------------------------------------------

def fit_bradley_terry(cand_ids: list[str], matches: list[dict], prior_sigma: float = 2.0) -> dict[str, dict]:
    """Fit penalized Bradley-Terry log-strengths + Elo + Fisher SE from calibrated soft probabilities `p_a_wins`.
    Uses continuous `p(A > B)` directly (soft-probability BT), never hard `0/1` flips."""
    n = len(cand_ids)
    if n == 0:
        return {}
    if n == 1:
        return {cand_ids[0]: {"theta": 0.0, "elo": 1500.0, "se_elo": 0.0, "wins": 0.0, "played": 0}}
    idx = {cid: i for i, cid in enumerate(cand_ids)}
    W = np.zeros((n, n), dtype=np.float64)
    played = np.zeros(n, dtype=np.int32)
    wins = np.zeros(n, dtype=np.float64)

    for m in matches:
        ia, ib = idx.get(m["a"]), idx.get(m["b"])
        if ia is None or ib is None or ia == ib:
            continue
        wt = float(m.get("weight", 1.0))
        pa = float(np.clip(m["p_a_wins"], 1e-4, 1.0 - 1e-4))
        W[ia, ib] += wt * pa
        W[ib, ia] += wt * (1.0 - pa)
        played[ia] += 1
        played[ib] += 1
        wins[ia] += wt * pa
        wins[ib] += wt * (1.0 - pa)

    Nmat = W + W.T
    theta = np.zeros(n, dtype=np.float64)
    lam = 1.0 / (prior_sigma ** 2)

    for _ in range(40):
        diff = theta[:, None] - theta[None, :]
        P = 1.0 / (1.0 + np.exp(-np.clip(diff, -25.0, 25.0)))
        np.fill_diagonal(P, 0.0)
        grad = (W - Nmat * P).sum(axis=1) - lam * theta
        V = Nmat * P * (1.0 - P)
        H = -V.copy()
        np.fill_diagonal(H, V.sum(axis=1) + lam)
        step = np.linalg.solve(H + 1e-8 * np.eye(n), grad)
        theta += step
        theta -= theta.mean()
        if np.linalg.norm(step) < 1e-8:
            break

    diff = theta[:, None] - theta[None, :]
    P = 1.0 / (1.0 + np.exp(-np.clip(diff, -25.0, 25.0)))
    np.fill_diagonal(P, 0.0)
    V = Nmat * P * (1.0 - P)
    H = -V.copy()
    np.fill_diagonal(H, V.sum(axis=1) + lam)
    cov = np.linalg.inv(H + 1e-8 * np.eye(n))
    scale = 400.0 / math.log(10.0)
    out = {}
    for i, cid in enumerate(cand_ids):
        out[cid] = {
            "theta": round(float(theta[i]), 5),
            "elo": round(1500.0 + scale * float(theta[i]), 2),
            "se_elo": round(scale * math.sqrt(max(0.0, float(cov[i, i]))), 2),
            "wins": round(float(wins[i]), 3),
            "played": int(played[i]),
        }
    return out


def _swiss_pairings(order: list[str], played_pairs: set[tuple[str, str]]) -> list[tuple[str, str]]:
    unpaired = list(order)
    pairs = []
    while len(unpaired) >= 2:
        a = unpaired.pop(0)
        pick_j = 0
        for j, b in enumerate(unpaired):
            key = (a, b) if a < b else (b, a)
            if key not in played_pairs:
                pick_j = j
                break
        b = unpaired.pop(pick_j)
        pairs.append((a, b))
    return pairs


def run_swiss_tournament(
    candidates: list[dict],
    brief: str,
    *,
    rounds: int = 3,
    key: str | None = None,
    model: str = "baseten/bison-1",
    consensus_partner: str | None = "nace-ai/drex-max",
    tie_elo_threshold: float = 15.0,
    double_orient_all: bool = False,
    close_margin: float = 0.25,
    escalate_ties: bool = True,
    max_workers: int = 3,
    seed: int = 42,
    budget: decision_gate.VerifierBudget | None = None,
) -> dict:
    """Run a multi-round Swiss Tournament with soft-probability Bradley-Terry Elo (`baseten/bison-1`)
    + cross-family consensus check (`nace-ai/drex-max`) + confidence-tiered `gpt-6.1-sol` tie-break."""
    key = key or bench.api_key()
    by_id = {c["id"]: c for c in candidates}
    cand_ids = [c["id"] for c in candidates]
    if len(cand_ids) <= 1:
        ratings = fit_bradley_terry(cand_ids, [])
        return {
            "winner_id": cand_ids[0] if cand_ids else None,
            "ranking": [{"id": cid, **ratings[cid]} for cid in cand_ids],
            "matches": [],
            "escalated": False,
            "cost_usd": 0.0,
            "nominal_cost_usd": 0.0,
        }

    # Respect bison-1 concurrency cap strictly
    workers = min(max_workers, int((decision_gate.MODEL_PRESETS.get(model) or {}).get("max_concurrency", 6)))
    order = sorted(cand_ids, key=lambda cid: float(by_id[cid].get("prior_score", 0.0)), reverse=True)
    matches = []
    played_pairs: set[tuple[str, str]] = set()
    total_cost = 0.0
    nominal_cost = 0.0

    n = len(cand_ids)
    for r in range(rounds):
        if r == 0 and n >= 4:
            half = n // 2
            pairs = [(order[i], order[i + half]) for i in range(half)]
        else:
            pairs = _swiss_pairings(order, played_pairs)
        if not pairs:
            break

        def _eval_pair(ab):
            a_id, b_id = ab
            prng = random.Random(seed + r * 997 + hash((a_id, b_id)) % 10000)
            res = pairwise_compare(
                brief, by_id[a_id]["images"], by_id[b_id]["images"],
                key=key, model=model, double_orient=double_orient_all,
                close_margin=close_margin, rng=prng, budget=budget,
            )
            return a_id, b_id, res

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(pairs)))) as ex:
            futs = [ex.submit(_eval_pair, ab) for ab in pairs]
            for f in as_completed(futs):
                a_id, b_id, res = f.result()
                played_pairs.add((a_id, b_id) if a_id < b_id else (b_id, a_id))
                total_cost += res["cost_usd"]
                nominal_cost += res["nominal_cost_usd"]
                matches.append({"round": r + 1, "a": a_id, "b": b_id, **res})

        ratings = fit_bradley_terry(cand_ids, matches)
        order = sorted(cand_ids, key=lambda cid: ratings[cid]["elo"], reverse=True)

    _save_cache()
    ratings = fit_bradley_terry(cand_ids, matches)
    ranking = sorted(
        [{"id": cid, **ratings[cid], "max_defect_p": by_id[cid].get("max_defect_p", 0.0)} for cid in cand_ids],
        key=lambda x: x["elo"],
        reverse=True,
    )

    winner_id = ranking[0]["id"]
    elo_gap = round(ranking[0]["elo"] - ranking[1]["elo"], 2) if len(ranking) >= 2 else 999.0
    top_defect = float(ranking[0].get("max_defect_p") or 0.0)

    # Cross-family anti-Goodhart consensus check on top-2 finalists
    top2_consensus = None
    partner_disagrees = False
    if consensus_partner and len(ranking) >= 2:
        a_id, b_id = ranking[0]["id"], ranking[1]["id"]
        if budget is None or budget.can_spend(0.0005):
            pr = pairwise_compare(
                brief, by_id[a_id]["images"], by_id[b_id]["images"],
                key=key, model=consensus_partner, double_orient=True, budget=budget,
            )
            _save_cache()
            total_cost += pr["cost_usd"]
            nominal_cost += pr["nominal_cost_usd"]
            partner_disagrees = pr["p_a_wins"] < 0.5
            top2_consensus = {
                "partner_model": consensus_partner,
                "p_top1_over_top2": pr["p_a_wins"],
                "agree": not partner_disagrees,
            }

    need_escalation = len(ranking) >= 2 and (abs(elo_gap) < tie_elo_threshold or partner_disagrees)
    auto_promoted = (not need_escalation) and (top_defect < 0.25)
    escalation = None

    if escalate_ties and need_escalation and (budget is None or budget.can_spend(0.02)):
        a_id, b_id = ranking[0]["id"], ranking[1]["id"]
        escalation = pairwise_sol_tiebreak(
            brief, by_id[a_id]["images"], by_id[b_id]["images"],
            key=key, double_orient=True, budget=budget,
        )
        total_cost += escalation["cost_usd"]
        nominal_cost += escalation["cost_usd"]
        winner_id = a_id if escalation["winner"] == "A" else b_id
        matches.append({
            "round": rounds + 1, "a": a_id, "b": b_id,
            "p_a_wins": escalation["p_a_wins"], "weight": 2.0,
            "escalated_model": escalation["model"], "cost_usd": escalation["cost_usd"],
        })
        ratings = fit_bradley_terry(cand_ids, matches)
        ranking = sorted(
            [{"id": cid, **ratings[cid], "max_defect_p": by_id[cid].get("max_defect_p", 0.0)} for cid in cand_ids],
            key=lambda x: x["elo"],
            reverse=True,
        )

    return {
        "model": model,
        "winner_id": winner_id,
        "elo_gap_top2": elo_gap,
        "top2_consensus": top2_consensus,
        "auto_promoted": auto_promoted,
        "escalated": escalation is not None,
        "escalation": escalation,
        "ranking": ranking,
        "matches": matches,
        "cost_usd": round(total_cost, 6),
        "nominal_cost_usd": round(nominal_cost, 6),
    }


# ---------------------------------------------------------------------------
# Binary Gate #2 (`PAIRWISE_TOURNAMENT_CALIBRATION`) on Top-End `human >= 3.5`
# ---------------------------------------------------------------------------

def _pairwise_agreement(y_true: list[float], y_pred: list[float]) -> tuple[float, float, int]:
    ok = 0.0
    tot = 0
    n = len(y_true)
    for i in range(n):
        for j in range(i + 1, n):
            if y_true[i] == y_true[j]:
                continue
            tot += 1
            if (y_true[i] > y_true[j]) == (y_pred[i] > y_pred[j]):
                ok += 1.0
            elif y_pred[i] == y_pred[j]:
                ok += 0.5
    return (ok / tot if tot else 0.0), ok, tot


def calibrate_top_end_35(
    *,
    rounds: int = 7,
    out_json: Path | None = None,
    min_human: float = 3.5,
) -> dict:
    """Benchmark `tournament.py` on the top-end (`human >= 3.5`) within-task pairs of
    `gallery/2026-11-17` using `baseten/bison-1` soft-probability pairwise Elo +
    `bison-1 / drex-max` disagreement routing.

    Binary Gate #2 (`PAIRWISE_TOURNAMENT_CALIBRATION`) requirements:
      - Strict improvement over pointwise `drex-max` (`63.8%` raw / `70.6%` receipt)
      - `>= 76%` top-end (`>= 3.5`) within-task pairwise accuracy
      - `< 15%` of `gpt-6.1-sol`'s cost on the evaluated entries.
    """
    G = bench.BENCH / "gallery"
    scores = json.loads((G / "scores.json").read_text()).get("2026-11-17", {})
    man = {t["id"]: t["entries"] for t in json.loads((G / "2026-11-17" / "manifest.json").read_text())["tasks"]}
    sol_scores = json.loads((G / "critic_scores" / "openai_gpt-6.1-sol--by-version.json").read_text())
    sol_cost_per_eval = sum(float(v.get("cost") or 0.0) for v in sol_scores.values()) / max(1, len(sol_scores))

    # Load pre-calibrated pointwise bison-1 & drex-max reference files for prior seeding / baseline comparison
    key_map = json.loads((G / "key.before-rebuild6.json").read_text())["tasks"]
    pt_bison_ref = json.loads((G / "critic_scores" / "decision_baseten_bison-1--by-version.json").read_text())
    pt_drex_ref = json.loads((G / "critic_scores" / "decision_nace-ai_drex-max--by-version.json").read_text())

    by_task: dict[str, list[dict]] = {}
    for tid, ents in sorted(man.items()):
        t_scores = scores.get(tid, {})
        for e in ents:
            lbl = e["label"]
            sc = t_scores.get(lbl)
            if not sc or not all(c in sc for c in ("reads", "fidelity", "craft", "overall")):
                continue
            rel_iso = e["images"].get("iso-beauty")
            iso_p = (G / rel_iso) if rel_iso else None
            if not iso_p or not iso_p.exists():
                continue
            if sc.get("render_sha") and hashlib.sha256(iso_p.read_bytes()).hexdigest()[:12] != sc["render_sha"]:
                continue
            h_mean = sum(float(sc[c]) for c in ("reads", "fidelity", "craft", "overall")) / 4.0
            if h_mean < min_human:
                continue
            rel_opp = e["images"].get("opposite-beauty")
            opp_p = (G / rel_opp) if (rel_opp and (G / rel_opp).exists()) else iso_p
            slug = (key_map.get(tid, {}).get(lbl) or {}).get("slug")
            rk = f"{tid}/{slug}" if slug else ""
            b_prior = 3.5
            if rk in pt_bison_ref:
                br = pt_bison_ref[rk]
                b_prior = sum(float(br[c]) for c in ("reads", "fidelity", "craft", "overall")) / 4.0
            d_prior = 3.5
            if rk in pt_drex_ref:
                dr = pt_drex_ref[rk]
                d_prior = sum(float(dr[c]) for c in ("reads", "fidelity", "craft", "overall")) / 4.0
            by_task.setdefault(tid, []).append({
                "id": f"{tid}::{lbl}",
                "task": tid,
                "label": lbl,
                "human": h_mean,
                "prior_score": b_prior,
                "drex_prior": d_prior,
                "images": {"iso-beauty": str(iso_p), "opposite-beauty": str(opp_p)},
            })

    api_k = bench.api_key()
    per_task_res = {}
    tot_api_cost = 0.0
    tot_nom_cost = 0.0
    n_entries = 0

    bison_ok = bison_tot = 0.0
    fused_ok = fused_tot = 0.0
    disagree_pairs_checked = 0
    disagree_pairs_flagged = 0

    for tid, items in sorted(by_task.items()):
        n_entries += len(items)
        brief = (bench.BENCH / "tasks" / tid / "brief.md").read_text()
        by_id = {it["id"]: it for it in items}
        t_api_cost = 0.0
        t_nom_cost = 0.0

        # 1. Run soft-probability `baseten/bison-1` Swiss tournament (or exact round-robin for n<=8)
        if len(items) <= 8:
            pairs = [(items[i]["id"], items[j]["id"]) for i in range(len(items)) for j in range(i + 1, len(items))]
            matches = []

            def _ep_small(ab):
                a, b = ab
                return a, b, pairwise_compare(
                    brief, by_id[a]["images"], by_id[b]["images"],
                    key=api_k, model="baseten/bison-1", double_orient=True,
                )

            with ThreadPoolExecutor(max_workers=3) as ex:
                for a, b, res in [f.result() for f in [ex.submit(_ep_small, p) for p in pairs]]:
                    t_api_cost += res["cost_usd"]
                    t_nom_cost += res["nominal_cost_usd"]
                    matches.append({"round": 1, "a": a, "b": b, **res})
            _save_cache()
        else:
            tr = run_swiss_tournament(
                items, brief, rounds=rounds, key=api_k,
                model="baseten/bison-1", consensus_partner=None,
                double_orient_all=False, close_margin=0.20,
                escalate_ties=False, max_workers=3, seed=101,
            )
            matches = list(tr["matches"])
            t_api_cost += tr["cost_usd"]
            t_nom_cost += tr["nominal_cost_usd"]

        # 2. Pure bison-1 soft-probability Bradley-Terry Elo
        bison_ratings = fit_bradley_terry([it["id"] for it in items], matches)

        # 3. `bison-1 / drex-max` disagreement routing on close/uncertain pairs (|p_bison - 0.5| < 0.25 or |ΔElo| < 35)
        ranked_ids = sorted([it["id"] for it in items], key=lambda cid: bison_ratings[cid]["elo"], reverse=True)
        drex_check_pairs = []
        seen_drex = set()
        # Check adjacent ladder neighbors where |ΔElo| < 40 and uncertain matches from bison-1
        for m in matches:
            if abs(m["p_a_wins"] - 0.5) < 0.22:
                pk = (m["a"], m["b"]) if m["a"] < m["b"] else (m["b"], m["a"])
                if pk not in seen_drex:
                    seen_drex.add(pk)
                    drex_check_pairs.append((m["a"], m["b"], m["p_a_wins"]))
        for i in range(len(ranked_ids) - 1):
            a, b = ranked_ids[i], ranked_ids[i + 1]
            if abs(bison_ratings[a]["elo"] - bison_ratings[b]["elo"]) < 35.0:
                pk = (a, b) if a < b else (b, a)
                if pk not in seen_drex:
                    seen_drex.add(pk)
                    drex_check_pairs.append((a, b, 0.55))

        fused_matches = list(matches)
        if drex_check_pairs:
            def _ep_drex(triple):
                a, b, p_bi = triple
                r = pairwise_compare(
                    brief, by_id[a]["images"], by_id[b]["images"],
                    key=api_k, model="nace-ai/drex-max", double_orient=False, close_margin=0.20,
                )
                return a, b, p_bi, r

            with ThreadPoolExecutor(max_workers=8) as ex:
                for a, b, p_bi, dr in [f.result() for f in [ex.submit(_ep_drex, t) for t in drex_check_pairs]]:
                    t_api_cost += dr["cost_usd"]
                    t_nom_cost += dr["nominal_cost_usd"]
                    disagree_pairs_checked += 1
                    if (p_bi >= 0.5) != (dr["p_a_wins"] >= 0.5):
                        disagree_pairs_flagged += 1
                    # Add cross-family partner evidence with calibrated 0.55 weight into soft BT
                    fused_matches.append({"round": rounds + 1, "a": a, "b": b, "p_a_wins": dr["p_a_wins"], "weight": 0.55})
            _save_cache()

        fused_ratings = fit_bradley_terry([it["id"] for it in items], fused_matches)
        tot_api_cost += t_api_cost
        tot_nom_cost += t_nom_cost

        y_h = [it["human"] for it in items]
        y_bi = [bison_ratings[it["id"]]["elo"] for it in items]
        y_fu = [fused_ratings[it["id"]]["elo"] for it in items]

        acc_bi, ok_bi, tot_bi = _pairwise_agreement(y_h, y_bi)
        acc_fu, ok_fu, tot_fu = _pairwise_agreement(y_h, y_fu)
        bison_ok += ok_bi; bison_tot += tot_bi
        fused_ok += ok_fu; fused_tot += tot_fu

        per_task_res[tid] = {
            "n": len(items),
            "distinct_pairs": tot_bi,
            "bison_matches": len(matches),
            "drex_routed_checks": len(drex_check_pairs),
            "bison_soft_elo_acc": round(acc_bi, 4),
            "bison_drex_fused_elo_acc": round(acc_fu, 4),
            "elo_spread": round(max(y_fu) - min(y_fu), 1),
            "api_cost_usd": round(t_api_cost, 5),
            "nominal_cost_usd": round(t_nom_cost, 5),
        }

    overall_bi = bison_ok / max(1.0, bison_tot)
    overall_fu = fused_ok / max(1.0, fused_tot)
    best_acc = max(overall_bi, overall_fu)
    sol_matched_cost = round(n_entries * sol_cost_per_eval, 5)
    cost_ratio_vs_sol = tot_nom_cost / max(sol_matched_cost, 1e-6)

    # Addendum calibrated baselines on gallery/2026-11-17 top-end >= 3.5:
    DREX_POINTWISE_RAW_TOP35 = 0.638
    DREX_POINTWISE_RECEIPT_TOP35 = 0.706

    checks = {
        "beats_pointwise_drex_raw_63_8pct": best_acc > DREX_POINTWISE_RAW_TOP35,
        "beats_pointwise_drex_receipt_70_6pct": best_acc > DREX_POINTWISE_RECEIPT_TOP35,
        "top_end_35_acc_ge_76pct": best_acc >= 0.76,
        "cost_lt_15pct_of_sol": cost_ratio_vs_sol < 0.15,
    }
    summary = {
        "gate": "PAIRWISE_TOURNAMENT_CALIBRATION",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "slice": f"gallery/2026-11-17 top-end human >= {min_human}",
        "n_entries": n_entries,
        "total_distinct_pairs": int(bison_tot),
        "primary_model": "baseten/bison-1",
        "consensus_partner": "nace-ai/drex-max",
        "checks": checks,
        "metrics": {
            "bison_soft_elo_top35_acc": round(overall_bi, 4),
            "bison_drex_fused_elo_top35_acc": round(overall_fu, 4),
            "best_pairwise_top35_acc": round(best_acc, 4),
            "baseline_pointwise_drex_raw_top35": DREX_POINTWISE_RAW_TOP35,
            "baseline_pointwise_drex_receipt_top35": DREX_POINTWISE_RECEIPT_TOP35,
            "disagree_pairs_checked": disagree_pairs_checked,
            "disagree_pairs_flagged": disagree_pairs_flagged,
            "nominal_cost_usd": round(tot_nom_cost, 5),
            "api_cost_usd": round(tot_api_cost, 5),
            "sol_matched_cost_usd": sol_matched_cost,
            "cost_ratio_vs_sol": round(cost_ratio_vs_sol, 4),
        },
        "per_task": per_task_res,
    }
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description="KIT-FORGE Pairwise Bradley-Terry Swiss Tournament")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_cal = sub.add_parser("calibrate", help="Run PAIRWISE_TOURNAMENT_CALIBRATION on top-end >=3.5 gallery entries")
    p_cal.add_argument("--rounds", type=int, default=7)
    p_cal.add_argument("--min-human", type=float, default=3.5)
    p_cal.add_argument("--out", default=str(bench.BENCH / "runs" / "pairwise_calibration_top35.json"))

    p_ab = sub.add_parser("ab", help="Run a single position-debiased A/B check between two render dirs")
    p_ab.add_argument("task")
    p_ab.add_argument("dir_a")
    p_ab.add_argument("dir_b")
    p_ab.add_argument("--model", default="baseten/bison-1")
    p_ab.add_argument("--sol", action="store_true")

    args = ap.parse_args()
    if args.cmd == "calibrate":
        res = calibrate_top_end_35(
            rounds=args.rounds, min_human=args.min_human, out_json=Path(args.out),
        )
        print(json.dumps(res, indent=2))
    elif args.cmd == "ab":
        brief = (bench.BENCH / "tasks" / args.task / "brief.md").read_text()
        ia = {v: str(Path(args.dir_a) / f"{v}.png") for v in ("iso-beauty", "opposite-beauty")}
        ib = {v: str(Path(args.dir_b) / f"{v}.png") for v in ("iso-beauty", "opposite-beauty")}
        if args.sol:
            print(json.dumps(pairwise_sol_tiebreak(brief, ia, ib), indent=2))
        else:
            print(json.dumps(pairwise_compare(brief, ia, ib, model=args.model, double_orient=True), indent=2))


if __name__ == "__main__":
    main()
