from io import StringIO

from pipelines.angles.angle_runner import _emit_status


class _AsciiOnlyStream(StringIO):
    def write(self, s):
        if any(ord(ch) > 127 for ch in s):
            raise UnicodeEncodeError("ascii", s, 0, 1, "ordinal not in range(128)")
        return super().write(s)


def test_emit_status_falls_back_to_ascii(monkeypatch):
    stream = _AsciiOnlyStream()
    monkeypatch.setattr("pipelines.angles.angle_runner.sys.stdout", stream)

    _emit_status("cache hit 📂")

    assert stream.getvalue() == "cache hit ?\n"
