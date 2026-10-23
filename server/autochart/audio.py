"""ffmpeg: preparación del audio, entrada de silencio (R6) y salida Vorbis."""
from __future__ import annotations

import subprocess
from pathlib import Path

MIN_SEC = 20.0
MAX_SEC = 15 * 60.0


class AudioError(Exception):
    pass


def _run(cmd: list, what: str) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise AudioError(f"{cmd[0]} no está instalado")
    except subprocess.TimeoutExpired:
        raise AudioError(f"{what}: tiempo agotado")
    if r.returncode != 0:
        raise AudioError(f"{what} falló: {r.stderr.strip()[-300:]}")
    return r.stdout


def duration(path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path)], "ffprobe")
    try:
        d = float(out.strip())
    except ValueError:
        raise AudioError("No se pudo leer la duración del audio")
    if d != d or d <= 0:
        raise AudioError("Audio vacío o inválido")
    return d


def prepare(src, out_wav) -> Path:
    """Convierte a WAV 44,1 kHz estéreo; valida duración 20 s – 15 min."""
    src = Path(src)
    if not src.is_file():
        raise AudioError("Archivo de audio inexistente")
    d = duration(src)
    if d < MIN_SEC:
        raise AudioError(f"El audio dura {d:.0f} s; el mínimo es {MIN_SEC:.0f} s")
    if d > MAX_SEC:
        raise AudioError(f"El audio dura {d / 60:.1f} min; el máximo es 15 min")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100",
          "-c:a", "pcm_s16le", str(out_wav)], "ffmpeg")
    return Path(out_wav)


def encode_ogg(wav, out_ogg, lead_in_sec: float = 0.0) -> Path:
    """Vorbis q5 44,1 kHz; antepone `lead_in_sec` de silencio si hace falta."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(wav)]
    if lead_in_sec > 0:
        ms = int(round(lead_in_sec * 1000))
        cmd += ["-af", f"adelay={ms}|{ms}"]
    cmd += ["-c:a", "libvorbis", "-q:a", "5", "-ar", "44100", str(out_ogg)]
    _run(cmd, "codificación Vorbis")
    return Path(out_ogg)


def is_silent(wav, dbfs: float = -40.0, fraction: float = 0.85) -> bool:
    """Silencioso si su RMS queda bajo `dbfs` durante más de `fraction` del tiempo."""
    import numpy as np
    import soundfile as sf

    y, sr = sf.read(str(wav), always_2d=True)
    if y.size == 0:
        return True
    mono = y.mean(axis=1)
    hop = int(sr * 0.1)
    n = len(mono) // hop
    if n == 0:
        return True
    frames = mono[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    return float((db < dbfs).mean()) > fraction


def rms_per_measure(wav, measure_secs: list) -> list:
    """RMS por compás (para R3). `measure_secs` = [(inicio, fin)]."""
    import numpy as np
    import soundfile as sf

    y, sr = sf.read(str(wav), always_2d=True)
    mono = y.mean(axis=1)
    out = []
    for a, b in measure_secs:
        seg = mono[max(0, int(a * sr)):max(0, int(b * sr))]
        out.append(float(np.sqrt((seg ** 2).mean())) if seg.size else 0.0)
    return out
