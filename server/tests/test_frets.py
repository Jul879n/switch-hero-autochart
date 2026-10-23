from autochart import frets
from helpers import note, s2t


def test_ascending_scale():
    pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [note(i * 0.25, p) for i, p in enumerate(pitches)]
    tr = frets.map(notes, s2t)
    fr = [n.fret for n in tr]
    assert fr == sorted(fr) and fr[0] == 0 and max(fr) == 4
