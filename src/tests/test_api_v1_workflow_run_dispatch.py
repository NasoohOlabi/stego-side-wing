"""Every WORKFLOW_COMMANDS entry must dispatch to its own runner method.

``POST /workflows/run`` validates the command against WORKFLOW_COMMANDS and then walks an
if/elif chain, whose ``else`` runs the full pipeline. A command that is listed but has no
branch therefore runs something completely different while the response still echoes the
requested command -- which is exactly what happened to ``stego-receiver-live``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.app_factory import create_app
from app.routes.api_v1.constants import WORKFLOW_COMMANDS

# Runner method each command must reach, and a minimal valid body for it.
COMMAND_DISPATCH: dict[str, tuple[str, dict[str, Any]]] = {
    "data-load": ("run_data_load", {}),
    "research": ("run_research", {}),
    "gen-angles": ("run_gen_angles", {}),
    "prep-until-google-quota-then-stego": (
        "run_prep_until_google_quota_then_stego",
        {"payload": "p", "tag": "t"},
    ),
    "double-process-new-post": ("run_double_process_new_post", {"sender_user_id": "u"}),
    "batch-angles-determinism": ("run_batch_angles_determinism", {"post_ids": ["p1"]}),
    "validate-post": ("validate_post_pipeline", {"post_id": "p1"}),
    "stego": ("run_stego", {"payload": "p"}),
    "decode": ("run_decode", {"stego_text": "x", "angles": [{"tangent": "t"}]}),
    "receiver": ("run_receiver", {"sender_user_id": "u", "post": {"id": "p1"}}),
    "stego-receiver-live": ("run_stego_receiver_live_sim", {"sender_user_id": "u"}),
    "gen-terms": ("run_gen_search_terms", {"post_id": "p1"}),
    "full": ("run_full_pipeline", {}),
}


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_dispatch_table_covers_every_supported_command() -> None:
    assert set(COMMAND_DISPATCH) == set(WORKFLOW_COMMANDS)


@pytest.mark.parametrize("command", sorted(COMMAND_DISPATCH))
def test_each_command_reaches_its_own_runner_method(
    client, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    method_name, extra_body = COMMAND_DISPATCH[command]
    called: list[str] = []

    # Record which runner method the route actually invokes.
    for candidate, _ in COMMAND_DISPATCH.values():
        monkeypatch.setattr(
            client.application.config["WORKFLOW_RUNNER"],
            candidate,
            (lambda name: lambda *a, **k: called.append(name) or {"ok": True})(candidate),
            raising=False,
        )

    response = client.post("/api/v1/workflows/run", json={"command": command, **extra_body})

    assert response.status_code == 200, response.get_json()
    assert called == [method_name], (
        f"command {command!r} dispatched to {called!r}, expected [{method_name!r}]"
    )
