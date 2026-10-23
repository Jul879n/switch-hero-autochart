"""Batería: onsets del stem -> pistas de 5 pads + bombo (aunque Switch Hero no la juegue)."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

import numpy as np

from . import playability as P
from .difficulty import _enforce_sep
from .playability import ChartNote

KICK, RED, YELLOW, BLUE, GREEN = 0, 1, 2, 3, 4
CYMBAL_Y, CYMBAL_G = 66, 68
SEP = {"Expert": P.SIXTEENTH, "Hard": P.EIGHTH, "Medium": P.TICKS, "Easy": P.TICKS * 2}


def classify(low: float, mid: float, hi_mid: float, high: float, long_attack: bool) -> list:
    """Energías de banda (<150 Hz, 150–2 kHz, 2–5 kHz, >5 kHz) -> lanes."""
    total = low + mid + hi_mid + high
    if total <= 0:
        return []
    lo, mi, hm, hi = low / total, mid / total, hi_mid / total, high / total
    lanes: list = []
    if lo > 0.45:
        lanes.append(KICK)
    if mi > 0.35 and not (hi > 0.4):
        lanes.append(RED)
    if hi > 0.4:
        lanes += [GREEN, CYMBAL_G] if long_attack else [YELLOW, CYMBAL_Y]
    elif hm > 0.35:
        lanes.append(BLUE)
    if not lanes:
        lanes.append(KICK if lo >= max(mi, hm, hi) else RED)
    return lanes


def hits(wav_path: str) -> list:
    """[(segundos, lanes)] con librosa."""
    import librosa

    y, sr = librosa.load(wav_path, sr=22050, mono=True)
    if y.size == 0:
        return []
    env = librosa.onset.onset_strength(y=y, sr=sr)
    times = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="time", backtrack=False)
    win = int(0.05 * sr)
    out = []
    for t in times:
        i = int(t * sr)
        seg = y[i:i + win]
        if seg.size < 256:
            continue
        spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
        f = np.fft.rfftfreq(seg.size, 1 / sr)
        band = lambda a, b: float(spec[(f >= a) & (f < b)].sum())
        tail = np.abs(y[i + win:i + 3 * win])
        long_attack = tail.size > 0 and float(tail.mean()) > 0.5 * float(np.abs(seg).mean())
        lanes = classify(band(0, 150), band(150, 2000), band(2000, 5000), band(5000, 1e9), long_attack)
        if lanes:
            out.append((float(t), lanes))
    return out


def build_expert(events: list, sec_to_tick: Callable[[float], float]) -> list:
    """Cuantiza a 1/16, fusiona por tick y limita a bombo + 2 pads."""
    by_tick: dict = {}
    for t, lanes in events:
        tk = int(round(sec_to_tick(t) / P.SIXTEENTH)) * P.SIXTEENTH
        if tk < 0:
            continue
        by_tick.setdefault(tk, set()).update(lanes)
    out = []
    for tk in sorted(by_tick):
        lanes = by_tick[tk]
        pads = [x for x in (RED, YELLOW, BLUE, GREEN) if x in lanes][:2]
        keep = set(pads) | ({KICK} if KICK in lanes else set())
        if YELLOW in keep:
            keep |= {CYMBAL_Y} & lanes
        if GREEN in keep:
            keep |= {CYMBAL_G} & lanes
        if CYMBAL_Y in keep and CYMBAL_G in keep:     # nunca dos platillos distintos a la vez
            keep.discard(CYMBAL_G)
            keep.discard(GREEN)
        fr = tuple(sorted(keep))
        out.append(ChartNote(tk, fr, accent=1.0))
    return out


def reduce(expert: list) -> dict:
    res = {"Expert": expert}
    for name in ("Hard", "Medium", "Easy"):
        notes = _enforce_sep(list(expert), SEP[name])
        shaped = []
        last_kick = -10 ** 9
        for n in notes:
            fr = n.frets
            if name == "Medium" and KICK in fr:
                if n.tick - last_kick < P.TICKS:       # sin dobles de bombo
                    fr = tuple(x for x in fr if x != KICK)
                else:
                    last_kick = n.tick
            if name == "Easy" and KICK in fr and not P.is_downbeat(n.tick):
                fr = tuple(x for x in fr if x != KICK)
            if fr:
                shaped.append(replace(n, frets=fr))
        res[name] = shaped
    return res


def lint(track: list, difficulty: str) -> list:
    v = []
    for a, b in zip(track, track[1:]):
        if b.tick - a.tick < SEP[difficulty]:
            v.append(P.Violation("SEP", a.tick, "golpes demasiado juntos"))
    for n in track:
        pads = [x for x in n.frets if x in (RED, YELLOW, BLUE, GREEN)]
        if len(pads) > 2:
            v.append(P.Violation("PADS", n.tick, "más de 2 pads"))
    return v
