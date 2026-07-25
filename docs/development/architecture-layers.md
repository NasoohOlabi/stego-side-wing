# Layer boundaries

This document defines **allowed import direction** so `app`, `services`, `workflows`, `integrations`, and `content_acquisition` stay testable and navigable.

## Dependency graph (allowed)

```text
app (Flask routes, HTTP)
  → services (use-cases, facades, process-wide helpers)
      → workflows (runner, pipelines, adapters, workflow utils)
      → integrations (vendor HTTP clients)
  → infrastructure (config, logging, cache primitives)

workflows
  → integrations (optional; prefer via services if the call is API-shaped)
  → infrastructure

integrations
  → infrastructure

content acquisition (src/content_acquisition)
  → workflows (allowed: reuse adapters, config, shared utils)
  → infrastructure
  → integrations (HTTP helpers)
```

## Rules

1. **`app` must not import `workflows.*` directly** except through **`services.workflow_facade`** (or other `services` modules). This keeps HTTP wiring thin and preserves a single place to mock workflow entrypoints in tests.
2. **`workflows` must not import from `app`**.
3. **Avoid new `workflows` ↔ `content_acquisition` cycles**: workflow code should not depend on legacy acquisition modules for constants or prompts; shared angle LLM defaults live under **`workflows.utils.angles_llm_config`**. Legacy `angle_runner` may import that module so one source of truth remains.
4. **`services` is the home for facades** that exist only to satisfy layer (1), not for duplicating workflow business logic.
5. **`workflows` must not import `services`.** Where workflow code needs a use case, it declares a Protocol in **`workflows/ports.py`** and receives an implementation by constructor injection. Implementations live in `services` and satisfy the Protocol structurally, so neither module imports the other.

## Ports

`workflows/ports.py` states what workflow code needs from the outside:

| Port | Implementation |
|---|---|
| `LocalBackendPort` | `services.workflow_backend_client.LocalBackendClient` |
| `RemoteBackendPort` | `workflows.adapters.backend_api.HttpBackendClient` |

`BackendAPIAdapter` takes both via `local=` / `http=`. Omitting them builds the production
defaults, so no existing call site changed.

### Known remaining exceptions

Three deferred (function-local) `workflows -> services` imports survive, each for a stated
reason. They are pinned by the `import-linter` contract so no *new* ones can appear:

| Site | Why |
|---|---|
| `adapters/backend_api.py` `_build_default_local_client` | The one composition point where a zero-argument `BackendAPIAdapter()` still needs a concrete local client. Deferred so importing the module does not pull in the service layer. |
| `adapters/content.py` `fetch_url_content_crawl4ai` | Deferred because crawl4ai is a heavy optional import. |
| `pipelines/research.py` `search_bing` | Resolved per call so tests can monkeypatch `services.search_service.search_bing`. |

Deferred imports are also what lets tests patch dotted service paths: binding a service
function at module import freezes the original and silently ignores `monkeypatch.setattr`.

## Compatibility

- `app.routes.api_v1_routes` continues to **re-export** symbols tests monkeypatch (`runner`, `workflow_llm_prompts_path`, etc.). Those names are wired from the facade or underlying modules unchanged in behavior.
