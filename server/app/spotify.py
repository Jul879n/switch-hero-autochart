"""OAuth de Spotify (solo lectura de playlists, canciones guardadas, álbumes y artistas seguidos). Sin SPOTIFY_CLIENT_ID/SECRET queda desactivado."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from . import storage

AUTH = "https://accounts.spotify.com"
API = "https://api.spotify.com/v1"
SCOPE = "playlist-read-private playlist-read-collaborative user-library-read user-follow-read"
MAX_TRACKS = 500
_HTTP_ERRORS = {401: "La sesión de Spotify venció; vuelva a conectar", 403: "Spotify no entrega esa playlist",
                429: "Spotify limitó las consultas; intente en un minuto"}


class SpotifyError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def _http(url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    """Único punto de red."""
    body = urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read(4_000_000))
    except urllib.error.HTTPError as e:
        raise SpotifyError(_HTTP_ERRORS.get(e.code, f"Error de Spotify ({e.code})"), e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SpotifyError("Spotify no respondió; intente de nuevo")
    except ValueError:
        raise SpotifyError("Respuesta inválida de Spotify")


def auth_url(state: str, redirect: str) -> str:
    return f"{AUTH}/authorize?" + urlencode({"client_id": os.environ["SPOTIFY_CLIENT_ID"], "response_type": "code",
                                              "redirect_uri": redirect, "state": state, "scope": SCOPE})


def _token(form: dict) -> dict:
    cred = f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}".encode()
    return _http(f"{AUTH}/api/token", form, {"Authorization": "Basic " + base64.b64encode(cred).decode()})


def _save(uid: int, access: str, refresh: str, expires_in: int) -> None:
    storage._q("INSERT INTO spotify_tokens(user_id,access,refresh,expires) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE "
               "SET access=excluded.access, refresh=excluded.refresh, expires=excluded.expires",
               (uid, access, refresh, time.time() + int(expires_in)))


def connect(uid: int, code: str, redirect: str) -> None:
    r = _token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
    try:
        _save(uid, r["access_token"], r["refresh_token"], r.get("expires_in", 3600))
    except KeyError:
        raise SpotifyError("Respuesta inválida de Spotify")


def disconnect(uid: int) -> None:
    storage._q("DELETE FROM spotify_tokens WHERE user_id=?", (uid,))


def connected(uid: int) -> bool:
    return storage._q("SELECT 1 FROM spotify_tokens WHERE user_id=?", (uid,)).fetchone() is not None


def token_for(uid: int) -> str | None:
    row = storage._q("SELECT access, refresh, expires FROM spotify_tokens WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return None
    if row["expires"] > time.time() + 60:
        return row["access"]
    try:
        r = _token({"grant_type": "refresh_token", "refresh_token": row["refresh"]})
    except SpotifyError as e:
        if e.status in (400, 401):          # token revocado
            disconnect(uid)
            return None
        raise
    if not r.get("access_token"):
        raise SpotifyError("Respuesta inválida de Spotify")
    _save(uid, r["access_token"], r.get("refresh_token") or row["refresh"], r.get("expires_in", 3600))
    return r["access_token"]


def _get(url: str, token: str) -> dict:
    return _http(url, headers={"Authorization": f"Bearer {token}"})


def my_playlists(token: str) -> list[dict]:
    out, url = [], f"{API}/me/playlists?limit=50"
    for _ in range(20):
        page = _get(url, token)
        out += [{"id": p["id"], "name": p.get("name") or "", "total": (p.get("items") or {}).get("total", 0)}
                for p in page.get("items") or [] if p]
        url = page.get("next")
        if not url or not url.startswith(API + "/"):
            break
    return out


def playlist(pid: str, token: str) -> dict:
    data = _get(f"{API}/playlists/{pid}", token)
    page = data.get("items")
    if not page:
        raise SpotifyError("Spotify no entrega esa playlist", 403)
    tracks = []
    while True:
        for e in page.get("items") or []:
            t = (e or {}).get("item")
            if not t or t.get("type") != "track" or not t.get("name"):
                continue
            tracks.append({"artist": ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name")),
                           "title": t["name"].strip(), "duration": int((t.get("duration_ms") or 0) // 1000)})
        nxt = page.get("next")
        if len(tracks) >= MAX_TRACKS or not nxt or not nxt.startswith(API + "/"):
            break
        page = _get(nxt, token)
    if not tracks:
        raise SpotifyError("La playlist no tiene canciones", 404)
    return {"id": pid, "name": data.get("name") or "Playlist", "tracks": tracks[:MAX_TRACKS]}


def _artist_names(t: dict) -> str:
    return ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name"))


def _pages(url: str, token: str, pick, limit: int):
    """Recorre las páginas de una lista de Spotify; `pick(page)` devuelve (items, siguiente)."""
    out = []
    for _ in range(30):
        items, url = pick(_get(url, token))
        out += items
        if len(out) >= limit or not url or not url.startswith(API + "/"):
            break
    return out[:limit]


def _library_call(fn, *a):
    try:
        return fn(*a)
    except SpotifyError as e:
        if e.status == 403:           # la conexión es anterior a estos permisos
            raise SpotifyError("Vuelva a conectar Spotify para dar acceso a su biblioteca", 403)
        raise


def liked(token: str) -> dict:
    """«Canciones que me gustan»."""
    def pick(page):
        rows = []
        for e in page.get("items") or []:
            t = (e or {}).get("track") or (e or {}).get("item")
            if t and t.get("name"):
                rows.append({"artist": _artist_names(t), "title": t["name"].strip(), "duration": int((t.get("duration_ms") or 0) // 1000)})
        return rows, page.get("next")
    tracks = _library_call(_pages, f"{API}/me/tracks?limit=50", token, pick, MAX_TRACKS)
    if not tracks:
        raise SpotifyError("No tiene canciones guardadas", 404)
    return {"id": "liked", "name": "Canciones que me gustan", "tracks": tracks}


def library(token: str) -> dict:
    """Álbumes guardados y artistas seguidos: [{id, name, by}]."""
    def albums(page):
        return [{"id": a["id"], "name": a.get("name") or "", "by": _artist_names(a)}
                for a in (((e or {}).get("album") or (e or {}).get("item")) or {} for e in page.get("items") or []) if a.get("id")], page.get("next")
    def artists(page):
        box = page.get("artists") or {}
        return [{"id": a["id"], "name": a.get("name") or "", "by": ""} for a in box.get("items") or [] if a and a.get("id")], box.get("next")
    return {"albums": _library_call(_pages, f"{API}/me/albums?limit=50", token, albums, 200),
            "artists": _library_call(_pages, f"{API}/me/following?type=artist&limit=50", token, artists, 200)}
