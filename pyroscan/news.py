"""enforcement and court news for the study districts, found and read with tinyfish.

satellites give a count, the state gives a response: firs, fines, 'red entries' in land
records, notices to officials. none of that is published as data, so PyroScan finds it in the
news with tinyfish search, and has the tinyfish web agent read the enforcement articles and
pull the numbers out as structured json.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import config, tinyfish_client as tf

RELEVANT = re.compile(r"stubble|farm[- ]fire|field[- ]fire|paddy|straw|parali|crop residue|residue burning|crop burning", re.I)
TAGS = {
    "enforcement": r"\bFIRs?\b|\bfined?\b|\bfines\b|\bchallans?\b|penalt|red entr|environment(al)? compensation|booked|"
                   r"suspend|show[- ]cause|\barrest",
    "court": r"supreme court|\bSC\b|high court|\bNGT\b|tribunal|bench|amicus",
    "satellite": r"satellite|\bNASA\b|VIIRS|MODIS|geostationary|after 3 ?pm|evening|evade|timing",
    "air": r"\bAQI\b|air quality|smog|pollution|PM ?2\.5",
}

# the web agent's output schema (tinyfish accepts type / properties / required)
FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "districts": {"type": "array", "items": {"type": "string"}},
        "farm_fires_reported": {"type": "integer"},
        "firs_registered": {"type": "integer"},
        "fines_rs": {"type": "integer"},
        "red_entries": {"type": "integer"},
        "officials_acted_against": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": ["districts", "farm_fires_reported", "firs_registered", "fines_rs", "red_entries",
                 "officials_acted_against", "summary"],
}
FACTS_GOAL = (
    "Read this news article about crop residue (stubble) burning in Punjab or Haryana. Do not click links or leave the "
    "page. Return only facts stated in the article: districts = the Punjab/Haryana districts it covers; "
    "farm_fires_reported = number of farm fire cases stated; firs_registered = FIRs registered; fines_rs = total fines "
    "or environmental compensation in rupees; red_entries = red entries in land records; officials_acted_against = "
    "officials given show-cause notices, suspended or charge-sheeted; summary = one sentence on the enforcement action. "
    "Use -1 for any number the article does not state."
)


def _clean(s: str | None) -> str:
    return (s or "").replace("�", "'").strip()


def tag(text: str) -> list[str]:
    return [k for k, rx in TAGS.items() if re.search(rx, text or "", re.I)]


def queries(districts=config.DISTRICTS) -> list[str]:
    q = []
    for d in districts:
        q += [f"stubble burning {d}", f"farm fire FIR {d}", f"paddy straw burning {d} fine"]
    q += ["stubble burning satellite timing Punjab", "Supreme Court stubble burning satellite data",
          "Punjab farm fires red entries FIR"]
    return q


def search_news(after: date, before: date, districts=config.DISTRICTS, max_age_s: float | None = 6 * 3600) -> list[dict]:
    """tinyfish search (news) over a date range, de-duplicated, only stubble-burning stories."""
    seen: dict[str, dict] = {}
    for q in queries(districts):
        try:
            res = tf.search(q, domain_type="news", location="IN", language="en",
                            after_date=after.isoformat(), before_date=before.isoformat(), max_age_s=max_age_s)
        except Exception as e:  # one bad query should not kill the feed
            print("search failed:", q, e)
            continue
        for r in res:
            u = r.get("url")
            title, snippet = _clean(r.get("title")), _clean(r.get("snippet"))
            text = f"{title} {snippet}"
            if not u or u in seen or not RELEVANT.search(text):
                continue
            seen[u] = dict(
                title=title, url=u, snippet=snippet, date=r.get("date"),
                source=r.get("publisher") or r.get("site_name"), query=q, tags=tag(text),
                districts=[d for d in districts if d.lower() in text.lower()],
            )
    items = list(seen.values())
    # stories naming our districts first, then enforcement, then the rest
    items.sort(key=lambda x: (-len(x["districts"]), "enforcement" not in x["tags"], x["title"]))
    return items


def extract_facts(items: list[dict], max_items: int = 10) -> list[dict]:
    """tinyfish web agent reads the enforcement stories and returns structured numbers."""
    todo = [x for x in items if "enforcement" in x["tags"]][:max_items]
    for x in todo:
        try:
            r = tf.agent(x["url"], FACTS_GOAL, FACTS_SCHEMA)
        except Exception as e:
            x["facts_error"] = repr(e)[:200]
            continue
        res = r.get("result") or {}
        if isinstance(res, dict) and res.get("summary"):
            x["facts"] = {k: v for k, v in res.items() if v not in (-1, None, "")}
            x["facts_steps"] = r.get("num_of_steps")
    return items


def recent(days: int = 7, **kw) -> list[dict]:
    today = date.today()
    return search_news(today - timedelta(days=days), today, **kw)
