import os
import tempfile

import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_EMAIL"] = "admin@x.cl"
os.environ["ADMIN_PASSWORD"] = "clave-admin-123"
os.environ["INSTALLER_TOKEN"] = "tok"
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from app import jobs
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(jobs, "start", lambda: None)   # sin worker real
    with TestClient(app) as c:
        yield c


def login(client, email="admin@x.cl", pw="clave-admin-123"):
    return client.post("/api/auth/login", json={"email": email, "password": pw})


def test_requires_login(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/jobs", data={"url": "https://x.y"}).status_code == 401
    assert client.get("/api/admin/users").status_code == 401
    assert login(client, pw="mala").status_code == 401


def test_validation(client):
    assert login(client).status_code == 200
    assert client.post("/api/jobs", data={}).status_code == 400
    r = client.post("/api/jobs", data={"url": "https://x.y"}, files={"file": ("a.mp3", b"abc")})
    assert r.status_code == 400
    assert client.post("/api/jobs", data={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/jobs", data={"url": "http://127.0.0.1:8000/x"}).status_code == 400   # SSRF
    assert client.post("/api/jobs", data={"url": "http://169.254.169.254/latest"}).status_code == 400
    assert client.get("/api/jobs/zzzz").status_code == 404


def test_only_admin_manages_users(client):
    login(client)
    r = client.post("/api/admin/users", json={"email": "u@x.cl", "password": "una-clave-larga"})
    assert r.status_code == 201
    inv = client.post("/api/admin/invites", json={"email": "n@x.cl"})
    assert inv.status_code == 201 and inv.json()["emailed"] is False
    token = inv.json()["link"].split("invite=")[1]
    client.post("/api/auth/logout")
    assert client.post(f"/api/auth/invite/{token}", json={"name": "N", "password": "otra-clave-larga"}).status_code == 200
    assert client.post(f"/api/auth/invite/{token}", json={"name": "N", "password": "otra-clave-larga"}).status_code == 400
    assert login(client, "n@x.cl", "otra-clave-larga").status_code == 200
    assert client.post("/api/admin/invites", json={"email": "z@x.cl"}).status_code == 403
    assert client.post("/api/admin/users", json={"email": "z@x.cl", "password": "una-clave-larga"}).status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_create_list_delete_and_isolation(client):
    login(client)
    r = client.post("/api/jobs", files={"file": ("a.mp3", b"abc")}, data={"title": "T"})
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    assert client.get("/api/switch/songs").json() == []
    assert client.get(f"/api/switch/songs/{jid}/..%2Fx").status_code == 404
    client.post("/api/auth/logout")
    login(client, "u@x.cl", "una-clave-larga")
    assert client.get(f"/api/jobs/{jid}").status_code == 404      # no ve trabajos ajenos
    client.post("/api/auth/logout")
    login(client)
    assert client.delete(f"/api/jobs/{jid}").status_code == 200


def test_installer_token(client):
    assert client.get("/api/switch/songs").status_code == 401
    assert client.get("/api/switch/songs", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert client.get("/api/switch/songs", headers={"Authorization": "Bearer mal"}).status_code == 401


def test_recover_and_change_password(client, monkeypatch):
    from app import auth, mail
    sent = {}
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send", lambda to, subj, body: sent.update(to=to, body=body) or True)
    login(client)
    client.post("/api/admin/users", json={"email": "r@x.cl", "password": "clave-original-1"})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/recover", json={"email": "nadie@x.cl"}).status_code == 200
    assert not sent                                          # no revela ni envía a desconocidos
    assert client.post("/api/auth/recover", json={"email": "r@x.cl"}).status_code == 200
    temp = sent["body"].split("\n\n")[1]
    assert login(client, "r@x.cl", "clave-original-1").status_code == 401
    r = login(client, "r@x.cl", temp)
    assert r.status_code == 200 and r.json()["must_change"] is True
    assert client.post("/api/auth/password", json={"current": "mala", "new": "nueva-clave-123"}).status_code == 400
    r = client.post("/api/auth/password", json={"current": temp, "new": "nueva-clave-123"})
    assert r.status_code == 200 and r.json()["must_change"] is False
    assert login(client, "r@x.cl", "nueva-clave-123").status_code == 200


def test_recover_fails_safe_when_send_fails(client, monkeypatch):
    from app import mail
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send", lambda *a: False)
    login(client)
    client.post("/api/admin/users", json={"email": "s@x.cl", "password": "clave-original-1"})
    client.post("/api/auth/logout")
    client.post("/api/auth/recover", json={"email": "s@x.cl"})
    assert login(client, "s@x.cl", "clave-original-1").status_code == 200   # no quedó bloqueado


def test_security_headers_and_csrf(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/docs").status_code in (404, 405)
    r = client.post("/api/auth/login", json={"email": "a", "password": "b"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_must_change_blocks_api(client, monkeypatch):
    from app import mail
    monkeypatch.setattr(mail, "configured", lambda: True)
    sent = {}
    monkeypatch.setattr(mail, "send", lambda to, s, b: sent.update(b=b) or True)
    login(client)
    client.post("/api/admin/users", json={"email": "m@x.cl", "password": "clave-original-1"})
    client.post("/api/auth/logout")
    client.post("/api/auth/recover", json={"email": "m@x.cl"})
    login(client, "m@x.cl", sent["b"].split("\n\n")[1])
    assert client.get("/api/jobs").status_code == 403
    assert client.get("/api/auth/me").status_code == 200
