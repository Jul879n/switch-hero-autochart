from autochart.frets import Note
from autochart.transcribe import snap_to_onsets


def test_snap_and_ghosts():
    peaks = [1.0, 2.0]
    near = Note(1.02, 1.5, 60, 0.3)
    weak_far = Note(1.5, 1.7, 62, 0.3)
    strong_far = Note(1.6, 1.9, 64, 0.9)
    out = snap_to_onsets([near, weak_far, strong_far], peaks)
    assert [n.pitch for n in out] == [60, 64]
    assert out[0].onset == 1.0 and out[0].offset >= 1.02
    assert out[1].onset == 1.6
