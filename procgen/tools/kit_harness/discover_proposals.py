#!/usr/bin/env python3
"""Flip MoGe-2 Normal Residuals & Silhouette Overhangs from 'Defects' into 3D Design Proposals.

David Pye (Workmanship of Risk -> Workmanship of Certainty) + Gottfried Semper (Tectonics + Bekleidung):
  - Compares MoGe-2 predicted camera-space normals from FLUX 3 Klein paintings against the
    exact Float32 geometric normals rendered from procgen's calibrated views.
  - Detects BOTH:
      (A) Interior normal deviations (Δθ >= threshold): carved reliefs, window surrounds,
          corner quoins, recessed plaques, masonry courses, brackets, wall lanterns.
      (B) Silhouette overhang protrusions (outside Gen-k silhouette yet solid in Klein + MoGe-2):
          cantilevered Semperian entrance awnings, roof ventilator cupolas/lanterns, projecting cornices.
  - Back-projects high-residual regions into exact 3D procgen Z-up coordinates, clusters
    multi-view-consistent regions into ranked 3D Design Proposals (P01..P08), and routes
    each proposal into:
      * TIER_1_SHALLOW_RELIEF       -> 0x8007 SDF surface details (courses, grooves, rivets, signage)
      * TIER_2_STRUCTURAL_ORNAMENT  -> 3D procgen geometry (SDS, kitlib_arch, shrinkwrap, Semperian patch/loft)
  - Emits per-view residual heatmaps, per-proposal [Clay | Klein | Residual] triplet crops,
    and an annotated multi-view Proposal Contact Sheet for both the Observatory UI and the Modeling Agent.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def yup_to_zup(p: np.ndarray) -> np.ndarray:
    # Three.js Y-up (x, y, z) -> procgen Z-up (x, -z, y)
    return np.stack([p[..., 0], -p[..., 2], p[..., 1]], axis=-1)


def load_gbuff(path: Path, size: int) -> np.ndarray:
    arr = np.frombuffer(path.read_bytes(), dtype=np.float32).reshape((size, size, 4))
    # WebGL readRenderTargetPixels is bottom-up; flip vertically to top-down image space!
    return arr[::-1].copy()


def turbo_colormap(t: np.ndarray) -> np.ndarray:
    """Compact analytic Turbo-like perceptual heatmap for residual angles in [0, 1]."""
    t = np.clip(t, 0.0, 1.0)
    r = np.clip(0.1357 + t * (4.6154 + t * (-42.6603 + t * (132.1311 + t * (-152.9424 + t * 59.2864)))), 0, 1)
    g = np.clip(0.0914 + t * (2.2642 + t * (14.0268 + t * (-72.2120 + t * (104.2480 + t * -48.3292)))), 0, 1)
    b = np.clip(0.1067 + t * (12.6419 + t * (-60.5820 + t * (110.3628 + t * (-89.9031 + t * 27.3482)))), 0, 1)
    return (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)


def decode_moge_normal(img_path: Path, size: int) -> np.ndarray:
    im = Image.open(img_path).convert("RGB").resize((size, size), Image.Resampling.BILINEAR)
    arr = np.asarray(im, dtype=np.float32) / 255.0 * 2.0 - 1.0
    ln = np.linalg.norm(arr, axis=-1, keepdims=True)
    return arr / np.maximum(ln, 1e-6)


def decode_moge_valid(img_path: Path, size: int) -> np.ndarray:
    im = Image.open(img_path).convert("L").resize((size, size), Image.Resampling.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


def auto_align_moge_frame(geo_cam_n: np.ndarray, moge_n: np.ndarray, valid_mask: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Auto-detect exact camera-space sign convention between OpenGL view-space normals and MoGe-2 normal map."""
    best_score = -1e9
    best_signs = [1, 1, 1]
    if valid_mask.sum() < 50:
        return moge_n, [1, -1, -1]
    g = geo_cam_n[valid_mask]
    m = moge_n[valid_mask]
    for sx in (1, -1):
        for sy in (1, -1):
            for sz in (1, -1):
                dot = g[:, 0] * (m[:, 0] * sx) + g[:, 1] * (m[:, 1] * sy) + g[:, 2] * (m[:, 2] * sz)
                score = float(np.median(dot))
                if score > best_score:
                    best_score = score
                    best_signs = [sx, sy, sz]
    aligned = moge_n * np.array(best_signs, dtype=np.float32)[None, None, :]
    return aligned, best_signs


def describe_zone(center_zup: list[float], normal_zup: list[float], bbox_min: list[float], bbox_max: list[float]) -> str:
    x, y, z = center_zup
    nx, ny, nz = normal_zup
    parts = []
    if nz > 0.65 or z > 4.05:
        parts.append("Roof / Parapet Crown")
    elif ny < -0.55 or y < -1.8:
        parts.append("Front Facade (-Y)")
    elif ny > 0.55 or y > 1.8:
        parts.append("Rear Facade (+Y)")
    elif nx > 0.55:
        parts.append("East Side (+X)")
    elif nx < -0.55:
        parts.append("West Side (-X)")
    else:
        parts.append("Corner / Edge")

    if abs(x) > 2.35 and abs(y) > 1.65:
        parts.append("Corner Quoin Zone")
    elif abs(x) < 1.15 and y < -1.6 and 2.4 <= z <= 3.75:
        parts.append("Central Door Head / Awning / Frieze")
    elif abs(x) < 1.15 and y < -1.6 and z < 2.4:
        parts.append("Central Entrance Doorway")
    elif abs(x) >= 1.2 and y < -1.6 and 1.1 <= z <= 3.2:
        parts.append("Left Window Bay" if x < 0 else "Right Window Bay")
    elif z >= 3.65 and z <= 4.35:
        parts.append("Upper Cornice / Frieze")
    elif z < 0.85:
        parts.append("Plinth / Base")
    return " · ".join(parts)


def make_label_font(size: int = 18):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-proposals", type=int, default=8)
    args = ap.parse_args()

    views_dir = Path(args.views_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((views_dir / "views.json").read_text())
    views = manifest["views"]

    all_pts_zup = []
    all_nrm_zup = []
    all_res_deg = []
    all_is_overhang = []
    all_view_idx = []
    all_uv = []  # normalized [u, v] in [0, 1]

    view_metrics = []
    per_view_cache = []

    for vi, v in enumerate(views):
        G = int(v.get("gbuffSize", 512))
        geo_pos = load_gbuff(views_dir / v["geoPosBin"], G)
        geo_nrm = load_gbuff(views_dir / v["geoNrmBin"], G)
        geo_mask = geo_nrm[..., 3] > 0.5

        # Transform world normal into camera space using viewMatrix (column-major from Three.js)
        V = np.array(v["viewMatrix"], dtype=np.float32).reshape((4, 4), order="F")
        V_rot = V[:3, :3]
        geo_cam_n = geo_nrm[..., :3] @ V_rot.T
        geo_cam_n = geo_cam_n / np.maximum(np.linalg.norm(geo_cam_n, axis=-1, keepdims=True), 1e-6)

        moge_raw = decode_moge_normal(views_dir / v["mogeNormal"], G)
        moge_valid = decode_moge_valid(views_dir / v["mogeValid"], G) > 0.35
        moge_n, signs = auto_align_moge_frame(geo_cam_n, moge_raw, geo_mask & moge_valid)

        # Interior angular residual Δθ in degrees
        dot = np.clip(np.sum(geo_cam_n * moge_n, axis=-1), -1.0, 1.0)
        res_deg = np.degrees(np.arccos(dot))
        # Erode silhouette boundary slightly (3 px) so grazing edges don't count as interior residuals
        eroded = geo_mask.copy()
        for _ in range(3):
            eroded[1:-1, 1:-1] &= (
                eroded[:-2, 1:-1] & eroded[2:, 1:-1] & eroded[1:-1, :-2] & eroded[1:-1, 2:]
            )
        valid_interior = eroded & moge_valid

        # High-frequency normal relief energy (Sobel gradient of MoGe-2 normals)
        gx = np.abs(moge_n[:, 2:, :] - moge_n[:, :-2, :]).sum(axis=-1)
        gy = np.abs(moge_n[2:, :, :] - moge_n[:-2, :, :]).sum(axis=-1)
        grad = np.zeros((G, G), dtype=np.float32)
        grad[1:-1, 1:-1] = np.sqrt(gx[1:-1, :] ** 2 + gy[:, 1:-1] ** 2)

        # Silhouette Overhang Mask: outside geo_mask, near the building, where FLUX 3 Klein painted solid structure
        # (e.g., roof ventilator lantern rising above parapet, projecting awning, brackets, lanterns!)
        klein_im = Image.open(views_dir / v["painted"]).convert("RGB").resize((G, G), Image.Resampling.BILINEAR)
        clay_im = Image.open(views_dir / v["clay"]).convert("RGB").resize((G, G), Image.Resampling.BILINEAR)
        klein_arr = np.asarray(klein_im, dtype=np.float32)
        clay_arr = np.asarray(clay_im, dtype=np.float32)

        # Estimate neutral grey backdrop color from top corners of Klein image
        bg_samples = np.concatenate([
            klein_arr[:20, :20].reshape(-1, 3),
            klein_arr[:20, -20:].reshape(-1, 3),
        ], axis=0)
        bg_col = np.median(bg_samples, axis=0)
        bg_dist = np.linalg.norm(klein_arr - bg_col[None, None, :], axis=-1)

        # Dilate geo_mask by 48 pixels (~0.9m) to keep overhangs attached to building (exclude ground shadow/horizon)
        near_bldg = geo_mask.copy()
        for _ in range(44):
            nb = near_bldg.copy()
            nb[1:, :] |= near_bldg[:-1, :]
            nb[:-1, :] |= near_bldg[1:, :]
            nb[:, 1:] |= near_bldg[:, :-1]
            nb[:, :-1] |= near_bldg[:, 1:]
            near_bldg = nb

        # Exclude ground plane below building foot (keep pixels above lowest building pixel + slight margin)
        ys_bldg = np.where(geo_mask.any(axis=1))[0]
        y_floor_cut = int(ys_bldg.max()) - 8 if len(ys_bldg) else G
        above_floor = np.zeros((G, G), dtype=bool)
        above_floor[:max(0, y_floor_cut), :] = True

        overhang_mask = (~geo_mask) & near_bldg & above_floor & moge_valid & (bg_dist > 28.0)

        # Compute summary metrics on valid interior pixels
        interior_vals = res_deg[valid_interior]
        mean_deg = float(np.mean(interior_vals)) if len(interior_vals) else 0.0
        median_deg = float(np.median(interior_vals)) if len(interior_vals) else 0.0
        p90_deg = float(np.percentile(interior_vals, 90)) if len(interior_vals) else 0.0
        high_res_pct = float(np.mean(interior_vals >= 25.0) * 100.0) if len(interior_vals) else 0.0
        overhang_pct = float(overhang_mask.sum() / max(1, geo_mask.sum()) * 100.0)

        view_metrics.append({
            "id": v["id"],
            "label": v["label"],
            "mogeSignAlign": signs,
            "meanResidualDeg": round(mean_deg, 2),
            "medianResidualDeg": round(median_deg, 2),
            "p90ResidualDeg": round(p90_deg, 2),
            "highResidualPct": round(high_res_pct, 2),
            "overhangPct": round(overhang_pct, 2),
            "interiorPixels": int(valid_interior.sum()),
        })

        # Render visual Normal Residual Heatmap + Silhouette Overhang overlay (1024x1024)
        norm_t = np.clip((res_deg - 8.0) / 48.0, 0.0, 1.0)
        heat_rgb = turbo_colormap(norm_t).astype(np.float32)
        base_blend = clay_arr * 0.42 + klein_arr * 0.58
        alpha = np.where(valid_interior, np.clip(norm_t * 0.88 + 0.08, 0.0, 0.92), 0.0)[..., None]
        vis = base_blend * (1.0 - alpha) + heat_rgb * alpha
        # Paint detected silhouette overhangs in electric cyan so they pop immediately!
        cyan = np.array([34.0, 211.0, 238.0], dtype=np.float32)
        ov_a = np.where(overhang_mask, 0.78, 0.0)[..., None]
        vis = vis * (1.0 - ov_a) + cyan * ov_a
        heat_out_name = f"{v['id']}_residual_heat.png"
        Image.fromarray(np.clip(vis, 0, 255).astype(np.uint8)).resize((1024, 1024), Image.Resampling.BILINEAR).save(
            views_dir / heat_out_name
        )

        per_view_cache.append({
            "v": v,
            "klein": klein_im,
            "clay": clay_im,
            "heat": Image.fromarray(np.clip(vis, 0, 255).astype(np.uint8)),
            "res_deg": res_deg,
            "geo_mask": geo_mask,
            "overhang_mask": overhang_mask,
        })

        # Collect high-residual interior 3D points (strided by 2 for crisp fast clustering)
        # Use sharp >= 34 deg threshold for discrete structural elements (Tier 2), plus moderate 14..30 deg
        # band for shallow surface relief (Tier 1: ashlar courses, masonry joints, flat plaques)!
        stride = 2
        ys, xs = np.where(valid_interior[::stride, ::stride] & (res_deg[::stride, ::stride] >= 34.0))
        ys = ys * stride
        xs = xs * stride
        if len(ys):
            pts_z = yup_to_zup(geo_pos[ys, xs, :3])
            nrm_z = yup_to_zup(geo_nrm[ys, xs, :3])
            all_pts_zup.append(pts_z)
            all_nrm_zup.append(nrm_z)
            all_res_deg.append(res_deg[ys, xs])
            all_is_overhang.append(np.zeros(len(ys), dtype=bool))
            all_view_idx.append(np.full(len(ys), vi, dtype=np.int32))
            all_uv.append(np.stack([xs / float(G), ys / float(G)], axis=-1))

        # Exclude bottom 12% of building silhouette from overhang detection so ground contact shadow never counts as overhang
        if len(ys_bldg):
            b_top, b_bot = int(ys_bldg.min()), int(ys_bldg.max())
            y_floor_cut = b_bot - max(18, int((b_bot - b_top) * 0.14))
            overhang_mask[y_floor_cut:, :] = False

        # Collect silhouette-overhang 3D points by snapping each overhang pixel to nearest boundary building pixel!
        oys, oxs = np.where(overhang_mask[::4, ::4])
        oys = oys * 4
        oxs = oxs * 4
        if len(oys):
            # Build boundary pixel coordinate list
            b_edge = geo_mask & (~eroded)
            bys, bxs = np.where(b_edge)
            if len(bys):
                # Subsample boundary for instant nearest search
                step = max(1, len(bys) // 1200)
                bys_s, bxs_s = bys[::step].astype(np.float32), bxs[::step].astype(np.float32)
                b_pts = geo_pos[bys[::step], bxs[::step], :3]
                b_nrm = geo_nrm[bys[::step], bxs[::step], :3]

                dy = oys[:, None].astype(np.float32) - bys_s[None, :]
                dx = oxs[:, None].astype(np.float32) - bxs_s[None, :]
                nearest = np.argmin(dx * dx + dy * dy, axis=1)
                snap_pos = b_pts[nearest].copy()
                snap_nrm = b_nrm[nearest].copy()
                # Nudge slightly upward/outward in world Y-up based on pixel offset
                # Approximate world metres per pixel at depth d
                fov_rad = math.radians(v["fov"])
                depth_est = np.maximum(2.0, geo_pos[bys[::step][nearest], bxs[::step][nearest], 3])
                m_per_px = (2.0 * depth_est * math.tan(fov_rad * 0.5)) / float(G)
                # Camera right and up in world space (rows 0 and 1 of V_rot)
                cam_right = V_rot[0]
                cam_up = V_rot[1]
                px_dx = (oxs.astype(np.float32) - bxs_s[nearest]) * m_per_px
                px_dy = -(oys.astype(np.float32) - bys_s[nearest]) * m_per_px
                snap_pos = snap_pos + px_dx[:, None] * cam_right[None, :] + px_dy[:, None] * cam_up[None, :]

                pts_z = yup_to_zup(snap_pos)
                nrm_z = yup_to_zup(snap_nrm)
                all_pts_zup.append(pts_z)
                all_nrm_zup.append(nrm_z)
                all_res_deg.append(np.full(len(oys), 58.0, dtype=np.float32))
                all_is_overhang.append(np.ones(len(oys), dtype=bool))
                all_view_idx.append(np.full(len(oys), vi, dtype=np.int32))
                all_uv.append(np.stack([oxs / float(G), oys / float(G)], axis=-1))

    # Aggregate 3D residual points & cluster on a 0.22 m voxel grid + normal-octant coherence
    proposals = []
    if all_pts_zup:
        P = np.concatenate(all_pts_zup, axis=0)
        N = np.concatenate(all_nrm_zup, axis=0)
        R = np.concatenate(all_res_deg, axis=0)
        O = np.concatenate(all_is_overhang, axis=0)
        VI = np.concatenate(all_view_idx, axis=0)
        UV = np.concatenate(all_uv, axis=0)

        # Height-adaptive architectural zone bucketing (works for 1-storey pavilions AND 2-storey watchtowers!)
        z_max_all = float(np.percentile(P[:, 2], 98))
        x_abs_max = float(np.percentile(np.abs(P[:, 0]), 96))
        y_abs_max = float(np.percentile(np.abs(P[:, 1]), 96))
        is_tall = z_max_all > 5.8

        def elem_bucket(pt: np.ndarray, nrm: np.ndarray, is_ov: bool) -> int:
            x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
            nx, ny, nz = float(nrm[0]), float(nrm[1]), float(nrm[2])
            if is_ov and z > z_max_all * 0.78:
                return 10  # Roof / Watchtower Cupola Overhang!
            if z > z_max_all * 0.85 or nz > 0.65:
                return 11  # Upper Crown / Tower Cap / Parapet
            if is_tall and z > 6.1 and x > 0.8:
                return 17  # Rising Watchtower Oculus & Lantern Room
            if z > (5.6 if is_tall else 3.45) and ny < -0.5:
                return 12  # Front Dentilled Cornice & Brackets
            if abs(x) > x_abs_max * 0.78 and abs(y) > y_abs_max * 0.78:
                return 13 + (0 if x < 0 else 1) + (0 if y < 0 else 2)  # 4 corner quoin stacks!
            if ny < -0.5:
                if is_tall:
                    if z >= 3.2 and x < 1.2:
                        return 18  # Upper Front Watch Balcony & Railings
                    if z < 3.2 and abs(x + 0.85) < 1.05:
                        return 19  # Arched Boolean Entrance Portal & Fanlight
                    return 20 if x < 0 else 21
                if abs(x) < 1.15 and z >= 2.85:
                    return 17
                if abs(x) < 1.15 and z >= 1.95:
                    return 18
                if abs(x) < 1.15 and z < 1.95:
                    return 19
                return 20 if x < -1.15 else 21
            if nx > 0.5:
                return 22
            if nx < -0.5:
                return 23
            return 24

        voxel = 0.22
        q_pos = np.floor(P / voxel).astype(np.int32)
        eb = np.array([elem_bucket(P[i], N[i], bool(O[i])) for i in range(len(P))], dtype=np.int32)

        vox_map: dict[tuple[int, int, int, int], list[int]] = {}
        for idx_i in range(len(P)):
            key = (int(q_pos[idx_i, 0]), int(q_pos[idx_i, 1]), int(q_pos[idx_i, 2]), int(eb[idx_i]))
            vox_map.setdefault(key, []).append(idx_i)

        # Keep voxels with enough support (noise rejection)
        solid_vox = {k: pts for k, pts in vox_map.items() if len(pts) >= 4}
        visited = set()
        raw_clusters = []

        for seed_k in solid_vox:
            if seed_k in visited:
                continue
            q = deque([seed_k])
            visited.add(seed_k)
            cluster_pts = []
            while q:
                cx, cy, cz, cn = q.popleft()
                cluster_pts.extend(solid_vox[(cx, cy, cz, cn)])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            if dx == 0 and dy == 0 and dz == 0:
                                continue
                            nk = (cx + dx, cy + dy, cz + dz, cn)
                            if nk in solid_vox and nk not in visited:
                                visited.add(nk)
                                q.append(nk)
            if len(cluster_pts) >= 20:
                raw_clusters.append(np.array(cluster_pts, dtype=np.int32))

        # Rank clusters by energy = count * mean_residual * (1 + 0.5 * extra_views + overhang_boost)
        # Prioritize Front Hero / Roof Cupola / Awning / OCTROI / Window / Quoin diversity (max 1 cluster per bucket)
        best_per_bucket: dict[int, tuple] = {}
        for c_idx in raw_clusters:
            pts = P[c_idx]
            nrms = N[c_idx]
            ress = R[c_idx]
            ovs = O[c_idx]
            b_id = int(eb[c_idx[0]])
            vis_views = sorted(set(int(x) for x in VI[c_idx]))
            mean_r = float(np.mean(ress))
            p90_r = float(np.percentile(ress, 90))
            ov_ratio = float(np.mean(ovs))
            front_or_roof_boost = 1.65 if b_id in (10, 12, 13, 14, 17, 18, 20, 21) else 1.0
            score = len(c_idx) * (mean_r / 30.0) * (1.0 + 0.35 * (len(vis_views) - 1)) * (1.0 + 1.8 * ov_ratio) * front_or_roof_boost
            cand = (score, b_id, c_idx, pts, nrms, ress, ovs, vis_views, mean_r, p90_r, ov_ratio)
            if b_id not in best_per_bucket or score > best_per_bucket[b_id][0]:
                best_per_bucket[b_id] = cand

        scored = sorted(best_per_bucket.values(), key=lambda x: -x[0])
        top = scored[: args.max_proposals]

        BUCKET_LABELS = (
            {
                10: "Watchtower Copper Cupola & Finial (Silhouette Overhang)",
                11: "Upper Parapet & Tower Crown Moulding",
                12: "Front Dentilled Cornice & Brackets",
                13: "Front-West Corner Quoin Stack",
                14: "Front-East Tower Corner Quoin Stack",
                15: "Rear-West Corner Quoin Stack",
                16: "Rear-East Corner Quoin Stack",
                17: "Watchtower Circular Oculus & Bronze Bezel",
                18: "Upper Front Cantilevered Iron Watch Balcony",
                19: "Deep-Reveal Arched Entrance Portal & Fanlight",
                20: "Front-Left Deep-Reveal Window Bays",
                21: "Front-Right Tower Window Bays",
                22: "East Tower Elevation Ashlar & Oculus Trim",
                23: "West Elevation Ashlar & Deep Window Trim",
                24: "Rear Elevation Masonry & Service Door",
            }
            if is_tall
            else {
                10: "Roof Ventilator Cupola (Silhouette Overhang)",
                11: "Parapet & Upper Cornice Moulding",
                12: "Front Dentilled Cornice & Brackets",
                13: "Front-West Corner Quoin Stack",
                14: "Front-East Corner Quoin Stack",
                15: "Rear-West Corner Quoin Stack",
                16: "Rear-East Corner Quoin Stack",
                17: "Central 'OCTROI' Bronze Nameplate Frieze",
                18: "Semperian Striped Awning & Scroll Brackets",
                19: "Panelled Entrance Door & Globe Lantern Sconces",
                20: "Front-Left Stepped Window Surround & Pediment",
                21: "Front-Right Stepped Window Surround & Pediment",
                22: "East Elevation Ashlar Wall & Window Trim",
                23: "West Elevation Ashlar Wall & Window Trim",
                24: "Rear Elevation Masonry & Service Door",
            }
        )
        # Buckets 17 (bronze nameplate plaque), 22/23/24 (wall ashlar courses & seams) -> Tier 1 0x8007 shallow relief!
        TIER1_BUCKETS = {17, 22, 23, 24}

        for rank, (score, b_id, c_idx, pts, nrms, ress, ovs, vis_views, mean_r, p90_r, ov_ratio) in enumerate(top, start=1):
            pid = f"P{rank:02d}"
            bmin = np.percentile(pts, 4, axis=0)
            bmax = np.percentile(pts, 96, axis=0)
            ext = np.maximum(bmax - bmin, 0.05)
            center = np.mean(pts, axis=0)
            avg_n = np.mean(nrms, axis=0)
            avg_n = avg_n / max(1e-6, float(np.linalg.norm(avg_n)))

            est_d_over_w = round(float(math.tan(math.radians(min(mean_r, 65.0))) * 0.35 + ov_ratio * 0.55), 3)
            is_shallow_bucket = b_id in TIER1_BUCKETS and ov_ratio < 0.12
            is_structural = (not is_shallow_bucket) and ((ov_ratio >= 0.08) or (p90_r >= 38.0) or (est_d_over_w >= 0.22))
            tier = "TIER_2_STRUCTURAL_ORNAMENT" if is_structural else "TIER_1_SHALLOW_RELIEF"
            zone_label = BUCKET_LABELS.get(b_id, describe_zone([float(x) for x in center], [float(x) for x in avg_n], [float(x) for x in bmin], [float(x) for x in bmax]))
            routing = (
                "Synthesize 3D procgen geometry (kitlib_arch trim/quoins/surrounds, Chaikin SDS limit=1 lanterns/brackets, "
                "shrinkwrap bosses, or Semperian patch/loft awning canopy)"
                if is_structural
                else "Emit 0x8007 SDF surface details (G.courses ashlar joints, G.seam, G.rivets, G.fasteners, G.stamp, or kittype.inscribe plaque)"
            )

            # Pick best camera view observing this cluster (most points in that view)
            best_vi = max(vis_views, key=lambda vi_k: int((VI[c_idx] == vi_k).sum()))
            v_mask = VI[c_idx] == best_vi
            uvs = UV[c_idx][v_mask]
            u0, v0 = float(np.percentile(uvs[:, 0], 3)), float(np.percentile(uvs[:, 1], 3))
            u1, v1 = float(np.percentile(uvs[:, 0], 97)), float(np.percentile(uvs[:, 1], 97))
            # Pad crop nicely so context is unmistakable
            pad = 0.08
            cu0, cv0 = max(0.0, u0 - pad), max(0.0, v0 - pad)
            cu1, cv1 = min(1.0, u1 + pad), min(1.0, v1 + pad)
            # Enforce minimum crop size (0.24 of frame)
            if cu1 - cu0 < 0.24:
                mid = 0.5 * (cu0 + cu1)
                cu0, cu1 = max(0.0, mid - 0.12), min(1.0, mid + 0.12)
            if cv1 - cv0 < 0.24:
                mid = 0.5 * (cv0 + cv1)
                cv0, cv1 = max(0.0, mid - 0.12), min(1.0, mid + 0.12)

            vc = per_view_cache[best_vi]
            G_im = vc["klein"].size[0]
            box_px = (int(cu0 * G_im), int(cv0 * G_im), max(int(cu0 * G_im) + 16, int(cu1 * G_im)), max(int(cv0 * G_im) + 16, int(cv1 * G_im)))

            crop_h = 240
            def prep_crop(im_src: Image.Image) -> Image.Image:
                cr = im_src.crop(box_px)
                aspect = cr.size[0] / max(1, cr.size[1])
                w = max(140, min(320, int(round(crop_h * aspect))))
                return cr.resize((w, crop_h), Image.Resampling.LANCZOS)

            c_clay = prep_crop(vc["clay"])
            c_klein = prep_crop(vc["klein"])
            c_heat = prep_crop(vc["heat"])
            tw = c_clay.size[0]
            triplet = Image.new("RGB", (tw * 3 + 12, crop_h + 30), (18, 19, 22))
            triplet.paste(c_clay, (0, 30))
            triplet.paste(c_klein, (tw + 6, 30))
            triplet.paste(c_heat, (tw * 2 + 12, 30))
            draw = ImageDraw.Draw(triplet)
            font = make_label_font(14)
            tier_col = (34, 211, 238) if is_structural else (251, 191, 36)
            tier_short = "TIER 2 · 3D GEOMETRY" if is_structural else "TIER 1 · 0x8007 RELIEF"
            draw.text((8, 7), f"{pid} · {tier_short} · Δθ={mean_r:.1f}° · {views[best_vi]['label']}", fill=tier_col, font=font)
            crop_name = f"proposal_{pid}.png"
            triplet.save(out_dir / crop_name)

            # Z-up -> Y-up for Three.js Observatory 3D pin & bbox rendering: (x, y, z)_zup -> (x, z, -y)_yup
            center_z = [round(float(x), 3) for x in center]
            nrm_z = [round(float(x), 3) for x in avg_n]
            bmin_z = [round(float(x), 3) for x in bmin]
            bmax_z = [round(float(x), 3) for x in bmax]

            center_y = [center_z[0], center_z[2], -center_z[1]]
            bmin_y = [bmin_z[0], bmin_z[2], -bmax_z[1]]
            bmax_y = [bmax_z[0], bmax_z[2], -bmin_z[1]]

            proposals.append({
                "id": pid,
                "zone": zone_label,
                "tier": tier,
                "routing": routing,
                "center_zup": center_z,
                "bbox_min_zup": bmin_z,
                "bbox_max_zup": bmax_z,
                "extent_m": [round(float(x), 3) for x in ext],
                "normal_zup": nrm_z,
                "center_yup": [round(x, 3) for x in center_y],
                "bbox_min_yup": [round(x, 3) for x in bmin_y],
                "bbox_max_yup": [round(x, 3) for x in bmax_y],
                "mean_residual_deg": round(mean_r, 1),
                "p90_residual_deg": round(p90_r, 1),
                "overhang_ratio": round(ov_ratio, 3),
                "est_depth_to_width": est_d_over_w,
                "views_seen": [views[k]["id"] for k in vis_views],
                "best_view": views[best_vi]["id"],
                "best_view_idx": best_vi,
                "uv_box": [round(u0, 3), round(v0, 3), round(u1, 3), round(v1, 3)],
                "crop": crop_name,
                "point_count": int(len(c_idx)),
            })

    # Build a 2x3 Annotated Proposal Contact Sheet (showing FLUX 3 Klein views with numbered 3D proposal boxes!)
    tile = 440
    sheet = Image.new("RGB", (tile * 3 + 24, tile * 2 + 64), (15, 16, 20))
    sdraw = ImageDraw.Draw(sheet)
    hfont = make_label_font(18)
    lfont = make_label_font(14)
    sdraw.text((14, 14), "Bidirectional Co-Evolution · FLUX 3 Klein Painted Views + MoGe-2 Discovered 3D Proposals (Cyan=Tier 2 3D, Amber=Tier 1 0x8007)", fill=(240, 242, 245), font=hfont)

    for vi in range(min(6, len(per_view_cache))):
        col = vi % 3
        row = vi // 3
        ox = 8 + col * (tile + 8)
        oy = 48 + row * (tile + 8)
        im_k = per_view_cache[vi]["klein"].resize((tile, tile), Image.Resampling.LANCZOS)
        sheet.paste(im_k, (ox, oy))
        sdraw.rectangle([ox, oy, ox + tile, oy + 24], fill=(15, 16, 20))
        vm = view_metrics[vi]
        sdraw.text((ox + 6, oy + 4), f"{vm['label']} · Δθ={vm['meanResidualDeg']}° (>{vm['highResidualPct']}%)", fill=(215, 220, 228), font=lfont)

        # Draw all proposals whose best_view is this tile
        for p in proposals:
            if p["best_view_idx"] != vi:
                continue
            u0, v0, u1, v1 = p["uv_box"]
            x0, y0 = ox + int(u0 * tile), oy + int(v0 * tile)
            x1, y1 = ox + int(u1 * tile), oy + int(v1 * tile)
            col_box = (34, 211, 238) if p["tier"] == "TIER_2_STRUCTURAL_ORNAMENT" else (251, 191, 36)
            sdraw.rectangle([x0, y0, x1, y1], outline=col_box, width=3)
            sdraw.rectangle([x0, max(oy + 24, y0 - 18), x0 + 44, max(oy + 42, y0)], fill=col_box)
            sdraw.text((x0 + 5, max(oy + 25, y0 - 17)), p["id"], fill=(12, 14, 18), font=lfont)

    sheet_path = out_dir / "proposals_sheet.png"
    sheet.save(sheet_path)

    # Also build a 2x3 Residual Heatmap Sheet for instant visual comparison across generations
    h_sheet = Image.new("RGB", (tile * 3 + 24, tile * 2 + 64), (15, 16, 20))
    hdraw = ImageDraw.Draw(h_sheet)
    avg_res = float(np.mean([m["meanResidualDeg"] for m in view_metrics])) if view_metrics else 0.0
    avg_hi = float(np.mean([m["highResidualPct"] for m in view_metrics])) if view_metrics else 0.0
    avg_ov = float(np.mean([m["overhangPct"] for m in view_metrics])) if view_metrics else 0.0
    hdraw.text((14, 14), f"MoGe-2 Normal Residual Field · Mean Δθ = {avg_res:.2f}° · High-Residual (>25°) = {avg_hi:.1f}% · Silhouette Overhang = {avg_ov:.1f}%", fill=(240, 242, 245), font=hfont)
    for vi in range(min(6, len(per_view_cache))):
        col = vi % 3
        row = vi // 3
        ox = 8 + col * (tile + 8)
        oy = 48 + row * (tile + 8)
        im_h = per_view_cache[vi]["heat"].resize((tile, tile), Image.Resampling.LANCZOS)
        h_sheet.paste(im_h, (ox, oy))
        hdraw.rectangle([ox, oy, ox + tile, oy + 24], fill=(15, 16, 20))
        vm = view_metrics[vi]
        hdraw.text((ox + 6, oy + 4), f"{vm['label']} · Δθ={vm['meanResidualDeg']}° · Ov={vm['overhangPct']}%", fill=(251, 191, 36), font=lfont)
    h_sheet.save(out_dir / "residual_sheet.png")

    summary = {
        "meanResidualDeg": round(avg_res, 2),
        "highResidualPct": round(avg_hi, 2),
        "overhangPct": round(avg_ov, 2),
        "tier1Count": sum(1 for p in proposals if p["tier"] == "TIER_1_SHALLOW_RELIEF"),
        "tier2Count": sum(1 for p in proposals if p["tier"] == "TIER_2_STRUCTURAL_ORNAMENT"),
        "views": view_metrics,
        "proposals": proposals,
        "sheets": {
            "proposals": "proposals_sheet.png",
            "residual": "residual_sheet.png",
        },
    }
    (out_dir / "proposals.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({
        "meanResidualDeg": summary["meanResidualDeg"],
        "highResidualPct": summary["highResidualPct"],
        "overhangPct": summary["overhangPct"],
        "proposals": len(proposals),
        "tier1": summary["tier1Count"],
        "tier2": summary["tier2Count"],
    }))


if __name__ == "__main__":
    main()
