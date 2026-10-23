"""audio -> notas con basic-pitch."""
from __future__ import annotations

import numpy as np

from . import playability as P
from .frets import Note

MIN_VEL = 0.25


def snap_to_onsets(notes: list, peaks, tol: float = P.ONSET_TOL_SEC,
                   keep_vel: float = P.GHOST_KEEP_VEL) -> list:
    """Pega cada nota al golpe real más cercano (≤ tol); sin golpe cerca, descarta las débiles."""
    peaks = np.sort(np.asarray(peaks, dtype=float))
    out = []
    for n in notes:
        d, t = None, None
        if len(peaks):
            i = int(np.searchsorted(peaks, n.onset))
            for j in (i - 1, i):
                if 0 <= j < len(peaks) and (d is None or abs(peaks[j] - n.onset) < d):
                    d, t = abs(peaks[j] - n.onset), float(peaks[j])
        if d is not None and d <= tol:
            out.append(Note(t, max(n.offset, t + 0.02), n.pitch, n.vel))
        elif n.vel >= keep_vel:
            out.append(n)
    return out


def notes(stem_wav) -> list:
    from basic_pitch.inference import predict

    _, _, events = predict(str(stem_wav), onset_threshold=0.5, frame_threshold=0.3,
                           minimum_note_length=58)
    out = []
    for start, end, pitch, amp, _bends in events:
        vel = float(amp)
        if vel < MIN_VEL or end <= start:
            continue
        out.append(Note(float(start), float(end), int(pitch), min(vel, 1.0)))
    out.sort(key=lambda n: n.onset)
    import librosa
    y, sr = librosa.load(str(stem_wav), sr=22050, mono=True)
    peaks = librosa.onset.onset_detect(onset_envelope=librosa.onset.onset_strength(y=y, sr=sr, hop_length=256),
                                       sr=sr, hop_length=256, units="time", backtrack=False)
    snapped = snap_to_onsets(out, peaks)
    return snapped if len(snapped) >= 0.6 * len(out) else out
