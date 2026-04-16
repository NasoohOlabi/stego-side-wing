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

## Compatibility

- `app.routes.api_v1_routes` continues to **re-export** symbols tests monkeypatch (`runner`, `workflow_llm_prompts_path`, etc.). Those names are wired from the facade or underlying modules unchanged in behavior.
