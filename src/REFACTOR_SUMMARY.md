# Refactor Summary

This document summarizes the structural refactoring of the `src/` directory.

## Current notes (2026)

- Tests live under `src/tests/` (many `test_api_v1_*.py`, pipeline, runner, codec modules)—not only `test_parity.py`.
- **Layering** and validation checklists: repo root [`docs/architecture-layers.md`](../docs/architecture-layers.md), [`docs/validation-per-phase.md`](../docs/validation-per-phase.md).
- **`services/workflow_facade.py`**: re-exports workflow runner + prompt/protocol helpers for `app` so routes do not import deep `workflows` modules.
- **`workflows/utils/angles_llm_config.py`**: angle LLM prompts and model id defaults; `gen_angles` and legacy `angle_runner` both use it (single source of truth).
- **`workflows/runner_orchestration_utils.py`**: research breakdown, double-process FS claims, live-sim stego/receiver pair, angle normalization—extracted from `runner.py` for clarity.

## New Structure

```
src/
├── app/                    # Flask application layer
│   ├── app_factory.py      # Application factory
│   ├── routes/             # Route blueprints by domain
│   │   ├── posts_routes.py
│   │   ├── search_routes.py
│   │   ├── analysis_routes.py
│   │   ├── semantic_routes.py
│   │   ├── angles_routes.py
│   │   └── kv_routes.py
│   └── schemas/            # Request/response validation
│       └── validators.py
├── services/               # Business logic layer
│   ├── posts_service.py
│   ├── search_service.py
│   ├── analysis_service.py
│   ├── semantic_service.py
│   ├── angles_service.py
│   └── kv_service.py
├── integrations/           # External API clients
│   ├── news_api.py
│   ├── duckduckgo_api.py
│   ├── scrapingdog_api.py
│   └── lumen_api.py
├── infrastructure/        # Shared utilities
│   ├── cache.py           # Caching utilities
│   ├── config.py          # Configuration management
│   └── event_loop.py      # Event loop management
├── content_acquisition/    # Scraping, headless fetch, angles LLM
│   ├── ai_analyze.py
│   ├── headless_browser_analyzer.py
│   ├── scraper.py
│   └── angles/
│       └── angle_runner.py
├── scripts/               # CLI scripts
│   └── nest.py
├── util/                  # Backward compatibility shims
│   └── __init__.py        # Re-exports from integrations/
├── API.py                 # Compatibility entrypoint
└── tests/                 # Test suite
    └── test_parity.py
```

## Key Changes

### 1. Infrastructure Consolidation
- **`infrastructure/cache.py`**: Centralized caching utilities (`deterministic_hash_sha256`, `read_json_cache`, `write_json_cache`)
- **`infrastructure/config.py`**: Centralized configuration (`get_env`, `get_env_required`, `STEPS`, `POSTS_DIRECTORY`)
- **`infrastructure/event_loop.py`**: Re-exports from `event_loop_manager` for consistency

### 2. Service Layer Extraction
Business logic extracted from route handlers into service modules:
- **`services/posts_service.py`**: Post listing, retrieval, saving
- **`services/search_service.py`**: Search API wrappers (News, Google, Bing, Ollama)
- **`services/analysis_service.py`**: File processing and URL content fetching
- **`services/semantic_service.py`**: Semantic search and similarity matching
- **`services/angles_service.py`**: Angles analysis
- **`services/kv_service.py`**: Key-value store operations

### 3. Route Organization
Routes organized by domain into blueprints:
- **`app/routes/posts_routes.py`**: `/posts_list`, `/get_post`, `/save_post`, `/save_object`, `/save-json`
- **`app/routes/search_routes.py`**: `/search`, `/google_search`, `/bing_search`, `/ollama_search`
- **`app/routes/analysis_routes.py`**: `/process_file`, `/fetch_url_content`, `/fetch_url_content_crawl4ai`
- **`app/routes/semantic_routes.py`**: `/semantic_search`, `/needle_finder`, `/needle_finder_batch`
- **`app/routes/angles_routes.py`**: `/angles/analyze`
- **`app/routes/kv_routes.py`**: `/set`, `/get/<k>`

### 4. Integration Migration
External API clients moved from `util/` to `integrations/`:
- `util/newsApi.py` → `integrations/news_api.py`
- `util/DuckDuckApi.py` → `integrations/duckduckgo_api.py`
- `util/sdg.py` → `integrations/scrapingdog_api.py`
- `util/LumenApi.py` → `integrations/lumen_api.py`

Backward compatibility maintained via `util/__init__.py` shims.

### 5. Content acquisition package
Data processing modules live under `content_acquisition/`:
- `ai_analyze.py` → `content_acquisition/ai_analyze.py`
- `headless_browser_analyzer.py` → `content_acquisition/headless_browser_analyzer.py`
- `scraper.py` → `content_acquisition/scraper.py`
- `angles/angle_runner.py` → `content_acquisition/angles/angle_runner.py`

### 6. Application Factory
- **`app/app_factory.py`**: Centralized Flask app creation with blueprint registration
- **`API.py`**: Compatibility entrypoint that uses the new app factory

## Dependency Flow

```
routes → services → content_acquisition/integrations
         ↓
    infrastructure
```

Routes are thin adapters that:
1. Validate requests using `app/schemas/validators.py`
2. Call service layer functions
3. Return JSON responses

Services contain business logic and orchestrate:
- Pipeline modules for data processing
- Integration modules for external APIs
- Infrastructure modules for shared utilities

## Backward Compatibility

- **`API.py`**: Maintains the same entrypoint, now a thin wrapper over `app_factory`
- **`util/__init__.py`**: Provides import shims for old `util.*` imports

## Testing

Basic parity tests added in `tests/test_parity.py` to verify:
- Route structure and status codes
- Request/response formats
- Error handling

## Migration Notes

1. **Import Updates**:
   - `from util.newsApi import ...` → `from integrations.news_api import ...`
   - `from ai_analyze import ...` → `from content_acquisition.ai_analyze import ...`
   - `from headless_browser_analyzer import ...` → `from content_acquisition.headless_browser_analyzer import ...`

2. **Configuration**: Use `infrastructure.config` for:
   - Environment variables: `get_env()`, `get_env_required()`
   - Constants: `STEPS`, `POSTS_DIRECTORY`

3. **Caching**: Use `infrastructure.cache` for:
   - Hashing: `deterministic_hash_sha256()`
   - Cache I/O: `read_json_cache()`, `write_json_cache()`

## Next Steps

1. Keep deleting remaining duplicates after each import/test verification pass
2. Add comprehensive unit tests for services
3. Add integration tests for external APIs
4. Consider adding request/response models using Pydantic
5. Add logging configuration
6. Add API documentation (OpenAPI/Swagger)
