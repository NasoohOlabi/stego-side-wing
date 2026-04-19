import json
import time
from pathlib import Path

from services.double_process_history import list_double_process_runs


def test_list_empty_reports(tmp_path: Path) -> None:
    base = tmp_path / "dp"
    (base / "reports").mkdir(parents=True)
    r = list_double_process_runs(base_path=base)
    assert r.count == 0
    assert r.runs == []
    assert r.base_path == str(base.resolve())


def test_list_orders_newest_first(tmp_path: Path) -> None:
    base = tmp_path / "dp"
    rep = base / "reports"
    rep.mkdir(parents=True)
    (rep / "old.json").write_text(
        json.dumps({"post_id": "a", "succeeded": True}),
        encoding="utf-8",
    )
    time.sleep(0.02)
    (rep / "new.json").write_text(
        json.dumps({"post_id": "b", "succeeded": True}),
        encoding="utf-8",
    )
    r = list_double_process_runs(base_path=base, limit=10)
    assert [x.record["post_id"] for x in r.runs] == ["b", "a"]


def test_filter_post_id_exact(tmp_path: Path) -> None:
    base = tmp_path / "dp"
    rep = base / "reports"
    rep.mkdir(parents=True)
    (rep / "x.json").write_text(
        json.dumps({"post_id": "keep", "succeeded": True}),
        encoding="utf-8",
    )
    (rep / "y.json").write_text(
        json.dumps({"post_id": "drop", "succeeded": True}),
        encoding="utf-8",
    )
    r = list_double_process_runs(base_path=base, post_id="keep", limit=10)
    assert r.count == 1
    assert r.runs[0].record["post_id"] == "keep"


def test_limit(tmp_path: Path) -> None:
    base = tmp_path / "dp"
    rep = base / "reports"
    rep.mkdir(parents=True)
    for i in range(3):
        time.sleep(0.01)
        (rep / f"p{i}.json").write_text(
            json.dumps({"post_id": f"id{i}", "succeeded": True}),
            encoding="utf-8",
        )
    r = list_double_process_runs(base_path=base, limit=2)
    assert r.count == 2
    assert {x.record["post_id"] for x in r.runs} == {"id2", "id1"}


def test_skips_invalid_json(tmp_path: Path) -> None:
    base = tmp_path / "dp"
    rep = base / "reports"
    rep.mkdir(parents=True)
    (rep / "bad.json").write_text("not json", encoding="utf-8")
    (rep / "good.json").write_text(
        json.dumps({"post_id": "g", "succeeded": True}),
        encoding="utf-8",
    )
    r = list_double_process_runs(base_path=base, limit=10)
    assert r.count == 1
    assert r.runs[0].record["post_id"] == "g"
