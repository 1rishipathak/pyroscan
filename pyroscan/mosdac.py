"""isro's insat-3dr fire product from mosdac: one kml of ~4 km fire pixels for every 30-minute scan.

searching the catalogue is public, downloading needs a free mosdac account (MOSDAC_CREDS_FILE).
we log in once per run and never retry a failed login, since three failures lock the account for an hour.
"""
from __future__ import annotations

import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

SEARCH_URL = "https://mosdac.gov.in/apios/datasets.json"
TOKEN_URL = "https://mosdac.gov.in/download_api/gettoken"
REFRESH_URL = "https://mosdac.gov.in/download_api/refresh-token"
DOWNLOAD_URL = "https://mosdac.gov.in/download_api/download"
LOGOUT_URL = "https://mosdac.gov.in/download_api/logout"

FIRE_3DR = "3RIMG_L2P_FIR"  # insat-3dr, scans at :15 and :45 utc
FIRE_3DS = "3SIMG_L2P_FIR"  # insat-3ds, scans at :00 and :30 utc
DAILY_FILE_CAP = 5000  # mosdac allows 5000 downloads a day and 300 a minute
PER_MINUTE = 240

_NAME_TIME = re.compile(r"_(\d{2}[A-Z]{3}\d{4})_(\d{4})_")


def granule_time(identifier: str) -> datetime:
    """utc scan time from a name like 3RIMG_19OCT2026_0345_L2P_FIR_V01R00.kml (the catalogue's
    own timestamps use a 12-hour clock, so we don't trust them)."""
    m = _NAME_TIME.search(identifier)
    if not m:
        raise ValueError(f"no scan time in {identifier}")
    return datetime.strptime(m.group(1) + m.group(2), "%d%b%Y%H%M").replace(tzinfo=timezone.utc)


def search(dataset: str, start: str, end: str, client: httpx.Client | None = None) -> list[dict]:
    """every catalogue entry between two utc dates, oldest first. no login needed."""
    own = client is None
    client = client or httpx.Client(timeout=90)
    entries, seen, start_index = [], set(), 1
    try:
        while True:
            r = client.get(SEARCH_URL, params={"datasetId": dataset, "startTime": start, "endTime": end,
                                               "startIndex": start_index, "count": 100})
            if r.status_code != 200:  # "no data" comes back as a 500
                if entries:
                    break
                raise RuntimeError(f"mosdac search {dataset} {start}..{end}: {r.status_code} {r.text[:200]}")
            body = r.json()
            page = body.get("entries") or []
            for e in page:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    entries.append(e)
            if len(page) < 100 or len(entries) >= int(body.get("totalResults") or 0):
                break
            start_index += len(page)
    finally:
        if own:
            client.close()
    return sorted(entries, key=lambda e: granule_time(e["identifier"]))


def read_creds(path: str | os.PathLike | None = None) -> tuple[str, str]:
    """(username, password) from a key=value file. don't print these."""
    if not path:
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        path = os.environ.get("MOSDAC_CREDS_FILE", "")
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("set MOSDAC_CREDS_FILE to a file with username=... and password=... lines")
    kv = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cut = [i for i in (line.find("="), line.find(":")) if i > 0]  # whichever separator comes first
        if cut:
            k, v = line[:min(cut)], line[min(cut) + 1:]
            kv[k.strip().lower()] = v.strip().strip("'\"")
    missing = [k for k in ("username", "password") if not kv.get(k)]
    if missing:
        raise KeyError(f"{path} has no value for: {', '.join(missing)}")
    return kv["username"], kv["password"]


class QuotaExceeded(RuntimeError):
    pass


class Session:
    """a logged-in session; use it with `with` so it always logs out. download() is thread-safe."""

    def __init__(self, creds_file: str | os.PathLike | None = None):
        self._creds_file = creds_file
        self._http = httpx.Client(timeout=120)
        self._lock = threading.Lock()
        self._pace_lock = threading.Lock()
        self._recent = deque()  # when the last minute's downloads started
        self._user = self._access = self._refresh = None

    def __enter__(self) -> "Session":
        user, pwd = read_creds(self._creds_file)
        r = self._http.post(TOKEN_URL, json={"username": user, "password": pwd})
        if r.status_code != 200 or not r.json().get("access_token"):
            self._http.close()
            raise RuntimeError(f"mosdac login failed ({r.status_code}): {_message(r)}. not retrying: three "
                               "failures in a row lock the account for an hour.")
        body = r.json()
        self._user, self._access, self._refresh = user, body["access_token"], body.get("refresh_token")
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._http.post(LOGOUT_URL, json={"username": self._user}, timeout=15)
        except httpx.HTTPError:
            pass  # it times out on its own anyway
        self._http.close()

    def _renew(self, stale: str) -> None:
        with self._lock:
            if self._access != stale:  # another thread got there first
                return
            if not self._refresh:
                raise RuntimeError("mosdac token expired and there is no refresh token")
            r = self._http.post(REFRESH_URL, json={"refresh_token": self._refresh})
            if r.status_code != 200 or not r.json().get("access_token"):
                raise RuntimeError(f"mosdac token refresh failed ({r.status_code}); not logging in again")
            body = r.json()
            self._access, self._refresh = body["access_token"], body.get("refresh_token", self._refresh)

    def _pace(self) -> None:
        with self._pace_lock:  # at most PER_MINUTE downloads in any minute, across threads
            while len(self._recent) >= PER_MINUTE:
                wait = 60 - (time.monotonic() - self._recent[0])
                if wait > 0:
                    time.sleep(wait)
                self._recent.popleft()
            self._recent.append(time.monotonic())

    def download(self, entry: dict, out_dir: Path) -> Path | None:
        """downloads one file into out_dir, or returns None if it's already there."""
        dest = out_dir / entry["identifier"]
        if dest.exists() and dest.stat().st_size > 0:
            return None
        attempt = throttled = 0
        while attempt < 5:
            attempt += 1
            self._pace()
            token = self._access
            try:
                r = self._http.get(DOWNLOAD_URL, params={"id": entry["id"]},
                                   headers={"Authorization": f"Bearer {token}"})
            except httpx.TransportError:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 200:
                head = r.content[:400].lstrip().lower()
                if not (head.startswith(b"<?xml") or head.startswith(b"<kml")):
                    raise RuntimeError(f"{dest.name}: response is not kml ({head[:80]!r})")
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(dest)
                return dest
            msg = _message(r)
            if r.status_code == 401 and "TOKEN" in msg.upper():
                self._renew(token)
                continue
            if "per minute" in msg.lower() and throttled < 10:  # hit the per-minute limit, wait it out
                throttled += 1
                attempt -= 1
                time.sleep(30)
                continue
            if "limit" in msg.lower() or "quota" in msg.lower():
                raise QuotaExceeded(f"mosdac download limit reached: {msg}")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"{dest.name}: download failed ({r.status_code}): {msg}")
        raise RuntimeError(f"{dest.name}: gave up after 5 attempts")


def _message(r: httpx.Response) -> str:
    try:
        b = r.json()
    except ValueError:
        return r.text[:200]
    if not isinstance(b, dict):
        return str(b)
    return " ".join(str(b[k]) for k in ("code", "message", "error") if b.get(k)) or str(b)


def parse_fire_kml(source: str | os.PathLike | bytes) -> list[dict]:
    """fire pixels in one file: position, scan time and the two brightness temperatures (kelvin).

    the temperatures sit in <address> as "tir1, tir1mir", but mosdac never resets that string, so each
    placemark repeats all the earlier values first. we strip off what we've already seen."""
    if isinstance(source, (str, os.PathLike)) and not str(source).lstrip().startswith("<"):
        root = ET.parse(source).getroot()
    else:
        root = ET.fromstring(source)
    points, prev = [], ""
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        text = {_local(el.tag): (el.text or "").strip() for el in pm.iter() if el.text and el.text.strip()}
        if not text.get("coordinates"):
            continue
        lon, lat = (float(v) for v in text["coordinates"].split()[0].split(",")[:2])
        rec = {"lon": lon, "lat": lat, "t": None, "bt_tir1": None, "bt_mir": None}
        if text.get("when"):
            try:
                rec["t"] = datetime.strptime(text["when"], "%d-%b-%YT%H:%M:%S UTC").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        a, _, b = text.get("address", "").partition(",")
        a, b = a.strip(), b.strip()
        if b.startswith(a) and a.startswith(prev):  # repeated earlier values in front
            tir1, mir, prev = a[len(prev):], b[len(a):], b
        else:
            tir1, mir = a, b
        try:
            rec["bt_tir1"], rec["bt_mir"] = float(tir1), float(mir)
        except ValueError:
            pass
        points.append(rec)
    return points


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
