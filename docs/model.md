# model refinement: v1 → v2

PyroScan's rule: a new model replaces the old one only if it wins on checks that do not depend on its own training labels. this file records one round of that.

## 1. first, why did fires go missing?

`scripts/diagnose_misses.py` looked at every fire the official satellites detected on cropland in sangrur and barnala (1,523 detections) that did *not* land on a v1 burn scar (349, 23%).

| why | share of misses |
|---|---|
| a scar *was* there, dated to a window that did not contain the detection | **95%** |
| burnt pixels nearby, but in blobs under one killa (0.4 ha) | 5% |
| the u-net saw it and the physics check refused it | 0% |
| nothing char-like at the next clear look | 0% |

so the model was not missing these fires. half of the "timing" cases were detections up to a few hours *after* a sentinel-2 look that already showed char: the same field still burning at the ~1:30 pm viirs/aqua pass after looking charred at the 11:15 am sentinel-2 pass. those fires were seen by the official count, so the matching window now runs to the same afternoon (6 h, `scripts/06_scars.py`). the other half were detections *before* a clear look that showed no char at that spot, i.e. a neighbouring field; those stay misses.

that fix is about honesty, not the model: it moved "burnt land never detected" from 83% to 79%.

## 2. v2: the same idea, trained properly

| | v1 | v2 |
|---|---|---|
| training patches | 1,980 (+519 held out) | 2,612 (+672 held out), 130 per date instead of 90 |
| schedule | stopped at epoch 9 of 24 | full 24 epochs, one-cycle learning rate annealed to zero |
| data loading | single thread | memory-mapped patches, 6 workers |
| inference | one pass | average of 4 flips (test-time augmentation) |
| threshold | 0.5 | 0.7: the strictest that keeps "satellite-seen fires on a scar" within half a point of its best (`scripts/sweep_threshold.py`) |

same labels, same u-net, same physics gate, same 5 km blocks held out.

## 3. results

**agreement with the automatic labels on the same 672 held-out patches** (`scripts/eval_ckpt.py`, `reports/eval_heldout_v2*.json`):

| | f1, whole season | f1, 16–30 nov (wheat sowing, where false burns came from) | wrongly flagged pixels |
|---|---|---|---|
| v1 | 0.976 | 0.901 | 3,494 |
| v2 + flips | **0.998** | **0.995** | **341** (−90%) |

**checks the labels never saw** (sangrur + barnala, 2025, corrected matching window):

| | v1 | v2 |
|---|---|---|
| fires the official satellites detected that land on a scar | 81.2% | **83.2%** |
| official gps events that land on a scar | 80.9% | **83.4%** |
| burnt area | 50,000 ha | 55,574 ha |
| burnt land never detected | 78.8% | 78.9% |
| visual check, 48 random never-detected scars, by area: clear / probable / doubtful | 85% / 13% / 2% (4 doubtful of 48) | **86% / 14% / 0.5%** (2 doubtful of 48) |

**where they disagree** (`scripts/compare_models.py`, sheets in `docs/compare/`): v2 contains almost everything v1 found. only 9 ha of v1's burns come as stand-alone scars of a killa or more that v2 drops. v2 adds 11,500 ha, mostly by filling whole fields where v1 marked only the darkest core, plus 1,444 ha of new scars. a random sheet of those new scars is mostly medium-grey fields (grey ash or partial burns): about 15 of 24 probable burns, 6 unclear, 3 doubtful.

**decision: v2 replaces v1.** it finds more of the fires we know happened, and its precision on the visual check held. the "never detected" share, the headline, did not move (79%), so it does not depend on which model is used.

## 4. a different year, unseen in training

v2 was trained only on 2025. run unchanged on 2021 (two satellites instead of three, a far bigger burning season), the visual check found:

| 2021, 24 random scars each | clear | probable | doubtful |
|---|---|---|---|
| seen by a satellite (88% of 2021's burnt area) | 81% | 19% | 0% |
| never detected (12%) | 54% | 27% | 19%, mostly cloud shadows and haze next to small clouds |

so the burnt area transfers well across years. two small biases pull 2021's never-detected share in opposite directions: cloud-shadow false scars push it up, and chance matches in such a dense year push it down. a time-shift null test (`06_scars.py --shift-days ±12`) measures the chance part: 18% of 2021's burnt area matches with detections moved 12 days away, 4% in 2025. corrected, the satellites missed 14% of burnt land in 2021 and 82% in 2025. the results are in the readme's control-year section.

## 5. tried or considered, and not used

- **self-training** (`03_make_labels.py --pseudo`): relabel the rule's "unsure" pixels with the model's own confident calls and retrain. it would push further into the medium-grey zone, where the evidence is weakest, so it was not worth the extra false-burn risk before the deadline.
- **looser physics gate**: the miss analysis showed the gate refused no satellite-seen fire, so there was nothing to gain.
- **smaller minimum scar**: the 0.4 ha killa minimum removes 44% of blobs but only 13% of area, and the removed blobs were the doubtful ones in the first visual check.

v1 stays on disk locally (`checkpoints/unet_2025_v1.pt`, not committed: the repo carries only the current model) and its numbers are in `reports/v1/`.
