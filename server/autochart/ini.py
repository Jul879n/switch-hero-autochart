"""song.ini de Switch Hero / Clone Hero."""
from __future__ import annotations


def _clean(s) -> str:
    return str(s or "").replace("\n", " ").replace("\r", " ").strip()


def level(nps: float | None) -> int:
    """Dificultad 0–6 según notas por segundo en Expert; -1 si se omitió la pista."""
    if nps is None:
        return -1
    for limit, lv in ((2, 1), (3, 2), (4, 3), (5, 4), (6, 5)):
        if nps < limit:
            return lv
    return 6


def render(meta: dict, stats: dict) -> str:
    lines = ["[song]",
             f"name = {_clean(meta.get('title')) or 'Sin título'}",
             f"artist = {_clean(meta.get('artist')) or 'Desconocido'}",
             f"album = {_clean(meta.get('album'))}",
             f"genre = {_clean(meta.get('genre')) or 'Autochart'}",
             f"year = {_clean(meta.get('year'))}",
             "charter = Switch Hero Autochart",
             f"song_length = {int(stats.get('song_length_ms', 0))}",
             f"preview_start_time = {int(stats.get('preview_start_ms', 0))}",
             "delay = 0"]
    for key, name in (("guitar", "diff_guitar"), ("coop", "diff_guitar_coop"), ("rhythm", "diff_rhythm"),
                      ("bass", "diff_bass"), ("keys", "diff_keys"), ("drums", "diff_drums")):
        lines.append(f"{name} = {level(stats.get('nps', {}).get(key))}")
    return "\n".join(lines) + "\n"


def write(path, meta: dict, stats: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(meta, stats))


def parse(path) -> dict:
    out: dict = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out
