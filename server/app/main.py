"""API FastAPI + front estático."""
from __future__ import annotations

import hmac
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, ftp, jobs, mail, storage, suggest

MAX_BYTES = 100 * 1024 * 1024
WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app):
    storage.init()
    auth.bootstrap_admin()
    jobs.start()
    yield


app = FastAPI(title="Switch Hero Autochart", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

CSP = ("default-src 'self'; img-src 'self' data: https://i.ytimg.com; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):       # CSRF: el Origin debe ser el propio host
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Origen no permitido"}, status_code=403)
    resp = await call_next(request)
    resp.headers.update({
        "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer", "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    })
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


MAX_ACTIVE_PER_USER = 5


def _public_url_ok(url: str) -> bool:
    """Rechaza URLs hacia redes internas (SSRF): solo hosts que resuelvan a IPs públicas."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def _public(j: dict) -> dict:
    out = {k: j[k] for k in ("id", "status", "stage", "progress", "title", "artist", "src_kind",
                              "error", "created_at", "folder", "sent")}
    if j["status"] == "running" and j.get("updated"):
        out["idle"] = int(max(0, time.time() - j["updated"]))      # segundos sin avanzar
    if j["status"] == "done":
        out["info"] = _song_info(j)
    return out


def _song_dir(j: dict) -> Path:
    return storage.job_dir(j["id"]) / j["folder"]


def _song_info(j: dict) -> dict:
    from autochart import ini
    p = _song_dir(j) / "song.ini"
    try:
        d = ini.parse(p)
    except OSError:
        return {}
    names = {"diff_guitar": "Guitarra", "diff_guitar_coop": "Co-op", "diff_rhythm": "Rítmica",
             "diff_bass": "Bajo", "diff_keys": "Teclado", "diff_drums": "Batería"}
    inst = [n for k, n in names.items() if d.get(k, "-1") not in ("-1", "")]
    return {"instruments": inst, "difficulties": ["Fácil", "Medio", "Difícil", "Experto"]}


def _job_or_404(jid: str, user: dict | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{12}", jid):
        raise HTTPException(404, "Trabajo inexistente")
    j = storage.get(jid)
    if not j or (user is not None and user["role"] == "user" and j["user_id"] != user["id"]):
        raise HTTPException(404, "Trabajo inexistente")
    return j


def _base_url(request: Request) -> str:
    return os.environ.get("PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


def _secure(request: Request) -> bool:
    return (os.environ.get("PUBLIC_URL", "").startswith("https://") or request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https")


def _me(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"],
            "must_change": bool(u.get("must_change"))}


# ---------- sesión ----------
@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    ip = request.client.host if request.client else "?"
    token = auth.login(auth.norm_email(str(body.get("email", ""))), str(body.get("password", "")), ip)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                        secure=_secure(request))
    return _me(auth.user_from_token(token))


@app.post("/api/auth/recover")
async def recover(request: Request):
    body = await request.json()
    email = auth.norm_email(str(body.get("email", "")))
    ip = request.client.host if request.client else "?"
    if not mail.configured():
        raise HTTPException(503, "El envío de correo no está configurado; pida al administrador una nueva contraseña")

    def send(to: str, temp: str) -> bool:
        return mail.send(to, "Contraseña temporal de Switch Hero Autochart",
                         f"Su contraseña temporal es:\n\n{temp}\n\nInicie sesión y el sistema le pedirá elegir una nueva.\n"
                         "Si no lo solicitó, ignore este mensaje.\n")
    auth.recover_password(email, ip, send)
    return {"ok": True}


@app.post("/api/auth/password")
async def change_password(request: Request, response: Response, u: dict = Depends(auth.current_user)):
    body = await request.json()
    cur, new = str(body.get("current", "")), str(body.get("new", ""))
    if not auth.verify_password(cur, u["pw_hash"]):
        raise HTTPException(400, "La contraseña actual no coincide")
    auth.check_password(new)
    if new == cur:
        raise HTTPException(400, "La nueva contraseña debe ser distinta")
    auth.set_password(u["id"], new)
    ip = request.client.host if request.client else "?"
    token = auth.login(u["email"], new, ip)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                        secure=_secure(request))
    return _me(auth.user_by_id(u["id"]))


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    t = request.cookies.get(auth.COOKIE)
    if t:
        auth.logout(t)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(u: dict = Depends(auth.current_user)):
    return _me(u)


@app.get("/api/auth/invite/{token}")
def invite_info(token: str):
    email = auth.invite_email(token)
    if not email:
        raise HTTPException(404, "Invitación inválida o vencida")
    return {"email": email}


@app.post("/api/auth/invite/{token}")
async def invite_accept(token: str, request: Request):
    body = await request.json()
    auth.accept_invite(token, str(body.get("name", "")).strip()[:100], str(body.get("password", "")))
    return {"ok": True}


# ---------- administración (solo admin) ----------
@app.get("/api/admin/users")
def admin_users(_: dict = Depends(auth.admin_user)):
    return auth.list_users()


@app.post("/api/admin/invites", status_code=201)
async def admin_invite(request: Request, _: dict = Depends(auth.admin_user)):
    body = await request.json()
    email = auth.norm_email(str(body.get("email", "")))
    u = auth.user_by_email(email)
    if u and u["pw_hash"]:
        raise HTTPException(409, "Ese correo ya tiene cuenta")
    link = f"{_base_url(request)}/?invite={auth.create_invite(email)}"
    sent = mail.send(email, "Invitación a Switch Hero Autochart",
                     f"Le invitaron a Switch Hero Autochart.\n\nCree su cuenta aquí (el enlace vence en 3 días):\n{link}\n")
    return {"email": email, "emailed": sent, "link": link}


@app.post("/api/admin/users", status_code=201)
async def admin_create_user(request: Request, _: dict = Depends(auth.admin_user)):
    body = await request.json()
    email = auth.norm_email(str(body.get("email", "")))
    password = str(body.get("password", ""))
    auth.check_password(password)
    role = "admin" if body.get("role") == "admin" else "user"
    if auth.user_by_email(email):
        raise HTTPException(409, "Ese correo ya existe")
    uid = auth.create_user(email, str(body.get("name", "")).strip()[:100], password, role)
    return _me(auth.user_by_id(uid))


@app.patch("/api/admin/users/{uid}")
async def admin_patch_user(uid: int, request: Request, admin: dict = Depends(auth.admin_user)):
    body = await request.json()
    u = auth.user_by_id(uid)
    if not u:
        raise HTTPException(404, "Usuario inexistente")
    if uid == admin["id"]:
        raise HTTPException(400, "No puede modificarse a sí mismo")
    if "active" in body:
        storage._q("UPDATE users SET active=? WHERE id=?", (1 if body["active"] else 0, uid))
        if not body["active"]:
            auth.revoke_user_sessions(uid)
    if body.get("role") in ("admin", "user"):
        storage._q("UPDATE users SET role=? WHERE id=?", (body["role"], uid))
    return _me(auth.user_by_id(uid))


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, admin: dict = Depends(auth.admin_user)):
    if uid == admin["id"]:
        raise HTTPException(400, "No puede eliminarse a sí mismo")
    auth.revoke_user_sessions(uid)
    storage._q("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@app.post("/api/jobs", status_code=201)
async def create_job(file: UploadFile | None = File(None), url: str = Form(""),
                     title: str = Form(""), artist: str = Form(""), user: dict = Depends(auth.current_user)):
    url = url.strip()
    has_file = file is not None and bool(file.filename)
    if has_file == bool(url):
        raise HTTPException(400, "Envíe un archivo o una URL (solo uno)")
    title, artist = title.strip()[:200], artist.strip()[:200]
    active = [j for j in storage.list_jobs(user_id=user["id"]) if j["status"] in ("queued", "running")]
    if len(active) >= MAX_ACTIVE_PER_USER:
        raise HTTPException(429, f"Ya tiene {MAX_ACTIVE_PER_USER} canciones en proceso; espere a que terminen")
    if url:
        if len(url) > 2000 or not _public_url_ok(url):
            raise HTTPException(400, "URL inválida o no permitida")
        jid = storage.create("url", url, title, artist, user["id"])
    else:
        jid = storage.create("file", "", title, artist, user["id"])
        up = storage.job_dir(jid) / "upload"
        up.mkdir(parents=True, exist_ok=True)
        ext = re.sub(r"[^A-Za-z0-9.]", "", Path(file.filename).suffix)[:8] or ".bin"
        dest = up / f"input{ext}"
        size = 0
        try:
            with open(dest, "wb") as fh:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "El archivo supera 100 MB")
                    fh.write(chunk)
        except HTTPException:
            shutil.rmtree(storage.job_dir(jid), ignore_errors=True)
            storage.delete(jid)
            raise
        if size == 0:
            shutil.rmtree(storage.job_dir(jid), ignore_errors=True)
            storage.delete(jid)
            raise HTTPException(400, "El archivo está vacío")
        storage.update(jid, src=str(dest))
    jobs.kick()
    return _public(storage.get(jid))


@app.get("/api/jobs")
def list_jobs(user: dict = Depends(auth.current_user)):
    return [_public(j) for j in storage.list_jobs(user_id=None if user["role"] == "admin" else user["id"])]


@app.get("/api/jobs/{jid}")
def get_job(jid: str, user: dict = Depends(auth.current_user)):
    return _public(_job_or_404(jid, user))


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    if j["status"] == "running":
        raise HTTPException(409, "El trabajo está en curso")
    shutil.rmtree(storage.job_dir(jid), ignore_errors=True)
    storage.delete(jid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: str, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    if j["status"] != "error":
        raise HTTPException(409, "Solo se reintentan los trabajos con error")
    storage.update(jid, status="queued", stage="", progress=0, error="")
    jobs.kick()
    return {"ok": True}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    if j["status"] == "queued":
        storage.update(jid, status="error", error=jobs.CANCELLED)
    elif j["status"] == "running":
        jobs.request(jid, "cancel")
    else:
        raise HTTPException(409, "Solo se cancelan trabajos en cola o en curso")
    return {"ok": True}


@app.post("/api/jobs/{jid}/resume")
def resume_job(jid: str, user: dict = Depends(auth.current_user)):
    """Continuar: reencola un trabajo con error o cancelado, o reinicia el paso de uno detenido (reusa los stems)."""
    j = _job_or_404(jid, user)
    if j["status"] == "error":
        storage.update(jid, status="queued", stage="", progress=0, error="")
        jobs.kick()
    elif j["status"] == "running":
        jobs.request(jid, "resume")
    else:
        raise HTTPException(409, "El trabajo no está detenido")
    return {"ok": True}


_search_cache: dict = {}
_search_hits: dict = {}


@app.get("/api/search")
def search_songs(q: str = "", source: str = "soundcloud", user: dict = Depends(auth.current_user)):
    q = " ".join(q.split())[:120]
    if len(q) < 2:
        raise HTTPException(400, "Escriba al menos 2 letras")
    if source not in jobs.SOURCES:
        raise HTTPException(400, "Fuente no válida")
    key = f"{source}:{q.lower()}"
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return {"items": hit[1]}
    now = time.time()
    recent = [t for t in _search_hits.get(user["id"], []) if now - t < 60]
    if len(recent) >= 10:                        # no gatillar bloqueos al servidor
        raise HTTPException(429, "Demasiadas búsquedas; espere un minuto")
    _search_hits[user["id"]] = recent + [now]
    try:
        items = jobs.search(q, source=source)
    except jobs.DownloadError as e:
        raise HTTPException(502, str(e))
    if len(_search_cache) > 300:
        _search_cache.clear()
    _search_cache[key] = (now, items)
    return {"items": items}


@app.get("/api/suggest")
def suggest_names(kind: str, q: str = "", artist: str = "", user: dict = Depends(auth.current_user)):
    if kind not in ("artist", "title", "song"):
        raise HTTPException(400, "kind inválido")
    items = suggest.search(kind, q, artist)
    meta = [f"{d // 60}:{d % 60:02d}" if (d := suggest.durations.get(i)) else "" for i in items] if kind == "song" else []
    return {"items": items, "meta": meta}


@app.get("/api/jobs/{jid}/zip")
def job_zip(jid: str, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    z = storage.job_dir(jid) / "song.zip"
    if j["status"] != "done" or not z.is_file():
        raise HTTPException(404, "La canción aún no está lista")
    return FileResponse(z, media_type="application/zip", filename=f"{j['folder']}.zip")


@app.post("/api/ftp/check")
async def ftp_check(request: Request, user: dict = Depends(auth.current_user)):
    body = await request.json()
    try:
        port = int(body.get("port") or 5000)
        host = await run_in_threadpool(ftp.check, str(body.get("host", "")), port)
    except ValueError:
        raise HTTPException(400, "Puerto inválido")
    except ftp.FtpError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "host": host, "port": port}


@app.post("/api/jobs/{jid}/ftp")
async def job_ftp(jid: str, request: Request, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    if j["status"] != "done" or not j["folder"]:
        raise HTTPException(409, "La canción aún no está lista")
    body = await request.json()
    try:
        port = int(body.get("port") or 5000)
        n = await run_in_threadpool(ftp.push, str(body.get("host", "")), port, _song_dir(j), j["folder"])
    except ValueError:
        raise HTTPException(400, "Puerto inválido")
    except ftp.FtpError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "files": n}


def _files(j: dict) -> list:
    d = _song_dir(j)
    return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()] \
        if d.is_dir() else []


_switch_seen: dict = {}      # id de usuario -> último contacto de su Switch (.nro); sirve de «conectada»
SWITCH_ALIVE_SEC = 45


@app.post("/api/switch/login")
async def switch_login(request: Request):
    """Correo y contraseña de la cuenta -> token de sesión para el .nro (sin cookies)."""
    body = await request.json()
    ip = request.client.host if request.client else "?"
    token = auth.login(auth.norm_email(str(body.get("email", ""))), str(body.get("password", "")), ip, forever=True)
    return {"token": token}


# Cookies de YouTube (yt-dlp): el servidor está en un datacenter y YouTube le exige iniciar sesión.
@app.get("/api/admin/cookies")
def cookies_status(user: dict = Depends(auth.admin_user)):
    p = jobs.cookies_path()
    return {"present": p.is_file(), "bytes": p.stat().st_size if p.is_file() else 0,
            "modified": p.stat().st_mtime if p.is_file() else 0}


@app.put("/api/admin/cookies")
async def cookies_put(request: Request, user: dict = Depends(auth.admin_user)):
    raw = await request.body()
    if not raw or len(raw) > 512 * 1024:
        raise HTTPException(400, "Archivo vacío o demasiado grande (máx. 512 KB)")
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
    rows = [ln for ln in text.split("\n") if ln.strip() and not ln.startswith("#")]
    ok = [ln for ln in rows if len(ln.split("\t")) == 7]
    if not ok or not any("youtube.com" in ln or "google.com" in ln for ln in ok):
        raise HTTPException(400, "No parece un cookies.txt (formato Netscape) con cookies de youtube.com")
    p = jobs.cookies_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("# Netscape HTTP Cookie File\n" + "\n".join(ok) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return {"ok": True, "cookies": len(ok)}


@app.delete("/api/admin/cookies")
def cookies_delete(user: dict = Depends(auth.admin_user)):
    jobs.cookies_path().unlink(missing_ok=True)
    return {"ok": True}


# Vinculación por QR: la Switch pide un código, el usuario lo confirma desde el teléfono con su sesión.
PAIR_TTL = 600
PAIR_MAX = 200
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_pairs: dict = {}            # código -> {"secret", "exp", "token"}


def _pairs_purge() -> None:
    now = time.time()
    for c in [c for c, p in _pairs.items() if p["exp"] < now]:
        del _pairs[c]


@app.post("/api/switch/pair")
def pair_start(request: Request):
    _pairs_purge()
    if len(_pairs) >= PAIR_MAX:
        raise HTTPException(429, "Demasiadas vinculaciones pendientes; intente en unos minutos")
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(6))
    secret = secrets.token_urlsafe(24)
    _pairs[code] = {"secret": secret, "exp": time.time() + PAIR_TTL, "token": None}
    return {"code": code, "secret": secret, "expires_in": PAIR_TTL, "url": f"{_base_url(request)}/?pair={code}"}


@app.post("/api/switch/pair/poll")
async def pair_poll(request: Request):
    body = await request.json()
    secret = str(body.get("secret", ""))
    _pairs_purge()
    for code, p in _pairs.items():
        if hmac.compare_digest(p["secret"], secret):
            if p["token"] is None:
                return {"status": "wait"}
            del _pairs[code]
            return {"status": "ok", "token": p["token"]}
    raise HTTPException(410, "Código vencido")


@app.post("/api/switch/pair/confirm")
async def pair_confirm(request: Request, user: dict = Depends(auth.current_user)):
    body = await request.json()
    _pairs_purge()
    p = _pairs.get(str(body.get("code", "")).strip().upper())
    if not p or p["token"] is not None:
        raise HTTPException(404, "Código inválido o vencido")
    p["token"] = auth.new_session(user["id"], forever=True)
    return {"ok": True}


@app.get("/api/switch/status")
def switch_status(user: dict = Depends(auth.current_user)):
    return {"connected": time.time() - _switch_seen.get(user["id"], 0.0) < SWITCH_ALIVE_SEC}


@app.post("/api/jobs/{jid}/send")
def job_send(jid: str, user: dict = Depends(auth.current_user)):
    """Marca la canción para que la Switch la instale sola en su próxima consulta."""
    j = _job_or_404(jid, user)
    if j["status"] != "done":
        raise HTTPException(409, "La canción aún no está lista")
    storage.update(jid, sent=1)
    return {"ok": True}


@app.post("/api/switch/songs/{jid}/ack")
def switch_ack(jid: str, user: dict = Depends(auth.installer_or_user)):
    _job_or_404(jid, None if user["role"] == "installer" else user)
    storage.update(jid, sent=0)
    return {"ok": True}


@app.get("/api/switch/songs")
def switch_songs(user: dict = Depends(auth.installer_or_user)):
    if user.get("via") == "switch":
        _switch_seen[user["id"]] = time.time()
    out = []
    # una Switch con sesión de cuenta ve solo las canciones de esa cuenta; el token fijo, todas
    for j in storage.list_jobs("done", None if user["role"] == "installer" else user["id"]):
        files = _files(j)
        if files:
            out.append({"id": j["id"], "name": j["title"], "artist": j["artist"], "folder": j["folder"],
                        "bytes": sum(f["bytes"] for f in files), "files": files, "pending": bool(j["sent"])})
    return out


@app.get("/api/switch/songs/{jid}/{name}")
def switch_file(jid: str, name: str, user: dict = Depends(auth.installer_or_user)):
    j = _job_or_404(jid, None if user["role"] == "installer" else user)
    if j["status"] != "done" or name not in {f["name"] for f in _files(j)}:
        raise HTTPException(404, "Archivo inexistente")
    return FileResponse(_song_dir(j) / name)


def _asset_version() -> str:
    import hashlib
    h = hashlib.sha1()
    for n in ("style.css", "app.js", "index.html"):
        h.update((WEB / n).read_bytes())
    return h.hexdigest()[:10]


@app.get("/")
@app.get("/index.html")
def index():
    """index.html con CSS/JS versionados: Cloudflare cachea los estáticos 4 h y serviría una versión vieja."""
    v = _asset_version()
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="style.css"', f'href="style.css?v={v}"').replace('src="app.js"', f'src="app.js?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
