"""Deterministic, blinded task slates for offline Codex judgments."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from pydantic import BaseModel, validate_call


class JudgeTask(BaseModel):
    task_id: str
    pair_id: str
    post_id: str
    metric: str
    method: str
    prompt_fields: dict[str, Any]
    answer: dict[str, Any]


class StandoutTask(JudgeTask):
    pass


class WeakLinkTask(JudgeTask):
    pass


class PointwiseTask(JudgeTask):
    pass


class AttributionTask(JudgeTask):
    pass


class RegisterTask(JudgeTask):
    pass


def _seed(metric: str, post_id: str, pair_id: str) -> int:
    return int(hashlib.sha256(f"{metric}:{post_id}:{pair_id}".encode()).hexdigest()[:8], 16)


def _clean_comments(post: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(text for text in _walk(post.get("comments")) if _valid(text)))


def _walk(items: Any) -> list[str]:
    result: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                body = item.get("body")
                if isinstance(body, str):
                    result.append(" ".join(body.split()))
                result.extend(_walk(item.get("replies")))
    return result


def _valid(text: str) -> bool:
    return (
        text.lower() not in {"[deleted]", "[removed]"}
        and len(text) >= 15
        and len(text.split()) <= 200
    )


def load_post_comments(dataset_dir: Path, post_id: str) -> tuple[dict[str, Any], list[str]]:
    post = __import__("json").loads((dataset_dir / f"{post_id}.json").read_text(encoding="utf-8"))
    return post, _clean_comments(post) if isinstance(post, dict) else []


def _base(
    metric: str,
    pair: dict[str, dict[str, Any]],
    method: str,
    fields: dict[str, Any],
    answer: dict[str, Any],
) -> dict[str, Any]:
    row = pair.get(method, pair["our_method"])
    return {
        "task_id": hashlib.sha256(f"{metric}:{row['pair_id']}:{method}".encode()).hexdigest(),
        "pair_id": str(row["pair_id"]),
        "post_id": str(row["post_id"]),
        "metric": metric,
        "method": method,
        "prompt_fields": fields,
        "answer": answer,
    }


def _post_fields(post: dict[str, Any]) -> dict[str, str]:
    return {
        "subreddit": str(post.get("subreddit") or post.get("subreddit_name") or "unknown"),
        "post_title": str(post.get("title") or ""),
        "post_body": str(post.get("selftext") or ""),
    }


@validate_call
def build_standout(
    pair: dict[str, dict[str, Any]], post: dict[str, Any], comments: list[str]
) -> list[StandoutTask]:
    row = pair["our_method"]
    rng = random.Random(_seed("standout", str(row["post_id"]), str(row["pair_id"])))
    distractors = rng.sample(comments, k=min(9, len(comments)))
    slot = rng.randrange(len(distractors) + 1)
    tasks = []
    for method in ("our_method", "zlg"):
        slate = distractors.copy()
        slate.insert(slot, str(pair[method].get("stegotext") or ""))
        tasks.append(
            StandoutTask(
                **_base(
                    "standout",
                    pair,
                    method,
                    {**_post_fields(post), "numbered_comments": _numbered(slate)},
                    {"inserted_index": slot + 1},
                )
            )
        )
    return tasks


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(items, 1))


@validate_call
def build_pointwise(
    metric: str, pair: dict[str, dict[str, Any]], post: dict[str, Any], comments: list[str]
) -> list[PointwiseTask]:
    row = pair["our_method"]
    rng = random.Random(_seed(metric, str(row["post_id"]), str(row["pair_id"])))
    human = rng.choice(comments)
    context = [x for x in comments if x != human][:5]
    fields = {**_post_fields(post), "numbered_context_comments": _numbered(context)}
    out = []
    for method, candidate in [
        ("our_method", pair["our_method"]["stegotext"]),
        ("zlg", pair["zlg"]["stegotext"]),
        ("human", human),
    ]:
        out.append(
            PointwiseTask(
                **_base(
                    metric,
                    pair,
                    method,
                    {**fields, "candidate": str(candidate)},
                    {"human": method == "human"},
                )
            )
        )
    return out


@validate_call
def build_weak_link(
    pair: dict[str, dict[str, Any]], post: dict[str, Any], comments: list[str]
) -> WeakLinkTask:
    row = pair["our_method"]
    rng = random.Random(_seed("weak_link", str(row["post_id"]), str(row["pair_id"])))
    human = rng.choice(comments)
    candidates = [
        ("human", human),
        ("our_method", pair["our_method"]["stegotext"]),
        ("zlg", pair["zlg"]["stegotext"]),
    ]
    rng.shuffle(candidates)
    fields = {
        **_post_fields(post),
        "numbered_context_comments": _numbered([x for x in comments if x != human][:5]),
    }
    fields.update({f"candidate_{i}": str(value) for i, (_, value) in enumerate(candidates, 1)})
    return WeakLinkTask(
        **_base("weak_link", pair, "paired", fields, {"methods": [x[0] for x in candidates]})
    )


@validate_call
def build_register(
    pair: dict[str, dict[str, Any]], post: dict[str, Any], comments: list[str]
) -> list[RegisterTask]:
    return [
        RegisterTask(**item.model_dump())
        for item in build_pointwise("register", pair, post, comments)
    ]


@validate_call
def build_attribution(
    pair: dict[str, dict[str, Any]],
    post: dict[str, Any],
    comments: list[str],
    corpus: list[dict[str, Any]],
) -> list[AttributionTask]:
    row = pair["our_method"]
    rng = random.Random(_seed("attribution", str(row["post_id"]), str(row["pair_id"])))
    alternatives = [x for x in corpus if str(x.get("post_id")) != str(row["post_id"])]
    same_sub = [
        x for x in alternatives if _post_fields(x)["subreddit"] == _post_fields(post)["subreddit"]
    ]
    pool = same_sub if len(same_sub) >= 3 else alternatives
    slate = [*rng.sample(pool, k=min(3, len(pool))), post]
    rng.shuffle(slate)
    true_index = slate.index(post) + 1
    fields = {f"sub_{i}": _post_fields(item)["subreddit"] for i, item in enumerate(slate, 1)}
    fields.update(
        {f"title_{i}": _post_fields(item)["post_title"] for i, item in enumerate(slate, 1)}
    )
    fields.update(
        {f"snippet_{i}": _post_fields(item)["post_body"][:700] for i, item in enumerate(slate, 1)}
    )
    return [
        AttributionTask(
            **_base(
                "attribution",
                pair,
                method,
                {**fields, "candidate": str(candidate)},
                {"thread_index": true_index},
            )
        )
        for method, candidate in [
            ("our_method", pair["our_method"]["stegotext"]),
            ("zlg", pair["zlg"]["stegotext"]),
            ("human", rng.choice(comments)),
        ]
    ]
