"""Reducción de Expert a Hard/Medium/Easy conservando los patrones."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from . import playability as P
from .frets import measure_signature, unify_repeats
from .playability import ChartNote

_REMAP = {
    "Expert": lambda f: f,
    "Hard": lambda f: f,
    "Medium": lambda f: int(round(f * 3 / 4)),
    "Easy": lambda f: int(round(f * 2 / 4)),
}


def _default_t2s(t: float) -> float:
    return t / (2 * P.TICKS)


def _shape(n: ChartNote, d: P.Diff) -> ChartNote:
    """Remapea trastes por rango y recorta acordes según la dificultad."""
    remap = _REMAP[d.name]
    main = remap(n.fret)
    frets = sorted({min(d.max_fret, remap(f)) for f in n.frets})
    allowed = d.max_chord > 1 and (not d.chord_downbeat_only or P.is_downbeat(n.tick))
    if not allowed:
        frets = [min(d.max_fret, main)]
    while len(frets) > d.max_chord:
        frets.remove(max(frets, key=lambda f: abs(f - main)))
    shape_ok = len(frets) == 1 or tuple(frets) in P.STANDARD_CHORDS | P.TRIPLE_CHORDS
    if not shape_ok:
        frets = [min(d.max_fret, main)]
    return replace(n, frets=tuple(frets), main=min(d.max_fret, main))


def _enforce_sep(notes: list, min_sep: int) -> list:
    """Conserva primero el beat más fuerte (downbeat > beat > 1/8 > 1/16)."""
    order = sorted(range(len(notes)), key=lambda i: (-P.beat_rank(notes[i].tick), -notes[i].accent, i))
    kept: list = []
    ticks: list = []
    import bisect
    for i in order:
        t = notes[i].tick
        j = bisect.bisect_left(ticks, t)
        near = (j > 0 and t - ticks[j - 1] < min_sep) or (j < len(ticks) and ticks[j] - t < min_sep)
        if not near:
            ticks.insert(j, t)
            kept.append(i)
    return [notes[i] for i in sorted(kept)]


def _enforce_nps(notes: list, max_nps: int, t2s: Callable[[float], float]) -> list:
    notes = list(notes)
    while True:
        times = [t2s(n.tick) for n in notes]
        j, bad = 0, None
        for i, t in enumerate(times):
            while times[j] <= t - 1.0:
                j += 1
            if i - j + 1 > max_nps:
                bad = (j, i)
                break
        if bad is None:
            return notes
        a, b = bad
        victim = min(range(a, b + 1), key=lambda k: (P.beat_rank(notes[k].tick), notes[k].accent))
        del notes[victim]


def reduce_one(expert: list, difficulty: str, t2s: Callable[[float], float] | None = None,
               master: dict | None = None) -> list:
    d = P.DIFF[difficulty]
    t2s = t2s or _default_t2s
    if not expert:
        return []
    notes = [_shape(n, d) for n in sorted(expert, key=lambda n: n.tick)]
    notes = _enforce_sep(notes, d.min_sep)
    notes = _enforce_nps(notes, d.max_nps, t2s)
    notes = P.smooth(notes, d)
    if master is not None:
        notes = unify_repeats(notes, master)
        notes = _enforce_sep(notes, d.min_sep)
    return P.finalize_sustains(notes, d)


def reduce(expert: list, t2s: Callable[[float], float] | None = None) -> dict:
    """{Expert, Hard, Medium, Easy} -> list[ChartNote]. Compases repetidos de Expert quedan idénticos."""
    from collections import defaultdict
    by_m = defaultdict(list)
    for n in expert:
        by_m[n.tick // P.MEASURE].append(n)
    seen, master = {}, {}
    for m in sorted(by_m):
        if len(by_m[m]) >= 2:
            master[m] = seen.setdefault(measure_signature(by_m[m]), m)
    return {name: reduce_one(expert, name, t2s, master) for name in P.DIFFICULTIES}
