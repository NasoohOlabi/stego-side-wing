"""Pipeline paths honor WORKFLOW_LLM_BACKEND (mocked LLM / crawl)."""

import asyncio
from pathlib import Path
from typing import Any

import dotenv
import pytest
from pydantic import BaseModel, Field

import infrastructure.config as infra_config
import pipelines.scraper as scraper_mod
from pipelines import ai_analyze
from workflows.adapters.llm import LLMAdapter


class _MiniSchema(BaseModel):
    title: str = Field(...)
    summary: str = Field(...)


def test_structured_dict_from_llm_plain_json() -> None:
    raw = '{"title": "a", "summary": "b"}'
    out = scraper_mod.parse_structured_llm_schema_text(raw, _MiniSchema)
    assert out == {"title": "a", "summary": "b"}


def test_structured_dict_from_llm_fenced() -> None:
    raw = '```json\n{"title": "x", "summary": "y"}\n```'
    out = scraper_mod.parse_structured_llm_schema_text(raw, _MiniSchema)
    assert out == {"title": "x", "summary": "y"}


def test_llm_topic_list_uses_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(self: LLMAdapter, *a: object, **kw: object) -> str:
        assert kw.get("provider") in ("gemini", "lm_studio")
        return 'Prefix ["q1", "q2"] suffix'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake)
    topics = ai_analyze.llm_topic_list_from_article_json('{"x":1}')
    assert topics == ["q1", "q2"]


@pytest.fixture
def clear_llm_for_scraper(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "WORKFLOW_LLM_BACKEND",
        "GOOGLE_PALM_API_KEY",
        "GOOGLE_AI_API_KEYS",
        "GOOGLE_AI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    loaded: dict[str, str | None] = {}
    if infra_config.ENV_FILE_PATH.exists():
        loaded = dict(dotenv.dotenv_values(str(infra_config.ENV_FILE_PATH)))
    for key in (
        "WORKFLOW_LLM_BACKEND",
        "GOOGLE_PALM_API_KEY",
        "GOOGLE_AI_API_KEYS",
        "GOOGLE_AI_API_KEY",
    ):
        loaded.pop(key, None)
    monkeypatch.setattr(infra_config, "_dotenv_values_cache", loaded)


@pytest.mark.usefixtures("clear_llm_for_scraper")
def test_extract_structured_google_backend_uses_llm_after_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")

    class _FakeMd:
        fit_markdown = "# Page\n\nBody paragraph."
        raw_markdown = ""
        markdown_with_citations = ""

    class _FakeResult:
        success = True
        error_message = ""
        extracted_content = None
        _markdown = _FakeMd()

    class _FakeCrawler:
        def __init__(self, verbose: bool = False) -> None:
            pass

        async def __aenter__(self) -> "_FakeCrawler":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def arun(self, url: str, config: Any) -> _FakeResult:
            return _FakeResult()

    def fake_llm(self: LLMAdapter, *a: object, **kw: object) -> str:
        return '{"title": "T", "summary": "S1. S2."}'

    monkeypatch.setattr(scraper_mod, "AsyncWebCrawler", _FakeCrawler)
    monkeypatch.setattr(LLMAdapter, "call_llm", fake_llm)

    async def _run() -> dict[str, Any]:
        out = await scraper_mod.extract_structured_data(
            url="https://example.test/page",
            schema=_MiniSchema,
            model_name="mistral-nemo-instruct-2407-abliterated",
            instruction="Extract.",
        )
        assert out is not None
        assert isinstance(out, dict)
        return out

    result = asyncio.run(_run())
    assert result.get("title") == "T"
    assert result.get("summary") == "S1. S2."
