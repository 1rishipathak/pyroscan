"""the physics behind the labels and the gate, on hand-made pixels (bands: b03 b04 b8a b11 b12)."""
import numpy as np

from pyroscan import indices

STRAW = [0.14, 0.16, 0.26, 0.34, 0.27]  # harvested field, loose dry straw: bright
CHAR = [0.05, 0.06, 0.11, 0.13, 0.12]  # fresh black ash: dark, flat swir
GREEN = [0.07, 0.05, 0.40, 0.20, 0.10]  # standing crop
WET = [0.07, 0.08, 0.15, 0.14, 0.09]  # irrigated soil: dark, but water eats swir2


def px(*spectra):
    """[5, 1, n] reflectance from a list of spectra."""
    return np.array(spectra, np.float32).T[:, None, :]


def labels(pre, post):
    return indices.burn_labels(px(*pre), px(*post), np.ones((1, len(pre)), bool))[0]


def test_straw_to_char_is_a_new_burn():
    assert labels([STRAW], [CHAR])[0] == 1


def test_unchanged_straw_is_not_burnt():
    assert labels([STRAW], [STRAW])[0] == 0


def test_wet_soil_is_not_mistaken_for_char():
    assert labels([GREEN], [WET])[0] == 0


def test_old_char_is_not_a_new_burn():
    assert labels([CHAR], [CHAR])[0] != 1


def test_physics_gate():
    gate = indices.plausible_burn(px(STRAW, STRAW, GREEN), px(CHAR, STRAW, WET))[0]
    assert gate.tolist() == [True, False, False]


def test_invalid_pixels_are_ignored():
    lab = indices.burn_labels(px(STRAW), px(CHAR), np.zeros((1, 1), bool))
    assert lab[0, 0] == indices.IGNORE
