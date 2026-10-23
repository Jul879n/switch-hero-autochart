from autochart import frets
from helpers import note, s2t


def test_ascending_scale():
    pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [note(i * 0.25, p) for i, p in enumerate(pitches)]
    tr = frets.map(notes, s2t)
    fr = [n.fret for n in tr]
    assert fr == sorted(fr) and fr[0] == 0 and max(fr) == 4


def test_parallel_harmony_shift():
    from autochart.frets import assign_frets
    ns = frets.main_line([note(i * 0.25, p) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72])], s2t)
    base = assign_frets(ns)
    for sh in (-5, 7):
        f = assign_frets(ns, 2, sh)
        assert f == sorted(f) and all(0 <= x <= 4 for x in f)
    assert all(a <= b for a, b in zip(assign_frets(ns, 2, -5), base))
    assert assign_frets(ns, 2, 0) == base
