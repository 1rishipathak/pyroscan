"""step 7: pull the official numbers through tinyfish.

  official_<season>.json  creams bulletin: district totals 2020-<season>, daily punjab counts
  aqi_<season>.json       delhi aqi for every day of the season (cpcb 4 pm bulletin)
  news_<season>.json      enforcement / court news found with tinyfish search
  live.json               (--live) today's creams bulletin, today's delhi aqi, this week's news

run: .venv/Scripts/python.exe scripts/07_sources.py --season 2025
     .venv/Scripts/python.exe scripts/07_sources.py --live
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, news, sources, tinyfish_client as tf  # noqa: E402


def official(season: str) -> dict:
    """the season's last creams bulletin holds the season totals and the daily table."""
    y = int(season)
    bl = [b for b in sources.creams_bulletins() if b["year"] == y]
    if not bl:
        raise SystemExit(f"no creams bulletins found for {y}")
    last = bl[0]
    text, via = sources.page_text(last["url"])
    p = sources.parse_creams(text)
    return dict(source="CREAMS, ICAR-IARI daily paddy residue burning bulletin", bulletin=last, via=via,
                n_bulletins=len(bl), **{k: v for k, v in p.items()})


def aqi(season: str) -> dict:
    start, end = (date.fromisoformat(d) for d in config.SEASONS[season])
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def one(d):
        url = sources.cpcb_url(d)
        try:
            text, via = sources.page_text(url)
            r = sources.parse_cpcb_delhi(text)
            return d.isoformat(), (dict(**r, via=via, url=url) if r else None)
        except Exception as e:
            return d.isoformat(), dict(error=repr(e)[:200], url=url)

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = dict(ex.map(one, days))
    ok = sum(1 for v in rows.values() if v and "aqi" in v)
    print(f"delhi aqi: {ok}/{len(days)} days parsed")
    return dict(source="CPCB daily AQI bulletin (4 pm, 24 h average)", city="Delhi", days=rows)


def season_news(season: str, agent_n: int = 0) -> dict:
    start, end = (date.fromisoformat(d) for d in config.SEASONS[season])
    items = news.search_news(start, end + timedelta(days=15))
    print(f"news: {len(items)} articles, {sum('enforcement' in x['tags'] for x in items)} on enforcement")
    if agent_n:
        news.extract_facts(items, max_items=agent_n)
        print(f"tinyfish agent read {sum('facts' in x for x in items)} enforcement articles")
    return dict(source="TinyFish Search (news)", after=start.isoformat(), before=(end + timedelta(days=15)).isoformat(),
                items=items)


def live() -> dict:
    """what the government web says today."""
    out = dict(fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        bl = sources.creams_bulletins(max_age_s=1800)
        latest = bl[0]
        text, via = sources.page_text(latest["url"], max_age_s=1800)
        p = sources.parse_creams(text)
        out["creams"] = dict(bulletin=latest, via=via, **p)
    except Exception as e:
        out["creams"] = dict(error=repr(e)[:300])
    try:
        cl = sources.cpcb_latest(max_age_s=1800)
        for b in cl[:3]:  # newest bulletin can lag a few hours
            text, via = sources.page_text(b["url"], max_age_s=1800)
            r = sources.parse_cpcb_delhi(text)
            if r:
                out["aqi"] = dict(date=b["date"], via=via, url=b["url"], **r)
                break
    except Exception as e:
        out["aqi"] = dict(error=repr(e)[:300])
    if tf.available():
        try:
            out["news"] = news.recent(days=10, max_age_s=1800)[:25]
        except Exception as e:
            out["news_error"] = repr(e)[:300]
    return out


def main(season: str | None, do_live: bool, skip_news: bool, agent_n: int = 0) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    if season:
        o = official(season)
        (config.PROCESSED / f"official_{season}.json").write_text(json.dumps(o, indent=1), encoding="utf-8")
        dc = o.get("district_cumulative", {})
        print("official district totals:", {k: v.get(int(season)) for k, v in dc.items()})
        a = aqi(season)
        (config.PROCESSED / f"aqi_{season}.json").write_text(json.dumps(a, indent=1), encoding="utf-8")
        if tf.available() and not skip_news:
            n = season_news(season, agent_n)
            (config.PROCESSED / f"news_{season}.json").write_text(json.dumps(n, indent=1, ensure_ascii=False), encoding="utf-8")
    if do_live:
        lv = live()
        (config.PROCESSED / "live.json").write_text(json.dumps(lv, indent=1, ensure_ascii=False), encoding="utf-8")
        c = lv.get("creams", {})
        print("live creams:", c.get("date"), "punjab today", c.get("day_punjab"), "season", c.get("season_punjab"))
        print("live aqi:", lv.get("aqi"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default=None)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--skip-news", action="store_true")
    ap.add_argument("--agent", type=int, default=0, help="have the tinyfish web agent read the top N enforcement articles")
    a = ap.parse_args()
    main(a.season, a.live, a.skip_news, a.agent)
