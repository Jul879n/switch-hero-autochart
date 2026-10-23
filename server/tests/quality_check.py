"""Chequeo de calidad (script, no pytest).

Uso: python quality_check.py <carpeta-cancion> [carpeta-stems] [parte=stem ...]
Por parte (Expert): notas/s, % de notas a < 35 ms de un golpe real del stem y % de trastes
iguales a los de la guitarra en los ticks comunes.
"""
import re
import sys
from pathlib import Path

import librosa
import numpy as np

# stem con el que se compara cada parte; Rítmica/Co-op/Teclado suelen derivarse de otro stem (ver log de transcripción)
STEM = {"guitar": "guitar", "coop": "guitar", "rhythm": "guitar", "bass": "bass", "keys": "piano"}
SECTION = {"Single": "guitar", "DoubleGuitar": "coop", "DoubleRhythm": "rhythm",
           "DoubleBass": "bass", "Keyboard": "keys"}
TOL = 0.035
TOL_WIDE = 0.075      # la cuantización a 1/16 mueve las notas decenas de ms


def parse(path: Path):
    text = path.read_text(encoding="utf-8")
    sync = [(int(t), int(b)) for t, b in re.findall(r"(\d+) = B (\d+)", text)]
    res = int(re.search(r"Resolution = (\d+)", text).group(1))
    parts = {}
    for sec, body in re.findall(r"\[(\w+)\]\s*\{(.*?)\}", text, re.S):
        m = re.fullmatch(r"Expert(\w+)", sec)
        if m and m.group(1) in SECTION:
            notes = {}
            for t, f in re.findall(r"(\d+) = N (\d) \d+", body):
                notes.setdefault(int(t), []).append(int(f))
            parts[SECTION[m.group(1)]] = {t: tuple(sorted(f)) for t, f in notes.items()}
    return sync, res, parts


def tick_to_sec(sync, res):
    sync = sorted(sync)
    def f(tick):
        sec, last_t, bpm = 0.0, 0, sync[0][1]
        for t, b in sync:
            if t >= tick:
                break
            sec += (t - last_t) / res * 60000 / bpm
            last_t, bpm = t, b
        return sec + (tick - last_t) / res * 60000 / bpm
    return f


def peaks(wav):
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    return np.sort(librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=256, units="time"))


def main(folder: Path, stems: Path, overrides: dict):
    STEM.update(overrides)
    sync, res, parts = parse(folder / "notes.chart")
    t2s = tick_to_sec(sync, res)
    guitar = parts.get("guitar", {})
    for key, notes in parts.items():
        times = np.array([t2s(t) for t in sorted(notes)])
        dur = max(times.max() - times.min(), 1e-9)
        wav = next(iter(stems.glob(f"{STEM[key]}.wav")), None)
        align = "n/d"
        if wav is not None:
            pk = peaks(wav)
            d = np.abs(times[:, None] - pk[None, :]).min(axis=1) if len(pk) else np.full(len(times), 9.0)
            near = pk[np.abs(times[:, None] - pk[None, :]).argmin(axis=1)] if len(pk) else times
            off = 1000 * float(np.median(times - near))
            align = f"{100 * (d < TOL).mean():.0f}% (<75 ms: {100 * (d < TOL_WIDE).mean():.0f}%, desfase {off:+.0f} ms)"
        common = set(notes) & set(guitar)
        same = f"{100 * sum(notes[t] == guitar[t] for t in common) / len(common):.0f}%" if common and key != "guitar" else "-"
        print(f"{key:7s} notas/s={len(notes) / dur:.2f} alineación={align} trastes=guitarra:{same}")


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    args = sys.argv[2:]
    stems = Path(args.pop(0)) if args and "=" not in args[0] else folder / "work" / "stems"
    main(folder, stems, dict(a.split("=") for a in args))
