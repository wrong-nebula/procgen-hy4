#!/usr/bin/env python3
"""Weight-Space Programming (WeightCode, arXiv:2611.25476) applied to the local DINOv2 kit judge.

Tests closed-form, gradient-free weight programs W' = p(W, I) directly on cached local DINOv2 ViTs
(dinov2_vits14 and dinov2_vitb14), keeping DINOv2's architecture and embed.py forward pass unchanged.

Evaluates all 4 WeightCode pillars:
  1. Installation (no gradients): Late LayerScale SNR Damper (0.007% weights),
     Hybrid (Damper + Task-Orthogonalized Projection), and Terminal B10->B11 PatchStd Circuit.
  2. Comparison vs. matched gradient fine-tuning on the exact same LayerScale parameters.
  3. Mechanism / Causal ablations (permuted control, component removal).
  4. Cross-architecture zero-LLM reuse: rerun the EXACT same weight program unchanged on dinov2_vitb14.
"""
import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
try:
    torch.set_num_threads(min(2, os.cpu_count() or 2))
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import calibrate  # noqa: E402
import embed      # noqa: E402

VIEWS = ("iso-beauty", "iso-clay", "opposite-beauty", "front-clay", "parts")
HUB_LOCAL = Path.home() / ".cache/torch/hub/facebookresearch_dinov2_main"
DEVICE = "cpu" if os.environ.get("KITFORGE_FORCE_CPU", "0") == "1" else ("mps" if torch.backends.mps.is_available() else "cpu")


def load_dinov2_local(arch: str):
    """Load cached DINOv2 strictly from local hub cache (no network calls)."""
    m = torch.hub.load(str(HUB_LOCAL), f"dinov2_{arch}", source="local", verbose=False)
    return m.to(DEVICE).eval()


def load_datasets():
    G = calibrate.G
    rows83 = []
    for t, e, h, slug in calibrate.entries("2026-11-17"):
        rows83.append({
            "task": t["id"],
            "slug": slug,
            "y": sum(h[c] for c in calibrate.CRITERIA) / 4.0,
            "imgs": [str(G / e["images"][v]) if v in e["images"] else None for v in VIEWS],
        })

    drex = json.loads((ROOT / "gallery/critic_scores/decision_all_drex_max.json").read_text())
    runs_dir = ROOT / "runs/2026-11-17"
    seen_tasks = {"arched-spandrel", "handwheel-valve", "reeving-block"}
    rows_u = []
    for k, v in sorted(drex.items()):
        if "error" in v or "overall" not in v:
            continue
        task, slug = k.split("/", 1)
        if task in seen_tasks:
            continue
        rdir = runs_dir / task / slug / "renders"
        if rdir.exists() and (rdir / "iso-beauty.png").exists():
            imgs = [str(rdir / f"{vw}.png") if (rdir / f"{vw}.png").exists() else None for vw in VIEWS]
            rows_u.append({
                "task": task,
                "slug": slug,
                "y": (v["reads"] + v["fidelity"] + v["craft"] + v["overall"]) / 4.0,
                "imgs": imgs,
            })

    def to_fp16(rows):
        N, V = len(rows), len(VIEWS)
        X = torch.zeros((N, V, 3, 224, 224), dtype=torch.float16)
        M = torch.zeros((N, V), dtype=torch.bool)
        for i, r in enumerate(rows):
            for j, p in enumerate(r["imgs"]):
                if p and Path(p).exists():
                    X[i, j] = embed.load(p, 224).half()
                    M[i, j] = True
        return X, M

    X83, M83 = to_fp16(rows83)
    Xu, Mu = to_fp16(rows_u)
    y83 = np.array([r["y"] for r in rows83], dtype=np.float32)
    t83 = np.array([r["task"] for r in rows83])
    yu = np.array([r["y"] for r in rows_u], dtype=np.float32)
    tu = np.array([r["task"] for r in rows_u])
    return (X83, M83, y83, t83), (Xu, Mu, yu, tu)


@torch.no_grad()
def forward_cls(model, X, M, batch=16):
    N, V = X.shape[:2]
    fx = X.view(N * V, 3, 224, 224)
    fm = M.view(N * V)
    idx = torch.where(fm)[0]
    D = model.embed_dim
    out = np.zeros((N * V, D), dtype=np.float32)
    for k in range(0, len(idx), batch):
        c = idx[k:k + batch]
        f = model(fx[c].to(device=DEVICE, dtype=torch.float32)).float().cpu().numpy()
        f /= np.linalg.norm(f, axis=1, keepdims=True) + 1e-8
        out[c.numpy()] = f
    if DEVICE == "mps":
        torch.mps.empty_cache()
    return out.reshape(N, V, D)


def ridge_eval_pair(E83, Eu, y83, yu, tu, lam=1000):
    N83, Nu = len(y83), len(yu)
    Xf83 = E83.reshape(N83, -1)
    Xfu = Eu.reshape(Nu, -1)

    loo_p = np.zeros(N83)
    for i in range(N83):
        tr = np.arange(N83) != i
        mu_, sd_ = Xf83[tr].mean(0), Xf83[tr].std(0) + 1e-6
        Xtr, xi = (Xf83[tr] - mu_) / sd_, (Xf83[i] - mu_) / sd_
        a = np.linalg.solve(Xtr @ Xtr.T + lam * np.eye(N83 - 1), y83[tr] - y83[tr].mean())
        loo_p[i] = y83[tr].mean() + (Xtr @ xi) @ a
    loo_pw = calibrate.pairwise(list(y83), list(loo_p))

    mu_, sd_ = Xf83.mean(0), Xf83.std(0) + 1e-6
    Xtr, Xte = (Xf83 - mu_) / sd_, (Xfu - mu_) / sd_
    a = np.linalg.solve(Xtr @ Xtr.T + lam * np.eye(N83), y83 - y83.mean())
    pu = y83.mean() + (Xte @ Xtr.T) @ a
    u48_pw = calibrate.pairwise(list(yu), list(pu))
    mj = tu == "mitre-gate-jamb"
    mj_pw = calibrate.pairwise(list(yu[mj]), list(pu[mj]))
    return loo_pw, u48_pw, mj_pw


# =============================================================================
# WEIGHT PROGRAM 1: Late-Block LayerScale SNR Damper (only 4*D params = 0.007%)
# =============================================================================
@torch.no_grad()
def wsp_late_ls_damper(model, X_tr, M_tr, y_tr, t_tr, last_scale=0.04, prev_scale=0.30, q_boost=0.18,
                       alpha_proj=0.0, random_control=False):
    """Closed-form WeightCode program:
    1) Measures between-task vs within-task quality variance of the final 2 blocks' attn & MLP CLS outputs.
    2) Damps high-TaskVar channels in LayerScale (ls1.gamma, ls2.gamma) while preserving quality-aligned channels.
    3) Optionally projects out pure between-task directions (orthogonal to within-task quality) from attn.proj & mlp.fc2.
    Works unchanged on any DINOv2 ViT (vits14, vitb14, vitl14).
    """
    N, _ = X_tr.shape[:2]
    L, D = len(model.blocks), model.embed_dim
    late = [L - 2, L - 1]
    tr_tasks = sorted(set(t_tr))
    use_v = [0, 1, 2]
    fx = X_tr[:, use_v].reshape(N * len(use_v), 3, 224, 224)
    fm = M_tr[:, use_v].reshape(N * len(use_v))
    idx = torch.where(fm)[0]

    d_attn = {l: np.zeros((N * len(use_v), D), np.float32) for l in late}
    d_mlp = {l: np.zeros((N * len(use_v), D), np.float32) for l in late}
    for k in range(0, len(idx), 16):
        c = idx[k:k + 16]
        x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
        cn = c.numpy()
        for i, blk in enumerate(model.blocks):
            ao = blk.ls1(blk.attn(blk.norm1(x)))
            x = x + ao
            mo = blk.ls2(blk.mlp(blk.norm2(x)))
            x = x + mo
            if i in late:
                d_attn[i][cn] = ao[:, 0].float().cpu().numpy()
                d_mlp[i][cn] = mo[:, 0].float().cpu().numpy()
    if DEVICE == "mps":
        torch.mps.empty_cache()

    yc = y_tr.copy().astype(np.float64)
    for t in tr_tasks:
        sel = t_tr == t
        yc[sel] -= yc[sel].mean()
    yc_n = yc / (np.linalg.norm(yc) + 1e-8)

    sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    changed = 0
    rng = np.random.default_rng(7)
    for l in late:
        base_s = last_scale if l == L - 1 else prev_scale
        for arr, key_ls, w_key, b_key in [
            (d_attn[l], f"blocks.{l}.ls1.gamma", f"blocks.{l}.attn.proj.weight", f"blocks.{l}.attn.proj.bias"),
            (d_mlp[l],  f"blocks.{l}.ls2.gamma", f"blocks.{l}.mlp.fc2.weight",   f"blocks.{l}.mlp.fc2.bias"),
        ]:
            Fm = arr.reshape(N, len(use_v), D).mean(1).astype(np.float64)
            gm = Fm.mean(0)
            bt = np.zeros(D)
            wt = np.zeros(D)
            Fc = Fm.copy()
            for t in tr_tasks:
                sel = t_tr == t
                mt = Fm[sel].mean(0)
                bt += sel.sum() * (mt - gm) ** 2
                wt += np.sum((Fm[sel] - mt) ** 2, axis=0)
                Fc[sel] -= mt
            tv = bt / (bt + wt + 1e-8)
            qcorr = np.abs(yc_n @ (Fc / (np.linalg.norm(Fc, axis=0, keepdims=True) + 1e-8)))
            keep = base_s * (1.0 - 0.7 * tv) + q_boost * (1.0 - tv) * (qcorr ** 2)
            keep = np.clip(keep, 0.01, 1.0)
            if random_control:
                keep = rng.permutation(keep)
            sd[key_ls] = sd[key_ls] * torch.from_numpy(keep.astype(np.float32))
            changed += sd[key_ls].numel()

            if alpha_proj > 0 and len(tr_tasks) >= 2:
                M_task = np.stack([Fm[t_tr == t].mean(0) - gm for t in tr_tasks], axis=0)
                q_dir = yc_n @ Fc
                q_dir = q_dir / (np.linalg.norm(q_dir) + 1e-8)
                M_orth = M_task - np.outer(M_task @ q_dir, q_dir)
                _, S, Vt = np.linalg.svd(M_orth, full_matrices=False)
                rank = min(len(tr_tasks) - 1, int((S > 1e-5 * S[0]).sum()))
                if rank > 0:
                    Q = Vt[:rank]
                    P = torch.from_numpy((np.eye(D) - alpha_proj * (Q.T @ Q)).astype(np.float32))
                    sd[w_key] = P @ sd[w_key]
                    sd[b_key] = P @ sd[b_key]
                    changed += sd[w_key].numel() + sd[b_key].numel()
    return sd, {"changed": changed}


# =============================================================================
# WEIGHT PROGRAM 2: Terminal-Block PatchStd Circuit (B[L-2].mlp -> B[L-1].attn)
# Zero interference with intermediate blocks! Patches are discarded after B[L-1]!
# =============================================================================
@torch.no_grad()
def wsp_terminal_patchstd(model, X_tr, M_tr, y_tr, t_tr, K=12, gain=2.0, last_scale=0.06, prev_scale=0.45, q_boost=0.25):
    """Installs a paired-GELU |u^T p - mu| spatial-spread circuit in B[L-2].mlp
    and repurposes the highest-TaskVar head in B[L-1].attn as a uniform 1/T spatial
    mean-pooler straight into the final CLS token, stacked with the Late LS Damper.
    Because it acts at the terminal block, intermediate blocks 0..L-3 are 100% untouched!
    """
    N, _ = X_tr.shape[:2]
    L, D = len(model.blocks), model.embed_dim
    l_prev, l_last = L - 2, L - 1
    H_mlp = model.blocks[l_prev].mlp.fc1.out_features
    n_heads = model.blocks[l_last].attn.num_heads
    head_dim = D // n_heads
    tr_tasks = sorted(set(t_tr))

    use_v = [0, 1, 2]
    fx = X_tr[:, use_v].reshape(N * len(use_v), 3, 224, 224)
    fm = M_tr[:, use_v].reshape(N * len(use_v))
    idx = torch.where(fm)[0]

    mlp_contrib = np.zeros(H_mlp, dtype=np.float64)
    mlp_mean_h = np.zeros(H_mlp, dtype=np.float64)
    p_n2_mean = np.zeros(D, dtype=np.float64)
    p_n2_std = np.zeros((N * len(use_v), D), dtype=np.float32)
    out_cov = np.zeros((D, D), dtype=np.float64)
    out_mean = np.zeros(D, dtype=np.float64)
    head_cls = np.zeros((N * len(use_v), n_heads, D), dtype=np.float32)
    cnt_tok = 0

    b_prev, b_last = model.blocks[l_prev], model.blocks[l_last]
    w2_norm = (b_prev.mlp.fc2.weight * b_prev.ls2.gamma[:, None]).norm(dim=0).cpu().numpy()

    for k in range(0, len(idx), 16):
        c = idx[k:k + 16]
        x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
        cn = c.numpy()
        for i in range(l_prev):
            blk = model.blocks[i]
            x = x + blk.ls1(blk.attn(blk.norm1(x)))
            x = x + blk.ls2(blk.mlp(blk.norm2(x)))
        # Block L-2
        x = x + b_prev.ls1(b_prev.attn(b_prev.norm1(x)))
        xn2 = b_prev.norm2(x)
        h = F.gelu(b_prev.mlp.fc1(xn2))
        mlp_contrib += h.abs().sum(dim=(0, 1)).cpu().numpy() * w2_norm
        mlp_mean_h += h.sum(dim=(0, 1)).cpu().numpy()
        pn = xn2[:, 1:, :].float().cpu().numpy()
        p_n2_mean += pn.sum(axis=(0, 1))
        p_n2_std[cn] = pn.std(axis=1)
        x = x + b_prev.ls2(b_prev.mlp.fc2(h))

        # Input to final block L-1 attn
        x1n = b_last.norm1(x)
        p1 = x1n[:, 1:, :].reshape(-1, D).float().cpu().numpy()
        out_mean += p1.sum(axis=0)
        out_cov += p1.T @ p1
        cnt_tok += p1.shape[0]

        B_cur, T_cur, _ = x1n.shape
        qkv = b_last.attn.qkv(x1n).reshape(B_cur, T_cur, 3, n_heads, head_dim).permute(2, 0, 3, 1, 4)
        q, kk, vv = qkv[0], qkv[1], qkv[2]
        att = ((q * b_last.attn.scale) @ kk.transpose(-2, -1)).softmax(dim=-1)
        ho = (att @ vv)[:, :, 0, :]
        Wp, g1 = b_last.attn.proj.weight, b_last.ls1.gamma
        for hh in range(n_heads):
            Wh = Wp[:, hh * head_dim:(hh + 1) * head_dim]
            head_cls[cn, hh] = ((ho[:, hh, :] @ Wh.T) * g1).float().cpu().numpy()
    if DEVICE == "mps":
        torch.mps.empty_cache()

    mlp_mean_h /= (len(idx) * 257)
    p_n2_mean /= cnt_tok
    out_mean /= cnt_tok
    out_cov = out_cov / cnt_tok - np.outer(out_mean, out_mean)

    # First apply Late LS Damper (preserving the base model's L-2..L-1 SNR calibration)
    sd, info_ls = wsp_late_ls_damper(model, X_tr, M_tr, y_tr, t_tr,
                                     last_scale=last_scale, prev_scale=prev_scale, q_boost=q_boost)
    changed = info_ls["changed"]

    # Pick top-K craft-spread coordinates in B[L-2].norm2 patch std
    yc = y_tr.copy().astype(np.float64)
    for t in tr_tasks:
        sel = t_tr == t
        yc[sel] -= yc[sel].mean()
    yc_n = yc / (np.linalg.norm(yc) + 1e-8)

    S_img = p_n2_std.reshape(N, len(use_v), D).mean(1).astype(np.float64)
    gm = S_img.mean(0)
    bt = np.zeros(D)
    wt = np.zeros(D)
    Sc = S_img.copy()
    for t in tr_tasks:
        sel = t_tr == t
        mt = S_img[sel].mean(0)
        bt += sel.sum() * (mt - gm) ** 2
        wt += np.sum((S_img[sel] - mt) ** 2, axis=0)
        Sc[sel] -= mt
    tv_s = bt / (bt + wt + 1e-8)
    q_s = np.abs(yc_n @ (Sc / (np.linalg.norm(Sc, axis=0, keepdims=True) + 1e-8)))
    chosen_dims = np.argsort(q_s * (1.0 - tv_s))[::-1][:K]

    # Pick K quietest directions in B[L-1].norm1 patch space
    _, eigvecs = np.linalg.eigh(out_cov)
    quiet_dirs = eigvecs[:, :K].T

    # Pick 2K least-used MLP units in B[L-2]
    idle_units = np.argsort(mlp_contrib)[:2 * K]

    # Pick highest-TaskVar head in B[L-1].attn to replace!
    head_tv = []
    for hh in range(n_heads):
        Fm = head_cls[:, hh, :].reshape(N, len(use_v), D).mean(1)
        gm_h = Fm.mean(0)
        bt_h = sum((t_tr == t).sum() * np.sum((Fm[t_tr == t].mean(0) - gm_h) ** 2) for t in tr_tasks)
        tot_h = np.sum((Fm - gm_h) ** 2) + 1e-8
        head_tv.append(bt_h / tot_h)
    h_star = int(np.argmax(head_tv))

    # Write paired-GELU |z_d - mu_d| units in B[L-2].mlp
    W1 = sd[f"blocks.{l_prev}.mlp.fc1.weight"].numpy().astype(np.float64)
    b1_m = sd[f"blocks.{l_prev}.mlp.fc1.bias"].numpy().astype(np.float64)
    W2 = sd[f"blocks.{l_prev}.mlp.fc2.weight"].numpy().astype(np.float64)
    b2_m = sd[f"blocks.{l_prev}.mlp.fc2.bias"].numpy().astype(np.float64)
    ls2_p = sd[f"blocks.{l_prev}.ls2.gamma"].numpy().astype(np.float64)
    ls2_p_safe = np.where(np.abs(ls2_p) > 1e-3, ls2_p, np.sign(ls2_p + 1e-9) * 1e-3)

    for u in idle_units:
        b2_m += W2[:, u] * mlp_mean_h[u]

    s_gate, write_amp = 1.5, 0.35
    for k_i, d_idx in enumerate(chosen_dims):
        u_pos, u_neg = idle_units[2 * k_i], idle_units[2 * k_i + 1]
        W1[u_pos, :] = 0.0; W1[u_pos, d_idx] = +s_gate; b1_m[u_pos] = -s_gate * p_n2_mean[d_idx]
        W1[u_neg, :] = 0.0; W1[u_neg, d_idx] = -s_gate; b1_m[u_neg] = +s_gate * p_n2_mean[d_idx]
        out_col = (write_amp / s_gate) * (quiet_dirs[k_i] / ls2_p_safe)
        W2[:, u_pos] = out_col
        W2[:, u_neg] = out_col

    sd[f"blocks.{l_prev}.mlp.fc1.weight"] = torch.from_numpy(W1.astype(np.float32))
    sd[f"blocks.{l_prev}.mlp.fc1.bias"] = torch.from_numpy(b1_m.astype(np.float32))
    sd[f"blocks.{l_prev}.mlp.fc2.weight"] = torch.from_numpy(W2.astype(np.float32))
    sd[f"blocks.{l_prev}.mlp.fc2.bias"] = torch.from_numpy(b2_m.astype(np.float32))
    changed += 2 * K * (2 * D + 1)

    # Repurpose highest-TaskVar head h_star in B[L-1].attn as exact uniform spatial mean-pooler!
    Wqkv = sd[f"blocks.{l_last}.attn.qkv.weight"].numpy().astype(np.float64)
    bqkv = sd[f"blocks.{l_last}.attn.qkv.bias"].numpy().astype(np.float64)
    Wproj = sd[f"blocks.{l_last}.attn.proj.weight"].numpy().astype(np.float64)
    ls1_l = sd[f"blocks.{l_last}.ls1.gamma"].numpy().astype(np.float64)
    ls1_l_safe = np.where(np.abs(ls1_l) > 1e-3, ls1_l, np.sign(ls1_l + 1e-9) * 1e-3)

    s0, s1 = h_star * head_dim, (h_star + 1) * head_dim
    Wqkv[s0:s1, :] = 0.0; bqkv[s0:s1] = 0.0
    Wqkv[D + s0:D + s1, :] = 0.0; bqkv[D + s0:D + s1] = 0.0
    Wqkv[2 * D + s0:2 * D + s1, :] = 0.0; bqkv[2 * D + s0:2 * D + s1] = 0.0
    Wqkv[2 * D + s0:2 * D + s0 + K, :] = quiet_dirs
    Wproj[:, s0:s1] = 0.0
    for k_i in range(K):
        Wproj[:, s0 + k_i] = (gain / write_amp) * (quiet_dirs[k_i] / ls1_l_safe)

    sd[f"blocks.{l_last}.attn.qkv.weight"] = torch.from_numpy(Wqkv.astype(np.float32))
    sd[f"blocks.{l_last}.attn.qkv.bias"] = torch.from_numpy(bqkv.astype(np.float32))
    sd[f"blocks.{l_last}.attn.proj.weight"] = torch.from_numpy(Wproj.astype(np.float32))
    changed += head_dim * (4 * D + 3)

    return sd, {"changed": changed, "h_star": h_star}


# =============================================================================
# WEIGHT PROGRAM 3: Exact Fisher Discriminant compiled into blocks[-1].mlp
# using the exact identity GELU(z) - GELU(-z) = z (inspired by peasant98/dinov2-weight-surgery,
# but ZERO appended blocks — fits inside DINOv2's existing 4D MLP!).
# =============================================================================
@torch.no_grad()
def wsp_fisher_gelu_mlp(
    model,
    X_tr,
    M_tr,
    y_tr,
    t_tr,
    last_ls1=0.06,
    prev_ls=0.35,
    q_boost_ls=0.22,
    beta_shrink=8.0,
    gamma_qual=0.65,
    rank_cap=96,
    keep_best_nonlinear=256,
):
    """Compiles an exact closed-form Fisher discriminant linear map M into blocks[-1].mlp
    using paired GELU units: GELU(v^T z) - GELU(-v^T z) = v^T z (exact for all z!).
    1) First applies Late LayerScale SNR Damper to B[L-2] and B[L-1].ls1 (attenuating the
       giant object-ID attention blast while preserving B09-B10 geometry).
    2) Measures CLS entering B[L-1].mlp (`x_mid`) and its LayerNorm output `z = norm2(x_mid)`.
    3) Computes Fisher shrinkage on between-task covariance Sigma_B vs within-task Sigma_W
       + boosts within-task quality direction (orthogonal to Sigma_B), maps z -> Delta_x*
       in closed form, and compiles M = U S V^T EXACTLY into 2*R units of blocks[-1].mlp!
    4) Optionally retains the top `keep_best_nonlinear` highest-quality, lowest-TaskVar
       original nonlinear MLP units so in-task LOO stays high!
    Works unchanged on any DINOv2 ViT (vits14, vitb14, vitl14) with 0 appended blocks.
    """
    N, _ = X_tr.shape[:2]
    L, D = len(model.blocks), model.embed_dim
    l_last = L - 1
    H_mlp = model.blocks[l_last].mlp.fc1.out_features
    tr_tasks = sorted(set(t_tr))
    use_v = [0, 1, 2]

    # Step 1: Apply Late LS Damper on B[L-2] and B[L-1].ls1 (leave B[L-1].ls2 = 1.0 for exact linear MLP)
    sd, info_ls = wsp_late_ls_damper(
        model, X_tr, M_tr, y_tr, t_tr,
        last_scale=last_ls1, prev_scale=prev_ls, q_boost=q_boost_ls, alpha_proj=0.0,
    )
    # Temporarily load damped B[0..L-2] + damped B[L-1].ls1 to measure exact x_mid & z entering B[L-1].mlp!
    orig_sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(sd)

    fx = X_tr[:, use_v].reshape(N * len(use_v), 3, 224, 224)
    fm = M_tr[:, use_v].reshape(N * len(use_v))
    idx = torch.where(fm)[0]

    x_mid_all = np.zeros((N * len(use_v), D), dtype=np.float32)
    z_all = np.zeros((N * len(use_v), D), dtype=np.float32)
    h_orig_all = np.zeros((N * len(use_v), H_mlp), dtype=np.float32)

    for k in range(0, len(idx), 16):
        c = idx[k:k + 16]
        x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
        cn = c.numpy()
        for i in range(l_last):
            blk = model.blocks[i]
            x = x + blk.ls1(blk.attn(blk.norm1(x)))
            x = x + blk.ls2(blk.mlp(blk.norm2(x)))
        blk_l = model.blocks[l_last]
        x = x + blk_l.ls1(blk_l.attn(blk_l.norm1(x)))
        x_mid_all[cn] = x[:, 0].float().cpu().numpy()
        z = blk_l.norm2(x)
        z_all[cn] = z[:, 0].float().cpu().numpy()
        h_orig_all[cn] = F.gelu(blk_l.mlp.fc1(z[:, 0])).float().cpu().numpy()
    if DEVICE == "mps":
        torch.mps.empty_cache()

    # Restore model to orig_sd cleanly (program is pure functional)
    model.load_state_dict(orig_sd)

    # Average across views per sample for clean task/quality statistics
    Xm = x_mid_all.reshape(N, len(use_v), D).mean(1).astype(np.float64)
    Zm = z_all.reshape(N, len(use_v), D).mean(1).astype(np.float64)
    Hm = h_orig_all.reshape(N, len(use_v), H_mlp).mean(1).astype(np.float64)

    # Within-task centered quality labels
    yc = y_tr.copy().astype(np.float64)
    for t in tr_tasks:
        sel = t_tr == t
        yc[sel] -= yc[sel].mean()
    yc_n = yc / (np.linalg.norm(yc) + 1e-8)

    # Compute between-task covariance Sigma_B and within-task covariance Sigma_W on x_mid
    gm_x = Xm.mean(0)
    Sigma_B = np.zeros((D, D), dtype=np.float64)
    Sigma_W = np.zeros((D, D), dtype=np.float64)
    Xc = Xm.copy()
    for t in tr_tasks:
        sel = t_tr == t
        mt = Xm[sel].mean(0)
        diff_t = mt - gm_x
        Sigma_B += (sel.sum() / N) * np.outer(diff_t, diff_t)
        dx = Xm[sel] - mt
        Sigma_W += (dx.T @ dx) / N
        Xc[sel] = dx

    # Quality direction in x_mid space (normalized)
    q_dir = yc_n @ Xc
    q_dir = q_dir / (np.linalg.norm(q_dir) + 1e-8)

    # Generalized eigendecomposition of Sigma_B wrt (Sigma_W + reg*I) via symmetric whitening
    reg_w = 0.15 * (np.trace(Sigma_W) / D + 1e-6)
    ew, Uw = np.linalg.eigh(Sigma_W + reg_w * np.eye(D))
    ew = np.maximum(ew, 1e-6)
    W_half_inv = Uw @ np.diag(1.0 / np.sqrt(ew)) @ Uw.T
    W_half = Uw @ np.diag(np.sqrt(ew)) @ Uw.T

    B_tilde = W_half_inv @ Sigma_B @ W_half_inv
    B_tilde = 0.5 * (B_tilde + B_tilde.T)
    eb, Ub = np.linalg.eigh(B_tilde)
    eb = np.maximum(eb, 0.0)

    # Fisher shrinkage operator in whitened space: shrink high between-task eigenvalues!
    shrink = 1.0 / (1.0 + beta_shrink * eb)
    A_fisher = W_half @ (Ub @ np.diag(shrink) @ Ub.T) @ W_half_inv

    # Also gently boost the within-task quality direction (projected through A_fisher so it stays task-clean!)
    q_clean = A_fisher @ q_dir
    q_clean = q_clean / (np.linalg.norm(q_clean) + 1e-8)
    A_total = A_fisher + gamma_qual * np.outer(q_clean, q_clean)

    # Target residual Delta_X* = (A_total - I) (X - mu_X) at the per-view level
    X_all64 = x_mid_all.astype(np.float64)
    Z_all64 = z_all.astype(np.float64)
    valid_rows = np.linalg.norm(X_all64, axis=1) > 1e-6
    Xv = X_all64[valid_rows]
    Zv = Z_all64[valid_rows]
    Delta_target = (Xv - gm_x) @ (A_total - np.eye(D)).T

    # Closed-form ridge fit: Delta_target ~= Zv @ M.T + c
    zm = Zv.mean(0)
    dm = Delta_target.mean(0)
    Zc = Zv - zm
    Dc = Delta_target - dm
    reg_m = 0.25 * (np.trace(Zc.T @ Zc) / D + 1e-6)
    # Solve (Zc^T Zc + reg_m I) M^T = Zc^T Dc
    Mt = np.linalg.solve(Zc.T @ Zc + reg_m * np.eye(D), Zc.T @ Dc)
    M = Mt.T  # (D, D)
    c_bias = dm - M @ zm

    # Optional: keep top `keep_best_nonlinear` low-TaskVar, high-quality original MLP units for in-task LOO!
    max_pairs = (H_mlp - keep_best_nonlinear) // 2
    R = min(rank_cap, D, max_pairs)
    U_m, S_m, Vt_m = np.linalg.svd(M, full_matrices=False)
    U_m, S_m, Vt_m = U_m[:, :R], S_m[:R], Vt_m[:R, :]

    # Select which original MLP units to preserve (highest quality corr * (1 - TaskVar))
    W1_new = np.zeros((H_mlp, D), dtype=np.float64)
    b1_new = np.zeros(H_mlp, dtype=np.float64)
    W2_new = np.zeros((D, H_mlp), dtype=np.float64)
    b2_new = c_bias.copy()

    if keep_best_nonlinear > 0:
        gm_h = Hm.mean(0)
        bt_h = np.zeros(H_mlp)
        wt_h = np.zeros(H_mlp)
        Hc = Hm.copy()
        for t in tr_tasks:
            sel = t_tr == t
            mt = Hm[sel].mean(0)
            bt_h += sel.sum() * (mt - gm_h) ** 2
            wt_h += np.sum((Hm[sel] - mt) ** 2, axis=0)
            Hc[sel] -= mt
        tv_h = bt_h / (bt_h + wt_h + 1e-8)
        q_h = np.abs(yc_n @ (Hc / (np.linalg.norm(Hc, axis=0, keepdims=True) + 1e-8)))
        keep_idx = np.argsort(q_h * (1.0 - tv_h))[::-1][:keep_best_nonlinear]

        W1_orig = orig_sd[f"blocks.{l_last}.mlp.fc1.weight"].numpy().astype(np.float64)
        b1_orig = orig_sd[f"blocks.{l_last}.mlp.fc1.bias"].numpy().astype(np.float64)
        W2_orig = orig_sd[f"blocks.{l_last}.mlp.fc2.weight"].numpy().astype(np.float64)
        ls2_orig = orig_sd[f"blocks.{l_last}.ls2.gamma"].numpy().astype(np.float64)
        # Scale kept nonlinear units gently (0.12x original ls2) so they refine fine LOO detail without task blast
        nl_scale = 0.12
        for slot, u in enumerate(keep_idx):
            dst = 2 * R + slot
            W1_new[dst] = W1_orig[u]
            b1_new[dst] = b1_orig[u]
            W2_new[:, dst] = (ls2_orig * nl_scale) * W2_orig[:, u]

    # Compile EXACT linear map M = U_m diag(S_m) Vt_m into slots 0 .. 2*R-1 via GELU(v^T z) - GELU(-v^T z) = v^T z!
    for k_i in range(R):
        W1_new[2 * k_i] = +Vt_m[k_i]
        W1_new[2 * k_i + 1] = -Vt_m[k_i]
        W2_new[:, 2 * k_i] = +S_m[k_i] * U_m[:, k_i]
        W2_new[:, 2 * k_i + 1] = -S_m[k_i] * U_m[:, k_i]

    sd[f"blocks.{l_last}.mlp.fc1.weight"] = torch.from_numpy(W1_new.astype(np.float32))
    sd[f"blocks.{l_last}.mlp.fc1.bias"] = torch.from_numpy(b1_new.astype(np.float32))
    sd[f"blocks.{l_last}.mlp.fc2.weight"] = torch.from_numpy(W2_new.astype(np.float32))
    sd[f"blocks.{l_last}.mlp.fc2.bias"] = torch.from_numpy(b2_new.astype(np.float32))
    sd[f"blocks.{l_last}.ls2.gamma"] = torch.ones(D, dtype=torch.float32)

    changed = sum(int((sd[k] != orig_sd[k]).sum().item()) for k in orig_sd)
    return sd, {"changed": changed, "rank": R, "kept_nl": keep_best_nonlinear}


# =============================================================================
# GRADIENT FINE-TUNING BASELINE (Matched parameters: final 2 blocks' LayerScale)
# =============================================================================
def ft_late_ls_baseline(model, X_tr, M_tr, y_tr, t_tr, steps=40, lr=3e-2):
    """Matched gradient fine-tuning baseline: trains ONLY the exact same 4 LayerScale vectors
    (blocks.{L-2,L-1}.ls{1,2}.gamma) + a temporary linear probe on within-task centered scores
    using AdamW (no leakage), then returns the fine-tuned DINOv2 state_dict."""
    L, D = len(model.blocks), model.embed_dim
    N = X_tr.shape[0]
    tr_tasks = sorted(set(t_tr))
    yc = y_tr.copy().astype(np.float32)
    for t in tr_tasks:
        sel = t_tr == t
        yc[sel] -= yc[sel].mean()
    yc_t = torch.from_numpy(yc).to(DEVICE)

    for p in model.parameters():
        p.requires_grad_(False)
    trainable = []
    for l in (L - 2, L - 1):
        for p in (model.blocks[l].ls1.gamma, model.blocks[l].ls2.gamma):
            p.requires_grad_(True)
            trainable.append(p)

    # Precompute frozen B[L-3] outputs once so fine-tuning B[L-2..L-1] is super fast & tiny RAM!
    use_v = [0, 1]
    fx = X_tr[:, use_v].reshape(N * len(use_v), 3, 224, 224)
    fm = M_tr[:, use_v].reshape(N * len(use_v))
    idx = torch.where(fm)[0]
    cached = torch.zeros((N * len(use_v), 257, D), dtype=torch.float32)
    with torch.no_grad():
        for k in range(0, len(idx), 16):
            c = idx[k:k + 16]
            x = model.prepare_tokens_with_masks(fx[c].to(device=DEVICE, dtype=torch.float32))
            for i in range(L - 2):
                blk = model.blocks[i]
                x = x + blk.ls1(blk.attn(blk.norm1(x)))
                x = x + blk.ls2(blk.mlp(blk.norm2(x)))
            cached[c] = x.cpu()
    if DEVICE == "mps":
        torch.mps.empty_cache()

    head = torch.nn.Linear(D, 1).to(DEVICE)
    opt = torch.optim.AdamW(trainable + list(head.parameters()), lr=lr, weight_decay=1e-2)
    rng = np.random.default_rng(0)

    for _ in range(steps):
        bi = rng.choice(N, size=min(16, N), replace=False)
        # View 0 (iso-beauty)
        xb = cached[bi * len(use_v)].to(DEVICE)
        for l in (L - 2, L - 1):
            blk = model.blocks[l]
            xb = xb + blk.ls1(blk.attn(blk.norm1(xb)))
            xb = xb + blk.ls2(blk.mlp(blk.norm2(xb)))
        cls = F.normalize(model.norm(xb)[:, 0], dim=-1)
        pred = head(cls).squeeze(-1)
        loss = F.mse_loss(pred, yc_t[bi])
        opt.zero_grad()
        loss.backward()
        opt.step()

    sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    for p in model.parameters():
        p.requires_grad_(False)
    del cached, head, opt
    if DEVICE == "mps":
        torch.mps.empty_cache()
    return sd, {"changed": 4 * D}


def run_benchmark(arch: str, data83, datau, lam=1000, run_ft=False):
    X83, M83, y83, t83 = data83
    Xu, Mu, yu, tu = datau
    unique_tasks = sorted(set(t83))

    print(f"\n================ {arch.upper()} (device={DEVICE}) ================", flush=True)
    m = load_dinov2_local(arch)
    base_sd = {k: v.detach().cpu().clone() for k, v in m.state_dict().items()}
    total_params = sum(p.numel() for p in m.parameters())

    def eval_one(name, program_fn):
        t0 = time.time()
        pt = {}
        for held in unique_tasks:
            tr, te = t83 != held, t83 == held
            m.load_state_dict(base_sd)
            sd, _ = program_fn(m, X83[tr], M83[tr], y83[tr], t83[tr])
            m.load_state_dict(sd)
            E_tr = forward_cls(m, X83[tr], M83[tr]).reshape(tr.sum(), -1)
            E_te = forward_cls(m, X83[te], M83[te]).reshape(te.sum(), -1)
            mu_, sd_ = E_tr.mean(0), E_tr.std(0) + 1e-6
            Xtr, Xte = (E_tr - mu_) / sd_, (E_te - mu_) / sd_
            a = np.linalg.solve(Xtr @ Xtr.T + lam * np.eye(tr.sum()), y83[tr] - y83[tr].mean())
            pred = y83[tr].mean() + (Xte @ Xtr.T) @ a
            pt[held] = calibrate.pairwise(list(y83[te]), list(pred))
        loto = sum(pt[t] * (t83 == t).sum() for t in unique_tasks) / len(y83)

        m.load_state_dict(base_sd)
        sd_full, info = program_fn(m, X83, M83, y83, t83)
        m.load_state_dict(sd_full)
        E83_e = forward_cls(m, X83, M83)
        Eu_e = forward_cls(m, Xu, Mu)
        loo, u48, mj = ridge_eval_pair(E83_e, Eu_e, y83, yu, tu, lam=lam)
        cos = float(np.sum(E83_base * E83_e, axis=-1).mean())
        chg = info.get("changed", 0)
        print(
            f"{name:42s} | LOO={loo:.1%} | LOTO={loto:.1%} "
            f"({pt['arched-spandrel']:.0%}/{pt['handwheel-valve']:.0%}/{pt['reeving-block']:.0%}) | "
            f"U48={u48:.1%} | Jamb={mj:.1%} | cos={cos:.3f} | "
            f"chg={chg:7d} ({100*chg/total_params:.3f}%) [{time.time()-t0:.0f}s]",
            flush=True,
        )
        return sd_full

    m.load_state_dict(base_sd)
    E83_base = forward_cls(m, X83, M83)
    eval_one("Baseline (unmodified)", lambda mod, *a: (base_sd, {"changed": 0}))
    if run_ft:
        eval_one("Fine-Tune Baseline (4 LS vectors, AdamW)", lambda mod, X, M, y, t: ft_late_ls_baseline(mod, X, M, y, t))
    eval_one("WSP-A1: Late LS Damper (0.04/0.30)", lambda mod, X, M, y, t: wsp_late_ls_damper(mod, X, M, y, t, 0.04, 0.30, 0.18))
    eval_one("WSP-H: Sweet-Spot LS+Proj (0.08/0.45)", lambda mod, X, M, y, t: wsp_late_ls_damper(mod, X, M, y, t, 0.08, 0.45, 0.28, alpha_proj=0.85))
    eval_one("WSP-T: Terminal B10->B11 PatchStd+Damper", lambda mod, X, M, y, t: wsp_terminal_patchstd(mod, X, M, y, t, K=12, gain=1.5, last_scale=0.06, prev_scale=0.45, q_boost=0.25))

    del m, base_sd
    gc.collect()
    if DEVICE == "mps":
        torch.mps.empty_cache()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="vits14")
    ap.add_argument("--ft", action="store_true", help="Include matched gradient fine-tuning baseline")
    args = ap.parse_args()
    t0 = time.time()
    data83, datau = load_datasets()
    print(f"Loaded 83 human + 48 unseen (16 tasks) in fp16 ({(data83[0].nbytes+datau[0].nbytes)/1e6:.1f} MB) in {time.time()-t0:.1f}s", flush=True)
    for arch in args.arch.split(","):
        run_benchmark(arch.strip(), data83, datau, run_ft=args.ft)


if __name__ == "__main__":
    main()
