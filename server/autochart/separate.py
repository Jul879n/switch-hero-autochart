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


def run(wav, out_dir, on_progress: Callable[[int], None] | None = None) -> dict:
    """Ejecuta demucs; `on_progress(0..100)` se llama al avanzar su barra de progreso (stderr)."""
    wav, out_dir = Path(wav), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "-n", MODEL, "-j", "1", "-o", str(out_dir), str(wav)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    tail, buf, last = b"", b"", -1
    assert proc.stderr is not None
    while True:
        chunk = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
        if not chunk:
            break
        tail = (tail + chunk)[-600:]
        buf = (buf + chunk)[-64:]
        m = _PCT.findall(buf)
        if m and on_progress:
            pct = max(0, min(100, int(m[-1])))
            if pct != last:
                last = pct
                on_progress(pct)
    if proc.wait() != 0:
        raise SeparationError(f"demucs falló: {tail.decode(errors='replace').strip()[-300:]}")
    folder = out_dir / MODEL / wav.stem
    stems = {s: folder / f"{s}.wav" for s in STEMS}
    missing = [s for s, p in stems.items() if not p.is_file()]
    if missing:
        raise SeparationError(f"demucs no generó los stems: {', '.join(missing)}")
    return stems
