"""step 5: run the u-net over every date of the season and build the burn map.

each pixel counts once, on the first date it shows up newly burnt, together with the window it
burnt in (its last clear look before, this look)."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, indices, timeline  # noqa: E402
from pyroscan.model import UNet  # noqa: E402

_spec = importlib.util.spec_from_file_location("train", Path(__file__).with_name("04_train.py"))
_train = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_train)

TILE, OV = 1024, 64


@torch.no_grad()
def _forward(model, tt, tta: bool):
    """sigmoid output, averaged over 4 flips when tta is on."""
    with torch.autocast("cuda", dtype=torch.float16):
        if not tta:
            return torch.sigmoid(model(tt).float())
        acc = 0
        for dims in ([], [2], [3], [2, 3]):
            x = torch.flip(tt, dims) if dims else tt
            y = torch.sigmoid(model(x).float())
            acc = acc + (torch.flip(y, dims) if dims else y)
        return acc / 4


@torch.no_grad()
def predict(model, pre, post, valid, mean, std, device="cuda", tta: bool = False) -> np.ndarray:
    """probability map for one date pair, tiled with overlap."""
    h, w = valid.shape
    x = np.concatenate([pre, post], 0)
    x = np.where(np.isfinite(x) & valid[None], x, 0.0)
    xq = np.concatenate([np.clip(x * 10000, 0, 65535), valid[None].astype(np.float32)], 0).astype(np.uint16)
    out = np.zeros((h, w), np.float32)
    step = TILE - 2 * OV
    for r0 in range(0, h, step):
        for c0 in range(0, w, step):
            ra, ca = max(r0 - OV, 0), max(c0 - OV, 0)
            rb, cb = min(r0 + step + OV, h), min(c0 + step + OV, w)
            if not valid[ra:rb, ca:cb].any():
                continue
            t = _train.normalise(xq[:, ra:rb, ca:cb], mean, std)
            ph, pw = (-t.shape[1]) % 16, (-t.shape[2]) % 16
            t = np.pad(t, ((0, 0), (0, ph), (0, pw)))
            tt = torch.from_numpy(t)[None].to(device)
            p = _forward(model, tt, tta)[0, 0].cpu().numpy()
            p = p[: rb - ra, : cb - ca]
            rr, cc = r0 - ra, c0 - ca
            out[r0:min(r0 + step, h), c0:min(c0 + step, w)] = p[rr: rr + min(step, h - r0), cc: cc + min(step, w - c0)]
    return out


def main(season: str, thr: float, model_season: str = "2025", ckpt: str | None = None, tta: bool = False,
         save_prob: bool = False) -> None:
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    h, w = aoi["shape"]
    tf = rasterio.Affine(*aoi["transform"])
    with rasterio.open(config.INTERIM / "worldcover.tif") as src:
        crop = src.read(1) == 40
    ck_path = Path(ckpt) if ckpt else config.CHECKPOINTS / f"unet_{model_season}.pt"  # the 2025 model also runs the live season
    ck = torch.load(ck_path, map_location="cuda", weights_only=False)
    print(f"model {ck_path.name} (epoch {ck.get('epoch')}), tta={tta}")
    prob_dir = config.INTERIM / "prob" / season / ck_path.stem
    if save_prob:
        prob_dir.mkdir(parents=True, exist_ok=True)
    model = UNet().cuda().eval()
    model.load_state_dict(ck["model"])
    mean, std = ck["mean"], ck["std"]

    burn_idx = np.zeros((h, w), np.uint8)
    pre_idx = np.zeros((h, w), np.uint8)
    prob_at = np.zeros((h, w), np.uint8)
    rule_idx = np.zeros((h, w), np.uint8)
    rule_pre = np.zeros((h, w), np.uint8)
    seen_clear = np.zeros((h, w), np.uint16)  # how many clear looks each pixel got
    keys = []
    for st in timeline.walk(season):
        keys.append(dict(key=st.key, datetime=st.datetime))
        seen_clear += st.clear
        valid = st.valid & crop
        if not valid.any():
            continue
        p = predict(model, st.pre, st.post, valid, mean, std, tta=tta)
        if save_prob:  # per-date probabilities, for diagnosis and self-training
            pq = np.where(valid, np.clip(p * 254 + 1, 1, 255), 0).astype(np.uint8)  # 0 = no data
            with rasterio.open(prob_dir / f"{st.key}.tif", "w", driver="GTiff", height=h, width=w, count=1,
                               dtype="uint8", crs=config.CRS, transform=tf, compress="deflate", nodata=0) as dst:
                dst.write(pq, 1)
        plaus = indices.plausible_burn(st.pre, st.post)
        new = (p > thr) & valid & plaus & (burn_idx == 0)
        burn_idx[new] = st.i + 1
        pre_idx[new] = st.pre_idx[new] + 1
        prob_at[new] = np.clip(p[new] * 255, 0, 255).astype(np.uint8)
        lab = indices.burn_labels(st.pre, st.post, valid)
        rnew = (lab == 1) & (rule_idx == 0)
        rule_idx[rnew] = st.i + 1
        rule_pre[rnew] = st.pre_idx[rnew] + 1
        raw = ((p > thr) & valid).sum()
        print(f"{st.key}: u-net new burn {new.sum() * 0.04:,.0f} ha (raw {raw * 0.04:,.0f}) | rules {rnew.sum() * 0.04:,.0f} ha", flush=True)

    out = config.PROCESSED / season
    out.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", height=h, width=w, count=1, crs=config.CRS, transform=tf, compress="deflate")
    for name, arr in [("burn_idx", burn_idx), ("burn_pre_idx", pre_idx), ("burn_prob", prob_at),
                      ("rule_idx", rule_idx), ("rule_pre_idx", rule_pre), ("clear_looks", seen_clear)]:
        with rasterio.open(out / f"{name}.tif", "w", dtype=arr.dtype, **prof) as dst:
            dst.write(arr, 1)
    (out / "scenes.json").write_text(json.dumps(keys, indent=1), encoding="utf-8")
    print(f"season burnt area: u-net {np.count_nonzero(burn_idx) * 0.04:,.0f} ha, "
          f"rules {np.count_nonzero(rule_idx) * 0.04:,.0f} ha (whole window, cropland only)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    # the strictest threshold that still matches nearly as many satellite-seen fires as the best one
    # (scripts/sweep_threshold.py)
    ap.add_argument("--thr", type=float, default=0.7)
    ap.add_argument("--model", default="2025", help="season whose trained u-net to use")
    ap.add_argument("--ckpt", default=None, help="explicit checkpoint path (overrides --model)")
    ap.add_argument("--no-tta", dest="tta", action="store_false", help="skip averaging over 4 flips (faster)")
    ap.add_argument("--save-prob", action="store_true", help="save per-date probability maps to data/interim/prob")
    a = ap.parse_args()
    main(a.season, a.thr, a.model, a.ckpt, a.tta, a.save_prob)
