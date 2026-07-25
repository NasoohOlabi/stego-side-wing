"""Constructor injection seams for the workflow pipelines.

These exist so tests and the app factory can substitute collaborators without
``__new__``-ing a half-built object. Zero-argument construction must keep building the
production defaults, so both directions are covered here.
"""

from __future__ import annotations

from typing import Any

from workflows.adapters.backend_api import BackendAPIAdapter, HttpBackendClient
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.receiver import ReceiverPipeline
from workflows.pipelines.stego import StegoPipeline
from workflows.runner import WorkflowRunner


class _StubLocalClient:
    """Minimal stand-in for LocalBackendClient."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def needle_finder_batch(self, needles: list[Any], haystack: list[str]) -> dict[str, Any]:
        self.calls.append(("needle_finder_batch", needles))
        return {"results": [{"best_match": "stubbed"} for _ in needles]}


def test_backend_adapter_uses_injected_local_client() -> None:
    stub = _StubLocalClient()

    def unreachable_post(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP client should not be reached")

    adapter = BackendAPIAdapter(
        base_url="http://backend.invalid",
        local=stub,  # pyright: ignore[reportArgumentType]
        http=HttpBackendClient("http://backend.invalid"),
    )

    assert adapter.local is stub
    result = adapter._needle_finder_batch_local(needles=["a"], haystack=["x"])
    assert result["results"][0]["best_match"] == "stubbed"
    assert stub.calls == [("needle_finder_batch", ["a"])]


def test_decode_pipeline_accepts_injected_collaborators(fake_llm, fake_backend) -> None:
    llm = fake_llm(["decoded"])
    backend = fake_backend()

    pipeline = DecodePipeline(backend=backend, llm=llm)  # pyright: ignore[reportArgumentType]

    assert pipeline.llm is llm
    assert pipeline.backend is backend


def test_stego_pipeline_accepts_injected_collaborators(fake_llm, fake_backend) -> None:
    llm = fake_llm(["candidate"])
    backend = fake_backend()
    decode = DecodePipeline(backend=backend, llm=llm)  # pyright: ignore[reportArgumentType]

    pipeline = StegoPipeline(
        backend=backend,  # pyright: ignore[reportArgumentType]
        llm=llm,  # pyright: ignore[reportArgumentType]
        decode_pipeline=decode,
    )

    assert pipeline.llm is llm
    assert pipeline.backend is backend
    assert pipeline.decode_pipeline is decode


def test_runner_accepts_injected_pipelines(fake_llm, fake_backend) -> None:
    backend = fake_backend()
    decode = DecodePipeline(backend=backend, llm=fake_llm())  # pyright: ignore[reportArgumentType]
    receiver = ReceiverPipeline(decode=decode)

    runner = WorkflowRunner(
        backend=backend,  # pyright: ignore[reportArgumentType]
        decode=decode,
        receiver=receiver,
    )

    assert runner.backend is backend
    assert runner.decode is decode
    assert runner.receiver is receiver
    # Everything not injected still gets a real production instance.
    assert isinstance(runner.stego, StegoPipeline)


def test_zero_argument_construction_still_builds_defaults() -> None:
    pipeline = DecodePipeline()

    assert isinstance(pipeline.backend, BackendAPIAdapter)
    assert pipeline.llm is not None
