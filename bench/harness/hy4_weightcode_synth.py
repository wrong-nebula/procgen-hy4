#!/usr/bin/env python3
"""WeightCode synthesizer driven by free/ultra-cheap `tencent/hy4-flash` (xhigh reasoning).

Runs a WeightCode-style closed-loop synthesis session (default 5 checks) on local `dinov2_vits14`:
  - `hy4-flash` receives the layer-by-layer mechanistic profile + strict no-gradient WeightCode rules.
  - Each turn it emits a Python function `weight_program(model, X_tr, M_tr, y_tr, t_tr, helpers)`.
  - The harness evaluates the candidate under STRICT zero-leakage LOTO (3 human tasks),
    16-unseen-task transfer (48 entries + mitre-gate-jamb n=22), and in-task LOO retention.
  - Saves the best qualifying program to `harness/wsp_best_hy4.py` and logs `runs/hy4-weightcode-session.md`.
"""
import argparse
import gc
import json
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import bench             # noqa: E402
import calibrate         # noqa: E402
import weightcode_judge as wj  # noqa: E402

DEVICE = wj.DEVICE
MODEL_ID = "tencent/hy4-flash"


def make_helpers():
    """Memory-safe (batch=16) activation helpers passed to synthesized weight programs."""
    @torch.no_grad()
    def collect_late(model, X_tr, M_tr, block_ids, views=(0, 1, 2)):
        N = X_tr.shape[0]
        D = model.embed_dim
        fx = X_tr[:, list(views)].reshape(N * len(views), 3, 224, 224)
        fm = M_tr[:, list(views)].reshape(N * len(views))
        idx = torch.where(fm)[0]
        bset = set(block_ids)
        out = {
            "in_cls": {l: np.zeros((N * len(views), D), np.float32) for l in bset},
            "d_attn": {l: np.zeros((N * len(views), D), np.float32) for l in bset},
            "d_mlp":  {l: np.zeros((N * len(views), D), np.float32) for l in bset},
            "h_mlp":  {l: np.zeros((N * len(views), model.blocks[l].mlp.fc1.out_features), np.float32) for l in bset},
            "views":  len(views),
        }
        for k in range(0, len(idx), 16):
            c = idx[k:k + 16]
            x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
            cn = c.numpy()
            for i, blk in enumerate(model.blocks):
                if i in bset:
                    out["in_cls"][i][cn] = x[:, 0].float().cpu().numpy()
                ao = blk.ls1(blk.attn(blk.norm1(x)))
                x = x + ao
                xn2 = blk.norm2(x)
                h = F.gelu(blk.mlp.fc1(xn2))
                mo = blk.ls2(blk.mlp.fc2(h))
                x = x + mo
                if i in bset:
                    out["d_attn"][i][cn] = ao[:, 0].float().cpu().numpy()
                    out["d_mlp"][i][cn] = mo[:, 0].float().cpu().numpy()
                    out["h_mlp"][i][cn] = h[:, 0].float().cpu().numpy()
        if DEVICE == "mps":
            torch.mps.empty_cache()
        return out

    @torch.no_grad()
    def collect_head_cls(model, X_tr, M_tr, block_id, views=(0, 1, 2)):
        N = X_tr.shape[0]
        D = model.embed_dim
        blk_t = model.blocks[block_id]
        n_heads = blk_t.attn.num_heads
        head_dim = D // n_heads
        fx = X_tr[:, list(views)].reshape(N * len(views), 3, 224, 224)
        fm = M_tr[:, list(views)].reshape(N * len(views))
        idx = torch.where(fm)[0]
        out = np.zeros((N * len(views), n_heads, D), np.float32)
        for k in range(0, len(idx), 16):
            c = idx[k:k + 16]
            x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
            cn = c.numpy()
            for i in range(block_id):
                b = model.blocks[i]
                x = x + b.ls1(b.attn(b.norm1(x)))
                x = x + b.ls2(b.mlp(b.norm2(x)))
            xn = blk_t.norm1(x)
            B_c, T_c, _ = xn.shape
            qkv = blk_t.attn.qkv(xn).reshape(B_c, T_c, 3, n_heads, head_dim).permute(2, 0, 3, 1, 4)
            q, kk, vv = qkv[0], qkv[1], qkv[2]
            att = ((q * blk_t.attn.scale) @ kk.transpose(-2, -1)).softmax(dim=-1)
            ho = (att @ vv)[:, :, 0, :]
            Wp, g1 = blk_t.attn.proj.weight, blk_t.ls1.gamma
            for hh in range(n_heads):
                Wh = Wp[:, hh * head_dim:(hh + 1) * head_dim]
                out[cn, hh] = ((ho[:, hh, :] @ Wh.T) * g1).float().cpu().numpy()
        if DEVICE == "mps":
            torch.mps.empty_cache()
        return out.reshape(N, len(views), n_heads, D).mean(1)

    return {"collect_late": collect_late, "collect_head_cls": collect_head_cls, "DEVICE": DEVICE}


def call_hy4(messages, effort="none"):
    key = bench.api_key()
    body = {
        "model": MODEL_ID,
        "messages": messages,
        "max_tokens": 6000,
        "temperature": 0.3,
        "reasoning": {"enabled": False} if effort == "none" else {"effort": effort},
        "usage": {"include": True},
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    choice = resp["choices"][0]
    msg = choice["message"]
    text = msg.get("content") or ""
    usage = resp.get("usage") or {}
    return text, float(usage.get("cost") or 0.0), int(usage.get("total_tokens") or 0), choice.get("finish_reason")


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if not blocks:
        return ""
    for b in reversed(blocks):
        if "def weight_program" in b:
            return b.strip()
    return blocks[-1].strip()


SYSTEM_PROMPT = """You are a Weight-Space Programming (WeightCode, arXiv:2611.25476) engineer.
Your job is to write a closed-form, GRADIENT-FREE Python weight program `weight_program(model, X_tr, M_tr, y_tr, t_tr, helpers)` that directly edits the weights (`state_dict`) of `dinov2_vits14` (22.06M params, 12 blocks, embed_dim=384, 6 heads, mlp_hidden=1536, LayerScale ls1.gamma & ls2.gamma) so that its final L2-normalized CLS embedding transfers to UNSEEN 3D object types when used by a frozen ridge regression judge, while RETAINING high in-task LOO accuracy.

STRICT RULES:
1. NO gradient descent / backpropagation / `.backward()` / `torch.optim`. Pure closed-form math (activation statistics, SVD/PCA, projection, LayerScale / head / MLP-unit surgery).
2. Edit AT MOST 10% of the network's weights (prefer < 3%!). Architecture & forward pass are UNCHANGED.
3. Your function signature MUST be EXACTLY:
   `def weight_program(model, X_tr, M_tr, y_tr, t_tr, helpers):`
   returning `(new_state_dict, changed_param_count)`.
   - `X_tr`: float16 tensor (N, 5, 3, 224, 224), `M_tr`: bool (N, 5)
   - `y_tr`: float32 numpy (N,) human quality scores 1..5
   - `t_tr`: str numpy (N,) training task IDs (2 tasks in LOTO folds, 3 in full eval — NEVER hardcode task names or embed_dim=384! Use `len(model.blocks)`, `model.embed_dim`, `sorted(set(t_tr))` so the program transfers to `dinov2_vitb14` too!)
   - `helpers["collect_late"](model, X_tr, M_tr, block_ids, views=(0,1,2))` returns dict with keys `"in_cls"`, `"d_attn"`, `"d_mlp"`, `"h_mlp"` (each `{l: (N*V, dim)}`) and `"views"`.
   - `helpers["collect_head_cls"](model, X_tr, M_tr, block_id, views=(0,1,2))` returns `(N, n_heads, D)` averaged across views.
4. Keep memory safe: always use `helpers` or `batch=16` with `@torch.no_grad()`. Clone `sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}` and return CPU tensors in `sd`.

MEASURED MECHANISTIC FACTS ABOUT `dinov2_vits14`:
- Baseline unmodified: In-task LOO = 86.6% | Strict LOTO (unseen human task) = 58.7% | 16 Unseen Tasks (U48) = 62.0% | mitre-gate-jamb (Jamb22) = 57.6%.
- Why it fails: Final CLS has 58.9% variance explained by task/object identity.
- Block profile:
  - B08 in_cls: norm=3.86, TaskVar=21.1%
  - B09 d_mlp:  norm=10.04, TaskVar=6.8%  (B09 CLS alone gets LOTO=73.3%, U48=74.6%, Jamb=81.8%, LOO=80.7%)
  - B10 d_attn: norm=3.40, TaskVar=48.9% | B10 d_mlp: norm=5.49, TaskVar=37.9%, MaxWithinCorr=0.792!
  - B11 d_attn: norm=20.68, TaskVar=54.6%, ls1_med=7.83! (Giant semantic blast overwrites B09 geometry!)
  - B11 d_mlp:  norm=14.23, TaskVar=44.1%, ls2_med=5.85!
- Known reference (WSP-A1: Late LS Damper on B10/B11 ls1/ls2, 1,536 params = 0.007%):
  - LOO = 82.5% | Strict LOTO = 71.2% | U48 = 77.6% | Jamb22 = 79.7%.

YOUR GOAL (BEAT WSP-A1 ON THE PARETO FRONTIER!):
Maximize `score = 0.5 * LOTO + 0.5 * U48` while keeping **In-Task LOO >= 84.5%** (retention!) AND **changed <= 10%**!
Think carefully about WHY WSP-A1 lost 4.1% LOO (86.6 -> 82.5%): it damped B11 too uniformly, suppressing fine-grained within-task craft heads/MLP units along with object-ID heads/units!
Consider surgical mechanisms:
- Per-head pruning/scaling in B10 & B11 (`attn.proj.weight` head slices) based on head TaskVar vs within-task quality correlation!
- Per-MLP-unit SNR gating in B10 & B11 (`mlp.fc2.weight` columns, folding removed mean into `fc2.bias`) — keep/boost high within-task-quality, low-TaskVar units (remember B10 d_mlp has MaxWithinCorr=0.792!), zero/damp pure TaskVar units!
- Selective LayerScale calibration (`ls1.gamma`, `ls2.gamma` in B10, B11) + final `norm.weight` per-channel SNR reweighting (amplifying task-invariant craft dimensions in the final LayerNorm!) — `norm.weight` is ONLY 384 params!
- Orthogonalized between-task subspace projection on `attn.proj` / `mlp.fc2` (removing between-task mean differences + top within-task nuisance PCA while protecting the within-task quality direction!).

Reply concisely with a 2-sentence hypothesis followed by a SINGLE ```python ... ``` block defining `weight_program` (including needed imports `numpy as np`, `torch`)."""


def evaluate_candidate(code: str, m, base_sd, data83, datau, helpers, lam=1000):
    forbidden = (".backward(", "torch.optim", "autograd.grad", "requires_grad_(True)")
    for bad in forbidden:
        if bad in code:
            return None, f"REJECTED: forbidden gradient operation `{bad}` found in program."
    ns = {"np": np, "torch": torch, "F": F}
    try:
        exec(code, ns)
    except Exception:
        return None, "EXEC ERROR:\n" + traceback.format_exc()[-1200:]
    if "weight_program" not in ns:
        return None, "ERROR: `weight_program` function not defined."
    fn = ns["weight_program"]

    X83, M83, y83, t83 = data83
    Xu, Mu, yu, tu = datau
    unique_tasks = sorted(set(t83))
    total_params = sum(p.numel() for p in m.parameters())

    t0 = time.time()
    try:
        # Strict 3-fold LOTO
        pt = {}
        for held in unique_tasks:
            tr, te = t83 != held, t83 == held
            m.load_state_dict(base_sd)
            sd, chg = fn(m, X83[tr], M83[tr], y83[tr], t83[tr], helpers)
            m.load_state_dict(sd)
            E_tr = wj.forward_cls(m, X83[tr], M83[tr]).reshape(tr.sum(), -1)
            E_te = wj.forward_cls(m, X83[te], M83[te]).reshape(te.sum(), -1)
            mu_, sd_ = E_tr.mean(0), E_tr.std(0) + 1e-6
            Xtr, Xte = (E_tr - mu_) / sd_, (E_te - mu_) / sd_
            a = np.linalg.solve(Xtr @ Xtr.T + lam * np.eye(tr.sum()), y83[tr] - y83[tr].mean())
            pred = y83[tr].mean() + (Xte @ Xtr.T) @ a
            pt[held] = calibrate.pairwise(list(y83[te]), list(pred))
        loto = sum(pt[t] * (t83 == t).sum() for t in unique_tasks) / len(y83)

        # Full 83-entry run -> LOO + 16-unseen
        m.load_state_dict(base_sd)
        sd_full, chg = fn(m, X83, M83, y83, t83, helpers)
        # Measure actual changed entries directly!
        actual_chg = sum(int((sd_full[k] != base_sd[k]).sum().item()) for k in base_sd)
        pct = 100.0 * actual_chg / total_params
        m.load_state_dict(sd_full)
        E83_e = wj.forward_cls(m, X83, M83)
        Eu_e = wj.forward_cls(m, Xu, Mu)
        loo, u48, mj = wj.ridge_eval_pair(E83_e, Eu_e, y83, yu, tu, lam=lam)
    except Exception:
        return None, "RUNTIME ERROR:\n" + traceback.format_exc()[-1200:]

    res = {
        "loo": float(loo),
        "loto": float(loto),
        "per_task": {k: float(v) for k, v in pt.items()},
        "u48": float(u48),
        "jamb": float(mj),
        "actual_chg": actual_chg,
        "pct": pct,
        "score": 0.5 * float(loto) + 0.5 * float(u48),
        "retained": (float(loo) >= 0.845) and (pct <= 10.0),
        "sec": time.time() - t0,
    }
    return res, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", type=int, default=5)
    ap.add_argument("--effort", default="none")
    args = ap.parse_args()

    print(f"Starting WeightCode synthesis session driven by {MODEL_ID} (effort={args.effort}, checks={args.checks})", flush=True)
    data83, datau = wj.load_datasets()
    m = wj.load_dinov2_local("vits14")
    base_sd = {k: v.detach().cpu().clone() for k, v in m.state_dict().items()}
    helpers = make_helpers()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Propose Check #1: your first WeightCode program for `dinov2_vits14`. Aim for LOO >= 84.5% AND strong LOTO + U48 transfer with <= 10% edited weights."},
    ]

    log_lines = [
        f"# WeightCode Synthesis Session (`{MODEL_ID}`, effort=`{args.effort}`)",
        "",
        "| Check | LOO *(target ≥84.5%)* | Strict LOTO *(arch/valve/reev)* | U48 *(16 unseen)* | Jamb22 | Score | Edited | Retained? | API Cost |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| Ref: Baseline | 86.6% | 58.7% *(56/64/53)* | 62.0% | 57.6% | 60.4% | 0.00% | yes | $0 |",
        "| Ref: WSP-A1 | 82.5% | 71.2% *(64/74/100)* | 77.6% | 79.7% | 74.4% | 0.007% | no *(LOO<84.5%)* | $0 |",
    ]

    best_retained = None
    best_overall = None
    total_cost = 0.0

    for check_i in range(1, args.checks + 1):
        print(f"\n--- Check {check_i}/{args.checks}: calling {MODEL_ID} ---", flush=True)
        t_api = time.time()
        reply, cost, toks, fin = call_hy4(messages, effort=args.effort)
        total_cost += cost
        print(f"API reply in {time.time()-t_api:.1f}s ({toks} tok, ${cost:.5f}, finish={fin})", flush=True)
        code = extract_code(reply)
        messages.append({"role": "assistant", "content": reply})

        if not code:
            fb = "No ```python``` code block with `def weight_program` found. Please provide one."
            print(fb, flush=True)
            messages.append({"role": "user", "content": fb})
            continue

        res, err = evaluate_candidate(code, m, base_sd, data83, datau, helpers)
        if err:
            print(err, flush=True)
            log_lines.append(f"| {check_i} | ERROR | - | - | - | - | - | no | ${cost:.5f} |")
            messages.append({"role": "user", "content": f"Check #{check_i} FAILED:\n{err}\nFix the bug and write Check #{check_i+1}."})
            continue

        pt = res["per_task"]
        row_str = (
            f"| {check_i} | {res['loo']:.1%} | {res['loto']:.1%} "
            f"*({pt['arched-spandrel']:.0%}/{pt['handwheel-valve']:.0%}/{pt['reeving-block']:.0%})* | "
            f"{res['u48']:.1%} | {res['jamb']:.1%} | **{res['score']:.1%}** | "
            f"{res['pct']:.3f}% ({res['actual_chg']:,}) | {'YES' if res['retained'] else 'no'} | ${cost:.5f} |"
        )
        log_lines.append(row_str)
        print(f"CHECK {check_i} RESULT: LOO={res['loo']:.1%} | LOTO={res['loto']:.1%} ({pt['arched-spandrel']:.0%}/{pt['handwheel-valve']:.0%}/{pt['reeving-block']:.0%}) | U48={res['u48']:.1%} | Jamb={res['jamb']:.1%} | Score={res['score']:.1%} | chg={res['pct']:.3f}% | retained={res['retained']} [{res['sec']:.0f}s]", flush=True)

        if best_overall is None or res["score"] > best_overall[0]["score"]:
            best_overall = (res, code, check_i)
        if res["retained"] and (best_retained is None or res["score"] > best_retained[0]["score"]):
            best_retained = (res, code, check_i)

        if check_i < args.checks:
            fb = (
                f"Check #{check_i} evaluation:\n"
                f"- In-Task LOO: {res['loo']:.1%} (retention target >= 84.5%: {'PASS' if res['loo'] >= 0.845 else 'FAIL'})\n"
                f"- Strict LOTO: {res['loto']:.1%} (arch={pt['arched-spandrel']:.1%}, valve={pt['handwheel-valve']:.1%}, reeving={pt['reeving-block']:.1%})\n"
                f"- 16 Unseen Tasks (U48): {res['u48']:.1%} | mitre-gate-jamb (22): {res['jamb']:.1%}\n"
                f"- Combined Score (0.5*LOTO + 0.5*U48): {res['score']:.1%}\n"
                f"- Weights changed: {res['actual_chg']:,} ({res['pct']:.3f}%, limit <= 10.0%)\n\n"
                f"Diagnose what helped or hurt, and write an improved Check #{check_i+1} program."
            )
            messages.append({"role": "user", "content": fb})

    winner = best_retained or best_overall
    if winner:
        w_res, w_code, w_i = winner
        out_py = ROOT / "harness" / "wsp_best_hy4.py"
        header = (
            f'"""Best WeightCode program synthesized by {MODEL_ID} (Check #{w_i}, total session cost ${total_cost:.5f}).\n'
            f"vits14: LOO={w_res['loo']:.1%}, Strict LOTO={w_res['loto']:.1%}, U48={w_res['u48']:.1%}, "
            f"Jamb22={w_res['jamb']:.1%}, changed={w_res['actual_chg']:,} ({w_res['pct']:.3f}%).\n"
            f'"""\n'
        )
        out_py.write_text(header + w_code + "\n")
        print(f"\nSaved winning program (Check #{w_i}) -> {out_py} (total API cost: ${total_cost:.5f})", flush=True)
        log_lines += ["", f"Winning program: **Check #{w_i}** saved to `harness/wsp_best_hy4.py` (total session API cost: `${total_cost:.5f}`)."]

    out_md = ROOT / "runs" / "hy4-weightcode-session.md"
    out_md.write_text("\n".join(log_lines) + "\n")
    print(f"Wrote session log -> {out_md}", flush=True)

    del m, base_sd
    gc.collect()
    if DEVICE == "mps":
        torch.mps.empty_cache()


if __name__ == "__main__":
    main()
