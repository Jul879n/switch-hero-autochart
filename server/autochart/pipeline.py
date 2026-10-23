"""Orquestación: audio -> carpeta de canción Switch Hero + song.zip."""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, chartwriter, difficulty, drums, frets, ini, playability as P, separate, tempo, transcribe
from .chartwriter import Track

# instrumento -> stem preferido
ASSIGN = {"guitar": "guitar", "rhythm": "other", "bass": "bass", "keys": "piano", "drums": "drums"}
MIN_NOTES = 8


class ChartError(Exception):
    pass


@dataclass
class ChartResult:
    folder: Path
    zip_path: Path
    stats: dict = field(default_factory=dict)
    instruments: list = field(default_factory=list)


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", s or "").strip(" .")
    return s[:80] or "cancion"


def sync_offset(grid: tempo.Grid, ticks: list, env, env_t) -> float:
    """R7: offset global (s) por correlación cruzada de onsets vs. envolvente, ±60 ms."""
    if env is None or len(ticks) < 8:
        return 0.0
    times = np.array([grid.tick_to_sec(t) for t in ticks])
    best, best_d = -1.0, 0.0
    for d in np.arange(-P.SYNC_RANGE_SEC, P.SYNC_RANGE_SEC + 1e-9, P.SYNC_STEP_SEC):
        score = float(np.interp(times + d, env_t, env, left=0.0, right=0.0).sum())
        # desempate hacia 0
        if score > best + 1e-9 or (abs(score - best) <= 1e-9 and abs(d) < abs(best_d)):
            best, best_d = score, float(d)
    return best_d


def stars_for(notes: list) -> list:
    """Frase de 2 compases cada 8 (compases 8k+5 y 8k+6) con ≥4 notas y sin acordes de 3."""
    if not notes:
        return []
    last_m = notes[-1].tick // P.MEASURE
    out = []
    for k in range(last_m // 8 + 1):
        a, b = (8 * k + 5) * P.MEASURE, (8 * k + 7) * P.MEASURE
        inside = [n for n in notes if a <= n.tick < b]
        if len(inside) >= 4 and all(len(n.frets) < 3 for n in inside):
            out.append((a, b - a))
    return out


def _nps(notes: list, secs: float) -> float:
    return len(notes) / secs if secs > 0 else 0.0


def build_tracks(stem_notes: dict, drum_events, grid: tempo.Grid, quiet: set, secs: float):
    """-> (tracks, nps, grid) con lint aplicado."""
    tracks: dict = {}
    nps: dict = {}
    t2s = grid.tick_to_sec

    def add(key, expert):
        if len(expert) < MIN_NOTES:
            return
        per = difficulty.reduce(expert, t2s)
        stars = stars_for(per["Expert"])
        tracks[key] = {d: Track(per[d], stars if per[d] else []) for d in P.DIFFICULTIES}
        for d in P.DIFFICULTIES:
            bad = P.lint(per[d], d, t2s)
            if bad:
                raise ChartError(f"Error del generador ({key}/{d}): " +
                                 "; ".join(f"{v.rule}@{v.tick} {v.msg}" for v in bad[:5]))
        nps[key] = _nps(per["Expert"], secs)

    for key, notes in stem_notes.items():
        add(key, frets.map(notes, grid.sec_to_tick, quiet))
    if "rhythm" in tracks:
        tracks["coop"] = tracks["rhythm"]
        nps["coop"] = nps["rhythm"]
    if drum_events:
        dx = drums.build_expert(drum_events, grid.sec_to_tick)
        if len(dx) >= MIN_NOTES:
            per = drums.reduce(dx)
            tracks["drums"] = {d: Track(per[d]) for d in P.DIFFICULTIES}
            for d in P.DIFFICULTIES:
                bad = drums.lint(per[d], d)
                if bad:
                    raise ChartError(f"Error del generador (drums/{d}): {bad[0].rule}@{bad[0].tick}")
            nps["drums"] = _nps(per["Expert"], secs)
    if "guitar" not in tracks:
        raise ChartError("No se encontraron notas suficientes para generar una carta")
    return tracks, nps


def zip_folder(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(folder.iterdir()):
            if f.is_file():
                z.write(f, f"{folder.name}/{f.name}")


def run(job_dir, meta: dict, progress: Callable[[str, int], None] = lambda s, p: None) -> ChartResult:
    job_dir = Path(job_dir)
    src = Path(meta["source"])
    work = job_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    progress("descarga", 5)
    wav = audio.prepare(src, work / "input.wav")
    secs = audio.duration(wav)

    progress("separacion", 10)
    stems = separate.run(wav, work / "stems", lambda p: progress("separacion", 10 + int(p * 0.30)))
    silent = {k: audio.is_silent(p) for k, p in stems.items()}

    progress("tempo", 40)
    grid, env, env_t = tempo.beat_grid(str(wav))

    progress("transcripcion", 50)
    choice: dict = {}
    for instr, stem in ASSIGN.items():
        if instr == "drums":
            continue
        if not silent[stem]:
            choice[instr] = stem
    if "guitar" not in choice:
        # guitarra silenciosa: el primer stem melódico con señal (other, vocals, piano, bass)
        fallback = next((k for k in ("other", "vocals", "piano", "bass") if not silent[k]), None)
        if fallback is None:
            raise ChartError("El audio no tiene señal suficiente para generar una carta")
        choice["guitar"] = fallback
    transcribed: dict = {}
    cache: dict = {}
    for i, (instr, stem) in enumerate(choice.items()):
        if stem not in cache:
            cache[stem] = transcribe.notes(stems[stem])
        transcribed[instr] = cache[stem]
        progress("transcripcion", 50 + int(25 * (i + 1) / len(choice)))
    drum_events = [] if silent["drums"] else drums.hits(str(stems["drums"]))

    # R6: primera nota >= 3 s
    firsts = [n[0].onset for n in transcribed.values() if n]
    lead = 0.0
    if firsts and min(firsts) < P.FIRST_NOTE_MIN_SEC:
        lead = P.FIRST_NOTE_MIN_SEC - min(firsts)
    if lead:
        shift = lambda ns: [frets.Note(n.onset + lead, n.offset + lead, n.pitch, n.vel) for n in ns]
        transcribed = {k: shift(v) for k, v in transcribed.items()}
        drum_events = [(t + lead, l) for t, l in drum_events]
        grid = tempo.Grid.build([b + lead for b in grid.beats], grid.phase)
        env_t = env_t + lead
        secs += lead

    progress("carta", 78)
    # compases suaves (R3): RMS bajo el percentil 30 del stem principal
    gm = choice["guitar"]
    m_count = int(grid.end_tick // P.MEASURE) + 1
    spans = [(grid.tick_to_sec(m * P.MEASURE) - lead, grid.tick_to_sec((m + 1) * P.MEASURE) - lead)
             for m in range(m_count)]
    rms = audio.rms_per_measure(stems[gm], spans)
    thr = float(np.percentile(rms, 30)) if rms else 0.0
    quiet = {m for m, v in enumerate(rms) if v <= thr}

    tracks, nps = build_tracks(transcribed, drum_events, grid, quiet, secs)

    # R7: calibración global
    d = sync_offset(grid, [n.tick for n in tracks["guitar"]["Expert"].notes], env, env_t)
    if d:
        grid = grid.shifted(d)   # tick 0 sigue en t=0: cambia solo el BPM del tramo inicial

    progress("empaque", 90)
    name = safe_name(f"{meta.get('artist') or 'Desconocido'} - {meta.get('title') or src.stem}")
    out_dir = job_dir / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    song_secs = secs
    chartwriter.write(out_dir / "notes.chart", meta, grid.bpm_events(), tracks)
    audio.encode_ogg(wav, out_dir / "song.ogg", lead)
    if meta.get("thumb") and Path(meta["thumb"]).is_file():
        shutil.copy(meta["thumb"], out_dir / "album.jpg")
    loud = int(np.argmax(rms)) if rms else 0
    stats = {"song_length_ms": int(song_secs * 1000),
             "preview_start_ms": int(max(0, spans[loud][0] + lead) * 1000) if rms else 0,
             "nps": nps}
    ini.write(out_dir / "song.ini", meta, stats)
    zip_path = job_dir / "song.zip"
    zip_folder(out_dir, zip_path)
    shutil.rmtree(work, ignore_errors=True)
    return ChartResult(out_dir, zip_path, stats, sorted(nps))
