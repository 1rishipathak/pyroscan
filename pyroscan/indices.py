"""burn indices, and the rule-based labels the u-net learns from.

bands are always in the order B03, B04, B8A, B11, B12 (reflectance 0-1). nbr drops when a field
burns. nbr2 tells char (flat swir) apart from wet soil, its main look-alike.
"""
from __future__ import annotations

import numpy as np

G, R, NIR, SW1, SW2 = range(5)

IGNORE = 255


def _nd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return (a - b) / (a + b)


def nbr(x: np.ndarray) -> np.ndarray:
    return _nd(x[NIR], x[SW2])


def nbr2(x: np.ndarray) -> np.ndarray:
    return _nd(x[SW1], x[SW2])


# set from pixels next to firms fires and checked on 10 m true-colour images (docs/labels.md).
# the textbook dnbr > 0.1 rule alone also flags freshly harvested fields, so the char test
# does the real work and dnbr only confirms that something changed.
T = dict(
    dnbr_min=0.10,  # nbr dropped...
    dsw1_min=0.06,  # ...or swir1 got darker
    sw1_max=0.17,  # char is dark in swir1 (straw and dry soil sit at 0.25-0.40)
    red_max=0.08,  # and black in visible light
    nir_min=0.04,  # anything darker is water or shadow
    nbr_max=0.05,
    nbr2_max=0.08,  # wet soil has a high nbr2
    maybe_sw1=0.20,  # darker than usual but not clearly char: left unlabelled
    maybe_red=0.10,
    neg_sw1_min=0.21,  # clearly not burnt: bright straw or soil...
    neg_red_min=0.11,
    neg_nbr_min=0.30,  # ...or green crop
)


def char_like(x: np.ndarray, t: dict = T, sw1=None, red=None) -> np.ndarray:
    sw1 = t["sw1_max"] if sw1 is None else sw1
    red = t["red_max"] if red is None else red
    return (
        (x[SW1] < sw1) & (x[R] < red) & (x[NIR] > t["nir_min"])
        & (nbr(x) < t["nbr_max"]) & (nbr2(x) < t["nbr2_max"])
    )


def plausible_burn(pre: np.ndarray, post: np.ndarray, t: dict = T) -> np.ndarray:
    """the loose check every u-net detection has to pass: roughly char-like now, and changed since
    the last look. it keeps dark, freshly irrigated wheat fields (late november) out."""
    changed = ((nbr(pre) - nbr(post)) > t["dnbr_min"]) | ((pre[SW1] - post[SW1]) > t["dsw1_min"])
    return char_like(post, t, sw1=t["maybe_sw1"], red=t["maybe_red"]) & changed


def burn_labels(pre: np.ndarray, post: np.ndarray, valid: np.ndarray, t: dict = T) -> np.ndarray:
    """1 = new burn, 0 = clearly not burnt, IGNORE = unsure. valid = clear cropland on both dates."""
    nbr_post = nbr(post)
    dnbr = nbr(pre) - nbr_post
    dsw1 = pre[SW1] - post[SW1]
    changed = (dnbr > t["dnbr_min"]) | (dsw1 > t["dsw1_min"])

    pos = char_like(post, t) & changed
    # already char at the last look means an older burn
    pos &= ~char_like(pre, t, sw1=t["maybe_sw1"], red=t["maybe_red"])
    maybe = char_like(post, t, sw1=t["maybe_sw1"], red=t["maybe_red"]) & ~pos

    neg = (post[SW1] > t["neg_sw1_min"]) | (post[R] > t["neg_red_min"]) | (nbr_post > t["neg_nbr_min"]) \
        | (nbr2(post) > 0.12)
    neg &= ~pos & ~maybe

    lab = np.full(valid.shape, IGNORE, np.uint8)
    lab[neg] = 0
    lab[pos] = 1
    lab[~valid] = IGNORE
    lab[~np.isfinite(dnbr)] = IGNORE
    return lab
