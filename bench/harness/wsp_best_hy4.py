"""Best WeightCode program synthesized by tencent/hy4-flash (Check #4, total session cost $0.00206).
vits14: LOO=86.9%, Strict LOTO=61.1%, U48=67.8%, Jamb22=70.6%, changed=296,448 (1.344%).
"""
import numpy as np
import torch

def weight_program(model, X_tr, M_tr, y_tr, t_tr, helpers):
    """
    Check #4: stronger closed-form late-task suppression with LOO protection.

    Main changes vs Check #3:
      - stronger damping of final block residual streams, especially d_mlp
      - MLP-unit gating uses task-variance dominance, not just correlation score
      - attention head gating penalizes task variance more strongly
      - final LayerNorm reweighting is stronger and uses task-invariant quality SNR
      - adds closed-form between-task mean subtraction to late fc2 biases via
        activation statistics, without touching architecture or using gradients
    """
    with torch.no_grad():
        sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

        n_blocks = len(model.blocks)
        D = int(model.embed_dim)
        n_heads = int(getattr(model.blocks[0].attn, "num_heads", max(1, D // 64)))
        head_dim = D // n_heads if D % n_heads == 0 else None

        # -----------------------------
        # utilities
        # -----------------------------
        def as_tensor(x):
            if isinstance(x, torch.Tensor):
                return x.detach().cpu().float()
            return torch.as_tensor(np.asarray(x), dtype=torch.float32, device="cpu")

        def match_rows(feat, n_expected, V=3):
            feat = as_tensor(feat)
            if feat.ndim == 1:
                feat = feat[:, None]
            if feat.shape[0] == n_expected * V:
                return feat
            if feat.shape[0] == n_expected:
                return feat.repeat_interleave(V, dim=0)
            return None

        def corr_to_target(feat, target, eps=1e-6):
            feat = as_tensor(feat)
            target = as_tensor(target).reshape(-1)
            if feat.ndim == 1:
                feat = feat[:, None]
                scalar = True
            else:
                scalar = False
            if feat.shape[0] != target.shape[0]:
                return None
            fc = feat - feat.mean(0, keepdim=True)
            tc = target - target.mean()
            num = (fc * tc[:, None]).sum(0)
            den = torch.sqrt((fc * fc).sum(0).clamp_min(eps) * (tc * tc).sum().clamp_min(eps) + eps)
            out = num / den
            return out[0] if scalar else out

        def task_stats(feat, tasks, eps=1e-8):
            """
            Returns per-dim:
              tv_frac: between-task variance / total variance
              within_resid: within-task centered activations (n,d)
              task_mean_norm: norm of between-task mean variation
            """
            feat = as_tensor(feat)
            if feat.ndim == 1:
                feat = feat[:, None]
            tasks_np = np.asarray(tasks)
            if feat.shape[0] != len(tasks_np):
                return None

            grand = feat.mean(0, keepdim=True)
            total = ((feat - grand) ** 2).sum(0).clamp_min(eps)
            between = torch.zeros(feat.shape[1], dtype=torch.float32)
            within = torch.zeros_like(feat)

            uniq = sorted(set(tasks_np.tolist()))
            for tt in uniq:
                idx = torch.as_tensor(tasks_np == tt, dtype=torch.bool)
                n = int(idx.sum().item())
                if n == 0:
                    continue
                ft = feat[idx]
                mu = ft.mean(0, keepdim=True)
                between += float(n) * ((mu - grand) ** 2).squeeze(0)
                within[idx] = ft - mu

            tv = (between / total).clamp(0.0, 1.0)
            task_mean_norm = torch.sqrt(between.clamp_min(0.0))
            return tv, within, task_mean_norm

        def robust_score(x):
            x = as_tensor(x).reshape(-1)
            if x.numel() == 0:
                return x
            med = torch.median(x)
            mad = torch.median((x - med).abs()) * 1.4826
            if not torch.isfinite(mad) or float(mad.item()) < 1e-6:
                s = x.std()
                if not torch.isfinite(s) or float(s.item()) < 1e-6:
                    return torch.zeros_like(x)
                return (x - med) / (s + 1e-6)
            return (x - med) / (mad + 1e-6)

        # -----------------------------
        # labels
        # -----------------------------
        y = torch.as_tensor(np.asarray(y_tr, dtype=np.float32), dtype=torch.float32)
        tasks = np.asarray(t_tr)
        N = int(y.numel())

        V = 3
        yv = y.repeat_interleave(V)
        tasks_v = np.repeat(tasks, V)

        late_ids = [i for i in (n_blocks - 3, n_blocks - 2, n_blocks - 1) if 0 <= i < n_blocks]
        head_blocks = [i for i in (n_blocks - 2, n_blocks - 1) if i >= 0]

        late = helpers["collect_late"](model, X_tr, M_tr, late_ids, views=(0, 1, 2))

        # -----------------------------
        # 1) LayerScale damping: stronger on final block
        # -----------------------------
        # Check #3: 0.72/0.70 final, 0.90 penultimate.
        # Check #4: damp final more, but keep penultimate close to baseline to retain LOO.
        for l in late_ids:
            rel = n_blocks - 1 - l
            if rel == 0:
                ls1_damp, ls2_damp = 0.58, 0.52
            elif rel == 1:
                ls1_damp, ls2_damp = 0.88, 0.84
            else:
                continue

            for suffix, damp in (("ls1.gamma", ls1_damp), ("ls2.gamma", ls2_damp)):
                key = f"blocks.{l}.{suffix}"
                if key in sd:
                    sd[key] = (sd[key].float() * damp).to(sd[key].dtype)

        # -----------------------------
        # 2) Attention head surgery
        # -----------------------------
        for l in head_blocks:
            hc = as_tensor(helpers["collect_head_cls"](model, X_tr, M_tr, l, views=(0, 1, 2)))

            if hc.ndim != 3:
                continue
            if hc.shape[1] != n_heads and hc.shape[2] == n_heads:
                hc = hc.transpose(1, 2).contiguous()
            if hc.shape[1] != n_heads or hc.shape[2] != D:
                continue

            hc_v = match_rows(hc, N, V)
            if hc_v is None or hc_v.shape[0] != len(yv):
                continue

            tv_list, qc_list = [], []
            for h in range(n_heads):
                feat_h = hc_v[:, h, :].reshape(hc_v.shape[0], -1)
                st = task_stats(feat_h, tasks_v)
                c = corr_to_target(feat_h, yv)

                if st is None or c is None:
                    tv_list.append(torch.tensor(0.0))
                    qc_list.append(torch.tensor(0.0))
                    continue

                tv, _, tmn = st
                tv_mean = tv.mean()
                q = torch.quantile(c.abs(), 0.90)
                # penalize both task variance fraction and absolute task-mean shift
                qc_list.append(q)
                tv_list.append(tv_mean + 0.25 * torch.tanh(tmn.mean()))

            tv = torch.stack(tv_list)
            qc = torch.stack(qc_list)
            score = robust_score(qc - 1.10 * tv)

            scale = torch.sigmoid(score)
            strength = 0.72 if l == n_blocks - 1 else 0.42
            scale = 1.0 - strength * (1.0 - scale)

            # preserve the two best heads to avoid destroying in-task detail
            kkeep = min(2, n_heads)
            best = torch.topk(score, kkeep).indices
            scale[best] = torch.maximum(scale[best], torch.tensor(0.92))

            key = f"blocks.{l}.attn.proj.weight"
            if key in sd and head_dim is not None:
                w = sd[key].float()
                if w.shape[0] == D:
                    w2 = w.clone()
                    for h in range(n_heads):
                        w2[h * head_dim:(h + 1) * head_dim] *= scale[h]
                    sd[key] = w2.to(sd[key].dtype)

        # -----------------------------
        # 3) MLP unit surgery: prune task units, keep craft units
        # -----------------------------
        for l in head_blocks:
            if l not in late or "d_mlp" not in late:
                continue

            d_mlp = match_rows(late["d_mlp"][l], N, V)
            if d_mlp is None or d_mlp.shape[1] != D or d_mlp.shape[0] != len(yv):
                continue

            st = task_stats(d_mlp, tasks_v)
            c = corr_to_target(d_mlp, yv)
            if st is None or c is None:
                continue

            tv, within, tmn = st
            c = c.abs()

            # Strongly suppress units whose variance is mostly between-task.
            # Protect units with within-task quality correlation.
            score = robust_score(c - 1.25 * tv - 0.20 * torch.tanh(tmn))

            strength = 0.78 if l == n_blocks - 1 else 0.50
            scale = 1.0 - strength * (1.0 - torch.sigmoid(score))

            # hard-kill the most task-pure units in final block
            if l == n_blocks - 1:
                task_pure = (tv > 0.45) & (c < 0.10)
                scale[task_pure] *= 0.25

            # boost rare high-quality low-task units
            craft = (c > 0.12) & (tv < 0.25)
            scale[craft] = torch.clamp(scale[craft] * 1.20, max=1.20)

            # protect top craft units
            k = max(12, int(0.08 * D))
            top = torch.topk(score, min(k, D)).indices
            scale[top] = torch.maximum(scale[top], torch.tensor(0.95))

            wkey = f"blocks.{l}.mlp.fc2.weight"
            bkey = f"blocks.{l}.mlp.fc2.bias"

            if wkey in sd:
                w = sd[wkey].float()
                if w.shape[1] == D:
                    mean_act = d_mlp.mean(0)

                    # Bias compensation preserves expected residual contribution.
                    w_new = w * scale[None, :]
                    sd[wkey] = w_new.to(sd[wkey].dtype)

                    if bkey in sd:
                        b = sd[bkey].float()
                        if b.numel() == D:
                            b_new = b + ((scale - 1.0) * mean_act).to(b.dtype)
                            sd[bkey] = b_new.to(sd[bkey].dtype)

        # -----------------------------
        # 4) Final LayerNorm SNR reweighting
        # -----------------------------
        final_l = n_blocks - 1
        if final_l in late and "in_cls" in late:
            cls = match_rows(late["in_cls"][final_l], N, V)
            if cls is not None and cls.shape[1] == D and cls.shape[0] == len(yv):
                st = task_stats(cls, tasks_v)
                c = corr_to_target(cls, yv)
                if st is not None and c is not None:
                    tv, _, tmn = st
                    score = robust_score(c.abs() - 1.00 * tv - 0.15 * torch.tanh(tmn))
                    ln_scale = 1.0 + 0.22 * torch.tanh(score)
                    ln_scale = torch.clamp(ln_scale, 0.78, 1.22)

                    key = "norm.weight"
                    if key in sd and sd[key].numel() == ln_scale.numel():
                        sd[key] = (sd[key].float() * ln_scale).to(sd[key].dtype)

        # -----------------------------
        # changed count
        # -----------------------------
        changed = 0
        for l in late_ids:
            for suffix in ("ls1.gamma", "ls2.gamma"):
                k = f"blocks.{l}.{suffix}"
                if k in sd:
                    changed += sd[k].numel()
        for l in head_blocks:
            for k in (
                f"blocks.{l}.attn.proj.weight",
                f"blocks.{l}.mlp.fc2.weight",
                f"blocks.{l}.mlp.fc2.bias",
            ):
                if k in sd:
                    changed += sd[k].numel()
        if "norm.weight" in sd:
            changed += sd["norm.weight"].numel()

        return sd, int(changed)
