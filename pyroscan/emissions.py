"""invisible smoke: fine particles (pm2.5) from the burnt area, and from the part nobody counted.

an order-of-magnitude estimate, deliberately conservative in the central case:

  pm2.5 = burnt area x straw left per hectare x share of it the fire consumes x pm2.5 per kg burnt

  straw per hectare   6.5 t   punjab pollution control board planning factor for non-basmati paddy
                              (report to the ngt, oa 632/2023: 31.67 lakh ha -> 20.08 mt of straw). pau
                              breeders quote 8.5-9.5 t for common varieties.
  share consumed      0.80    ipcc 2006 guidelines, vol. 4, default combustion factor for rice residue
  pm2.5 per kg        8.3 g   kim oanh et al. (2011), atmospheric environment: open-field rice straw burns,
                              residual mass method (8.3 +- 2.7 g/kg). other field studies report 13-34 g/kg.

low / high use 5.5 t, 0.70, 5 g/kg and 8.5 t, 0.90, 13 g/kg.
"""
from __future__ import annotations

STRAW_T_PER_HA = {"low": 5.5, "mid": 6.5, "high": 8.5}
COMBUSTION = {"low": 0.70, "mid": 0.80, "high": 0.90}
PM25_G_PER_KG = {"low": 5.0, "mid": 8.3, "high": 13.0}


def pm25_kg_per_ha(case: str = "mid") -> float:
    return STRAW_T_PER_HA[case] * 1000 * COMBUSTION[case] * PM25_G_PER_KG[case] / 1000


def pm25_tonnes(ha: float) -> dict:
    """{'mid': t, 'low': t, 'high': t} of pm2.5 for a burnt area in hectares."""
    return {c: round(ha * pm25_kg_per_ha(c) / 1000, 0) for c in ("low", "mid", "high")}
