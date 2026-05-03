"""Recent git updates service for API state endpoints."""

from __future__ import annotations

import subprocess
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, validate_call

from infrastructure.config import REPO_ROOT

_MARKER = "__COMMIT__"


class CommitSummary(BaseModel):
    commit: str
    short_commit: str
    author: str
    committed_at: str
    subject: str
    files_changed_total: int
    files_changed_visible: int
    generated_files_changed: int
    insertions: int
    deletions: int
    paths: list[str]
    has_more_paths: bool


class PathSummary(BaseModel):
    path: str
    touches: int


class RecentUpdatesPayload(BaseModel):
    generated_at_utc: str
    days: int
    limit: int
    count: int
    authors: list[str]
    top_paths: list[PathSummary]
    commits: list[CommitSummary]


@validate_call
def get_recent_git_updates(days: int = 7, limit: int = 20) -> dict[str, Any]:
    safe_days, safe_limit = _validated_window(days, limit)
    lines = _git_log_lines(safe_days)
    commits = _parse_commits(lines, safe_limit)
    payload = RecentUpdatesPayload(
        generated_at_utc=datetime.now(UTC).isoformat(),
        days=safe_days,
        limit=safe_limit,
        count=len(commits),
        authors=sorted({item.author for item in commits}),
        top_paths=_top_paths(commits),
        commits=commits,
    )
    return payload.model_dump(mode="json")


def _validated_window(days: int, limit: int) -> tuple[int, int]:
    safe_days = max(1, min(days, 90))
    safe_limit = max(1, min(limit, 100))
    return safe_days, safe_limit


def _git_log_lines(days: int) -> list[str]:
    cmd = [
        "git",
        "-C",
        str(REPO_ROOT),
        "log",
        f"--since={days} days ago",
        "--date=iso-strict",
        "--pretty=format:__COMMIT__%n%H%n%h%n%an%n%ad%n%s",
        "--numstat",
        "--no-merges",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "git log failed"
        raise RuntimeError(stderr)
    return result.stdout.splitlines()


def _parse_commits(lines: list[str], limit: int) -> list[CommitSummary]:
    commits: list[CommitSummary] = []
    i = 0
    while i < len(lines) and len(commits) < limit:
        if lines[i].strip() != _MARKER:
            i += 1
            continue
        commit, i = _parse_one_commit(lines, i + 1)
        if commit is not None:
            commits.append(commit)
    return commits


def _parse_one_commit(lines: list[str], start: int) -> tuple[CommitSummary | None, int]:
    if start + 4 >= len(lines):
        return None, len(lines)
    header = lines[start : start + 5]
    paths, insertions, deletions, cursor = _parse_numstat(lines, start + 5)
    visible_paths = sorted(path for path in paths if not _is_generated_path(path))
    sampled_paths = visible_paths[:40]
    generated_count = len(paths) - len(visible_paths)
    commit = CommitSummary(
        commit=header[0],
        short_commit=header[1],
        author=header[2],
        committed_at=header[3],
        subject=header[4],
        files_changed_total=len(paths),
        files_changed_visible=len(visible_paths),
        generated_files_changed=generated_count,
        insertions=insertions,
        deletions=deletions,
        paths=sampled_paths,
        has_more_paths=len(visible_paths) > len(sampled_paths),
    )
    return commit, cursor


def _parse_numstat(lines: list[str], start: int) -> tuple[set[str], int, int, int]:
    paths: set[str] = set()
    insertions = 0
    deletions = 0
    i = start
    while i < len(lines) and lines[i].strip() != _MARKER:
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        paths.add(parts[2])
        insertions += _as_int(parts[0])
        deletions += _as_int(parts[1])
    return paths, insertions, deletions, i


def _as_int(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _top_paths(commits: list[CommitSummary]) -> list[PathSummary]:
    touched = Counter(path for item in commits for path in item.paths)
    rows = touched.most_common(20)
    return [PathSummary(path=path, touches=count) for path, count in rows]


def _is_generated_path(path: str) -> bool:
    lowered = path.lower()
    prefixes = (
        "metrics/",
        "datasets/",
        "logs/",
        "output-results/",
        "cache-directory/",
    )
    return lowered.startswith(prefixes)
