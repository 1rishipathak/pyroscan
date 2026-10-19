"""spectral indices and the rule-based burn labels that train the u-net.

band order everywhere: B03 green, B04 red, B8A nir, B11 swir1, B12 swir2 (reflectance 0-1).

why these indices
- nbr  = (nir - swir2) / (nir + swir2). the standard burn index (key & benson).
  healthy crop is high, charred fields are near zero or negative.
- dnbr = nbr_before - nbr_after. the standard burn-severity change index.
- nbr2 = (swir1 - swir2) / (swir1 + swir2). separates char (flat swir, low nbr2)
  from wet soil after irrigation (water eats swir2, high nbr2), its main look-alike.
- swir1 brightness: dry straw and dry soil are bright, char is dark.
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


# thresholds come from docs/calibration: spectra next to firms fires vs random cropland,
# and 10 m true-colour chips where fresh char is black (see docs/labels.md).
# note the textbook rule (dnbr > 0.1) flags 20-50% of the area per week here, because
# harvest itself drops nbr. so the char signature does the work, dnbr only confirms change.
T = dict(
    dnbr_min=0.10,  # the burn lowered nbr ...
    dsw1_min=0.06,  # ... or darkened swir1 (straw left after harvest already has low nbr)
    sw1_max=0.17,  # fresh char is dark in swir1 (dry straw/soil 0.25-0.40)
    red_max=0.08,  # and black in visible light (tilled or moist soil stays above ~0.09)
    nir_min=0.04,  # below this it is water or deep shadow
    nbr_max=0.05,  # wet soil and regrowth stay above this
    nbr2_max=0.08,  # wet soil has high nbr2 (water eats swir2), char is flat
    # "maybe" zone: darker than usual but not clearly char. left unlabelled.
    maybe_sw1=0.20,
    maybe_red=0.10,
    # confident "not burnt"
    neg_sw1_min=0.21,  # bright straw / dry soil
    neg_red_min=0.11,
    neg_nbr_min=0.30,  # green crop
)


def char_like(x: np.ndarray, t: dict = T, sw1=None, red=None) -> np.ndarray:
    sw1 = t["sw1_max"] if sw1 is None else sw1
    red = t["red_max"] if red is None else red
    return (
        (x[SW1] < sw1) & (x[R] < red) & (x[NIR] > t["nir_min"])
        & (nbr(x) < t["nbr_max"]) & (nbr2(x) < t["nbr2_max"])
    )


def plausible_burn(pre: np.ndarray, post: np.ndarray, t: dict = T) -> np.ndarray:
    """loose physics check applied to every u-net detection: at least char-ish now
    (the 'maybe' zone) and changed since the last look. the u-net proposes, physics disposes.
    this is what keeps dark tilled or irrigated soil at wheat sowing (late nov) out."""
    changed = ((nbr(pre) - nbr(post)) > t["dnbr_min"]) | ((pre[SW1] - post[SW1]) > t["dsw1_min"])
    return char_like(post, t, sw1=t["maybe_sw1"], red=t["maybe_red"]) & changed


def burn_labels(pre: np.ndarray, post: np.ndarray, valid: np.ndarray, t: dict = T) -> np.ndarray:
    """1 = new burn between the two dates, 0 = clearly not burnt, IGNORE = unsure / invalid.

    pre, post: [5, h, w] reflectance. valid: bool [h, w], both dates clear and cropland.
    """
    nbr_post = nbr(post)
    dnbr = nbr(pre) - nbr_post
    dsw1 = pre[SW1] - post[SW1]
    changed = (dnbr > t["dnbr_min"]) | (dsw1 > t["dsw1_min"])

    pos = char_like(post, t) & changed
    # already char at the last look = an older burn, not a new one
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
