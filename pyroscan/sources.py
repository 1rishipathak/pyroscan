"""the official numbers PyroScan checks itself against, read through tinyfish.

- creams (icar-iari): the government's daily paddy-residue fire bulletin. this *is* the
  official fire count. pdfs listed on a wordpress page.
- cpcb: the daily 4 pm air quality bulletin, per city. pdfs behind base64 links.

tinyfish fetch renders the listing pages and the pdfs. if a pdf cannot be read that way
we download it and read it locally, and record which path was used.
"""
from __future__ import annotations

import base64
import io
import re
from datetime import date, datetime

import httpx

from . import config, tinyfish_client as tf

CREAMS_PAGE = "https://creams.iari.res.in/?page_id=1123"
CREAMS_BASE = "https://creams.iari.res.in"
CPCB_PAGE = "https://cpcb.gov.in/AQI_Bulletin.php"

PDF_CACHE = config.RAW / "pdf_cache"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def _pdf_text_local(url: str) -> str:
    """fallback: download the pdf ourselves and extract the text locally."""
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1].split("?id=")[-1])[-120:]
    p = PDF_CACHE / name
    if not p.exists():
        r = httpx.get(url, timeout=90, follow_redirects=True)
        r.raise_for_status()
        p.write_bytes(r.content)
    try:  # pymupdf is ~50x faster than pypdf on these chart-heavy bulletins
        import pymupdf

        with pymupdf.open(p) as doc:
            return "\n".join(pg.get_text() for pg in doc)
    except ImportError:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(p.read_bytes()))
        return "\n".join(pg.extract_text() or "" for pg in reader.pages)


def page_text(url: str, prefer_tinyfish: bool = True, max_age_s: float | None = None) -> tuple[str, str]:
    """(text, via) where via is 'tinyfish' or 'local-pdf'."""
    if prefer_tinyfish and tf.available():
        try:
            r = tf.fetch([url], max_age_s=max_age_s)[0]
            if r["error"] is None and len(r["text"]) > 200:
                return r["text"], "tinyfish"
        except Exception:
            pass
    return _pdf_text_local(url), "local-pdf"


# ---------------------------------------------------------------- creams bulletins

_BULLETIN_RE = re.compile(r"RiceResidueFireBulletin_(\d{1,2})([A-Za-z]{3})_(\d{4})", re.I)


def _links(url: str, purpose: str, max_age_s: float | None) -> tuple[set[str], str]:
    """all links on a page, via tinyfish fetch (falls back to a plain http get without a key)."""
    if tf.available():
        r = tf.fetch([url], links=True, max_age_s=max_age_s, purpose=purpose)[0]
        if r["error"] is None:
            return set(r["links"]) | set(re.findall(r"\(([^)\s]+)\)", r["text"])), "tinyfish"
    html = httpx.get(url, timeout=60, follow_redirects=True).text
    return set(re.findall(r'href=["\']([^"\']+)["\']', html)), "http"


def creams_bulletins(max_age_s: float | None = 3 * 3600) -> list[dict]:
    """every rice-residue bulletin pdf on the creams page: [{date, year, url}], newest first."""
    hrefs, _ = _links(CREAMS_PAGE, "list the daily paddy residue burning bulletin pdfs", max_age_s)
    out = {}
    for h in hrefs:
        m = _BULLETIN_RE.search(h)
        if not m:
            continue
        url = h if h.startswith("http") else CREAMS_BASE + ("" if h.startswith("/") else "/") + h
        d = datetime.strptime(f"{m.group(1)} {m.group(2)[:3].title()} {m.group(3)}", "%d %b %Y").date()
        out[d] = dict(date=d.isoformat(), year=d.year, url=url.replace(" ", "%20"))
    return [out[k] for k in sorted(out, reverse=True)]


def parse_creams(text: str) -> dict:
    """pull the numbers out of one bulletin."""
    t = _norm(text)
    res: dict = {}
    m = re.search(r"events detected on (\d{1,2}-[A-Za-z]{3}-\d{4}) were (\d+) in Punjab, (\d+) in Haryana", t)
    if m:
        res["date"] = datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat()
        res["day_punjab"], res["day_haryana"] = int(m.group(2)), int(m.group(3))
    m = re.search(r"Total (\d+) burning events were detected in the six states between (\S+) and (\S+?),? which are "
                  r"distributed as ([\d ,and]+?) in Punjab", t)
    if m:
        nums = [int(x) for x in re.findall(r"\d+", m.group(4))]
        res["season_punjab"], res["season_haryana"] = nums[0], nums[1]
    # district-wise cumulative table, all years. pick the table that lists sangrur (punjab's):
    # its position varies between years. 2021 bulletins compare two years and use an odd dash.
    hdr_rx = re.compile(r"District 15[ -]?Sep\W+\d{1,2}[ -]?[A-Za-z]{3} ((?:20\d\d ){1,7}20\d\d)", re.I)
    for hdr in hdr_rx.finditer(t):
        years = [int(y) for y in hdr.group(1).split()]
        sub = t[hdr.end(): hdr.end() + 3000]
        nxt = hdr_rx.search(sub)  # stop at the next state's table
        if nxt:
            sub = sub[: nxt.start()]
        if not re.search(r"\bSangrur\b", sub, re.I):
            continue
        dist = {}
        for name in ["Sangrur", "Barnala", "Malerkotla", "Patiala", "Mansa", "Bathinda", "Ludhiana"]:
            mm = re.search(rf"\b{name}\b((?: \d+){{{len(years)}}})", sub, re.I)
            if mm:
                dist[name] = dict(zip(years, [int(v) for v in mm.group(1).split()]))
        tot = re.search(rf"\bTotal((?: \d+){{{len(years)}}})", sub, re.I)
        if tot:
            dist["Punjab total"] = dict(zip(years, [int(v) for v in tot.group(1).split()]))
        res["district_cumulative"] = dist
        res["years"] = years
        break
    # event list for punjab: satellite, lat, lon, time
    ev = []
    blk = re.search(r"Details of (?:(?:paddy|rice) )?residue burning events in Punjab on (\S+)(.*?)"
                    r"(?:Details of |The study used)", t, re.I)
    if blk:
        for mm in re.finditer(r"(\d+) ([A-Z][A-Z .()&-]+?) (S-NPP|NOAA-?20|NOAA-?21|TERRA|AQUA|Terra|Aqua|MODIS) "
                              r"(\d{2}\.\d+) (\d{2}\.\d+) (\d{1,2}:\d{2}(?::\d{2})?) ([DN])", blk.group(2)):
            ev.append(dict(place=mm.group(2).strip(), satellite=mm.group(3), lat=float(mm.group(4)),
                           lon=float(mm.group(5)), time_ist=mm.group(6), daynight=mm.group(7)))
    res["punjab_events"] = ev
    res["daily"] = parse_creams_daily(t)
    return res


def parse_creams_daily(text: str) -> dict:
    """the 'state-wise and date-wise' table: {iso date: {punjab, haryana}} for the bulletin's own year."""
    t = _norm(text)
    hdr = re.search(r"Date ((?:20\d\d ){3,23}20\d\d)", t)
    if not hdr:
        return {}
    years = [int(y) for y in hdr.group(1).split()]
    per_state = len(set(years))  # one column per year, repeated per state (punjab first, then haryana)
    yr = max(years)  # the bulletin's own year is the last column of each block
    out = {}
    end = re.search(r"Total(?: \d+){%d}" % len(years), t[hdr.end():])
    body = t[hdr.end(): hdr.end() + (end.start() if end else 20000)]
    for m in re.finditer(r"(\d{2}-[A-Z][a-z]{2})((?: \d+){%d})" % len(years), body):
        nums = [int(v) for v in m.group(2).split()]
        d = datetime.strptime(f"{m.group(1)}-{yr}", "%d-%b-%Y").date().isoformat()
        out[d] = dict(punjab=nums[per_state - 1], haryana=nums[2 * per_state - 1])
    return out


# ---------------------------------------------------------------- cpcb aqi bulletin

def cpcb_url(d: date) -> str:
    rel = f"Downloads/AQI_Bulletin_{d:%Y%m%d}.pdf"
    return "https://cpcb.gov.in/displaypdf.php?id=" + base64.b64encode(rel.encode()).decode()


# stations read "39/39" from 2022 on, a single count before that
_AQI_RE = re.compile(r"\bDelhi (Good|Satisfactory|Moderate|Poor|Very Poor|Severe) (\d{2,3}) ([A-Z0-9., ]+?) (\d+(?:/\d+)?)\b")


def parse_cpcb_delhi(text: str) -> dict | None:
    m = _AQI_RE.search(_norm(text))
    if not m:
        return None
    return dict(category=m.group(1), aqi=int(m.group(2)), pollutant=m.group(3).strip(), stations=m.group(4))


def cpcb_latest(max_age_s: float | None = 3 * 3600) -> list[dict]:
    """latest bulletins listed on the cpcb page: [{date, url}]."""
    hrefs, _ = _links(CPCB_PAGE, "list the daily aqi bulletin pdfs", max_age_s)
    out = []
    for h in hrefs:
        m = re.search(r"displaypdf\.php\?id=([A-Za-z0-9+/=%]+)", h)
        if not m:
            continue
        try:
            rel = base64.b64decode(m.group(1).replace("%3D", "=")).decode()
        except Exception:
            continue
        mm = re.search(r"AQI_Bulletin_(\d{8})\.pdf", rel)
        if mm:
            d = datetime.strptime(mm.group(1), "%Y%m%d").date()
            out.append(dict(date=d.isoformat(), url=h if h.startswith("http") else "https://cpcb.gov.in/" + h.lstrip("/")))
    return sorted(out, key=lambda x: x["date"], reverse=True)
