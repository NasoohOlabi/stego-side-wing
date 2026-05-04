import json
from pathlib import Path

import pytest

from services.stego_metrics_service import run_divergence_metrics
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.output_results_shape import n8n_save_object_body
from workflows.utils.stego_codec import extract_invisible_payload

FORBIDDEN_INVISIBLE_CHARS = {"\u200c", "\u200d", "\u2060", "\u2063"}


def _build_post(idx: int) -> dict:
    return {
        "id": f"e2e-post-{idx}",
        "title": f"Zero KLD post {idx}",
        "selftext": "",
        "comments": [
            {
                "id": f"c{idx}a",
                "author": "alice",
                "body": (
                    "The carrier keeps the wording close to the original discussion so the token "
                    "distribution remains compatible with the matched post."
                ),
                "replies": [],
            },
            {
                "id": f"c{idx}b",
                "author": "bob",
                "body": (
                    "The visible text stays natural because it is literally drawn from "
                    "the same conversation instead of being regenerated."
                ),
                "replies": [],
            },
            {
                "id": f"c{idx}c",
                "author": "carol",
                "body": (
                    "The method also leaves enough surface area to carry a much larger hidden "
                    "payload without moving the unigram counts at all."
                ),
                "replies": [],
            },
        ],
    }


def _expected_visible_text(post: dict) -> str:
    return "\n".join(comment["body"] for comment in post["comments"])


def _payload_for(idx: int) -> str:
    return f"REAL_PAYLOAD_{idx}_" + (f"block{idx:02d}_" * 384)


def test_e2e_extractive_zero_kld_multi_sample_large_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    monkeypatch.setenv("WORKFLOW_STEGO_GENERATION_MODE", "extractive_zero_kld")
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "1")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "32")

    posts = [_build_post(idx) for idx in range(1, 9)]

    dataset_dir = tmp_path / "dataset"
    output_dir = tmp_path / "output-results"
    metrics_dir = tmp_path / "metrics"
    dataset_dir.mkdir()
    output_dir.mkdir()

    for post in posts:
        angles_post = GenAnglesPipeline().process_post(post)
        payload = _payload_for(int(str(post["id"]).split("-")[-1]))
        result = StegoPipeline().encode(payload=payload, post=angles_post, tag="version_e2e")

        assert result["succeeded"] is True
        assert result["stego_text"] == _expected_visible_text(post)
        assert extract_invisible_payload(result["stego_text"]) is None
        assert not (set(result["stego_text"]) & FORBIDDEN_INVISIBLE_CHARS)
        assert result["breakdown"]["embedded_payload_bits"] == len(payload.encode("utf-8")) * 8

        (dataset_dir / f'{post["id"]}.json').write_text(
            json.dumps({"comments": post["comments"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifact = n8n_save_object_body(result)
        (output_dir / f'{post["id"]}_version_e2e.json').write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    divergence = run_divergence_metrics(output_dir, dataset_dir, metrics_dir)
    report = divergence["report"]

    assert report["dataset_summary"]["usable_stego_samples"] == len(posts)
    assert abs(report["primary_baseline_matched_post"]["average_kl_stego_vs_matched_post"]) < 1e-12
    assert (
        abs(report["secondary_baseline_global_corpus"]["average_kl_stego_vs_global_corpus"])
        < 1e-12
    )
