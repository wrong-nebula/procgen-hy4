#!/usr/bin/env python3
"""Evaluate procedural kit renders with System One Decision models on OpenRouter
(`POST https://openrouter.ai/api/alpha/decisions`).

Two roles:
1. Fast calibrated visual QA gate (`gate_renders`): checks reusable architectural/mechanical
   sanity predicates (`opening_blocked`, `text_clipped_or_low`, `floating_or_detached`,
   `missing_brief_parts`) plus 1..5 rubric `score` distributions in a single ~300 ms forward pass
   (~$0.0002-$0.0005 per call, no prose generation). With `typesafe/jev-1.14`, can also localise
   the offending defect with a `box` query.
2. Repair loop (`--repair <task> <slug>`): runs the decision gate on a finished build; if any
   defect predicate triggers above threshold, synthesises a deterministic repair prompt from the
   triggered predicates (and bounding box if present), runs a short `hy4-flash-xhigh` repair pass,
   re-renders, and re-verifies with the decision gate.

Usage:
  python3 harness/decision_gate.py compare iron-shopfront
  python3 harness/decision_gate.py check <task> <slug> [--model nace-ai/drex-max]
  python3 harness/decision_gate.py calibrate [--model nace-ai/drex-max]
"""
import base64
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODELS = [
    "baseten/bison-1",
    "baseten/groundhog-1",
    "nace-ai/drex-max",
    "typesafe/jev-1.14",
    "liquid/d1.2-ultra",
    "perplexity/pplx-decider-v1.3:free",
    "google/gemini-decision-1",
]

MODEL_PRESETS = {
    "baseten/bison-1": {
        "max_concurrency": 3,
        "role": "finalist_and_router",
        "est_cost_usd": 0.00044,
        "context_tokens": 33000,
        "notes": "#1 raw visual judge (81.1% all / 79.2% in-task / 69.4% top-end >=3.5); cap concurrency=3 + 429 retry.",
    },
    "baseten/groundhog-1": {
        "max_concurrency": 8,
        "role": "tier_s1_within_task_screener",
        "est_cost_usd": 0.00015,
        "notes": "#1 cost/speed within-task screener (77.8% in-task / 68.7% top-end >=3.5); rank within-task only.",
    },
    "nace-ai/drex-max": {
        "max_concurrency": 8,
        "role": "tier_s2_anti_goodhart_partner",
        "est_cost_usd": 0.00022,
        "notes": "Cross-family anti-Goodhart consensus partner (85.7% accuracy with groundhog-1 agreement, 84.8% with bison-1).",
    },
    "typesafe/jev-1.14": {
        "max_concurrency": 6,
        "role": "bbox_localizer",
        "est_cost_usd": 0.00030,
        "notes": "2D normalized bounding-box defect localizer paired with `pgm pick`.",
    },
}

_SEM_LOCK = threading.Lock()
_MODEL_SEMS: dict[str, threading.Semaphore] = {}


def get_model_semaphore(model: str) -> threading.Semaphore:
    with _SEM_LOCK:
        if model not in _MODEL_SEMS:
            cap = int((MODEL_PRESETS.get(model) or {}).get("max_concurrency", 6))
            _MODEL_SEMS[model] = threading.Semaphore(cap)
        return _MODEL_SEMS[model]


class VerifierBudget:
    """Thread-safe verifier spend tracker enforcing a hard <= $0.05 cap per optimized asset."""

    def __init__(self, cap_usd: float = 0.05):
        self.cap_usd = float(cap_usd)
        self._lock = threading.Lock()
        self.by_model: dict[str, float] = {}
        self.calls_by_model: dict[str, int] = {}

    @property
    def total_usd(self) -> float:
        with self._lock:
            return round(sum(self.by_model.values()), 6)

    @property
    def remaining_usd(self) -> float:
        return round(max(0.0, self.cap_usd - self.total_usd), 6)

    def can_spend(self, est_usd: float) -> bool:
        with self._lock:
            return (sum(self.by_model.values()) + float(est_usd)) <= (self.cap_usd + 1e-9)

    def record(self, model: str, cost_usd: float):
        c = max(0.0, float(cost_usd or 0.0))
        with self._lock:
            self.by_model[model] = round(self.by_model.get(model, 0.0) + c, 6)
            self.calls_by_model[model] = self.calls_by_model.get(model, 0) + 1

    def to_dict(self) -> dict:
        with self._lock:
            tot = round(sum(self.by_model.values()), 6)
            return {
                "cap_usd": self.cap_usd,
                "total_usd": tot,
                "remaining_usd": round(max(0.0, self.cap_usd - tot), 6),
                "within_cap": tot <= (self.cap_usd + 1e-9),
                "by_model": dict(self.by_model),
                "calls_by_model": dict(self.calls_by_model),
            }


def post_decisions(body: dict, *, key: str | None = None, timeout: int = 90, max_retries: int = 5) -> tuple[dict | None, float, str | None]:
    """Rate-limit-safe POST to `/api/alpha/decisions` respecting per-model concurrency semaphores
    (`baseten/bison-1` <= 3, `baseten/groundhog-1` <= 8) with jittered exponential backoff on HTTP 429/5xx."""
    key = key or bench.api_key()
    model = str(body.get("model") or "")
    sem = get_model_semaphore(model)
    payload = json.dumps(body).encode()
    t0 = time.perf_counter()
    last_err = None
    for attempt in range(max_retries):
        try:
            with sem:
                req = urllib.request.Request(
                    DECISIONS_URL,
                    data=payload,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = json.loads(resp.read())
            return raw, round((time.perf_counter() - t0) * 1000, 1), None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="ignore")[:300]
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < max_retries:
                backoff = min(20.0, (1.25 * (2 ** attempt)) + random.uniform(0.15, 0.85))
                time.sleep(backoff)
                continue
            return None, round((time.perf_counter() - t0) * 1000, 1), last_err
        except Exception as e:
            last_err = repr(e)[:300]
            if attempt + 1 < max_retries:
                time.sleep(min(10.0, 1.0 * (attempt + 1) + random.uniform(0.1, 0.5)))
                continue
            return None, round((time.perf_counter() - t0) * 1000, 1), last_err
    return None, round((time.perf_counter() - t0) * 1000, 1), last_err

# Reusable defect predicates (not task-specific; asked against the brief + renders).
DEFECT_QUESTIONS = {
    "opening_blocked": {
        "type": "noul",
        "instructions": (
            "Look closely at any doorway, recessed entrance, arch opening, bore or walkable passage "
            "described in the brief. Is the clear walk-through or through-bore obstructed by an "
            "unintended bar, mullion, sill, transom rail, cross-piece or solid wall crossing in front of it?"
        ),
        "criteria": {
            "true": "A bar, rail, sill, mullion or wall crosses and blocks an entrance doorway, arch or open bore that should be clear to walk or flow through.",
            "false": "Every doorway, recessed entrance, arch and open bore is clear and unobstructed.",
        },
        "fix": "An entrance doorway, passage or through-bore is obstructed by a bar, sill, mullion or cross-rail running across the clear opening. Split or interrupt horizontal sills, rails and glazing bars at the entrance jambs so the walkable doorway/opening is completely clear from floor to head.",
    },
    "text_clipped_or_low": {
        "type": "noul",
        "instructions": (
            "If the brief asks for signage, fascia lettering, inscriptions or a house number plaque, "
            "is the text illegible, missing, clipped by a border, or sitting too low against the bottom rail?"
        ),
        "criteria": {
            "true": "Required lettering or house number is missing, tiny/illegible, touches or clips the panel frame, or sits noticeably below vertical centre.",
            "false": "All required lettering and numbers are legible, clear of frame edges, and well centred on their panels (or the brief asks for no text).",
        },
        "fix": "Signage or house-number lettering is illegible, clipped, or positioned too low on its board/plaque (remember `G.text` anchors at vertical centre, NOT baseline; or use `kittype.inscribe` / `kittype.number_plaque`). Make the lettering clearly legible and vertically centred.",
    },
    "broken_or_floating_parts": {
        "type": "noul",
        "instructions": "Do the renders show visibly floating/disconnected pieces, stray spikes, open roofless side boxes, or broken intersecting geometry?",
        "criteria": {
            "true": "Parts visibly float in mid-air, collide awkwardly, or leave unfinished open-topped cavities.",
            "false": "All parts connect cleanly as coherent architectural or mechanical construction.",
        },
        "fix": "Some geometry appears floating, detached, or open-topped/unfinished; ensure every sub-assembly connects cleanly and encloses properly.",
    },
    "unattached_supports": {
        "type": "noul",
        "instructions": (
            "Look closely at all brackets, diagonal struts, corbels, consoles, wall shoes, and posts in the "
            "front-clay and iso-clay views. Are any support brackets or wall shoes hanging out past a building "
            "corner in empty air with their lower/bearing end attached to nothing instead of bearing on a wall or pier?"
        ),
        "criteria": {
            "true": "At least one bracket, diagonal strut, or mounting shoe hangs in thin air beyond the wall face with its foot attached to nothing.",
            "false": "Every bracket, strut, corbel, and column bears solidly onto a wall, pier, beam, or ground.",
        },
        "fix": "Support brackets, diagonal struts, or wall shoes hang in empty air past the building wall corners with their lower bearing ends attached to nothing. Clamp every wall-mounted bracket and shoe coordinate strictly inside the actual supporting wall span (`[-X, +X]` / `[-Y, +Y]`) so every bracket bears solidly on masonry or framing.",
    },
    "wall_or_jamb_gaps": {
        "type": "noul",
        "instructions": (
            "Look at the junctions between pilasters/columns, recessed entrance return walls, transoms, and roofs "
            "in both iso-beauty and opposite-beauty. Are there unintended open vertical slits or missing closure "
            "panels between adjacent walls, pilasters, and entrance returns where weather or daylight leaks straight through?"
        ),
        "criteria": {
            "true": "Open vertical or horizontal air gaps/slits appear between pilasters and recessed entrance return walls or over transoms where solid framing or glazing should close the envelope.",
            "false": "All pilasters, recessed return walls, jambs, and transoms meet tightly without unintended air gaps.",
        },
        "fix": "Unintended open gaps appear between pilasters/columns and the recessed entrance return walls or transom head (e.g. moving inner pilasters outward without widening or bridging the return walls and front transom). Make pilasters, jamb closures, return walls, and transom lights meet tightly with zero daylight gaps.",
    },
}

SCORE_LEVELS = [
    "1 - Poor: fails to read as the object, broken or missing core parts",
    "2 - Weak: recognisable attempt but major proportion, opening or construction errors",
    "3 - Competent: complete and functional, plain detail or noticeable minor flaws",
    "4 - Strong: faithful proportions, clean construction and convincing period detail",
    "5 - Exceptional: competition-winning craft, complete front and back with no visual flaws",
]

RUBRIC_QUESTIONS = {
    "reads": {
        "type": "score",
        "instructions": "At a glance from the isometric views, how clearly does this 3D model read as the object in the brief?",
        "criteria": SCORE_LEVELS,
    },
    "fidelity": {
        "type": "score",
        "instructions": "How faithfully does the model include every required part, clear opening, sign and proportion in the brief without obstructing functional openings?",
        "criteria": SCORE_LEVELS,
    },
    "craft": {
        "type": "score",
        "instructions": "Rate the architectural/mechanical craft, proportions, surface detail, material use and absence of artefacts.",
        "criteria": SCORE_LEVELS,
    },
    "overall": {
        "type": "score",
        "instructions": "Overall competition score for this procedural 3D kit module against the brief.",
        "criteria": SCORE_LEVELS,
    },
}


def encode_png(path: Path, max_w: int = 800) -> str:
    """Downscale large 3.7 MB PNG renders to crisp ~55 KB JPEGs so Decision API payloads stay
    under 300 KB (40x faster upload, well inside d1.2-ultra's 4.5 MB and drex-max's 13.6 MB caps)."""
    import io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def render_6view_grid(pgm_bin: str | Path, doc_path: str | Path, out_png: str | Path) -> Path | None:
    """Render a 3x2 6-view oblique + orthographic composite RGB grid (`iso`, `opposite`, `front ortho`,
    `back ortho`, `side ortho`, `iso clay`) so back elevations, soffits and roof valleys have zero blind spots."""
    import subprocess
    import tempfile
    from PIL import Image, ImageDraw

    views = [
        ("iso (+x,-y,+z)", ["--view", "iso", "--pass", "beauty"]),
        ("opposite (-x,+y,+z)", ["--view", "opposite", "--pass", "beauty"]),
        ("front ortho (-y)", ["--view", "front", "--projection", "ortho", "--pass", "beauty"]),
        ("back ortho (+y)", ["--yaw", "180", "--pitch", "0", "--projection", "ortho", "--pass", "beauty"]),
        ("side ortho (+x)", ["--view", "side", "--projection", "ortho", "--pass", "beauty"]),
        ("iso clay", ["--view", "iso", "--pass", "clay"]),
    ]
    tiles = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (label, vargs) in enumerate(views):
            p = Path(tmp) / f"v{i}.png"
            subprocess.run([str(pgm_bin), "render", str(doc_path), "--out", str(p), "--size", "480x360", *vargs],
                           capture_output=True, timeout=60)
            im = Image.open(p).convert("RGB") if p.exists() else Image.new("RGB", (480, 360), (42, 42, 42))
            tiles.append((label, im))
    grid = Image.new("RGB", (480 * 3, 360 * 2), (24, 24, 24))
    dr = ImageDraw.Draw(grid)
    for i, (label, im) in enumerate(tiles):
        x, y = 480 * (i % 3), 360 * (i // 3)
        grid.paste(im, (x, y))
        dr.rectangle([x, y, x + 170, y + 20], fill=(0, 0, 0))
        dr.text((x + 6, y + 4), label, fill=(240, 240, 240))
    out_p = Path(out_png)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out_p)
    return out_p


def localize_defect_parts(pgm_bin: str | Path, doc_path: str | Path, box_ans: dict, view: str = "iso") -> list[dict]:
    """Back-project a 2D `jev-1.14` defect box onto pre-bake 3D authoring parts via `pgm pick`."""
    import subprocess
    b = (box_ans or {}).get("box") or {}
    if not all(k in b for k in ("left", "top", "right", "bottom")) or not Path(doc_path).exists():
        return []
    box_str = f"{b['left']:.4f},{b['top']:.4f},{b['right']:.4f},{b['bottom']:.4f}"
    r = subprocess.run([str(pgm_bin), "pick", str(doc_path), "--view", view, "--box", box_str, "--coord", "norm"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        return []
    try:
        return (json.loads(r.stdout.strip().splitlines()[-1]).get("parts") or [])[:6]
    except Exception:
        return []


def call_decisions(model: str, brief: str, image_paths: dict, *, include_box: bool = False,
                   include_checklist: bool = False, key: str = None, budget: VerifierBudget | None = None):
    key = key or bench.api_key()
    state = [{"type": "text", "text": "MODELING BRIEF:\n" + brief.strip() + "\n\nRENDERS OF THE BUILT 3D MODULE:"}]
    # Most decision models cap at 4 images per request: use 6view-grid + 3 hero views if present, else the 4 standard views.
    view_order = ("6view-grid", "iso-beauty", "opposite-beauty", "front-clay") if "6view-grid" in image_paths else (
        "iso-beauty", "opposite-beauty", "front-clay", "iso-clay"
    )
    for view in view_order:
        p = image_paths.get(view)
        if p and Path(p).exists():
            state.append({"type": "text", "text": f"View: {view}"})
            state.append({"type": "image_url", "image_url": {"url": encode_png(Path(p), max_w=1024 if view == "6view-grid" else 800)}})

    questions = {}
    for k, q in DEFECT_QUESTIONS.items():
        questions[k] = {fk: fv for fk, fv in q.items() if fk != "fix"}
    questions.update(RUBRIC_QUESTIONS)
    checklist_keys = []
    if include_checklist:
        import calibrate
        for cid, req_text in calibrate.extract_brief_checklist(brief, max_items=5):
            qk = f"brief_{cid}"
            checklist_keys.append((qk, req_text))
            questions[qk] = {
                "type": "noul",
                "instructions": f"Does the rendered 3D module visibly satisfy this brief requirement: {req_text}",
                "criteria": {
                    "true": f"Yes, the renders clearly satisfy: {req_text}",
                    "false": f"No, this requirement is missing or visibly violated: {req_text}",
                },
            }
    if include_box:
        questions["blocked_opening_box"] = {
            "type": "box",
            "instructions": "Locate any bar, rail, overhanging unattached bracket, or visual defect in the iso-beauty render.",
        }

    body = {"model": model, "state": state, "questions": questions}
    raw, ms, err = post_decisions(body, key=key, timeout=90)
    if err or raw is None:
        return {"error": err or "empty_response", "ms": ms}
    parsed = parse_reply(raw, ms)
    if budget is not None:
        budget.record(model, float(parsed.get("cost") or 0.0))
    return parsed


# ---------------------------------------------------------------------------
# 3-Tier KIT-FORGE Search Verifier Stack (Tier-S1 / Tier-S2 / Tier-S3)
# ---------------------------------------------------------------------------

def tier_s1_screen(
    brief: str,
    image_paths: dict,
    *,
    local_judge_score: float | None = None,
    budget: VerifierBudget | None = None,
    key: str | None = None,
    model: str = "baseten/groundhog-1",
) -> dict:
    """Tier-S1 Dense Within-Task Screener (`baseten/groundhog-1`, $0.00015/eval, jobs=8)
    paired with $0 DINOv2 `local_judge_score` to evaluate brief-conditioned energy `E(z | brief)`.
    IMPORTANT: Use ONLY for within-task ranking (never compare raw groundhog-1 across tasks)."""
    est = float((MODEL_PRESETS.get(model) or {}).get("est_cost_usd", 0.00015))
    if budget is not None and not budget.can_spend(est):
        lj = float(local_judge_score) if local_judge_score is not None else 2.5
        return {
            "tier": "S1",
            "model": "local_judge_only_budget_cap",
            "brief_score": None,
            "local_judge_score": lj,
            "energy": round(lj, 4),
            "max_defect_p": 0.0,
            "cost_usd": 0.0,
            "budget_capped": True,
        }
    res = call_decisions(model, brief, image_paths, key=key, budget=budget)
    if "error" in res or res.get("mean_score") is None:
        lj = float(local_judge_score) if local_judge_score is not None else 2.5
        return {
            "tier": "S1",
            "model": model,
            "error": res.get("error"),
            "brief_score": None,
            "local_judge_score": lj,
            "energy": round(lj, 4),
            "max_defect_p": 0.0,
            "cost_usd": float(res.get("cost") or 0.0),
        }
    brief_s = float(res["mean_score"])
    defects = res.get("defects") or {}
    max_def = max((float(v) for v in defects.values() if isinstance(v, (int, float))), default=0.0)
    # Brief-conditioned energy E(z | brief): 70% brief-aware groundhog-1 + 30% $0 DINOv2 prior - defect penalty
    if local_judge_score is not None:
        energy = 0.70 * brief_s + 0.30 * float(local_judge_score) - 0.35 * max_def
    else:
        energy = brief_s - 0.35 * max_def
    return {
        "tier": "S1",
        "model": model,
        "brief_score": round(brief_s, 4),
        "local_judge_score": round(float(local_judge_score), 4) if local_judge_score is not None else None,
        "energy": round(energy, 4),
        "max_defect_p": round(max_def, 4),
        "defects": defects,
        "cost_usd": float(res.get("cost") or 0.0),
    }


def tier_s2_consensus_gate(
    brief: str,
    imgs_a: dict,
    imgs_b: dict,
    *,
    use_bison: bool = False,
    budget: VerifierBudget | None = None,
    key: str | None = None,
    double_orient: bool = True,
) -> dict:
    """Tier-S2 Two-Backbone Anti-Goodhart Consensus Gate:
    Pairs `baseten/groundhog-1` (or `baseten/bison-1` for finalists) with `nace-ai/drex-max`
    (distinct model families). Empirically 85.7% / 84.8% accuracy on agreement pairs; flags
    only the ~20% disagreement pairs as `uncertain` for Tier-S3 escalation."""
    import tournament
    primary_model = "baseten/bison-1" if use_bison else "baseten/groundhog-1"
    partner_model = "nace-ai/drex-max"
    est_cost = (0.00044 if use_bison else 0.00015) * (2 if double_orient else 1) + 0.00022 * (2 if double_orient else 1)
    if budget is not None and not budget.can_spend(est_cost):
        return {"tier": "S2", "agree": False, "winner": "uncertain", "budget_capped": True, "cost_usd": 0.0}

    r_prim = tournament.pairwise_compare(brief, imgs_a, imgs_b, key=key, model=primary_model,
                                         double_orient=double_orient, budget=budget)
    r_part = tournament.pairwise_compare(brief, imgs_a, imgs_b, key=key, model=partner_model,
                                         double_orient=double_orient, budget=budget)
    pa_1 = float(r_prim["p_a_wins"])
    pa_2 = float(r_part["p_a_wins"])
    w1 = "A" if pa_1 >= 0.5 else "B"
    w2 = "A" if pa_2 >= 0.5 else "B"
    agree = (w1 == w2)
    p_blend = round(0.60 * pa_1 + 0.40 * pa_2, 5)
    return {
        "tier": "S2",
        "primary_model": primary_model,
        "partner_model": partner_model,
        "p_primary": pa_1,
        "p_partner": pa_2,
        "p_blend": p_blend,
        "agree": agree,
        "winner": w1 if agree else "uncertain",
        "auto_accept": agree,
        "cost_usd": round(float(r_prim.get("cost_usd") or 0.0) + float(r_part.get("cost_usd") or 0.0), 6),
    }


def tier_s3_finalist_select(
    candidates: list[dict],
    brief: str,
    *,
    rounds: int = 3,
    budget: VerifierBudget | None = None,
    key: str | None = None,
    escalate_ties: bool = True,
) -> dict:
    """Tier-S3 Finalist Selection & Tie-Break:
    Runs `tournament.py` position-debiased `[A|B]` Swiss soft-probability Bradley-Terry Elo using
    `baseten/bison-1` + `nace-ai/drex-max` consensus check on top-2, escalating ONLY top-2 pairs
    where `bison-1` and `drex-max` disagree or `|ΔElo| < 15` to `openai/gpt-6.1-sol`."""
    import tournament
    return tournament.run_swiss_tournament(
        candidates, brief,
        rounds=rounds,
        key=key,
        model="baseten/bison-1",
        consensus_partner="nace-ai/drex-max",
        tie_elo_threshold=15.0,
        escalate_ties=escalate_ties,
        max_workers=3,
        budget=budget,
    )


def _noul_prob(ans: dict):
    if not isinstance(ans, dict) or ans.get("type") == "refusal":
        return None
    if "noul" in ans and isinstance(ans["noul"], (int, float)):
        return float(ans["noul"])
    if "probability" in ans and isinstance(ans["probability"], (int, float)):
        return float(ans["probability"])
    if isinstance(ans.get("decision"), bool) and isinstance(ans.get("confidence"), (int, float)):
        c = float(ans["confidence"])
        return c if ans["decision"] else 1.0 - c
    return None


def _score_1_to_5(ans: dict):
    """Convert 0..4 expected score (or discrete probabilities) onto the 1..5 rubric scale."""
    if not isinstance(ans, dict) or ans.get("type") == "refusal":
        return None
    probs = ans.get("probabilities")
    if isinstance(probs, dict) and probs:
        total_p = sum(float(v) for v in probs.values())
        if total_p > 0:
            exp0 = 0.0
            for idx, k in enumerate(SCORE_LEVELS):
                p = probs.get(str(idx), probs.get(k, 0.0))
                exp0 += idx * float(p)
            if exp0 == 0.0 and any(k not in ("0", SCORE_LEVELS[0]) for k in probs):
                # fallback if keys are arbitrary strings in order
                exp0 = sum(i * float(v) for i, v in enumerate(probs.values()))
            return round(1.0 + exp0 / total_p, 3)
    if isinstance(ans.get("score"), (int, float)):
        return round(1.0 + float(ans["score"]), 3)
    return None


def parse_reply(raw: dict, ms: float) -> dict:
    answers = raw.get("answers") or {}
    defects = {k: _noul_prob(answers.get(k)) for k in DEFECT_QUESTIONS}
    rubric = {k: _score_1_to_5(answers.get(k)) for k in RUBRIC_QUESTIONS}
    checklist = {k: _noul_prob(v) for k, v in answers.items() if k.startswith("brief_")}
    vals = [v for v in rubric.values() if v is not None]
    usage = raw.get("usage") or {}
    out = {
        "model": raw.get("model"),
        "ms": ms,
        "cost": usage.get("cost"),
        "usage": usage,
        "defects": defects,
        "rubric": rubric,
        "mean_score": round(sum(vals) / len(vals), 3) if vals else None,
        "raw_answers": answers,
    }
    if checklist:
        out["checklist"] = checklist
    if "blocked_opening_box" in answers:
        out["blocked_opening_box"] = answers["blocked_opening_box"]
    return out


def run_imgs(task: str, slug: str, date: str = "2026-11-17") -> dict:
    rdir = bench.BENCH / "runs" / date / task / slug / "renders"
    return {v: rdir / f"{v}.png" for v in ("iso-beauty", "iso-clay", "opposite-beauty", "front-clay", "parts")
            if (rdir / f"{v}.png").exists()}


def synthesize_fixes(res: dict, threshold: float = 0.50) -> list:
    fixes = []
    for k, p in (res.get("defects") or {}).items():
        if p is not None and p >= threshold:
            fixes.append((k, round(p, 3), DEFECT_QUESTIONS[k]["fix"]))
    return fixes


def cmd_compare(task: str, date: str = "2026-11-17"):
    brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
    tdir = bench.BENCH / "runs" / date / task
    slugs = sorted(d.name for d in tdir.iterdir() if (d / "renders" / "iso-beauty.png").exists())
    print(f"Comparing {len( DEFAULT_MODELS )} decision models on {task}: {slugs}\n")
    summary = {}
    for m in DEFAULT_MODELS:
        summary[m] = {}
        for slug in slugs:
            inc_box = "jev" in m
            r = call_decisions(m, brief, run_imgs(task, slug, date), include_box=inc_box)
            summary[m][slug] = r
            if "error" in r:
                print(f"  {m:36} | {slug:28} | ERROR ({r['ms']}ms): {r['error'][:140]}")
            else:
                d = r["defects"]
                box_str = f" box={r.get('blocked_opening_box')}" if "blocked_opening_box" in r else ""
                print(f"  {m:36} | {slug:28} | {r['ms']:6.0f}ms ${r['cost'] or 0:.5f} | "
                      f"mean={r['mean_score']} | blocked={d['opening_blocked']} "
                      f"text_bad={d['text_clipped_or_low']} broken={d['broken_or_floating_parts']}{box_str}")
        print()
    out = bench.BENCH / "runs" / f"decision-compare-{task}.json"
    out.write_text(json.dumps(summary, indent=1))
    print(f"saved {out}")


def draw_defect_box(src_png: Path, dst_png: Path, box_ans: dict):
    from PIL import Image, ImageDraw
    im = Image.open(src_png).convert("RGB")
    b = (box_ans or {}).get("box") or {}
    if all(k in b for k in ("left", "top", "right", "bottom")):
        w, h = im.size
        x0, y0 = int(b["left"] * w), int(b["top"] * h)
        x1, y1 = int(b["right"] * w), int(b["bottom"] * h)
        dr = ImageDraw.Draw(im)
        for pad in range(5):
            dr.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(235, 45, 45))
    im.save(dst_png)


def repair_run(task: str, seed_slug: str, out_slug: str = None, *,
               gate_model: str = "nace-ai/drex-max", box_model: str = "typesafe/jev-1.14",
               repair_base: str = "hy4-flash-xhigh", threshold: float = 0.60, max_turns: int = 55):
    """Spot visual/functional defects on `seed_slug` with Decision models, run a targeted
    `hy4-flash-xhigh` repair pass on the existing builder, and re-verify with the Decision gate."""
    import gzip
    import os
    import shutil

    os.environ["PGBENCH_DATE"] = bench.TODAY
    brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
    seed_dir = bench.BENCH / "runs" / bench.TODAY / task / seed_slug
    imgs0 = run_imgs(task, seed_slug, bench.TODAY)

    gate0 = call_decisions(gate_model, brief, imgs0)
    g_probs = gate0.get("defects") or {}
    need_box = box_model and any((g_probs.get(k) or 0) >= threshold for k in ("opening_blocked", "unattached_supports", "broken_or_floating_parts"))
    jev0 = call_decisions(box_model, brief, imgs0, include_box=True) if need_box else {}
    triggered = [(k, round(p, 3), DEFECT_QUESTIONS[k]["fix"])
                 for k, p in g_probs.items() if p is not None and p >= threshold]
    print(f"[{task}] pre-repair gate ({gate_model}): mean={gate0.get('mean_score')} defects={gate0.get('defects')}")
    if box_model and need_box:
        print(f"[{task}] pre-repair localiser ({box_model}): defects={jev0.get('defects')} box={jev0.get('blocked_opening_box')}")
    if not triggered:
        print(f"[{task}] {seed_slug} passed all decision defect gates (< {threshold}); no repair needed.")
        return {"repaired": False, "before": gate0}

    if out_slug is None:
        _, cond, rep, _ = bench.split_slug(seed_slug)
        out_slug = f"{repair_base}--{cond or 'H'}-r{(rep or 1) + 1}"

    box = bench.make_sandbox(task, out_slug)[0]
    shutil.copy2(seed_dir / "build.py", box / "recipes" / "build_bench_kit.py")
    shutil.copy2(seed_dir / "build.py", box / ".bench" / "last_valid.py")
    shutil.copy2(seed_dir / "build.py", box / ".bench" / "last_good.py")
    if (seed_dir / "NOTES.md").exists():
        shutil.copy2(seed_dir / "NOTES.md", box / "NOTES.md")

    spec, base_prompt = bench.load_task(task, out_slug)

    # Stage 0: Attempt $0.00 C++ / AST local geometric offset repair before spending LLM tokens!
    import local_repair
    lr_res = local_repair.try_local_repair(box, spec["module"], spec.get("contract"), box / "bin" / "pgm")
    if lr_res.get("patched"):
        print(f"[{task}] $0.00 local_repair patched {lr_res['applied']} ({lr_res['before_score']} -> {lr_res['after_score']})")
        shutil.copy2(box / "recipes" / "build_bench_kit.py", box / ".bench" / "last_valid.py")
        shutil.copy2(box / "recipes" / "build_bench_kit.py", box / ".bench" / "last_good.py")

    for v in ("iso-beauty", "opposite-beauty", "front-clay"):
        if v in imgs0:
            shutil.copy2(imgs0[v], box / "renders" / f"current-{v}.png")
    box_note = ""
    b_ans = jev0.get("blocked_opening_box")
    if b_ans and isinstance(b_ans.get("box"), dict) and "iso-beauty" in imgs0:
        draw_defect_box(imgs0["iso-beauty"], box / "renders" / "defect-highlighted.png", b_ans)
        bb = b_ans["box"]
        box_note = (
            f"\n- Visual localisation (`renders/defect-highlighted.png`): red box at "
            f"x={bb['left']:.2f}..{bb['right']:.2f}, y={bb['top']:.2f}..{bb['bottom']:.2f} on `current-iso-beauty.png`."
        )
        seed_doc = seed_dir / "out" / f"{spec['module']}.json"
        hit_parts = localize_defect_parts(box / "bin" / "pgm", seed_doc, b_ans, view="iso")
        if hit_parts:
            part_lines = ", ".join(
                f"`{p['source']}` ({p['material']}, bounds z={p['lo'][2]:.2f}..{p['hi'][2]:.2f}m)"
                for p in hit_parts[:4]
            )
            box_note += f"\n- Back-projected 3D authoring parts inside defect box (`pgm pick`): {part_lines}."
    bullets = "\n".join(f"- **{k}** (probability {p:.2f}): {fix}" for k, p, fix in triggered)
    repair_prompt = (
        f"REPAIR PASS ON EXISTING MODULE `{spec['module']}`\n\n"
        f"`recipes/build_bench_kit.py` is already written and passes `pgm check`, `pgm sweep` and `pgm audit`, "
        f"but automated visual QA flagged the following defect(s) in `renders/current-*.png`:\n\n"
        f"{bullets}{box_note}\n\n"
        f"Instructions:\n"
        f"1. Read `renders/current-iso-beauty.png` (and `renders/defect-highlighted.png` if present) plus `recipes/build_bench_kit.py`.\n"
        f"2. Make targeted surgical edits to `recipes/build_bench_kit.py` to fix every flagged defect while keeping the rest of the architecture intact.\n"
        f"3. Regenerate (`python3 recipes/build_bench_kit.py`), run `pgm check`, `pgm sweep`, and `pgm audit`, render new `iso` and `front` beauty views into `renders/`, and visually inspect them with Read to confirm the defect is completely gone.\n"
        f"4. Update `NOTES.md` briefly and stop.\n\n"
        f"--- ORIGINAL BRIEF FOR REFERENCE ---\n{(bench.BENCH / 'tasks' / task / 'brief.md').read_text()}"
    )

    orig_max = bench.MAX_TURNS
    bench.MAX_TURNS = max_turns
    try:
        print(f"[{task}] running repair -> {out_slug} (max_turns={max_turns})...")
        bench.run_agent(task, out_slug, box, repair_prompt)
        bench.collect(task, out_slug)
        rec = json.loads((bench.BENCH / "runs" / bench.TODAY / task / out_slug / "receipt.json").read_text())
    finally:
        bench.MAX_TURNS = orig_max
        if not os.environ.get("PGBENCH_KEEP_SANDBOX"):
            shutil.rmtree(box, ignore_errors=True)

    out_dir = bench.BENCH / "runs" / bench.TODAY / task / out_slug
    tr = out_dir / "transcript.jsonl"
    if tr.exists():
        (out_dir / "transcript.jsonl.gz").write_bytes(gzip.compress(tr.read_bytes()))
        tr.unlink()

    imgs1 = run_imgs(task, out_slug, bench.TODAY)
    gate1 = call_decisions(gate_model, brief, imgs1)
    jev1 = call_decisions(box_model, brief, imgs1, include_box=True) if box_model else {}
    print(f"[{task}] post-repair ({out_slug}): valid={rec.get('valid')} contract={(rec.get('contract') or {}).get('ok')} "
          f"cost=${rec.get('cost_usd_openrouter')}")
    print(f"[{task}] post-repair gate ({gate_model}): mean={gate1.get('mean_score')} defects={gate1.get('defects')}")
    if box_model:
        print(f"[{task}] post-repair localiser ({box_model}): defects={jev1.get('defects')}")

    dl = Path.home() / "Downloads" / "hy4-flash" / f"{task}-repaired"
    dl.mkdir(parents=True, exist_ok=True)
    for v in ("iso-beauty", "opposite-beauty", "front-clay", "parts"):
        if v in imgs1:
            shutil.copy2(imgs1[v], dl / f"{v}.png")
    report = {
        "repaired": True, "task": task, "seed_slug": seed_slug, "repaired_slug": out_slug,
        "triggered": triggered, "repair_cost_usd": rec.get("cost_usd_openrouter"),
        "valid": rec.get("valid"), "contract_ok": (rec.get("contract") or {}).get("ok"),
        "before": {"gate": gate0, "localiser": jev0},
        "after": {"gate": gate1, "localiser": jev1},
    }
    (out_dir / "decision_repair.json").write_text(json.dumps(report, indent=2))
    return report


def repair_loop(task: str, seed_slug: str, *, max_rounds: int = 2, **kw):
    """Run up to `max_rounds` decision-gated repair passes until all defect probabilities drop below threshold."""
    cur = seed_slug
    history = []
    for r in range(1, max_rounds + 1):
        res = repair_run(task, cur, **kw)
        history.append(res)
        if not res.get("repaired"):
            break
        cur = res["repaired_slug"]
        g_def = ((res.get("after") or {}).get("gate") or {}).get("defects") or {}
        thr = kw.get("threshold", 0.60)
        still_bad = any((g_def.get(k) or 0) >= thr for k in DEFECT_QUESTIONS)
        if not still_bad:
            break
    return {"final_slug": cur, "rounds": history}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if mode == "compare":
        cmd_compare(sys.argv[2] if len(sys.argv) > 2 else "iron-shopfront")
    elif mode == "check":
        task, slug = sys.argv[2], sys.argv[3]
        model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "nace-ai/drex-max"
        brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
        r = call_decisions(model, brief, run_imgs(task, slug), include_box=("jev" in model))
        print(json.dumps(r, indent=2))
    elif mode == "repair":
        task, seed = sys.argv[2], sys.argv[3]
        if "--out" in sys.argv:
            repair_run(task, seed, sys.argv[sys.argv.index("--out") + 1])
        else:
            repair_loop(task, seed)
