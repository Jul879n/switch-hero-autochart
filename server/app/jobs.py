"""Worker de un solo hilo: descarga (si hay URL), genera la carta y registra el resultado."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import threading
from pathlib import Path

from . import auth, mail, storage

log = logging.getLogger("jobs")
_wake = threading.Event()
_started = False
MAX_BYTES = 100 * 1024 * 1024


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


CANCELLED = "Cancelado por el usuario"
_flags: dict[str, str] = {}      # id -> "cancel" | "resume", pedido sobre un trabajo en curso


def request(jid: str, action: str) -> None:
    """Pide cancelar o continuar (reiniciar el paso) un trabajo en curso; mata el demucs si está corriendo."""
    from autochart import separate
    _flags[jid] = action
    separate.kill_active()


def kick() -> None:
    _wake.set()


MAX_BYTES = 100 * 1024 * 1024
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".aiff", ".mp4", ".mkv", ".webm", ".mov")


def _host_is_public(host: str) -> bool:
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def _normalize_url(url: str) -> str:
    """Enlaces «para ver» de Dropbox y Google Drive -> enlace de descarga directa."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host.endswith("dropbox.com"):
        q = dict(parse_qsl(u.query))
        q["dl"] = "1"
        return urlunparse(u._replace(query=urlencode(q)))
    m = re.match(r"/file/d/([^/]+)", u.path) if host == "drive.google.com" else None
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def direct_download(url: str, dest: Path) -> dict | None:
    """Enlace directo a un archivo de audio/video: descarga por HTTP. None si la URL no es un archivo."""
    import mimetypes
    import urllib.request
    req = urllib.request.Request(_normalize_url(url), headers={"User-Agent": "Mozilla/5.0 (Switch Hero Autochart)"})
    size = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            final = r.geturl()
            if not _host_is_public(urlparse(final).hostname or ""):
                return None
            ctype = (r.headers.get_content_type() or "").lower()
            ext = Path(urlparse(final).path).suffix.lower()
            if not (ctype.startswith(("audio/", "video/")) or ctype == "application/octet-stream" or ext in AUDIO_EXTS):
                return None
            if ext not in AUDIO_EXTS:
                ext = mimetypes.guess_extension(ctype) or ".bin"
            out = dest / f"input{ext}"
            with open(out, "wb") as fh:
                while chunk := r.read(1 << 16):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        out.unlink(missing_ok=True)
                        raise DownloadError("El archivo supera 100 MB")
                    fh.write(chunk)
    except DownloadError:
        raise
    except Exception:
        return None
    if size == 0:
        return None
    meta = {"source": str(out)}
    meta.update({k: v for k, v in tags(out).items() if v})
    return meta


def cookies_path() -> Path:
    return Path(os.environ.get("YTDLP_COOKIES", "/data/cookies.txt"))


def download(url: str, dest: Path) -> dict:
    """yt-dlp -> dest/input.<ext>; devuelve {title, artist, thumb, source}."""
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-x", "--audio-format", "best", "--write-info-json", "--write-thumbnail",
           "--convert-thumbnails", "jpg", "--max-filesize", "100M", "--no-playlist",
           "-o", str(dest / "input.%(ext)s")]
    cookies = cookies_path()
    if cookies.is_file():
        cmd += ["--cookies", str(cookies)]
    r = subprocess.run(cmd + ["--", url], capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or ["error desconocido"])[-1]
        if "Sign in" in msg or "bot" in msg:
            raise DownloadError("YouTube bloqueó la descarga desde el servidor (exige iniciar sesión). "
                                "Suba el archivo de audio directamente, o pida al administrador que cargue las "
                                "cookies de YouTube en Administración.")
        direct = direct_download(url, dest)        # cualquier enlace directo a un archivo (o Dropbox/Drive)
        if direct:
            return direct
        raise DownloadError(f"Descarga fallida: {msg[:240]}. Pegue un enlace de YouTube, SoundCloud, Bandcamp, "
                            "Drive, Dropbox u otro sitio compatible, o un enlace directo a un archivo de audio.")
    audio = [p for p in dest.glob("input.*") if p.suffix not in (".json", ".jpg", ".png", ".webp")]
    if not audio:
        raise DownloadError("La descarga no produjo un archivo de audio")
    meta = {"source": str(audio[0])}
    info = dest / "input.info.json"
    if info.is_file():
        try:
            j = json.loads(info.read_text(encoding="utf-8"))
            meta["title"] = j.get("track") or j.get("title") or ""
            meta["artist"] = j.get("artist") or j.get("uploader") or ""
            meta["album"] = j.get("album") or ""
            meta["genre"] = (j.get("genre") or "").split(",")[0].strip()
            meta["year"] = str(j.get("release_year") or (j.get("release_date") or j.get("upload_date") or "")[:4] or "")
        except (OSError, ValueError):
            pass
    thumb = dest / "input.jpg"
    if thumb.is_file():
        meta["thumb"] = str(thumb)
    return meta


def tags(path: Path) -> dict:
    try:
        import mutagen
        f = mutagen.File(path, easy=True)
        if f and f.tags:
            return {"title": (f.tags.get("title") or [""])[0], "artist": (f.tags.get("artist") or [""])[0],
                    "album": (f.tags.get("album") or [""])[0], "genre": (f.tags.get("genre") or [""])[0],
                    "year": (f.tags.get("date") or [""])[0][:4]}
    except Exception:
        pass
    return {}


def cover(path: Path, dest: Path) -> str:
    """Carátula incrustada (ID3/MP4/FLAC/Ogg) -> dest en JPEG; '' si no hay."""
    try:
        import base64
        import mutagen
        from mutagen.flac import Picture
        f = mutagen.File(path)
        raw = b""
        if f is None:
            return ""
        if getattr(f, "pictures", None):
            raw = f.pictures[0].data
        elif f.tags is not None and hasattr(f.tags, "getall") and f.tags.getall("APIC"):
            raw = f.tags.getall("APIC")[0].data
        elif f.tags is not None and f.tags.get("covr"):
            raw = bytes(f.tags["covr"][0])
        elif f.tags is not None and f.tags.get("metadata_block_picture"):
            raw = Picture(base64.b64decode(f.tags["metadata_block_picture"][0])).data
        if not raw:
            return ""
        tmp = dest.with_suffix(".src")
        tmp.write_bytes(raw)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-frames:v", "1", str(dest)],
                           capture_output=True, timeout=60)
        tmp.unlink(missing_ok=True)
        return str(dest) if r.returncode == 0 and dest.is_file() else ""
    except Exception:
        return ""


def _notify(job: dict, subject: str, body: str) -> None:
    u = auth.user_by_id(job.get("user_id") or 0)
    if u:
        mail.send(u["email"], subject, body)


def process(job: dict) -> None:
    from autochart import pipeline

    jid = job["id"]
    jdir = storage.job_dir(jid)
    jdir.mkdir(parents=True, exist_ok=True)

    def progress(stage: str, pct: int) -> None:
        if jid in _flags:
            raise Cancelled()
        storage.update(jid, stage=stage, progress=int(pct))

    storage.update(jid, status="running", stage="descarga", progress=1, error="")
    meta: dict = {}
    if job["src_kind"] == "url":
        meta.update(download(job["src"], jdir / "dl"))
    else:
        meta["source"] = job["src"]
        meta.update({k: v for k, v in tags(Path(job["src"])).items() if v})
        thumb = cover(Path(job["src"]), jdir / "cover.jpg")
        if thumb:
            meta["thumb"] = thumb
    meta["title"] = job["title"] or meta.get("title") or Path(meta["source"]).stem
    meta["artist"] = job["artist"] or meta.get("artist") or "Desconocido"
    res = pipeline.run(jdir, meta, progress)
    storage.update(jid, status="done", stage="listo", progress=100, folder=res.folder.name,
                   title=meta["title"], artist=meta["artist"])
    _notify(job, f"Su canción «{meta['title']}» está lista",
            f"La carta de «{meta['artist']} - {meta['title']}» ya se generó. Descárguela desde la web.\n")
    shutil.rmtree(jdir / "dl", ignore_errors=True)
    src = Path(meta["source"])
    if src.parent == jdir / "upload" or src.parent == jdir:
        shutil.rmtree(jdir / "upload", ignore_errors=True)


def _loop() -> None:
    try:
        os.nice(int(os.environ.get("WORKER_NICE", "10")))     # el trabajo pesado cede CPU a la web y a otras apps
    except (OSError, ValueError):
        pass
    storage.requeue_running()
    while True:
        job = storage.next_queued()
        if not job:
            _wake.wait(5)
            _wake.clear()
            continue
        _flags.pop(job["id"], None)
        try:
            process(job)
            _flags.pop(job["id"], None)
        except Exception as e:      # el worker nunca debe morir
            act = _flags.pop(job["id"], None)
            if act == "resume":
                storage.update(job["id"], status="queued", stage="", progress=0, error="")
                continue
            if act == "cancel":
                storage.update(job["id"], status="error", stage="", progress=0, error=CANCELLED)
                continue
            log.exception("job %s falló", job["id"])
            storage.update(job["id"], status="error", error=str(e)[:500])
            _notify(job, "No se pudo generar su canción", f"Error: {str(e)[:500]}\n")


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="worker", daemon=True).start()
