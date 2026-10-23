from autochart.difficulty import reduce
from autochart.playability import STANDARD_CHORDS
from helpers import cn


def sixteen():
    return [cn(i * 48, i % 5) for i in range(16)]


def test_counts():
    r = reduce(sixteen())
    assert len(r["Hard"]) == 8 and len(r["Medium"]) == 4 and len(r["Easy"]) == 2


def test_ranges_and_chords():
    ex = [cn(0, 2), cn(192, 4), cn(384, 3), cn(576, 4)]
    r = reduce(ex)
    assert all(len(n.frets) == 1 and max(n.frets) <= 2 for n in r["Easy"])
    assert all(max(n.frets) <= 3 for n in r["Medium"])
