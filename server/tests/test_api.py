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


def test_cover_extraction(tmp_path):
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return
    from app import jobs
    img = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(img)], check=True)
    mp3 = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-i", str(img), "-map", "0", "-map", "1",
                    "-metadata", "title=T", "-metadata", "artist=A", "-id3v2_version", "3", str(mp3)], check=True)
    assert jobs.tags(mp3)["title"] == "T"
    out = jobs.cover(mp3, tmp_path / "cover.jpg")
    assert out and (tmp_path / "cover.jpg").stat().st_size > 0
    assert jobs.cover(tmp_path / "missing.mp3", tmp_path / "x.jpg") == ""


def test_ftp_push(client, tmp_path, monkeypatch):
    from app import ftp, storage
    assert login(client).status_code == 200
    # validación de host/puerto: solo red local
    for h in ("127.0.0.1", "169.254.169.254", "0.0.0.0", "midominio.cl", ""):
        with pytest.raises(ftp.FtpError):
            ftp.check_host(h, 5000)
    with pytest.raises(ftp.FtpError):
        ftp.check_host("192.168.1.5", 0)
    assert ftp.check_host("192.168.1.5", 5000) == "192.168.1.5"
    assert ftp.check_host("100.101.102.103", 5000) == "100.101.102.103"     # Tailscale
    assert ftp.check_host("8.8.8.8", 5000) == "8.8.8.8"
    assert ftp.check_host(" 192,168,100,9 ", 5000) == "192.168.100.9"
    # endpoint: job listo, FTP simulado
    jid = storage.create("file", "", "T", "A", 1)
    d = storage.job_dir(jid) / "A - T"
    d.mkdir(parents=True)
    (d / "notes.chart").write_text("x")
    storage.update(jid, status="done", folder="A - T")
    sent = {}
    monkeypatch.setattr(ftp, "push", lambda host, port, folder, name: sent.update(h=host, p=port, n=name) or 1)
    r = client.post(f"/api/jobs/{jid}/ftp", json={"host": "192.168.1.50", "port": 5000})
    assert r.status_code == 200 and sent == {"h": "192.168.1.50", "p": 5000, "n": "A - T"}
    assert client.post(f"/api/jobs/{jid}/ftp", json={"host": "192.168.1.50", "port": "x"}).status_code == 400
    monkeypatch.undo()
    r = client.post(f"/api/jobs/{jid}/ftp", json={"host": "127.0.0.1", "port": 5000})
    assert r.status_code == 400 and "no permitida" in r.json()["detail"]


def test_ftp_check(client, monkeypatch):
    from app import ftp
    assert client.post("/api/ftp/check", json={"host": "192.168.1.5"}).status_code == 401
    assert login(client).status_code == 200
    monkeypatch.setattr(ftp, "check", lambda h, p: h)
    r = client.post("/api/ftp/check", json={"host": "192.168.100.9", "port": 5000})
    assert r.status_code == 200 and r.json() == {"ok": True, "host": "192.168.100.9", "port": 5000}
    monkeypatch.undo()
    assert client.post("/api/ftp/check", json={"host": "127.0.0.1"}).status_code == 400


def test_send_to_switch_queue(client):
    from app import storage
    assert login(client).status_code == 200
    jid = storage.create("file", "", "T", "A", 1)
    assert client.post(f"/api/jobs/{jid}/send").status_code == 409          # aún no está lista
    d = storage.job_dir(jid) / "A - T"
    d.mkdir(parents=True)
    (d / "notes.chart").write_text("x")
    storage.update(jid, status="done", folder="A - T")
    assert client.get("/api/switch/status").json() == {"connected": False}
    assert client.post(f"/api/jobs/{jid}/send").status_code == 200
    c2 = TestClient(app)                                                    # el .nro: sin cookies
    assert c2.post("/api/switch/login", json={"email": "admin@x.cl", "password": "mala"}).status_code == 401
    tok = c2.post("/api/switch/login", json={"email": "admin@x.cl", "password": "clave-admin-123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    songs = c2.get("/api/switch/songs", headers=h).json()
    assert [s["pending"] for s in songs if s["id"] == jid] == [True]
    assert client.get("/api/switch/status").json() == {"connected": True}   # el .nro consultó
    assert c2.post(f"/api/switch/songs/{jid}/ack", headers=h).status_code == 200
    assert [s["pending"] for s in c2.get("/api/switch/songs", headers=h).json() if s["id"] == jid] == [False]
    assert c2.post(f"/api/switch/songs/{jid}/ack").status_code == 401
    assert c2.get("/api/switch/songs", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_pairing_by_qr(client):
    assert client.post("/api/switch/pair/poll", json={"secret": "x"}).status_code == 410
    p = client.post("/api/switch/pair").json()
    assert len(p["code"]) == 6 and p["url"].endswith(f"/?pair={p['code']}")
    poll = lambda: client.post("/api/switch/pair/poll", json={"secret": p["secret"]})
    assert poll().json() == {"status": "wait"}
    assert client.post("/api/switch/pair/confirm", json={"code": p["code"]}).status_code == 401   # sin sesión
    assert login(client).status_code == 200
    assert client.post("/api/switch/pair/confirm", json={"code": "ZZZZZZ"}).status_code == 404
    assert client.post("/api/switch/pair/confirm", json={"code": p["code"].lower()}).status_code == 200
    r = poll().json()
    assert r["status"] == "ok"
    h = {"Authorization": f"Bearer {r['token']}"}
    c2 = TestClient(app)
    assert c2.get("/api/switch/songs", headers=h).status_code == 200            # el token vincula a la cuenta
    assert poll().status_code == 410                                            # un solo uso


def test_switch_session_never_expires_but_follows_account(client):
    from app import auth, storage
    assert login(client).status_code == 200
    p = client.post("/api/switch/pair").json()
    client.post("/api/switch/pair/confirm", json={"code": p["code"]})
    tok = client.post("/api/switch/pair/poll", json={"secret": p["secret"]}).json()["token"]
    row = storage._q("SELECT expires FROM sessions WHERE token_hash=?", (auth._th(tok),)).fetchone()
    assert row["expires"] > 4e10 - 1e10                       # sin vencimiento práctico
    web = storage._q("SELECT MAX(expires) m FROM sessions WHERE expires<?", (auth.NEVER,)).fetchone()["m"]
    assert web < 4e9                                           # la sesión web conserva su vencimiento
    h = {"Authorization": f"Bearer {tok}"}
    c2 = TestClient(app)
    assert c2.get("/api/switch/songs", headers=h).status_code == 200
    storage._q("UPDATE users SET active=0 WHERE email=?", ("admin@x.cl",))        # cuenta desactivada
    assert c2.get("/api/switch/songs", headers=h).status_code == 401
    storage._q("UPDATE users SET active=1 WHERE email=?", ("admin@x.cl",))


def test_youtube_cookies_admin(client, tmp_path, monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIES", str(tmp_path / "cookies.txt"))
    assert client.get("/api/admin/cookies").status_code == 401
    assert login(client).status_code == 200
    assert client.get("/api/admin/cookies").json()["present"] is False
    assert client.put("/api/admin/cookies", content=b"hola").status_code == 400
    ck = b"# Netscape HTTP Cookie File\r\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tabc\r\n"
    assert client.put("/api/admin/cookies", content=ck).json() == {"ok": True, "cookies": 1}
    assert client.get("/api/admin/cookies").json()["present"]
    assert (tmp_path / "cookies.txt").stat().st_mode & 0o777 == 0o600
    assert client.delete("/api/admin/cookies").status_code == 200
    assert client.get("/api/admin/cookies").json()["present"] is False


def test_youtube_bot_error_is_actionable(tmp_path, monkeypatch):
    import subprocess

    class R:
        returncode = 1
        stderr = "ERROR: [youtube] x: Sign in to confirm you're not a bot."
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(jobs.DownloadError) as e:
        jobs.download("https://youtu.be/x", tmp_path)
    assert "cookies" in str(e.value) and "audio" in str(e.value)


def test_index_is_versioned_and_retry(client):
    html = client.get("/").text
    assert "style.css?v=" in html and "app.js?v=" in html
    assert client.get("/").headers["cache-control"] == "no-cache"
    from app import storage
    assert login(client).status_code == 200
    jid = storage.create("url", "https://youtu.be/x", "", "", 1)
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 409            # no está en error
    storage.update(jid, status="error", error="YouTube bloqueó")
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 200
    j = storage.get(jid)
    assert j["status"] == "queued" and j["error"] == ""


def test_direct_url_download(tmp_path, monkeypatch):
    import http.server
    import subprocess
    import threading
    from app import jobs

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, ctype = (b"ID3" + b"\0" * 300, "audio/mpeg") if self.path.endswith(".mp3") else (b"<html>", "text/html")
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        log_message = lambda *a: None
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    class R:
        returncode = 1
        stderr = "ERROR: Unsupported URL"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(jobs, "_host_is_public", lambda h: True)
    meta = jobs.download(f"{base}/song.mp3", tmp_path / "a")
    assert meta["source"].endswith("input.mp3") and (tmp_path / "a" / "input.mp3").stat().st_size == 303
    with pytest.raises(jobs.DownloadError) as e:
        jobs.download(f"{base}/page", tmp_path / "b")
    assert "enlace directo" in str(e.value)
    assert jobs._normalize_url("https://www.dropbox.com/s/abc/x.mp3?dl=0") == "https://www.dropbox.com/s/abc/x.mp3?dl=1"
    assert jobs._normalize_url("https://drive.google.com/file/d/ID123/view?usp=sharing") == "https://drive.google.com/uc?export=download&id=ID123"
    srv.shutdown()


def test_suggest_and_cancel_resume(client, monkeypatch):
    from app import auth, jobs, storage, suggest
    assert login(client).status_code == 200
    uid = auth.user_by_email("admin@x.cl")["id"]
    monkeypatch.setattr(suggest, "_deezer", lambda *a: [])     # Deezer sin resultados: respaldo MusicBrainz
    monkeypatch.setattr(suggest, "_fetch", lambda url: {"artists": [{"name": "Radiohead"}, {"name": "Radiohead"}, {"name": "Radio Dept."}],
                                                       "recordings": [{"title": "Creep"}]})
    suggest._cache.clear()
    r = client.get("/api/suggest", params={"kind": "artist", "q": "radio"})
    assert r.json()["items"] == ["Radiohead", "Radio Dept."]
    assert client.get("/api/suggest", params={"kind": "title", "q": "cr", "artist": "Radiohead"}).json()["items"] == ["Creep"]
    assert client.get("/api/suggest", params={"kind": "artist", "q": "r"}).json()["items"] == []
    assert client.get("/api/suggest", params={"kind": "otro", "q": "radio"}).status_code == 400

    jid = storage.create("url", "https://example.com/x", "t", "a", user_id=uid)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
    j = storage.get(jid)
    assert (j["status"], j["error"]) == ("error", jobs.CANCELLED)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 409
    assert client.post(f"/api/jobs/{jid}/resume").status_code == 200
    assert storage.get(jid)["status"] == "queued"

    storage.update(jid, status="running", progress=20)
    jobs._flags.pop(jid, None)
    assert client.post(f"/api/jobs/{jid}/resume").status_code == 200
    assert jobs._flags[jid] == "resume"
    assert any(x["id"] == jid and "idle" in x for x in client.get("/api/jobs").json())
    jobs._flags.pop(jid, None)


def test_song_search(client, monkeypatch):
    import json as _json
    import subprocess
    from app import jobs
    assert login(client).status_code == 200
    entries = [{"id": "a1", "title": "ASIAN KUNG-FU GENERATION - Rewrite (Official Video)", "channel": "AKG Official YouTube Channel", "duration": 228},
               {"id": "b2", "title": "Re:Re:", "channel": "ASIAN KUNG-FU GENERATION - Topic", "duration": 229},
               {"id": "c3", "title": "Mix de 2 horas", "channel": "X", "duration": 7200},
               {"id": "d4", "title": "En vivo", "channel": "Y", "duration": None}]

    class R:
        returncode = 0
        stdout = _json.dumps({"entries": entries})
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    r = client.get("/api/search", params={"q": "asian kung"})
    items = r.json()["items"]
    assert [i["url"] for i in items] == ["https://www.youtube.com/watch?v=a1", "https://www.youtube.com/watch?v=b2"]
    assert (items[0]["artist"], items[0]["title"]) == ("ASIAN KUNG-FU GENERATION", "Rewrite")
    assert (items[1]["artist"], items[1]["title"]) == ("ASIAN KUNG-FU GENERATION", "Re:Re:")
    assert client.get("/api/search", params={"q": "Asian  Kung"}).status_code == 200 and len(calls) == 1   # caché
    assert client.get("/api/search", params={"q": "a"}).status_code == 400
    for i in range(12):
        last = client.get("/api/search", params={"q": f"tema {i}"})
    assert last.status_code == 429
    jobs_err = R(); jobs_err.returncode = 1
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: jobs_err)
    from app import main as m
    m._search_hits.clear()
    assert client.get("/api/search", params={"q": "otra cosa"}).status_code == 502


def test_song_suggestions(client, monkeypatch):
    from app import suggest
    assert login(client).status_code == 200
    suggest._cache.clear()
    monkeypatch.setattr(suggest, "_get", lambda url: {"data": [{"title_short": "Re:Re:", "duration": 229, "artist": {"name": "ASIAN KUNG-FU GENERATION"}}]})
    r = client.get("/api/suggest", params={"kind": "song", "q": "rere"})
    assert r.json() == {"items": ["ASIAN KUNG-FU GENERATION - Re:Re:"], "meta": ["3:49"]}
