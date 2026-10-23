"""Reglas de jugabilidad (R1–R8) y linter. Todas las constantes viven aquí."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

TICKS = 192          # resolución (ticks por negra)
MEASURE = TICKS * 4
SIXTEENTH = TICKS // 4
EIGHTH = TICKS // 2
TRIPLET = TICKS // 3
HALF_BEAT = TICKS // 2
RELEASE_GAP = EIGHTH          # R5: tiempo para soltar antes de la nota siguiente
MIN_TRIMMED_SUSTAIN = HALF_BEAT
BURST_GAP = EIGHTH            # R1/R2: "ráfaga" = notas separadas por menos de esto

STANDARD_CHORDS = {(0, 1), (1, 2), (2, 3), (3, 4), (0, 2), (1, 3), (2, 4)}
TRIPLE_CHORDS = {(0, 1, 2), (1, 2, 3), (2, 3, 4)}

CHORD_ACCENT_PCT = 85
TRIPLE_ACCENT_PCT = 97
BACKGROUND_RATIO = 0.35       # saliencia mínima relativa a la mediana del compás
TRIPLET_GAIN = 0.40
QUIET_NOTES_PER_MEASURE = 4
FIRST_NOTE_MIN_SEC = 3.0      # R6
SYNC_RANGE_SEC = 0.060        # R7
SYNC_STEP_SEC = 0.002
SILENT_DBFS = -40.0
SILENT_FRACTION = 0.85

DIFFICULTIES = ("Expert", "Hard", "Medium", "Easy")


@dataclass(frozen=True)
class Diff:
    name: str
    min_sep: int
    max_nps: int
    max_fret: int
    max_chord: int
    chord_downbeat_only: bool
    max_jump: int
    min_sustain: int


DIFF = {
    "Expert": Diff("Expert", SIXTEENTH, 12, 4, 3, False, 2, TICKS),
    "Hard": Diff("Hard", SIXTEENTH, 7, 4, 2, False, 2, TICKS),
    "Medium": Diff("Medium", EIGHTH, 4, 3, 2, True, 1, HALF_BEAT),
    "Easy": Diff("Easy", TICKS, 2, 2, 1, True, 1, HALF_BEAT),
}


@dataclass(frozen=True)
class ChartNote:
    tick: int
    frets: tuple
    dur: int = 0          # duración cruda de la nota (ticks)
    sustain: int = 0      # sustain final escrito
    accent: float = 0.0
    main: int = -1        # traste de la melodía (para reducir acordes)

    @property
    def fret(self) -> int:
        return self.main if self.main >= 0 else self.frets[0]


@dataclass(frozen=True)
class Violation:
    rule: str
    tick: int
    msg: str


def beat_rank(tick: int) -> int:
    """Jerarquía: downbeat 3 > beat 2 > 1/8 1 > resto 0."""
    if tick % MEASURE == 0:
        return 3
    if tick % TICKS == 0:
        return 2
    if tick % EIGHTH == 0:
        return 1
    return 0


def is_downbeat(tick: int) -> bool:
    return tick % MEASURE == 0


def finalize_sustains(notes: Sequence[ChartNote], diff: Diff) -> list[ChartNote]:
    """R5: sustains cómodos."""
    out = []
    for i, n in enumerate(notes):
        s = 0
        if n.dur >= diff.min_sustain:
            s = n.dur
            if i + 1 < len(notes):
                s = min(s, notes[i + 1].tick - RELEASE_GAP - n.tick)
            if s < MIN_TRIMMED_SUSTAIN:
                s = 0
        out.append(replace(n, sustain=s))
    return out


def _single(n: ChartNote):
    return n.frets[0] if len(n.frets) == 1 else None


def smooth(notes: Sequence[ChartNote], diff: Diff) -> list[ChartNote]:
    """R4 (fusionar), R2 (zig-zag), R1 (saltos) con los límites de la dificultad."""
    # R4: mismo traste y demasiado cerca -> una nota con sustain
    merged: list[ChartNote] = []
    for n in notes:
        if merged:
            p = merged[-1]
            if _single(p) is not None and p.frets == n.frets and n.tick - p.tick < diff.min_sep:
                merged[-1] = replace(p, dur=max(p.dur, n.tick + n.dur - p.tick))
                continue
        merged.append(n)
    out = list(merged)
    # R2: a-b-a rápido con |a-b|>=3
    for i in range(1, len(out) - 1):
        a, b, c = out[i - 1], out[i], out[i + 1]
        fa, fb, fc = _single(a), _single(b), _single(c)
        if None in (fa, fb, fc):
            continue
        if fa == fc and abs(fa - fb) >= 3 and b.tick - a.tick < BURST_GAP and c.tick - b.tick < BURST_GAP:
            step = 2 if fb > fa else -2
            nf = max(0, min(diff.max_fret, fa + step))
            out[i] = replace(b, frets=(nf,), main=nf)
    # R1: ráfagas sin saltos bruscos
    for i in range(1, len(out)):
        p, n = out[i - 1], out[i]
        fp, fn = _single(p), _single(n)
        if fp is None or fn is None or n.tick - p.tick >= BURST_GAP:
            continue
        if abs(fn - fp) > diff.max_jump:
            nf = fp + diff.max_jump * (1 if fn > fp else -1)
            nf = max(0, min(diff.max_fret, nf))
            out[i] = replace(n, frets=(nf,), main=nf)
    return out


def lint(track: Sequence[ChartNote], difficulty: str,
         tick_to_sec: Callable[[int], float] | None = None) -> list[Violation]:
    d = DIFF[difficulty]
    t2s = tick_to_sec or (lambda t: t / (2 * TICKS))   # 120 BPM por defecto
    v: list[Violation] = []
    notes = list(track)
    for i, n in enumerate(notes):
        if max(n.frets) > d.max_fret:
            v.append(Violation("RANGE", n.tick, f"traste {max(n.frets)} > {d.max_fret}"))
        if len(n.frets) > d.max_chord or (len(n.frets) > 1 and d.max_chord == 1):
            v.append(Violation("CHORD", n.tick, f"acorde de {len(n.frets)}"))
        elif len(n.frets) > 1:
            if d.chord_downbeat_only and not is_downbeat(n.tick):
                v.append(Violation("CHORD", n.tick, "acorde fuera del downbeat"))
            shapes = STANDARD_CHORDS if len(n.frets) == 2 else TRIPLE_CHORDS
            if tuple(sorted(n.frets)) not in shapes:
                v.append(Violation("CHORD", n.tick, f"forma no estándar {n.frets}"))
        if n.sustain:
            if n.sustain < MIN_TRIMMED_SUSTAIN:
                v.append(Violation("R5", n.tick, "sustain demasiado corto"))
            if i + 1 < len(notes) and n.tick + n.sustain > notes[i + 1].tick - RELEASE_GAP:
                v.append(Violation("R5", n.tick, "sustain sin tiempo para soltar"))
        if i + 1 < len(notes):
            nx = notes[i + 1]
            gap = nx.tick - n.tick
            if gap <= 0:
                v.append(Violation("SEP", n.tick, "dos notas en el mismo tick"))
                continue
            if gap < d.min_sep:
                same = _single(n) is not None and n.frets == nx.frets
                v.append(Violation("R4" if same else "SEP", n.tick, f"separación {gap} < {d.min_sep}"))
            fa, fb = _single(n), _single(nx)
            if fa is not None and fb is not None and gap < BURST_GAP and abs(fa - fb) > d.max_jump:
                v.append(Violation("R1", n.tick, f"salto {fa}->{fb} en ráfaga"))
            if i + 2 < len(notes) and fa is not None and fb is not None:
                nn = notes[i + 2]
                fc = _single(nn)
                if (fc == fa and abs(fa - fb) >= 3 and gap < BURST_GAP
                        and nn.tick - nx.tick < BURST_GAP):
                    v.append(Violation("R2", n.tick, f"zig-zag {fa}<->{fb}"))
    times = [t2s(n.tick) for n in notes]
    j = 0
    for i, t in enumerate(times):
        while times[j] <= t - 1.0:
            j += 1
        # ventana (t-1, t]; cuenta de notas dentro
        if i - j + 1 > d.max_nps:
            v.append(Violation("NPS", notes[i].tick, f"{i - j + 1} notas/s > {d.max_nps}"))
            break
    return v
