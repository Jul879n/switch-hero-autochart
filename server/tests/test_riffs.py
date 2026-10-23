from autochart import difficulty, frets, playability as P
from helpers import note, s2t


def test_repeated_riff_same_frets():
    riff = [60, 64, 62, 67, 65, 69, 62, 60]
    notes = []
    for m in range(4):
        for i, p in enumerate(riff):
            notes.append(note(m * 2.0 + i * 0.25, p + (12 if m == 3 else 0)))  # el último compás, transpuesto
    tr = frets.map(notes, s2t)
    per = difficulty.reduce(tr)
    for d in P.DIFFICULTIES:
        by_m = {}
        for n in per[d]:
            by_m.setdefault(n.tick // P.MEASURE, []).append((n.tick % P.MEASURE, n.frets))
        ms = list(by_m.values())
        assert all(m == ms[0] for m in ms), d
