"""Cuentas propias: scrypt, sesiones por cookie, invitaciones. Solo el admin crea/invita usuarios."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request

from . import storage

COOKIE = "sh_session"
SESSION_TTL = 14 * 86400
INVITE_TTL = 3 * 86400
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_fails: dict = defaultdict(list)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise HTTPException(400, f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres")


def norm_email(e: str) -> str:
    e = (e or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 200:
        raise HTTPException(400, "Correo inválido")
    return e


# --- usuarios ---
def user_by_email(email: str):
    r = storage._q("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return dict(r) if r else None


def user_by_id(uid: int):
    r = storage._q("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def list_users() -> list:
    rows = storage._q("SELECT id,email,name,role,active,created_at,must_change,(pw_hash!='') AS has_password "
                      "FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(email: str, name: str = "", password: str = "", role: str = "user") -> int:
    cur = storage._q("INSERT INTO users(email,name,pw_hash,role,created_at) VALUES(?,?,?,?,?)",
                     (email, name, hash_password(password) if password else "", role, time.time()))
    return int(cur.lastrowid)


def bootstrap_admin() -> None:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not pw:
        return
    u = user_by_email(email)
    if u is None:
        create_user(email, "Admin", pw, "admin")
    elif u["role"] != "admin":
        storage._q("UPDATE users SET role='admin', active=1 WHERE id=?", (u["id"],))


def set_password(uid: int, pw: str, must_change: bool = False) -> None:
    storage._q("UPDATE users SET pw_hash=?, must_change=? WHERE id=?",
               (hash_password(pw), 1 if must_change else 0, uid))
    revoke_user_sessions(uid)


_recover: dict = defaultdict(list)


def temp_password() -> str:
    return secrets.token_urlsafe(9)


def recover_password(email: str, ip: str, send) -> None:
    """Envía una contraseña temporal. No revela si el correo existe y no cambia nada si el envío falla."""
    now = time.time()
    for key in (("e", email), ("i", ip)):
        _recover[key] = [t for t in _recover[key] if now - t < 3600]
        if len(_recover[key]) >= 5:
            raise HTTPException(429, "Demasiadas solicitudes; intente más tarde")
        _recover[key].append(now)
    u = user_by_email(email)
    if not u or not u["active"]:
        return
    temp = temp_password()
    if send(u["email"], temp):
        set_password(u["id"], temp, must_change=True)


# --- invitaciones ---
def create_invite(email: str) -> str:
    token = secrets.token_urlsafe(32)
    storage._q("INSERT INTO invites(token_hash,email,expires) VALUES(?,?,?)",
               (_th(token), email, time.time() + INVITE_TTL))
    return token


def invite_email(token: str) -> str | None:
    r = storage._q("SELECT email FROM invites WHERE token_hash=? AND used=0 AND expires>?",
                   (_th(token), time.time())).fetchone()
    return r["email"] if r else None


def accept_invite(token: str, name: str, password: str) -> int:
    email = invite_email(token)
    if not email:
        raise HTTPException(400, "Invitación inválida o vencida")
    check_password(password)
    u = user_by_email(email)
    if u and u["pw_hash"]:
        raise HTTPException(409, "Ese correo ya tiene cuenta")
    if u:
        storage._q("UPDATE users SET pw_hash=?, name=?, active=1 WHERE id=?",
                   (hash_password(password), name, u["id"]))
        uid = u["id"]
    else:
        uid = create_user(email, name, password)
    storage._q("UPDATE invites SET used=1 WHERE token_hash=?", (_th(token),))
    return uid


# --- sesiones ---
def login(email: str, password: str, ip: str, forever: bool = False) -> str:
    now = time.time()
    key = (email, ip)
    _fails[key] = [t for t in _fails[key] if now - t < 600]
    if len(_fails[key]) >= 5:
        raise HTTPException(429, "Demasiados intentos; espere 10 minutos")
    u = user_by_email(email)
    ok = bool(u and u["active"] and u["pw_hash"] and verify_password(password, u["pw_hash"]))
    if not u:
        verify_password(password, "scrypt$00$00")      # tiempo constante aproximado
    if not ok:
        _fails[key].append(now)
        raise HTTPException(401, "Correo o contraseña incorrectos")
    return new_session(u["id"], forever)


NEVER = 32503680000.0     # año 3000: sesiones de la Switch, que valen mientras la cuenta esté activa


def new_session(uid: int, forever: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    storage._q("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)",
               (_th(token), uid, NEVER if forever else time.time() + SESSION_TTL))
    return token


def logout(token: str) -> None:
    storage._q("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


def revoke_user_sessions(uid: int) -> None:
    storage._q("DELETE FROM sessions WHERE user_id=?", (uid,))


def user_from_token(token: str | None):
    if not token:
        return None
    r = storage._q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                   "WHERE s.token_hash=? AND s.expires>? AND u.active=1", (_th(token), time.time())).fetchone()
    return dict(r) if r else None


# --- dependencias FastAPI ---
def current_user(request: Request) -> dict:
    u = user_from_token(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Inicie sesión")
    if u.get("must_change") and request.url.path not in ("/api/auth/password", "/api/auth/me", "/api/auth/logout"):
        raise HTTPException(403, "Debe cambiar su contraseña antes de continuar")
    return u


def admin_user(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Solo el administrador puede hacerlo")
    return u


def installer_or_user(request: Request) -> dict:
    """Sesión web, sesión de la Switch (`Authorization: Bearer <token de /api/switch/login>`)
    o token fijo del instalador (INSTALLER_TOKEN)."""
    expected = os.environ.get("INSTALLER_TOKEN", "")
    h = request.headers.get("authorization", "")
    if h.startswith("Bearer "):
        tok = h[7:]
        if expected and hmac.compare_digest(tok, expected):
            return {"id": 0, "role": "installer", "email": "installer"}
        u = user_from_token(tok)
        if u and not u.get("must_change"):
            return {**u, "via": "switch"}
        if u:
            raise HTTPException(403, "Debe cambiar su contraseña en la web antes de continuar")
        raise HTTPException(401, "Sesión inválida")
    return current_user(request)