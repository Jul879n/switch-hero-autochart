"""audio -> notas con basic-pitch."""
from __future__ import annotations

from .frets import Note

MIN_VEL = 0.25


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
    return out
