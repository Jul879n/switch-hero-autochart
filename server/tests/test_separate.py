import re
from autochart.separate import _PCT


def test_progress_regex():
    assert _PCT.findall(b"\r 42%|####   | 3/7") == [b"42"]
    assert _PCT.findall(b"100%|########|") == [b"100"]


def test_progress_from_stream(tmp_path, monkeypatch):
    """Simula demucs: barra tqdm con 61 columnas, en trozos pequeños."""
    import sys
    from autochart import separate
    bar = lambda p: f"\r{p:3d}%|{'█' * (p * 61 // 100):<61}| {p}/100 [00:01<00:02]".encode()
    out = b"".join(bar(p) for p in (0, 33, 67, 100))
    script = tmp_path / "fake.py"
    folder = tmp_path / "o" / separate.MODEL / "in"
    make = "import pathlib\nf = pathlib.Path(%r)\nf.mkdir(parents=True, exist_ok=True)\n" % str(folder) + \
           "".join("(f / '%s.wav').write_bytes(b'x')\n" % st for st in separate.STEMS)
    script.write_text("import sys\nsys.stdout.buffer.write(%r)\nsys.stdout.flush()\n%s" % (out, make))
    real = separate.subprocess.Popen
    monkeypatch.setattr(separate.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, str(script)], **kw))
    seen = []
    separate.run(tmp_path / "in.wav", tmp_path / "o", seen.append)
    assert seen[-1] == 100 and 33 in seen and 67 in seen


def test_reuses_existing_stems(tmp_path, monkeypatch):
    from autochart import separate
    folder = tmp_path / "o" / separate.MODEL / "in"
    folder.mkdir(parents=True)
    for st in separate.STEMS:
        (folder / f"{st}.wav").write_bytes(b"x")
    monkeypatch.setattr(separate.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe separar")))
    assert set(separate.run(tmp_path / "in.wav", tmp_path / "o")) == set(separate.STEMS)


def test_kill_active_stops_hung_demucs(tmp_path, monkeypatch):
    """Un demucs congelado (no escribe nada) termina al pedir cancelar."""
    import sys
    import threading
    import time
    import pytest
    from autochart import separate
    real = separate.subprocess.Popen
    monkeypatch.setattr(separate.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, "-c", "import time; time.sleep(60)"], **kw))
    threading.Timer(0.7, separate.kill_active).start()
    t0 = time.time()
    with pytest.raises(separate.SeparationError):
        separate.run(tmp_path / "in.wav", tmp_path / "o")
    assert time.time() - t0 < 10
