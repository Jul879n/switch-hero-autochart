"""Sugerencias de artistas y canciones con MusicBrainz (API gratuita, sin clave)."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

BASE = "https://musicbrainz.org/ws/2"
UA = "SwitchHeroAutochart/1.0 (https://switchhero.jarayacontreras.cl)"    # MusicBrainz exige un User-Agent identificable
MIN_GAP = 1.1          # su límite es 1 consulta por segundo
TTL = 6 * 3600
_cache: dict = {}
_lock = threading.Lock()
_last = 0.0


def _lucene(s: str) -> str:
    return "".join("\\" + c if c in '+-&|!(){}[]^"~*?:\\/' else c for c in s)


def _prefix(field: str, text: str) -> str:
    """Cada palabra como término; la última con comodín, para sugerir mientras se escribe."""
    words = [_lucene(w) for w in text.split()]
    return " AND ".join(f"{field}:{w}" + ("*" if i == len(words) - 1 else "") for i, w in enumerate(words))


def _fetch(url: str) -> dict:
    global _last
    with _lock:                                       # serializa y espacia las consultas
        wait = _last + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.load(r)


def search(kind: str, q: str, artist: str = "") -> list[str]:
    q, artist = q.strip()[:100], artist.strip()[:100]
    if len(q) < 2:
        return []
    key = (kind, q.lower(), artist.lower())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        if kind == "artist":
            data = _fetch(f"{BASE}/artist?" + urllib.parse.urlencode(
                {"query": _prefix("artist", q), "fmt": "json", "limit": 8}))
            names = [a.get("name", "") for a in data.get("artists", [])]
        else:
            lq = _prefix("recording", q) + (f' AND artist:"{_lucene(artist)}"' if artist else "")
            data = _fetch(f"{BASE}/recording?" + urllib.parse.urlencode({"query": lq, "fmt": "json", "limit": 12}))
            names = [r.get("title", "") for r in data.get("recordings", [])]
    except Exception:
        return []                                      # sin red o límite excedido: el campo sigue siendo libre
    out = list(dict.fromkeys(n for n in names if n))[:8]
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time(), out)
    return out
