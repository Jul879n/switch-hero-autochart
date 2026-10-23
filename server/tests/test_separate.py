import re
from autochart.separate import _PCT


def test_progress_regex():
    assert _PCT.findall(b"\r 42%|####   | 3/7") == [b"42"]
    assert _PCT.findall(b"100%|########|") == [b"100"]
