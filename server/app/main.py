"""API FastAPI + front estático."""
from __future__ import annotations

import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, jobs, mail, storage

MAX_BYTES = 100 * 1024 * 1024
WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app):
    storage.init()
    auth.bootstrap_admin()
    jobs.start()
    yield


app = FastAPI(title="Switch Hero Autochart", lifespan=lifespan)


def _public(j: dict) -> dict:
    out = {k: j[k] for k in ("id", "status", "stage", "progress", "title", "artist", "src_kind",
                              "error", "created_at", "folder")}
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


def _me(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}


# ---------- sesión ----------
@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    ip = request.client.host if request.client else "?"
    token = auth.login(auth.norm_email(str(body.get("email", ""))), str(body.get("password", "")), ip)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return _me(auth.user_from_token(token))


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
    if url:
        if not re.match(r"^https?://", url, re.I):
            raise HTTPException(400, "La URL debe comenzar con http:// o https://")
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


@app.get("/api/jobs/{jid}/zip")
def job_zip(jid: str, user: dict = Depends(auth.current_user)):
    j = _job_or_404(jid, user)
    z = storage.job_dir(jid) / "song.zip"
    if j["status"] != "done" or not z.is_file():
        raise HTTPException(404, "La canción aún no está lista")
    return FileResponse(z, media_type="application/zip", filename=f"{j['folder']}.zip")


def _files(j: dict) -> list:
    d = _song_dir(j)
    return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()] \
        if d.is_dir() else []


@app.get("/api/switch/songs")
def switch_songs(user: dict = Depends(auth.installer_or_user)):
    out = []
    for j in storage.list_jobs("done", None if user["role"] in ("admin", "installer") else user["id"]):
        files = _files(j)
        if files:
            out.append({"id": j["id"], "name": j["title"], "artist": j["artist"], "folder": j["folder"],
                        "bytes": sum(f["bytes"] for f in files), "files": files})
    return out


@app.get("/api/switch/songs/{jid}/{name}")
def switch_file(jid: str, name: str, user: dict = Depends(auth.installer_or_user)):
    j = _job_or_404(jid, None if user["role"] in ("admin", "installer") else user)
    if j["status"] != "done" or name not in {f["name"] for f in _files(j)}:
        raise HTTPException(404, "Archivo inexistente")
    return FileResponse(_song_dir(j) / name)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
