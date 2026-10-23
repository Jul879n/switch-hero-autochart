import numpy as np
import pytest
from autochart.tempo import Grid, grid_from_beats


def test_grid_120bpm():
    beats = [1.0 + 0.5 * i for i in range(40)]
    g = Grid.build(beats)
    ev = g.bpm_events()
    assert all(abs(b - 120000) <= 1000 for t, b in ev if t >= 192 * g.n)
    ticks = [g.sec_to_tick(b) for b in beats]
    assert all(abs(b - a - 192) < 1e-6 for a, b in zip(ticks, ticks[1:]))
    assert abs(g.tick_to_sec(g.sec_to_tick(7.3)) - 7.3) < 1e-9
    assert g.sec_to_tick(0.0) == 0 and (g.n * 192) % 192 == 0


def test_phase_downbeat():
    g = Grid.build([0.5 * i for i in range(1, 30)], phase=2)
    assert (g.n + 2) % 4 == 0


def test_click_track():
    librosa = pytest.importorskip("librosa")
    from autochart.tempo import beat_grid
    import soundfile as sf, tempfile, os
    sr = 22050
    y = np.zeros(sr * 30)
    for i in range(60):
        t = int(i * 0.5 * sr)
        y[t:t + 200] = np.hanning(400)[:200] * 0.9
    p = os.path.join(tempfile.mkdtemp(), "c.wav")
    sf.write(p, y, sr)
    g, _, _ = beat_grid(p)
    assert all(abs(b - 120000) <= 1000 for _, b in g.bpm_events()[2:-2])


def test_steady_fit_removes_jitter_and_outliers():
    from autochart.tempo import steady_fit
    rng = np.random.default_rng(0)
    beats = 1.0 + 0.5 * np.arange(60) + rng.normal(0, 0.01, 60)
    beats[10] += 0.12     # beat mal detectado
    beats[11] -= 0.1
    fit = steady_fit(beats)
    assert np.allclose(np.diff(fit), 0.5, atol=1e-3)


def test_steady_fit_keeps_tempo_changes():
    from autochart.tempo import steady_fit
    varied = np.cumsum(np.r_[1.0, np.linspace(0.6, 0.3, 59)])   # acelera mucho
    out = steady_fit(varied)
    assert np.diff(out)[0] > np.diff(out)[-1] * 1.5
