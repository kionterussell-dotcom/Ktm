"""Polite HTTP client: one request every few seconds, real user agent, aggressive
disk cache. We pull a few dozen pages a week, not at volume.

Nothing here ever returns a body on failure. A non-200, a timeout, or a blocked
CONNECT raises FetchError so the orchestrator can print FAILED with the reason
instead of a fetcher quietly parsing an error page into zero rows.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MIN_INTERVAL_S = 3.0          # per host
DEFAULT_TIMEOUT_S = 25
DEFAULT_TTL_S = 900           # 15 min; slate scans inside 6h of kickoff pass ttl=0

_last_hit: dict[str, float] = {}


class FetchError(RuntimeError):
    """A fetch did not produce a usable body. Never swallowed."""


def _throttle(host: str) -> None:
    last = _last_hit.get(host)
    if last is not None:
        wait = MIN_INTERVAL_S - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _cache_path(url: str, params: dict | None) -> Path:
    key = hashlib.sha256(f"{url}|{json.dumps(params or {}, sort_keys=True)}".encode()).hexdigest()[:20]
    host = url.split("//", 1)[-1].split("/", 1)[0].replace(":", "_")
    return CACHE_DIR / host / f"{key}.body"


def get(url: str, *, params: dict | None = None, ttl_s: int = DEFAULT_TTL_S,
        headers: dict | None = None, timeout_s: int = DEFAULT_TIMEOUT_S) -> str:
    """Return the response body as text, from cache when fresh.

    Raises FetchError on anything else. There is no empty-string return path.
    """
    cp = _cache_path(url, params)
    if ttl_s > 0 and cp.exists() and (time.time() - cp.stat().st_mtime) < ttl_s:
        return cp.read_text(encoding="utf-8")

    host = url.split("//", 1)[-1].split("/", 1)[0]
    _throttle(host)
    hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    hdrs.update(headers or {})

    try:
        r = requests.get(url, params=params, headers=hdrs, timeout=timeout_s)
    except requests.RequestException as e:
        raise FetchError(f"{host}: {type(e).__name__}: {e}") from e

    if r.status_code != 200:
        raise FetchError(f"{host}: HTTP {r.status_code} for {r.url}")
    if not r.text.strip():
        raise FetchError(f"{host}: empty body for {r.url}")

    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(r.text, encoding="utf-8")
    return r.text


def get_json(url: str, **kw) -> dict | list:
    body = get(url, **kw)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError(f"{url}: response was not JSON ({e})") from e
