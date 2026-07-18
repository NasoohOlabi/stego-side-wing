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
