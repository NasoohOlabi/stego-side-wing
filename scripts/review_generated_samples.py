from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from loguru import logger  # noqa: E402

PREFIX = "pareto_security_retry_rating_cont_"
RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"
LOGS_ROOT = _REPO_ROOT / "metrics" / "automation_logs"
GENERIC_OPENERS = (
    "yeah, i guess",
    "honestly",
    "to be fair",
    "i mean",
)
VISIBLE_TEXT_RE = re.compile(r"[A-Za-z0-9]")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _latest_run_dir() -> Path:
    candidates = [path for path in RUNS_ROOT.glob(f"{PREFIX}*") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError("No pareto continuation run directories found.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _extract_record(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    return None


def _score_review(record: dict[str, Any]) -> dict[str, Any]:
    stego_text = str(record.get("stegoText") or record.get("stego_text") or "")
    lowered = stego_text.lower()
    issues: list[str] = []
    improvements: list[str] = []
    score = 5

    if not stego_text.strip():
        issues.append("Empty stego text.")
        improvements.append("Regenerate until the model returns a non-empty visible comment.")
        score = 1
    if stego_text and not VISIBLE_TEXT_RE.search(stego_text):
        issues.append("No visible alphanumeric content in stego text.")
        improvements.append("Reject non-visible carrier output and regenerate.")
        score = min(score, 1)
    if any(ord(char) < 32 and char not in "\r\n\t" for char in stego_text):
        issues.append("Contains control characters.")
        improvements.append("Strip control characters before accepting a sample.")
        score = min(score, 2)
    if len(stego_text.split()) < 8:
        issues.append("Too short to read as a natural reply.")
        improvements.append("Ask for a fuller reply with one concrete point.")
        score = min(score, 2)
    if len(stego_text) > 320:
        issues.append("Longer than needed for a natural comment.")
        improvements.append("Bias generation toward shorter direct replies.")
        score = min(score, 4)
    if any(lowered.startswith(opener) for opener in GENERIC_OPENERS):
        issues.append("Starts with a generic filler phrase.")
        improvements.append("Start directly with the main point.")
        score = min(score, 4)

    context = record.get("embedding", {}).get("commentEmbedding", {}).get("pickedCommentChain", [])
    selected_angle = record.get("embedding", {}).get("angleEmbedding", {}).get("selectedAngle", {})
    context_text = " ".join(
        str(item.get("body", "")) for item in context if isinstance(item, dict)
    )
    context_text = f"{context_text} {selected_angle.get('tangent', '')} {selected_angle.get('source_quote', '')}".lower()
    overlap_terms = {
        token
        for token in re.findall(r"[a-z0-9']+", stego_text.lower())
        if len(token) >= 5 and token in context_text
    }
    if stego_text and len(overlap_terms) < 2:
        issues.append("Weak topical overlap with the selected context.")
        improvements.append("Anchor the reply more clearly to the chosen comment or angle.")
        score = min(score, 3)

    if not issues:
        opinion = "Natural and usable."
    elif score >= 4:
        opinion = "Usable, but it needs tightening."
    elif score == 3:
        opinion = "Borderline. The sample reads okay but drifts from the source context."
    else:
        opinion = "Weak sample. It should be regenerated."

    return {
        "reviewedAt": datetime.now(UTC).isoformat(),
        "reviewVersion": 1,
        "score": score,
        "opinion": opinion,
        "issues": issues,
        "improvements": improvements,
        "needsRetry": score <= 2,
        "signals": {
            "wordCount": len(stego_text.split()),
            "charCount": len(stego_text),
            "overlapTermCount": len(overlap_terms),
        },
    }


def _annotate_output_file(path: Path) -> bool:
    payload = _read_json(path)
    record = _extract_record(payload)
    if record is None:
        logger.warning("review_skipped_invalid_shape", file=str(path))
        return False
    if isinstance(record.get("qualityReview"), dict):
        return False
    record["qualityReview"] = _score_review(record)
    _write_json(path, payload)
    logger.info(
        "sample_reviewed",
        file=str(path),
        score=record["qualityReview"]["score"],
        needs_retry=record["qualityReview"]["needsRetry"],
    )
    return True


def _iter_output_files(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("*/output-results/*.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate generated stego samples with a review.")
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    configure_api_logging()
    run_dir = args.run_dir or _latest_run_dir()
    reviewed = 0
    for path in _iter_output_files(run_dir):
        if _annotate_output_file(path):
            reviewed += 1
    logger.info("sample_review_pass_complete", run_dir=str(run_dir), reviewed=reviewed)


if __name__ == "__main__":
    main()
