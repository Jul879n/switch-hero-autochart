"""Escritor de notes.chart (Resolution = 192)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .playability import DIFFICULTIES, TICKS

SECTIONS = {
    "guitar": "Single",
    "coop": "DoubleGuitar",
    "rhythm": "DoubleRhythm",
    "bass": "DoubleBass",
    "keys": "Keyboard",
    "drums": "Drums",
}


@dataclass
class Track:
    notes: list
    stars: list = field(default_factory=list)   # [(tick, longitud)]


def _clean(s) -> str:
    return str(s or "").replace('"', "'").replace("\n", " ").replace("\r", " ").strip()


def render(meta: dict, tempo: list, tracks: dict) -> str:
    if not tempo:
        raise ValueError("tempo vacío")
    out = ["[Song]", "{",
           f'  Name = "{_clean(meta.get("title"))}"',
           f'  Artist = "{_clean(meta.get("artist"))}"',
           f'  Charter = "Switch Hero Autochart"',
           f"  Offset = 0",
           f"  Resolution = {TICKS}",
           f'  MusicStream = "song.ogg"',
           "}", "[SyncTrack]", "{", "  0 = TS 4"]
    last = None
    for tick, bpm in sorted(tempo):
        if bpm <= 0:
            raise ValueError(f"BPM inválido en tick {tick}")
        if bpm != last:
            out.append(f"  {tick} = B {bpm}")
            last = bpm
    out += ["}", "[Events]", "{", "}"]
    for instr, section in SECTIONS.items():
        per = tracks.get(instr)
        if not per:
            continue
        for diff in DIFFICULTIES:
            tr = per.get(diff)
            if tr is None or not tr.notes:
                continue
            events = []
            for t, ln in tr.stars:
                events.append((t, 0, f"S 2 {ln}"))
            for n in tr.notes:
                for f in sorted(n.frets):
                    events.append((n.tick, 1, f"N {f} {n.sustain}"))
            events.sort(key=lambda e: (e[0], e[1]))
            out += [f"[{diff}{section}]", "{"]
            out += [f"  {t} = {txt}" for t, _, txt in events]
            out.append("}")
    return "\n".join(out) + "\n"


def write(path, meta: dict, tempo: list, tracks: dict) -> None:
    text = render(meta, tempo, tracks)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
