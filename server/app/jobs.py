"""Worker de un solo hilo: descarga (si hay URL), genera la carta y registra el resultado."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import auth, mail, storage

log = logging.getLogger("jobs")
_wake = threading.Event()
_started = False
MAX_BYTES = 100 * 1024 * 1024


class DownloadError(Exception):
    pass


def kick() -> None:
    _wake.set()


def download(url: str, dest: Path) -> dict:
    """yt-dlp -> dest/input.<ext>; devuelve {title, artist, thumb, source}."""
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-x", "--audio-format", "best", "--write-info-json", "--write-thumbnail",
           "--convert-thumbnails", "jpg", "--max-filesize", "100M", "--no-playlist",
           "-o", str(dest / "input.%(ext)s")]
    cookies = Path(os.environ.get("YTDLP_COOKIES", "/data/cookies.txt"))
    if cookies.is_file():
        cmd += ["--cookies", str(cookies)]
    r = subprocess.run(cmd + ["--", url], capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or ["error desconocido"])[-1]
        hint = " Suba el archivo de audio directamente." if "Sign in" in msg or "bot" in msg else ""
        raise DownloadError(f"Descarga fallida: {msg[:240]}.{hint}")
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
    storage.requeue_running()
    while True:
        job = storage.next_queued()
        if not job:
            _wake.wait(5)
            _wake.clear()
            continue
        try:
            process(job)
        except Exception as e:      # el worker nunca debe morir
            log.exception("job %s falló", job["id"])
            storage.update(job["id"], status="error", error=str(e)[:500])
            _notify(job, "No se pudo generar su canción", f"Error: {str(e)[:500]}\n")


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="worker", daemon=True).start()
