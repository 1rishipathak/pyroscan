"""scores several checkpoints on the same held-out patches (agreement with the automatic labels),
split by part of the season."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, timeline  # noqa: E402
from pyroscan.model import UNet  # noqa: E402

_spec = importlib.util.spec_from_file_location("train", Path(__file__).with_name("04_train.py"))
_train = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_train)


@torch.no_grad()
def score(ck_path: str, X, Y, idx, tta: bool) -> dict:
    ck = torch.load(ck_path, map_location="cuda", weights_only=False)
    model = UNet().cuda().eval()
    model.load_state_dict(ck["model"])
    tp = fp = fn = 0
    for s in range(0, len(idx), 32):
        b = idx[s:s + 32]
        x = np.stack([_train.normalise(np.asarray(X[j]), ck["mean"], ck["std"]) for j in b])
        tt = torch.from_numpy(x).cuda()
        with torch.autocast("cuda", dtype=torch.float16):
            if tta:
                pr = 0
                for dims in ([], [2], [3], [2, 3]):
                    xi = torch.flip(tt, dims) if dims else tt
                    yi = torch.sigmoid(model(xi).float())
                    pr = pr + (torch.flip(yi, dims) if dims else yi)
                pr = pr / 4
            else:
                pr = torch.sigmoid(model(tt).float())
        p = (pr[:, 0] > 0.5).cpu().numpy()
        y = Y[b]
        m = y != 255
        t = y == 1
        tp += int((p & t & m).sum()); fp += int((p & ~t & m).sum()); fn += int((~p & t & m).sum())
    prec, rec = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return dict(precision=round(prec, 4), recall=round(rec, 4), f1=round(2 * prec * rec / max(prec + rec, 1e-9), 4),
                iou=round(tp / max(tp + fp + fn, 1), 4), false_burn_px=fp)


def main(tag: str, ckpts: list[str], tta: bool) -> None:
    d = np.load(config.INTERIM / f"patches_2025{tag}.npz")
    X = np.load(config.INTERIM / str(d["x_path"]), mmap_mode="r") if "x_path" in d else d["x"]
    Y, M = d["y"], d["meta"]
    files = timeline.scene_files("2025")
    dates = np.array([files[i][0][:10] for i in M[:, 0]])
    va = np.nonzero(M[:, 3] == 1)[0]
    late = va[dates[va] >= "2025-11-16"]
    early = va[dates[va] < "2025-11-16"]
    res = {}
    for ck in ckpts:
        name = Path(ck).stem + (" +tta" if tta else "")
        res[name] = dict(all=score(ck, X, Y, va, tta), to_15_nov=score(ck, X, Y, early, tta),
                         from_16_nov=score(ck, X, Y, late, tta))
        print(name, json.dumps(res[name]))
    out = config.ROOT / "reports" / f"eval_heldout{tag}{'_tta' if tta else ''}.json"
    out.write_text(json.dumps(dict(n_val=int(len(va)), n_late=int(len(late)), results=res), indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpts", nargs="+")
    ap.add_argument("--tag", default="_v2")
    ap.add_argument("--tta", action="store_true")
    a = ap.parse_args()
    main(a.tag, a.ckpts, a.tta)
