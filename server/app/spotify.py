"""OAuth de Spotify (solo lectura de playlists). Sin SPOTIFY_CLIENT_ID/SECRET queda desactivado."""
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
SCOPE = "playlist-read-private playlist-read-collaborative"
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
