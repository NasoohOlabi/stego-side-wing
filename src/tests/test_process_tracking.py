from __future__ import annotations

import os
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from infrastructure import process_tracking


def test_append_current_pid_to_log_appends_pid(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    pid_log_path = tmp_path / "pid.log"
    pid_log_path.write_text("111\n", encoding="utf-8")
    monkeypatch.setattr(process_tracking, "PID_LOG_PATH", pid_log_path)

    returned_path = process_tracking.append_current_pid_to_log()

    assert returned_path == pid_log_path
    assert pid_log_path.read_text(encoding="utf-8").splitlines() == [
        "111",
        str(os.getpid()),
    ]
