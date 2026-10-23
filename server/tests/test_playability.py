import random

import pytest
from autochart import playability as P
from helpers import cn


def rules(track, diff="Expert"):
    return {v.rule for v in P.lint(track, diff)}


def test_burst_jump():
    assert "R1" in rules([cn(0, 0), cn(48, 4)])


def test_zigzag():
    assert "R2" in rules([cn(i * 48, 0 if i % 2 == 0 else 4) for i in range(6)])


def test_sustain_release():
    n = [P.ChartNote(0, (0,), sustain=184), cn(192, 1)]
    assert "R5" in rules(n)


def test_nps():
    n = [cn(i * 27, i % 2) for i in range(14)]
    assert "NPS" in rules(n)


def test_clean_track():
    assert rules([cn(0, 0), cn(192, 1), cn(384, 2)]) == set()


def test_finalize_sustains_trims():
    n = P.finalize_sustains([cn(0, 0, dur=400), cn(150, 1)], P.DIFF["Expert"])
    assert n[0].sustain == 0     # recortado a <1/2 beat
    n = P.finalize_sustains([cn(0, 0, dur=400), cn(576, 1)], P.DIFF["Expert"])
    assert n[0].sustain == 400 and P.lint(n, "Expert") == []


def test_span():
    t = [cn(0, 0), cn(48, 4), cn(96, 0), cn(144, 4)]
    assert "SPAN" in rules(t)
    assert rules(P.smooth(t, P.DIFF["Expert"])) == set()


def test_smooth_property():
    rnd = random.Random(7)
    for _ in range(200):
        tick, tr = 0, []
        for _ in range(rnd.randint(4, 30)):
            tick += rnd.choice((48, 64, 96, 192, 288))
            tr.append(cn(tick, rnd.randint(0, 4)))
        bad = rules(P.smooth(tr, P.DIFF["Expert"])) & {"SPAN", "R1", "R2"}
        assert not bad, (bad, tr)
