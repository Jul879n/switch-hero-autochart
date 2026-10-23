"""Rejilla de tempo variable: beats (s) <-> ticks (192 por negra)."""
from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np

from .playability import TICKS


@dataclass
class Grid:
    beats: list          # tiempos (s) de los beats reales, crecientes, >= 0
    n: int               # beats de relleno antes del primer beat real (tick inicial = TICKS*n)
    phase: int = 0

    @classmethod
    def build(cls, beats, phase: int = 0, n: int | None = None) -> "Grid":
        b = [float(x) for x in beats if x >= 0]
        if len(b) < 2:
            raise ValueError("No se detectaron beats suficientes")
        phase = phase % 4
        if n is None:
            iv = float(np.median(np.diff(b)))
            n = int(round(b[0] / iv)) if b[0] > 0.02 else 0
            if b[0] > 0.02:
                n = max(n, 1)
            # el beat de índice `phase` debe caer en un downbeat (múltiplo de 4)
            n += (-(n + phase)) % 4
        return cls(b, n, phase)

    # --- tramos: (tiempo, tick) de cada ancla ---
    def _anchors(self):
        t = ([0.0] if self.n > 0 else []) + self.beats
        k = ([0] if self.n > 0 else []) + [TICKS * (self.n + i) for i in range(len(self.beats))]
        return t, k

    def sec_to_tick(self, s: float) -> float:
        t, k = self._anchors()
        if s <= t[0]:
            return k[0] + (s - t[0]) * TICKS / self._iv(0)
        if s >= t[-1]:
            return k[-1] + (s - t[-1]) * TICKS / self._iv(len(t) - 2)
        i = bisect.bisect_right(t, s) - 1
        return k[i] + (s - t[i]) / (t[i + 1] - t[i]) * (k[i + 1] - k[i])

    def tick_to_sec(self, tk: float) -> float:
        t, k = self._anchors()
        if tk <= k[0]:
            return t[0] + (tk - k[0]) * self._iv(0) / TICKS
        if tk >= k[-1]:
            return t[-1] + (tk - k[-1]) * self._iv(len(t) - 2) / TICKS
        i = bisect.bisect_right(k, tk) - 1
        return t[i] + (tk - k[i]) / (k[i + 1] - k[i]) * (t[i + 1] - t[i])

    def _iv(self, i: int) -> float:
        t, k = self._anchors()
        return (t[i + 1] - t[i]) * TICKS / (k[i + 1] - k[i])

    def bpm_events(self) -> list:
        """[(tick, bpm_x1000)] — un evento por beat."""
        t, k = self._anchors()
        ev = []
        for i in range(len(t) - 1):
            bpm = 60.0 * (k[i + 1] - k[i]) / TICKS / (t[i + 1] - t[i])
            ev.append((k[i], int(round(bpm * 1000))))
        ev.append((k[-1], ev[-1][1]))
        return ev

    def shifted(self, d: float) -> "Grid":
        return Grid.build([b + d for b in self.beats], self.phase, self.n)

    @property
    def end_tick(self) -> int:
        return self._anchors()[1][-1]


def beat_grid(wav_path: str) -> tuple:
    """librosa -> (Grid, envolvente de onsets, tiempos de la envolvente)."""
    import librosa

    y, sr = librosa.load(wav_path, sr=22050, mono=True)
    hop = 512
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop, units="frames")
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    if len(times) < 2:
        raise ValueError("No se detectaron beats suficientes")
    ev_t = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=hop)
    return grid_from_beats(times, env, ev_t), env, ev_t


def steady_fit(times, tol: float = 0.04):
    """Si el tempo es casi constante, ajusta una rejilla lineal (mínimos cuadrados) a los beats.

    Elimina el jitter del tracker; con tempo variable (desviación > tol) deja los beats intactos.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 8:
        return times
    iv = np.diff(times)
    med = float(np.median(iv))
    if med <= 0 or float(np.max(np.abs(np.convolve(iv, np.ones(8) / 8, "valid") - med))) / med > tol:
        return times
    k = np.arange(len(times))
    slope, icpt = np.polyfit(k, times, 1)
    fit = icpt + slope * k
    return fit if float(np.max(np.abs(fit - times))) < 0.5 * med * 0.5 else times


def grid_from_beats(times, env=None, env_t=None) -> Grid:
    """Elige la fase de compás con mayor energía de onset en los downbeats."""
    times = steady_fit(times)
    phase = 0
    if env is not None and len(times) >= 8:
        scores = []
        for p in range(4):
            idx = np.searchsorted(env_t, times[p::4]).clip(0, len(env) - 1)
            scores.append(float(np.mean(env[idx])))
        phase = int(np.argmax(scores))
    return Grid.build(times, phase)
