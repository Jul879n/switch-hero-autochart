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


def _mail_ok(monkeypatch, ok=True):
    from app import mail
    sent = {}
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send", lambda to, subj, body: sent.update(to=to, body=body) or ok)
    return sent


def test_only_admin_manages_users(client, monkeypatch):
    sent = _mail_ok(monkeypatch)
    login(client)
    r = client.post("/api/admin/users", json={"email": "u@x.cl", "password": "una-clave-larga"})
    assert r.status_code == 201 and "password" not in r.text and "pw_hash" not in r.text
    temp = sent["body"].split("\n\n")[1]
    assert login(client, "iso@x.cl", "una-clave-larga").status_code == 401   # el campo password se ignora
    client.post("/api/auth/logout")
    r = login(client, "u@x.cl", temp)
    assert r.status_code == 200 and r.json()["must_change"] is True
    assert client.post("/api/auth/password", json={"current": temp, "new": "clave-definitiva-1"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/admin/users", json={"email": "z@x.cl"}).status_code == 401
    login(client, "u@x.cl", "clave-definitiva-1")
    assert client.post("/api/admin/users", json={"email": "z@x.cl"}).status_code == 403
    assert client.post("/api/admin/users/1/reset").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_create_user_mail_failures(client, monkeypatch):
    from app import auth, mail
    login(client)
    monkeypatch.setattr(mail, "configured", lambda: False)
    assert client.post("/api/admin/users", json={"email": "f@x.cl"}).status_code == 503
    _mail_ok(monkeypatch, ok=False)
    assert client.post("/api/admin/users", json={"email": "f@x.cl"}).status_code == 502
    assert auth.user_by_email("f@x.cl") is None


def test_admin_reset_user(client, monkeypatch):
    from app import auth
    sent = _mail_ok(monkeypatch)
    login(client)
    uid = client.post("/api/admin/users", json={"email": "q@x.cl"}).json()["id"]
    old = auth.user_by_id(uid)["pw_hash"]
    _mail_ok(monkeypatch, ok=False)
    assert client.post(f"/api/admin/users/{uid}/reset").status_code == 502
    assert auth.user_by_id(uid)["pw_hash"] == old
    sent = _mail_ok(monkeypatch)
    assert client.post(f"/api/admin/users/{uid}/reset").status_code == 200
    assert auth.user_by_id(uid)["pw_hash"] != old and sent["to"] == "q@x.cl"
    assert client.post("/api/admin/users/99999/reset").status_code == 404


def test_create_list_delete_and_isolation(client):
    from app import auth
    auth.create_user("iso@x.cl", "", "una-clave-larga")
    login(client)
    r = client.post("/api/jobs", files={"file": ("a.mp3", b"abc")}, data={"title": "T"})
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    assert client.get("/api/switch/songs").json() == []
    assert client.get(f"/api/switch/songs/{jid}/..%2Fx").status_code == 404
    client.post("/api/auth/logout")
    login(client, "iso@x.cl", "una-clave-larga")
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
    auth.create_user("r@x.cl", "", "clave-original-1")
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
    from app import auth, mail
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "send", lambda *a: False)
    login(client)
    auth.create_user("s@x.cl", "", "clave-original-1")
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
    from app import auth, mail
    monkeypatch.setattr(mail, "configured", lambda: True)
    sent = {}
    monkeypatch.setattr(mail, "send", lambda to, s, b: sent.update(b=b) or True)
    login(client)
    auth.create_user("m@x.cl", "", "clave-original-1")
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
    r = client.get("/api/search", params={"q": "asian kung", "source": "youtube"})
    items = r.json()["items"]
    assert calls[-1][-1].startswith("ytsearch")
    assert [i["url"] for i in items] == ["https://www.youtube.com/watch?v=a1", "https://www.youtube.com/watch?v=b2"]
    assert (items[0]["artist"], items[0]["title"]) == ("ASIAN KUNG-FU GENERATION", "Rewrite")
    assert (items[1]["artist"], items[1]["title"]) == ("ASIAN KUNG-FU GENERATION", "Re:Re:")
    assert client.get("/api/search", params={"q": "Asian  Kung", "source": "youtube"}).status_code == 200 and len(calls) == 1   # caché
    assert client.get("/api/search", params={"q": "a"}).status_code == 400
    assert client.get("/api/search", params={"q": "asian", "source": "spotify"}).status_code == 400
    # SoundCloud (por defecto): forma real de scsearch (webpage_url, uploader, miniaturas); omite la vista previa de 30 s
    sc = [{"id": "254112221", "title": "Around the World", "uploader": "Daft Punk", "duration": 30.0,
           "url": "https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A254112221",
           "webpage_url": "https://soundcloud.com/daftpunkofficialmusic/around-the-world", "thumbnails": [{"url": "https://i1.sndcdn.com/a.jpg"}]},
          {"id": "171951429", "title": "Daft Punk - Around The World", "uploader": "G,R,F", "duration": 238.1,
           "url": "https://api.soundcloud.com/tracks/x", "webpage_url": "https://soundcloud.com/g-r-f/daft-punk-around-the-world",
           "thumbnails": [{"url": "https://i1.sndcdn.com/s.jpg"}, {"url": "https://i1.sndcdn.com/b.jpg"}]}]
    Rs = R(); Rs.stdout = _json.dumps({"entries": sc})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or Rs)
    items = client.get("/api/search", params={"q": "daft punk"}).json()["items"]
    assert calls[-1][-1].startswith("scsearch") and "--cookies" not in calls[-1]
    assert [i["url"] for i in items] == ["https://soundcloud.com/g-r-f/daft-punk-around-the-world"]
    assert (items[0]["artist"], items[0]["title"], items[0]["thumb"], items[0]["source"]) == ("Daft Punk", "Around The World", "https://i1.sndcdn.com/b.jpg", "soundcloud")
    from app import main as m
    for i in range(m.SEARCH_PER_MIN + 2):
        last = client.get("/api/search", params={"q": f"tema {i}"})
    assert last.status_code == 429
    mp3 = lambda: client.get("/api/search", params={"q": "tema libre", "source": "mp3pm"})     # MP3.pm no cuenta para el límite
    monkeypatch.setattr(jobs, "_search_mp3pm", lambda q, limit: [])
    assert mp3().status_code == 200
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


def test_share_between_users(client):
    from app import auth, storage
    a = auth.create_user("a-share@x.cl", "", "clave-larga-aaa")
    b = auth.create_user("b-share@x.cl", "", "clave-larga-bbb")
    jid = storage.create("file", "", "T", "Art", a)
    d = storage.job_dir(jid) / "Art - T"
    d.mkdir(parents=True)
    (d / "song.ini").write_text("[song]\nname=T\n")
    storage.update(jid, status="done", stage="listo", progress=100, folder="Art - T")
    def shared():      # solo lo de estos dos usuarios: otros tests dejan canciones de otros dueños
        return [x["id"] for x in client.get("/api/shared").json() if x["owner"] in ("a-share@x.cl", "b-share@x.cl")]
    busy = storage.create("file", "", "En curso", "Art", a)          # sin terminar: nunca se comparte
    login(client, "b-share@x.cl", "clave-larga-bbb")
    assert shared() == [jid]      # compartir es lo predeterminado
    assert client.get(f"/api/jobs/{jid}").status_code == 404                 # ver la lista no da acceso al detalle
    assert client.post(f"/api/jobs/{busy}/clone").status_code == 404
    r = client.post(f"/api/jobs/{jid}/clone")
    assert r.status_code == 201 and r.json()["status"] == "done"
    new = r.json()["id"]
    assert (storage.job_dir(new) / "Art - T" / "song.ini").exists()
    assert client.get(f"/api/jobs/{new}").status_code == 200                 # la copia es suya
    assert jid in shared() and new not in shared()   # lo propio no aparece
    client.post("/api/auth/logout")
    login(client, "a-share@x.cl", "clave-larga-aaa")
    assert shared() == [new]      # B comparte, así que A ve su copia
    client.post("/api/auth/logout")
    login(client, "b-share@x.cl", "clave-larga-bbb")
    assert client.patch("/api/auth/share", json={"share": False}).json()["share"] is False
    client.post("/api/auth/logout")
    login(client, "a-share@x.cl", "clave-larga-aaa")
    assert shared() == []                            # B dejó de compartir
    assert client.patch("/api/auth/share", json={"share": False}).status_code == 200
    client.post("/api/auth/logout")
    login(client, "b-share@x.cl", "clave-larga-bbb")
    assert shared() == []         # A dejó de compartir
    assert client.post(f"/api/jobs/{jid}/clone").status_code == 404
    client.post("/api/auth/logout")
    login(client)
    assert any(x["id"] == jid for x in client.get("/api/jobs").json())       # el admin ve todo


def test_search_mp3pm(client, monkeypatch):
    import io
    import urllib.request
    page = ('<ul><li class="cplayer-sound-item" data-sound-id="1" data-sound-url="https://cs1.mp3.pm/listen/1/a.mp3" '
            'data-download-url="https://cs1.mp3.pm/download/1/a.mp3?x=1&amp;y=2" data-share-url="x"><h4><a><i class="cplayer-data-sound-author">Queen &amp; Co</i></a>'
            '<a><b class="cplayer-data-sound-title">Tema</b></a></h4><em class="cplayer-data-sound-time">04:11</em></li>'
            '<li class="cplayer-sound-item" data-download-url="https://evil.example/x.mp3"><i class="cplayer-data-sound-author">A</i>'
            '<b class="cplayer-data-sound-title">B</b><em class="cplayer-data-sound-time">03:00</em></li>'
            '<li class="cplayer-sound-item" data-download-url="https://cs1.mp3.pm/download/2/b.mp3"><i class="cplayer-data-sound-author">A</i>'
            '<b class="cplayer-data-sound-title">Largo</b><em class="cplayer-data-sound-time">45:00</em></li></ul>').encode()
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: seen.append(req.full_url) or io.BytesIO(page))
    login(client)
    items = client.get("/api/search", params={"q": "queen mp3pm", "source": "mp3pm"}).json()["items"]
    assert seen[0].startswith("https://mp3.pm/search?q=")
    assert items == [{"url": "https://cs1.mp3.pm/download/1/a.mp3?x=1&y=2", "name": "Queen & Co - Tema", "channel": "MP3.pm",
                      "duration": 251, "thumb": "", "artist": "Queen & Co", "title": "Tema", "source": "mp3pm"}]   # descarta otro dominio y >15 min


def _spotify_page(tracks):
    import json
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Mi lista", "trackList": tracks}}}}}}
    return ('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>").encode()


def test_spotify_playlist(client, monkeypatch):
    import io
    import urllib.request
    page = _spotify_page([{"title": "Tema Uno", "subtitle": "Banda\xa0Uno", "duration": 200500}, {"title": "", "subtitle": "x"}])
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: seen.append(req.full_url) or io.BytesIO(page))
    assert client.post("/api/playlist", json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}).status_code == 401
    login(client)
    ok = "https://open.spotify.com/intl-es/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"
    r = client.post("/api/playlist", json={"url": ok})
    assert r.status_code == 200 and r.json()["name"] == "Mi lista" and r.json()["id"] >= 1
    assert r.json()["tracks"] == [{"artist": "Banda Uno", "title": "Tema Uno", "duration": 200, "sel": True, "state": "pending", "match": None, "msg": "", "job": ""}]
    assert seen == ["https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M"]
    for bad in ("https://evil.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "http://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
                "https://open.spotify.com.evil.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/playlist/corto", ""):
        assert client.post("/api/playlist", json={"url": bad}).status_code == 400
    assert len(seen) == 1                                                   # ningún rechazo llegó a la red
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(b"<html>sin datos</html>"))
    assert client.post("/api/playlist", json={"url": ok}).status_code == 400


def test_best_match():
    from app import jobs
    items = [{"name": "Banda Uno - Tema Uno (Slowed Down)", "channel": "x", "duration": 205},
             {"name": "Otra - Tema Uno", "channel": "y", "duration": 200},
             {"name": "Banda Uno - Tema Uno (Remix)", "channel": "z", "duration": 201},
             {"name": "BANDA UNO - Tema Uno", "channel": "w", "duration": 203},
             {"name": "Banda Uno - Tema Uno", "channel": "w", "duration": 260}]
    m = jobs.best_match(items, "Banda Uno, Invitada", "Tema Uno (feat. Invitada)", 200)
    assert m["duration"] == 203                                              # descarta slowed, remix, otro artista y duración lejana
    assert jobs.best_match(items, "Banda Uno", "Tema Uno", 400) is None
    assert jobs.best_match(items, "Banda Uno", "Otro Título", 200) is None


def test_match_endpoint(client, monkeypatch):
    from app import jobs
    login(client)
    calls = []
    monkeypatch.setattr(jobs, "search", lambda q, source="soundcloud", limit=12: calls.append((q, source)) or
                        [{"url": "https://x.y/a", "name": "Banda Uno - Tema Uno", "channel": "c", "duration": 201}])
    r = client.post("/api/match", json={"artist": "Banda Uno", "title": "Tema Uno", "duration": 200, "source": "soundcloud"})
    assert r.status_code == 200 and r.json()["match"]["url"] == "https://x.y/a" and calls == [("Banda Uno Tema Uno", "soundcloud")]
    assert client.post("/api/match", json={"artist": "Banda Uno", "title": "Tema Uno", "duration": 90, "source": "soundcloud"}).json() == {"match": None}
    assert client.post("/api/match", json={"artist": "Banda Uno", "title": "Tema Uno", "duration": 207, "source": "soundcloud"}).json() == {"match": None}   # fuera de ±5 s
    assert client.post("/api/match", json={"artist": "Banda Uno", "title": "Tema Uno", "duration": 0, "source": "soundcloud"}).json() == {"match": None}
    assert client.post("/api/match", json={"artist": "a", "title": "", "source": "soundcloud"}).status_code == 400
    assert client.post("/api/match", json={"artist": "a", "title": "t", "source": "spotify"}).status_code == 400
    client.post("/api/auth/logout")
    assert client.post("/api/match", json={"artist": "a", "title": "t"}).status_code == 401


def test_saved_playlists(client, monkeypatch):
    import io
    import urllib.request
    from app import auth
    page = _spotify_page([{"title": "Uno", "subtitle": "A", "duration": 100000}, {"title": "Dos", "subtitle": "B", "duration": 200000}])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(page))
    url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    assert client.get("/api/playlists").status_code == 401
    login(client)
    pid = client.post("/api/playlist", json={"url": url}).json()["id"]
    tracks = client.get(f"/api/playlists/{pid}").json()["tracks"]
    tracks[0].update(state="found", match={"url": "https://x.y/a", "name": "A - Uno", "duration": 101})
    tracks[1].update(state="found", sel=False)                              # «encontrada» sin coincidencia no es válida
    assert client.put(f"/api/playlists/{pid}", json={"tracks": tracks + [{"title": ""}, "basura"]}).status_code == 200
    saved = client.get(f"/api/playlists/{pid}").json()["tracks"]
    tracks[0].update(state="queued", job="0123456789ab"); tracks[1].update(state="queued", job="no-es-un-id")
    client.put(f"/api/playlists/{pid}", json={"tracks": tracks})
    q = client.get(f"/api/playlists/{pid}").json()["tracks"]
    assert (q[0]["state"], q[0]["job"], q[1]["job"]) == ("queued", "0123456789ab", "")   # el id del trabajo se valida
    tracks[0].update(state="found", job=""); tracks[1].update(state="found", job="")
    client.put(f"/api/playlists/{pid}", json={"tracks": tracks})
    saved = client.get(f"/api/playlists/{pid}").json()["tracks"]
    assert [t["state"] for t in saved] == ["found", "pending"] and saved[0]["match"]["url"] == "https://x.y/a" and saved[1]["sel"] is False
    again = client.post("/api/playlist", json={"url": url}).json()           # volver a pegarla conserva el avance
    assert again["id"] == pid and again["tracks"][0]["state"] == "found"
    summary = [p for p in client.get("/api/playlists").json() if p["id"] == pid][0]
    assert (summary["total"], summary["found"], summary["none"], summary["queued"]) == (2, 1, 0, 0)
    auth.create_user("pl-otro@x.cl", "", "clave-larga-otro")
    client.post("/api/auth/logout")
    login(client, "pl-otro@x.cl", "clave-larga-otro")
    assert client.get(f"/api/playlists/{pid}").status_code == 404           # privadas de cada usuario
    assert client.put(f"/api/playlists/{pid}", json={"tracks": []}).status_code == 404
    assert client.delete(f"/api/playlists/{pid}").status_code == 404
    assert pid not in [p["id"] for p in client.get("/api/playlists").json()]
    client.post("/api/auth/logout")
    login(client)
    assert client.delete(f"/api/playlists/{pid}").status_code == 200
    assert client.get(f"/api/playlists/{pid}").status_code == 404


def _fake_spotify(monkeypatch, routes):
    """Reemplaza el único punto de red de spotify.py; `routes` asigna prefijo de URL -> respuesta (o excepción)."""
    from app import spotify
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    calls = []

    def fake(url, data=None, headers=None):
        calls.append((url, data))
        for prefix, resp in routes.items():
            if url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp(url, data) if callable(resp) else resp
        raise AssertionError(f"URL inesperada: {url}")
    monkeypatch.setattr(spotify, "_http", fake)
    return calls


def test_spotify_oauth(client, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    assert client.get("/api/spotify/status").status_code == 401
    login(client)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert client.get("/api/spotify/status").json() == {"configured": False, "connected": False}
    assert client.get("/api/spotify/login", follow_redirects=False).status_code == 503
    calls = _fake_spotify(monkeypatch, {"https://accounts.spotify.com/api/token":
                                        {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}})
    assert client.get("/api/spotify/status").json() == {"configured": True, "connected": False}
    r = client.get("/api/spotify/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://accounts.spotify.com/authorize?")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert client.get("/api/spotify/callback?code=abc&state=nope", follow_redirects=False).headers["location"] == "/?spotify=error"
    assert client.get(f"/api/spotify/callback?error=access_denied&state={state}", follow_redirects=False).headers["location"] == "/?spotify=error"
    r = client.get("/api/spotify/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert client.get(f"/api/spotify/callback?code=abc&state={state}", follow_redirects=False).headers["location"] == "/?spotify=ok"
    assert calls[-1][1]["grant_type"] == "authorization_code" and calls[-1][1]["code"] == "abc"
    assert client.get("/api/spotify/status").json()["connected"] is True
    assert client.get(f"/api/spotify/callback?code=abc&state={state}", follow_redirects=False).headers["location"] == "/?spotify=error"
    assert client.delete("/api/spotify").status_code == 200
    assert client.get("/api/spotify/status").json()["connected"] is False


def test_spotify_api_playlists(client, monkeypatch):
    import io
    import time
    import urllib.request
    from app import spotify, storage
    login(client)
    uid = client.get("/api/auth/me").json()["id"]

    def put_token(expires):
        storage._q("INSERT OR REPLACE INTO spotify_tokens(user_id,access,refresh,expires) VALUES(?,?,?,?)", (uid, "AT", "RT", expires))

    own = {"name": "Mía", "items": {"items": [
        {"item": {"type": "track", "name": "Tema", "artists": [{"name": "A"}, {"name": "B"}], "duration_ms": 200400}},
        {"item": {"type": "episode", "name": "Podcast"}}, {"item": None}], "next": None}}
    api = "https://api.spotify.com/v1"
    calls = _fake_spotify(monkeypatch, {
        f"{api}/me/playlists?limit=50": {"items": [{"id": "p1", "name": "Uno", "items": {"total": 3}}, None],
                                         "next": f"{api}/me/playlists?offset=50&limit=50"},
        f"{api}/me/playlists?offset": {"items": [{"id": "p2", "name": "Dos"}], "next": None},
        f"{api}/playlists/37i9dQZF1DXcBWIGoYBM5M": own,
        "https://accounts.spotify.com/api/token": {"access_token": "AT2", "expires_in": 3600}})
    urlopen_calls = []
    page = _spotify_page([{"title": "Embed", "subtitle": "Z", "duration": 100000}])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: urlopen_calls.append(req.full_url) or io.BytesIO(page))
    assert client.get("/api/spotify/playlists").status_code == 409
    put_token(time.time() + 3600)
    assert client.get("/api/spotify/playlists").json() == [{"id": "p1", "name": "Uno", "total": 3}, {"id": "p2", "name": "Dos", "total": 0}]
    url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    r = client.post("/api/playlist", json={"url": url})
    assert r.status_code == 200 and r.json()["tracks"][0]["artist"] == "A, B" and r.json()["tracks"][0]["duration"] == 200
    assert len(r.json()["tracks"]) == 1 and urlopen_calls == []
    own.pop("items")                                                        # playlist ajena: sin pistas en la API
    r = client.post("/api/playlist", json={"url": url})
    assert r.json()["tracks"][0]["title"] == "Embed" and len(urlopen_calls) == 1
    own["items"] = {"items": [], "next": None}                              # propia pero vacía: no cae al embed
    assert client.post("/api/playlist", json={"url": url}).status_code == 400 and len(urlopen_calls) == 1
    put_token(time.time() - 10)                                             # vencido: refresca y conserva el refresh anterior
    client.get("/api/spotify/playlists")
    assert [c for c in calls if c[1]][-1][1] == {"grant_type": "refresh_token", "refresh_token": "RT"}
    row = storage._q("SELECT access, refresh FROM spotify_tokens WHERE user_id=?", (uid,)).fetchone()
    assert (row["access"], row["refresh"]) == ("AT2", "RT")
    put_token(time.time() - 10)                                             # refresh revocado: se desconecta
    _fake_spotify(monkeypatch, {"https://accounts.spotify.com/api/token": spotify.SpotifyError("x", 400)})
    assert client.get("/api/spotify/playlists").status_code == 409
    assert not spotify.connected(uid)


def test_spotify_track(client, monkeypatch):
    import io
    import json
    import urllib.request
    from app import main
    data = {"props": {"pageProps": {"state": {"data": {"entity": {
        "type": "track", "name": "Tema Uno", "artists": [{"name": "Banda Uno"}], "duration": 200500}}}}}}
    page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>").encode()
    net = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: net.append(req.full_url) or io.BytesIO(page))
    monkeypatch.setattr(main, "_public_url_ok", lambda u: True)
    main._search_cache.clear()
    sources, hits = [], {}

    def fake_search(q, source="soundcloud", limit=12):
        sources.append(source)
        return hits.get(source, [])
    monkeypatch.setattr(jobs, "search", fake_search)
    track = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
    assert client.post("/api/spotify/track", json={"url": track}).status_code == 401
    login(client)
    before = len(client.get("/api/jobs").json())
    assert client.post("/api/spotify/track", json={"url": track}).status_code == 404       # nada exacto
    assert sources == ["mp3pm", "soundcloud", "youtube"] and len(client.get("/api/jobs").json()) == before
    hits["soundcloud"] = [{"url": "https://sc.x/a", "name": "Banda Uno - Tema Uno", "channel": "c", "duration": 202, "source": "soundcloud"}]
    main._search_cache.clear(); sources.clear()
    r = client.post("/api/spotify/track", json={"url": track})
    assert r.status_code == 201 and sources == ["mp3pm", "soundcloud"]
    assert (r.json()["job"]["title"], r.json()["job"]["artist"]) == ("Tema Uno", "Banda Uno")
    hits["mp3pm"] = [{"url": "https://mp3.x/a", "name": "Banda Uno - Tema Uno", "channel": "MP3.pm", "duration": 200, "source": "mp3pm"}]
    main._search_cache.clear(); sources.clear()
    assert client.post("/api/spotify/track", json={"url": track}).status_code == 201 and sources == ["mp3pm"]
    n = len(net)
    for bad in ("https://evil.com/track/4uLU6hMCjMI75M1A2tKUQC", "http://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
                "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"):
        assert client.post("/api/spotify/track", json={"url": bad}).status_code == 400
    assert len(net) == n


def test_best_match_exact():
    from app import jobs
    def m(name, dur, title="Tema Uno", want=200):
        return jobs.best_match([{"name": name, "channel": "", "duration": dur}], "Banda Uno", title, want, exact=True)
    assert m("Banda Uno - Tema Uno", 203)
    assert m("Banda Uno - Tema", 200, title="Tema Largo") is None          # falta un token del título
    assert m("Banda Uno - Tema Uno", 207) is None                          # fuera de ±5 s
    assert m("Banda Uno - Tema Uno", 200, want=0) is None                  # duración desconocida
    assert m("Banda Uno - Tema Uno (Remix)", 200) is None
    assert jobs.best_match([{"name": "Banda Uno - Tema Uno", "channel": "", "duration": 208}], "Banda Uno", "Tema Uno", 200)


def test_best_match_exact_unicode():
    items = [{"name": "Tatsuya Kitani - スカー", "channel": "", "duration": 259}]
    assert jobs.best_match(items, "Tatsuya Kitani", "スカー", 259, exact=True)
    assert jobs.best_match(items, "Tatsuya Kitani", "タナトフォビア", 259, exact=True) is None
    assert jobs.best_match([{"name": "Beyoncé - Halo", "channel": "", "duration": 261}], "Beyonce", "Halo", 261, exact=True)


def test_best_match_exact_romaji():
    def m(name, title, want=212, artist="Banda Uno", dur=212):
        return jobs.best_match([{"name": name, "channel": "", "duration": dur}], artist, title, want, exact=True)
    assert m("Creepy Nuts - Yofukashino Uta (ED Theme)", "よふかしのうた - Yofukashino Uta", 240, "Creepy Nuts", 240)     # romaji de «título - romaji»
    assert m("Banda Uno - Pastel Rain", "パステルレイン")                                                              # título japonés, resultado en romaji
    assert m("Banda Uno - Pastel Rain", "パステルレイン", dur=214) is None                                            # duración no casi idéntica
    assert m("Otro Artista - Pastel Rain", "パステルレイン") is None                                                    # otro artista
    assert m("Banda Uno - ファタール", "パステルレイン") is None                                                          # otra canción en japonés
    assert m("Banda Uno - Pastel Rain", "Fire Rain") is None                                                       # el modo romaji no aplica a títulos latinos
    assert m("Banda Uno - Rapport", "Rapport - Scar version.") is None                                               # «A - versión»: ambas latinas, no se parte
