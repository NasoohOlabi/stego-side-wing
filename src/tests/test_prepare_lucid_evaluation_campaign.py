from __future__ import annotations

import json
import runpy
from pathlib import Path

BUILD_MANIFEST = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/prepare_lucid_evaluation_campaign.py")
)["build_manifest"]


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_build_manifest_keeps_only_complete_researched_posts(tmp_path: Path) -> None:
    for name in ("news_url_fetched", "news_researched", "news_angles"):
        (tmp_path / name).mkdir()
    _write(tmp_path / "news_url_fetched/a.json", {"id": "a"})
    _write(tmp_path / "news_researched/a.json", {"search_results": [{"url": "x"}]})
    _write(
        tmp_path / "news_angles/a.json",
        {
            "angles": [{}] * 32,
            "angle_artifact": {"tangent_count": 32, "angles_target_reached": True},
        },
    )
    _write(tmp_path / "news_url_fetched/b.json", {"id": "b"})
    _write(tmp_path / "news_researched/b.json", {"search_results": []})
    _write(
        tmp_path / "news_angles/b.json",
        {
            "angles": [{}] * 32,
            "angle_artifact": {"tangent_count": 32, "angles_target_reached": True},
        },
    )

    manifest = BUILD_MANIFEST(tmp_path, repeats=6, batch_posts=25)

    assert manifest["eligible_posts"] == 1
    assert manifest["planned_embeddings"] == 6
    assert [row["post_id"] for row in manifest["artifacts"]] == ["a"]
    assert manifest["excluded"] == {"research_empty": 1}
