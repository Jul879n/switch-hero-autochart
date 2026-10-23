from autochart.playability import ChartNote, MEASURE, SIXTEENTH, TICKS
from autochart.frets import Note


def cn(tick, fret=0, dur=0, accent=0.5):
    return ChartNote(tick, (fret,), dur=dur, accent=accent, main=fret)


def s2t(sec):          # 120 BPM: 1 s = 384 ticks
    return sec * 2 * TICKS


def note(sec, pitch, dur=0.2, vel=0.8):
    return Note(sec, sec + dur, pitch, vel)
