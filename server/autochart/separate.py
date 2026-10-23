"""Separación de stems con demucs (htdemucs_6s)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")
MODEL = "htdemucs_6s"


class SeparationError(Exception):
    pass


def run(wav, out_dir) -> dict:
    wav, out_dir = Path(wav), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "-n", MODEL, "-j", "1", "-o", str(out_dir), str(wav)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SeparationError(f"demucs falló: {r.stderr.strip()[-300:]}")
    folder = out_dir / MODEL / wav.stem
    stems = {s: folder / f"{s}.wav" for s in STEMS}
    missing = [s for s, p in stems.items() if not p.is_file()]
    if missing:
        raise SeparationError(f"demucs no generó los stems: {', '.join(missing)}")
    return stems
