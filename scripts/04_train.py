"""step 4: train the burn-scar u-net on the rule labels.

the split is spatial: 20% of the 5 km blocks are never seen in training, on any date.
reports/train_<season>.json gets the curve and the held-out scores.

run: .venv/Scripts/python.exe scripts/04_train.py --season 2025 --epochs 30
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config  # noqa: E402
from pyroscan.model import UNet, masked_loss  # noqa: E402

# per-band reflectance mean/std (B03 B04 B8A B11 B12), measured on the training patches
MEAN = np.zeros(5, np.float32)
STD = np.ones(5, np.float32)


def band_stats(X: np.ndarray, idx: np.ndarray, n: int = 300) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    pick = rng.choice(idx, size=min(n, len(idx)), replace=False)
    s = X[pick]  # [n,11,h,w]
    v = s[:, 10] > 0
    vals = np.concatenate([s[:, :5], s[:, 5:10]], 0).astype(np.float32) / 10000.0  # before and after together
    vv = np.concatenate([v, v], 0)
    mean = np.array([vals[:, b][vv].mean() for b in range(5)], np.float32)
    std = np.array([vals[:, b][vv].std() for b in range(5)], np.float32)
    return mean, std


def normalise(xq: np.ndarray, mean: np.ndarray = None, std: np.ndarray = None) -> np.ndarray:
    """uint16 patch [11,h,w] (x10000 reflectance + validity) -> float32 network input."""
    mean = MEAN if mean is None else mean
    std = STD if std is None else std
    x = xq.astype(np.float32)
    refl = x[:10] / 10000.0
    v = x[10:11]
    m = np.concatenate([mean, mean])[:, None, None]
    s = np.concatenate([std, std])[:, None, None]
    refl = (refl - m) / s * v  # invalid pixels -> 0 (= the mean)
    return np.concatenate([refl, v], 0)


class Patches(Dataset):
    """x is either an array or the path of a .npy file, memory-mapped lazily in each worker."""

    def __init__(self, x, y, idx, augment: bool, mean: np.ndarray, std: np.ndarray):
        self.x_src, self.y, self.idx, self.augment = x, y, idx, augment
        self.mean, self.std = mean.copy(), std.copy()  # workers do not share the main process's globals
        self._x = None if isinstance(x, (str, Path)) else x

    @property
    def x(self):
        if self._x is None:
            self._x = np.load(self.x_src, mmap_mode="r")
        return self._x

    def __getstate__(self):
        st = self.__dict__.copy()
        if isinstance(self.x_src, (str, Path)):
            st["_x"] = None  # never pickle the big array to a worker
        return st

    def __len__(self):
        return len(self.idx)

    def __getitem__(self, i):
        j = self.idx[i]
        x = normalise(np.asarray(self.x[j]), self.mean, self.std)
        y = self.y[j].copy()
        if self.augment:
            rng = np.random
            # burn scars have no preferred direction, so flips and 90 degree turns are safe
            k = rng.randint(4)
            x, y = np.rot90(x, k, (1, 2)), np.rot90(y, k)
            if rng.rand() < 0.5:
                x, y = x[:, :, ::-1], y[:, ::-1]
            # brightness jitter, separately for before and after (haze, sun angle)
            v = x[10:11]
            for sl in (slice(0, 5), slice(5, 10)):
                g = rng.uniform(0.9, 1.1)
                o = rng.uniform(-0.15, 0.15)
                x[sl] = (x[sl] * g + o) * v
        return torch.from_numpy(np.ascontiguousarray(x)), torch.from_numpy(np.ascontiguousarray(y)).long()


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    tp = fp = fn = tn = 0
    losses = []
    for x, y in loader:
        x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.float16):
            lo = model(x)
        losses.append(masked_loss(lo.float(), y).item())
        p = (lo.squeeze(1) > 0)
        m = y != 255
        t = y == 1
        tp += (p & t & m).sum().item()
        fp += (p & ~t & m).sum().item()
        fn += (~p & t & m).sum().item()
        tn += (~p & ~t & m).sum().item()
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    iou = tp / max(tp + fp + fn, 1)
    return dict(loss=float(np.mean(losses)), precision=prec, recall=rec, f1=f1, iou=iou)


def main(season: str, epochs: int, batch: int, lr: float, tag: str = "", out: str | None = None,
         workers: int = 6, seed: int = 0) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)
    d = np.load(config.INTERIM / f"patches_{season}{tag}.npz")
    if "x" in d:  # old single-file format
        X = x_src = d["x"]
    else:
        x_src = str(config.INTERIM / str(d["x_path"]))
        X = np.load(x_src, mmap_mode="r")[: int(d["n"])]
    Y, M = d["y"], d["meta"]
    tr = np.nonzero(M[:, 3] == 0)[0]
    va = np.nonzero(M[:, 3] == 1)[0]
    print(f"{len(tr)} train / {len(va)} held-out patches; burnt px share train "
          f"{(Y[tr] == 1).mean():.3%}, labelled {(Y[tr] != 255).mean():.1%}")
    MEAN[:], STD[:] = band_stats(X, tr)
    print("band mean", MEAN.round(3), "std", STD.round(3))
    device = "cuda"
    torch.backends.cudnn.benchmark = True  # channels_last was 3x slower here (11 input channels)
    model = UNet().to(device)
    n_par = sum(p.numel() for p in model.parameters())
    print(f"u-net with {n_par / 1e6:.1f} M parameters")
    kw = dict(num_workers=workers, persistent_workers=workers > 0, pin_memory=True)
    if workers:
        kw["prefetch_factor"] = 4
    dl_tr = DataLoader(Patches(x_src, Y, tr, True, MEAN, STD), batch_size=batch, shuffle=True, drop_last=True, **kw)
    dl_va = DataLoader(Patches(x_src, Y, va, False, MEAN, STD), batch_size=batch, shuffle=False, **kw)
    ck_out = Path(out) if out else config.CHECKPOINTS / f"unet_{season}.pt"
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    steps = epochs * len(dl_tr)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    scaler = torch.amp.GradScaler()
    config.CHECKPOINTS.mkdir(exist_ok=True)
    best, hist = -1.0, []
    t0 = time.time()
    for ep in range(1, epochs + 1):
        model.train()
        tl = []
        for x, y in dl_tr:
            x, y = x.to(device, non_blocking=True), y.to(device)
            with torch.autocast("cuda", dtype=torch.float16):
                lo = model(x)
            loss = masked_loss(lo.float(), y)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            tl.append(loss.item())
        ev = evaluate(model, dl_va, device)
        row = dict(epoch=ep, train_loss=float(np.mean(tl)), **{f"val_{k}": v for k, v in ev.items()},
                   minutes=(time.time() - t0) / 60)
        hist.append(row)
        flag = ""
        if ev["f1"] > best:
            best = ev["f1"]
            torch.save(dict(model=model.state_dict(), mean=MEAN, std=STD, epoch=ep, val=ev), ck_out)
            flag = " *"
        print(f"ep {ep:2d}  train {row['train_loss']:.3f}  val loss {ev['loss']:.3f}  f1 {ev['f1']:.3f}  "
              f"iou {ev['iou']:.3f}  p {ev['precision']:.3f} r {ev['recall']:.3f}  "
              f"({row['minutes']:.1f} min){flag}", flush=True)
    torch.save(dict(model=model.state_dict(), mean=MEAN, std=STD, epoch=epochs, val=ev),
               ck_out.with_name(ck_out.stem + "_last.pt"))
    rep = config.ROOT / "reports"
    rep.mkdir(exist_ok=True)
    (rep / f"train_{season}{tag}.json").write_text(json.dumps(dict(
        checkpoint=ck_out.name, n_train=int(len(tr)), n_val=int(len(va)), params_m=n_par / 1e6, best_val_f1=best,
        epochs=epochs, history=hist), indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--tag", default="", help="patch file suffix, e.g. _v2")
    ap.add_argument("--out", default=None, help="checkpoint path (default checkpoints/unet_<season>.pt)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    main(a.season, a.epochs, a.batch, a.lr, a.tag, a.out, a.workers, a.seed)
