"""Separación de stems con demucs (htdemucs_6s)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")
MODEL = "htdemucs_6s"


class SeparationError(Exception):
    pass


_PCT = re.compile(rb"(\d{1,3})%\|")
_active: subprocess.Popen | None = None


def kill_active() -> None:
    """Termina el demucs en curso (cancelar o continuar un trabajo detenido)."""
    p = _active
    if p is not None and p.poll() is None:
        p.kill()


def run(wav, out_dir, on_progress: Callable[[int], None] | None = None) -> dict:
    """Ejecuta demucs; `on_progress(0..100)` se llama al avanzar su barra de progreso (stderr)."""
    wav, out_dir = Path(wav), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    folder = out_dir / MODEL / wav.stem
    done = {s: folder / f"{s}.wav" for s in STEMS}
    if all(p.is_file() for p in done.values()):         # «continuar» tras un corte: no se separa de nuevo
        return done
    cmd = [sys.executable, "-m", "demucs", "-n", MODEL, "-j", "1", "-o", str(out_dir), str(wav)]
    global _active
    proc = _active = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)   # tqdm escribe en stdout o stderr
    try:
        tail, buf, last = b"", b"", -1
        stream = proc.stdout
        assert stream is not None
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            tail = (tail + chunk)[-600:]
            data = buf + chunk
            buf = data[-8:]       # solo para tokens partidos entre lecturas
            for raw in _PCT.findall(data):
                pct = max(0, min(100, int(raw)))
                if pct > last and on_progress:      # solo avanza; el buffer puede repetir lecturas viejas
                    last = pct
                    on_progress(pct)
    finally:
        if proc.poll() is None:
            proc.kill()      # p. ej. cancelación desde el callback de progreso
    if proc.wait() != 0:
        raise SeparationError(f"demucs falló: {tail.decode(errors='replace').strip()[-300:]}")
    stems = done
    missing = [s for s, p in stems.items() if not p.is_file()]
    if missing:
        raise SeparationError(f"demucs no generó los stems: {', '.join(missing)}")
    return stems
