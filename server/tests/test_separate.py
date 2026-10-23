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
    script.write_text("import sys\nsys.stdout.buffer.write(%r)\nsys.stdout.flush()\n" % out)
    folder = tmp_path / "o" / separate.MODEL / "in"
    folder.mkdir(parents=True)
    for s in separate.STEMS:
        (folder / f"{s}.wav").write_bytes(b"x")
    real = separate.subprocess.Popen
    monkeypatch.setattr(separate.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, str(script)], **kw))
    seen = []
    separate.run(tmp_path / "in.wav", tmp_path / "o", seen.append)
    assert seen[-1] == 100 and 33 in seen and 67 in seen
