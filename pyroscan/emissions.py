"""pm2.5 from burnt farmland: hectares x straw per hectare x share that burns x pm2.5 per kg burnt.

mid case: 6.5 t of straw per ha (punjab pollution control board planning figure), 80% of it burnt
(ipcc 2006 default for rice residue) and 8.3 g of pm2.5 per kg (kim oanh et al. 2011, open-field rice
straw). the low and high cases bracket the published range.
"""
from __future__ import annotations

STRAW_T_PER_HA = {"low": 5.5, "mid": 6.5, "high": 8.5}
COMBUSTION = {"low": 0.70, "mid": 0.80, "high": 0.90}
PM25_G_PER_KG = {"low": 5.0, "mid": 8.3, "high": 13.0}


def pm25_kg_per_ha(case: str = "mid") -> float:
    return STRAW_T_PER_HA[case] * 1000 * COMBUSTION[case] * PM25_G_PER_KG[case] / 1000


def pm25_tonnes(ha: float) -> dict:
    return {c: round(ha * pm25_kg_per_ha(c) / 1000, 0) for c in ("low", "mid", "high")}
