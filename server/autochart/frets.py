"""Notas transcritas -> pista Expert jugable (línea principal, contorno, riffs, acordes)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import median
from typing import Callable, Iterable

import numpy as np

from . import playability as P
from .playability import ChartNote


@dataclass(frozen=True)
class Note:
    onset: float
    offset: float
    pitch: int
    vel: float


@dataclass
class _N:
    tick: int
    pitch: int
    dur: int
    vel: float
    extras: list      # [(pitch, vel)] voces secundarias del mismo tick


def quantize(ticks: list) -> list:
    """Cuantiza por compás a 1/16 o a tresillo 1/12."""
    by_m = defaultdict(list)
    for i, t in enumerate(ticks):
        by_m[int(t // P.MEASURE)].append(i)
    out = list(ticks)
    for idxs in by_m.values():
        def err(g):
            return sum(abs(ticks[i] - round(ticks[i] / g) * g) for i in idxs)
        e16, e12 = err(P.SIXTEENTH), err(P.TRIPLET)
        g = P.TRIPLET if e12 < e16 * (1 - P.TRIPLET_GAIN) else P.SIXTEENTH
        for i in idxs:
            out[i] = int(round(ticks[i] / g) * g)
    return out


def main_line(notes: Iterable[Note], sec_to_tick: Callable[[float], float]) -> list:
    """Cuantiza y deja la nota más saliente por tick; descarta fondo."""
    notes = [n for n in notes if sec_to_tick(n.onset) >= 0]
    if not notes:
        return []
    raw = [sec_to_tick(n.onset) for n in notes]
    q = quantize(raw)
    by_tick = defaultdict(list)
    for n, tk in zip(notes, q):
        d = max(0, int(round(sec_to_tick(n.offset) - sec_to_tick(n.onset))))
        by_tick[tk].append((n, d))
    out = []
    for tk in sorted(by_tick):
        group = sorted(by_tick[tk], key=lambda x: -x[0].vel)
        (m, d) = group[0]
        out.append(_N(tk, m.pitch, d, m.vel,
                      [(g.pitch, g.vel) for g, _ in group[1:] if g.pitch != m.pitch]))
    # fondo: < 35 % de la saliencia mediana del compás
    by_m = defaultdict(list)
    for n in out:
        by_m[n.tick // P.MEASURE].append(n)
    keep = []
    for ns in by_m.values():
        med = median(n.vel for n in ns)
        keep += [n for n in ns if n.vel >= P.BACKGROUND_RATIO * med]
    keep.sort(key=lambda n: n.tick)
    return keep


def _contour_table(pitches: list) -> dict:
    """Pitch -> traste 0..4 por rango; los intervalos grandes pesan más."""
    ps = sorted(set(pitches))
    if len(ps) == 1:
        return {ps[0]: 2}
    gaps = [min(b - a, 5) for a, b in zip(ps, ps[1:])]
    total = sum(gaps)
    pos, acc = [0.0], 0.0
    for g in gaps:
        acc += g
        pos.append(4 * acc / total)
    return {p: int(round(x)) for p, x in zip(ps, pos)}


def assign_frets(ns: list, window: int = 2) -> list:
    """Ventana de `window` compases; misma altura = mismo traste dentro de la ventana."""
    win = defaultdict(list)
    for n in ns:
        win[n.tick // (window * P.MEASURE)].append(n)
    frets = {}
    for items in win.values():
        table = _contour_table([n.pitch for n in items])
        for n in items:
            frets[id(n)] = table[n.pitch]
    return [frets[id(n)] for n in ns]


def _rest_hand(frets: list, ns: list, quiet: set) -> list:
    """R3: pasajes lentos/suaves en 0–2; el naranja queda para los picos."""
    by_m = defaultdict(list)
    for i, n in enumerate(ns):
        by_m[n.tick // P.MEASURE].append(i)
    out = list(frets)
    squash = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2}
    for m, idxs in by_m.items():
        if len(idxs) < P.QUIET_NOTES_PER_MEASURE or m in quiet:
            for i in idxs:
                out[i] = squash[out[i]]
    return out


def _percentile(vals: list, pct: float) -> float:
    return float(np.percentile(vals, pct)) if vals else 1.0


def _chords(ns: list, frets: list) -> list:
    """Devuelve la lista de ChartNote con acordes en golpes acentuados."""
    vels = [n.vel for n in ns]
    p85, p97 = _percentile(vels, P.CHORD_ACCENT_PCT), _percentile(vels, P.TRIPLE_ACCENT_PCT)
    out = []
    for i, (n, f) in enumerate(zip(ns, frets)):
        fr = (f,)
        gap_prev = n.tick - ns[i - 1].tick if i else 10 ** 9
        gap_next = ns[i + 1].tick - n.tick if i + 1 < len(ns) else 10 ** 9
        calm = gap_prev >= P.EIGHTH and gap_next >= P.EIGHTH    # nunca en ráfagas
        if n.extras and calm and n.vel >= p85 and n.tick % P.TICKS == 0:
            ex = max(n.extras, key=lambda e: e[1])[0]
            up = ex > n.pitch
            partner = f + 1 if (up and f < 4) or f == 0 else f - 1
            fr = tuple(sorted((f, partner)))
            if (len(n.extras) >= 2 and n.vel >= p97 and P.is_downbeat(n.tick)):
                for base in ((0, 1, 2), (1, 2, 3), (2, 3, 4)):
                    if f in base:
                        fr = base
                        break
        out.append(ChartNote(n.tick, fr, dur=n.dur, accent=n.vel, main=f))
    return out


def map(notes: Iterable[Note], sec_to_tick: Callable[[float], float],
        quiet_measures: set | None = None, window: int = 2) -> list:
    """Notas transcritas -> pista Expert (list[ChartNote], sin sustains finales)."""
    notes = [n for n in notes if n.offset >= n.onset and 0 <= n.pitch < 128 and n.vel > 0]
    ns = main_line(notes, sec_to_tick)
    if not ns:
        return []
    frets = _rest_hand(assign_frets(ns, window), ns, quiet_measures or set())
    track = _chords(ns, frets)
    track = P.smooth(track, P.DIFF["Expert"])
    track = unify_repeats(track, riff_groups(ns))
    return P.finalize_sustains(track, P.DIFF["Expert"])


def measure_signature(notes: list) -> tuple:
    """(ritmo cuantizado, intervalos relativos) de un compás."""
    if not notes:
        return ()
    base = notes[0].tick - notes[0].tick % P.MEASURE
    return tuple((n.tick - base, tuple(n.frets)) for n in notes)


def _pitch_signature(ns: list) -> tuple:
    base = ns[0].tick - ns[0].tick % P.MEASURE
    return (tuple(n.tick - base for n in ns), tuple(n.pitch - ns[0].pitch for n in ns))


def riff_groups(ns: list) -> dict:
    """Compás -> compás maestro (primer compás con la misma firma de ritmo+intervalos)."""
    by_m = defaultdict(list)
    for n in ns:
        by_m[n.tick // P.MEASURE].append(n)
    seen, master = {}, {}
    for m in sorted(by_m):
        if len(by_m[m]) < 2:
            continue
        sig = _pitch_signature(by_m[m])
        master[m] = seen.setdefault(sig, m)
    return master


def unify_repeats(track: list, master: dict | None = None) -> list:
    """Los compases repetidos adoptan exactamente la forma (trastes y ritmo) del compás maestro.

    `master` mapea compás -> maestro; si falta, se deduce de la firma (ritmo + trastes).
    """
    by_m = defaultdict(list)
    for n in track:
        by_m[n.tick // P.MEASURE].append(n)
    if master is None:
        seen, master = {}, {}
        for m in sorted(by_m):
            if len(by_m[m]) >= 2:
                master[m] = seen.setdefault(measure_signature(by_m[m]), m)
    out = []
    for m in sorted(by_m):
        ms = master.get(m, m)
        if ms == m or ms not in by_m:
            out += by_m[m]
        else:
            out += [replace(s, tick=s.tick - ms * P.MEASURE + m * P.MEASURE) for s in by_m[ms]]
    return sorted(out, key=lambda n: n.tick)
