"""thin wrapper over the official tinyfish sdk: on-disk cache + a call log.

tinyfish is how PyroScan reads the government web. none of the sources we need has an
api: the official fire bulletins are pdfs behind a wordpress page, the air-quality
bulletins hide behind base64 links, enforcement action only exists as news.

every call is logged to data/processed/tinyfish_log.jsonl, so the dashboard can show
what tinyfish fetched, when, and how long it took.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from . import config

CACHE = config.DATA / "cache" / "tinyfish"
LOG = config.PROCESSED / "tinyfish_log.jsonl"

_client = None


def client():
    global _client
    if _client is None:
        load_dotenv(config.ROOT / ".env", override=True)
        key = os.environ.get("TINYFISH_API_KEY", "").strip()
        if not key:
            raise RuntimeError("TINYFISH_API_KEY is missing. put it in .env (see .env.example)")
        from tinyfish import TinyFish

        _client = TinyFish(api_key=key)
    return _client


def available() -> bool:
    load_dotenv(config.ROOT / ".env", override=True)  # a key added while the server runs must win over an empty one
    return bool(os.environ.get("TINYFISH_API_KEY", "").strip())


def _dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_dump(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    return obj


def _key(kind: str, payload: dict) -> Path:
    h = hashlib.sha1(json.dumps([kind, payload], sort_keys=True, default=str).encode()).hexdigest()[:16]
    return CACHE / kind / f"{h}.json"


def _cached(kind: str, payload: dict, max_age_s: float | None):
    p = _key(kind, payload)
    if p.exists() and (max_age_s is None or time.time() - p.stat().st_mtime < max_age_s):
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _store(kind: str, payload: dict, data) -> None:
    p = _key(kind, payload)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def _log(kind: str, what: str, ok: bool, ms: float, note: str = "") -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = dict(t=datetime.now(timezone.utc).isoformat(timespec="seconds"), api=kind, what=what[:200],
               ok=ok, ms=round(ms), note=note[:200])
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def fetch(urls: list[str], *, format: str = "markdown", links: bool = False, purpose: str | None = None,
          max_age_s: float | None = 6 * 3600, timeout_ms: int = 90_000) -> list[dict]:
    """tinyfish fetch api: renders each url in a real browser, returns clean text.

    returns one dict per url: {url, final_url, title, text, links, error}.
    """
    payload = dict(urls=urls, format=format, links=links)
    hit = _cached("fetch", payload, max_age_s)
    if hit is not None:
        return hit
    t0 = time.time()
    try:
        r = client().fetch.get_contents(urls, format=format, links=links, purpose=purpose,
                                        per_url_timeout_ms=timeout_ms)
    except Exception as e:
        _log("fetch", ", ".join(urls), False, (time.time() - t0) * 1000, repr(e))
        raise
    res = {d.get("url"): d for d in _dump(r.results or [])}
    errs = {d.get("url"): d for d in _dump(r.errors or [])}
    out = []
    for u in urls:
        d = res.get(u) or next((v for k, v in res.items() if k and k.rstrip("/") == u.rstrip("/")), None)
        if d:
            out.append(dict(url=u, final_url=d.get("final_url"), title=d.get("title"), text=d.get("text") or "",
                            links=d.get("links") or [], error=None))
        else:
            e = errs.get(u, {})
            out.append(dict(url=u, final_url=None, title=None, text="", links=[], error=e.get("error", "missing")))
    ok = all(o["error"] is None for o in out)
    _log("fetch", ", ".join(urls), ok, (time.time() - t0) * 1000,
         "" if ok else "; ".join(f"{o['url']}: {o['error']}" for o in out if o["error"]))
    _store("fetch", payload, out)
    return out


def search(query: str, *, max_age_s: float | None = 6 * 3600, **params) -> list[dict]:
    """tinyfish search api: ranked live web/news results."""
    payload = dict(query=query, **params)
    hit = _cached("search", payload, max_age_s)
    if hit is not None:
        return hit
    t0 = time.time()
    try:
        r = client().search.query(query, **params)
    except Exception as e:
        _log("search", query, False, (time.time() - t0) * 1000, repr(e))
        raise
    out = _dump(r.results or [])
    _log("search", query, True, (time.time() - t0) * 1000, f"{len(out)} results")
    _store("search", payload, out)
    return out


def agent(url: str, goal: str, output_schema: dict | None = None, *, max_age_s: float | None = 24 * 3600) -> dict:
    """tinyfish web agent: a browser agent that follows a natural-language goal on a live site."""
    payload = dict(url=url, goal=goal, schema=output_schema)
    hit = _cached("agent", payload, max_age_s)
    if hit is not None:
        return hit
    t0 = time.time()
    try:
        r = client().agent.run(goal=goal, url=url, output_schema=output_schema)
    except Exception as e:
        _log("agent", f"{url} :: {goal}", False, (time.time() - t0) * 1000, repr(e))
        raise
    d = _dump(r)
    ok = str(d.get("status", "")).upper().endswith("COMPLETED")
    _log("agent", f"{url} :: {goal}", ok, (time.time() - t0) * 1000, str(d.get("num_of_steps", "")) + " steps")
    if ok:
        _store("agent", payload, d)
    return d
