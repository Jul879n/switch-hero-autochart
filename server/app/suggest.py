"""Sugerencias de artistas y canciones: Deezer (rápido, entiende palabras a medias) y MusicBrainz de respaldo.
Ambas APIs son gratuitas y sin clave."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import unicodedata
import urllib.request

BASE = "https://musicbrainz.org/ws/2"
DEEZER = "https://api.deezer.com/search"
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
    return _get(url)


def _norm(s: str) -> str:
    """Sin tildes, mayúsculas, espacios ni signos: «Re:Re:» y «rere» coinciden."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if c.isalnum() and not unicodedata.combining(c))


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.load(r)


def _deezer(kind: str, q: str, artist: str) -> list[str]:
    if kind == "artist":
        data = _get(f"{DEEZER}/artist?" + urllib.parse.urlencode({"q": q, "limit": 10}))
        return [a.get("name", "") for a in data.get("data", [])]
    data = _get(f"{DEEZER}/track?" + urllib.parse.urlencode({"q": f"{artist} {q}".strip(), "limit": 25}))
    rows = data.get("data", [])
    same = [t for t in rows if artist and (t.get("artist") or {}).get("name", "").lower() == artist.lower()]
    return [t.get("title_short") or t.get("title", "") for t in (same or rows)]


def _musicbrainz(kind: str, q: str, artist: str) -> list[str]:
    if kind == "artist":
        data = _fetch(f"{BASE}/artist?" + urllib.parse.urlencode({"query": _prefix("artist", q), "fmt": "json", "limit": 8}))
        return [a.get("name", "") for a in data.get("artists", [])]
    lq = _prefix("recording", q) + (f' AND artist:"{_lucene(artist)}"' if artist else "")
    data = _fetch(f"{BASE}/recording?" + urllib.parse.urlencode({"query": lq, "fmt": "json", "limit": 12}))
    return [r.get("title", "") for r in data.get("recordings", [])]


def search(kind: str, q: str, artist: str = "") -> list[str]:
    q, artist = q.strip()[:100], artist.strip()[:100]
    if len(q) < 2:
        return []
    key = (kind, q.lower(), artist.lower())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    names: list[str] = []
    for source in (_deezer, _musicbrainz):
        try:
            names = [n for n in source(kind, q, artist) if n]
        except Exception:
            names = []
        hits = [n for n in names if _norm(q) in _norm(n)]      # primero lo que contiene lo escrito
        names = hits or names
        if names:
            break
    seen: set = set()
    out = [n for n in names if not (n.lower() in seen or seen.add(n.lower()))][:8]     # sin duplicados por mayúsculas
    if not out:
        return []                                       # sin resultados o sin red: no se guarda en caché
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time(), out)
    return out
