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
