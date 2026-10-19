# how PyroScan labels burnt fields without hand-labelling

the u-net needs examples of "burnt" and "not burnt". nobody labelled any by hand. the labels come from physics, and only the confident ones are used.

## what a burnt paddy field looks like from space

after the combine harvester, a punjab paddy field is covered in loose straw and stubble. from sentinel-2 that is **bright**: short-wave infrared (b11, 1.6 µm) around 0.30–0.40 reflectance.

burning turns it into black char for a few days to a couple of weeks, until the field is tilled for wheat. char is **dark in every band**: b11 around 0.10–0.17, red (b04) below 0.08, and b11 ≈ b12 (flat swir).

two things look similar and must not be called burnt:

| look-alike | how we tell it apart |
|---|---|
| wet soil after pre-sowing irrigation | water absorbs b12 much more than b11, so nbr2 = (b11−b12)/(b11+b12) is high (> 0.1). char's swir is flat, nbr2 ≈ 0 |
| tilled or moist soil | darker than straw, but red stays above ~0.09. char is blacker |
| water, deep shadow | nir below 0.04 |

## the rule

a pixel is labelled **burnt (1)** when, on this look:

- b11 < 0.17 and b04 < 0.08 (char-dark)
- nir > 0.04, nbr < 0.05, nbr2 < 0.08 (not water, not wet soil, not crop)
- and it changed since its last clear look: dnbr > 0.10 or b11 dropped by > 0.06
- and it was not already char-dark on that last look (that is an older burn)

**not burnt (0)**: b11 > 0.21, or red > 0.11, or nbr > 0.30 (green crop), or nbr2 > 0.12 (wet).

everything else, including "darker than usual but not clearly char", is **unlabelled (255)** and ignored in training. about a quarter of cropland pixels end up unlabelled.

## why not just dnbr?

the textbook burn-severity index (dnbr > 0.1, post-fire nbr < 0.1) labels **28–54% of the whole window as burnt in a single 5–10 day window** in early november 2025 (`scripts/calibrate_labels.py`), because harvest itself removes the green crop and drops nbr. it cannot tell a harvested field from a burnt one. the char signature can.

## how the thresholds were set

1. `docs/calibration/spectra_2025-11-07_2C.png`: band values next to 766 firms fire points (2–7 nov 2025) against 20,000 random cropland pixels. fire-adjacent pixels are darker in b04, b11 and b12, and changed more (db11).
2. 10 m true-colour chips around fire points: fields that were light straw on 2 nov and black on 7 nov are outlined by the rule. some are next to a firms point, many are not. one chip even caught a live smoke plume at the 11:20 am overpass.
3. a rule-vs-rule comparison on three date pairs: area flagged, share of firms points with a flagged pixel within 360 m, and the same for random points.

## what the u-net adds

the rule only labels pixels it is sure of. the u-net sees before and after together (11 channels) and a 5 km neighbourhood. it decides the unclear pixels from field shape and context, and gives field-shaped scars instead of speckle. it is tested on 5 km blocks it never saw in training.

because the labels are automatic, agreement with them is not accuracy. so PyroScan also checks against things the labels never used:

- **firms detections**: what share of official-satellite fire detections on cropland land on a burn scar (recall against the fires that *were* seen)
- **a human check**: random never-detected scars, before and after, in `docs/qa_unseen_2025.png`
