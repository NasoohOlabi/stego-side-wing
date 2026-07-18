from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_publication_posts.py"
    spec = importlib.util.spec_from_file_location("prepare_publication_posts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_candidates_is_sorted_and_excludes_prior_posts(tmp_path: Path) -> None:
    module = _load_module()
    for name in ("c.json", "a.json", "b.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    selected = module.select_candidates(tmp_path, {"b"})

    assert [path.stem for path in selected] == ["a", "c"]


def test_reuse_prepared_angle_requires_multiple_angles(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{"angles": [{"angle": "a"}, {"angle": "b"}]}', encoding="utf-8")

    assert module._reuse_prepared_angle(source, destination) is True
    assert destination.read_bytes() == source.read_bytes()


def test_used_ids_skips_non_json_lines(tmp_path: Path) -> None:
    module = _load_module()
    run = tmp_path / "run"
    run.mkdir()
    (run / "results.jsonl").write_text('---\n{"post_id": "used"}\n', encoding="utf-8")

    assert module._used_zlg_post_ids(tmp_path) == {"used"}
