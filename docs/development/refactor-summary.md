# Refactor Summary

**Historical.** This records the 2024-era split of `src/` into
app / services / integrations / infrastructure. The tree under "New Structure" was already
out of date when this note was added and is kept only as a record of the intent at the time
— it predates `workflows/` entirely, lists an `integrations/lumen_api.py` that was never
created, and shows a single test file.

For the structure as it actually is, read
[`architecture-layers.md`](architecture-layers.md) (layer rules, ports, and the enforced
`.importlinter` contract) and [`refactor-baseline.md`](refactor-baseline.md) (measured
sizes, gates, and the maintainability work done since).

## Current notes (2026)

- Tests live under `src/tests/` (many `test_api_v1_*.py`, pipeline, runner, codec modules) — not only `test_parity.py`.
- **Layering** and validation checklists: [`architecture-layers.md`](architecture-layers.md), [`validation-per-phase.md`](validation-per-phase.md).
- **`services/workflow_facade.py`**: re-exports workflow runner + prompt/protocol helpers for `app` so routes do not import deep `workflows` modules.
- **`workflows/utils/angles_llm_config.py`**: angle LLM prompts and model id defaults; `gen_angles` and legacy `angle_runner` both use it (single source of truth).
- **`workflows/runner_orchestration_utils.py`**: research breakdown, double-process FS claims, live-sim stego/receiver pair, angle normalization — extracted from `runner.py` for clarity.
- **`infrastructure/mappings.py`**: `dict_field`/`list_field` coerce a possibly-absent JSON key to `{}`/`[]` in one call, replacing the double-`.get()` `isinstance` idiom that defeats type-narrowing. Shared by `services/` and `scripts/`.

## New Structure

```
src/
â”œâ”€â”€ app/                    # Flask application layer
â”‚   â”œâ”€â”€ app_factory.py      # Application factory
â”‚   â”œâ”€â”€ routes/             # Route blueprints by domain
â”‚   â”‚   â”œâ”€â”€ posts_routes.py
â”‚   â”‚   â”œâ”€â”€ search_routes.py
â”‚   â”‚   â”œâ”€â”€ analysis_routes.py
â”‚   â”‚   â”œâ”€â”€ semantic_routes.py
â”‚   â”‚   â”œâ”€â”€ angles_routes.py
â”‚   â”‚   â””â”€â”€ kv_routes.py
â”‚   â””â”€â”€ schemas/            # Request/response validation
â”‚       â””â”€â”€ validators.py
â”œâ”€â”€ services/               # Business logic layer
â”‚   â”œâ”€â”€ posts_service.py
â”‚   â”œâ”€â”€ search_service.py
â”‚   â”œâ”€â”€ analysis_service.py
â”‚   â”œâ”€â”€ semantic_service.py
â”‚   â”œâ”€â”€ angles_service.py
â”‚   â””â”€â”€ kv_service.py
â”œâ”€â”€ integrations/           # External API clients
â”‚   â”œâ”€â”€ news_api.py
â”‚   â”œâ”€â”€ duckduckgo_api.py
â”‚   â”œâ”€â”€ scrapingdog_api.py
â”‚   â””â”€â”€ lumen_api.py
â”œâ”€â”€ infrastructure/        # Shared utilities
â”‚   â”œâ”€â”€ cache.py           # Caching utilities
â”‚   â”œâ”€â”€ config.py          # Configuration management
â”‚   â””â”€â”€ event_loop.py      # Event loop management
â”œâ”€â”€ content_acquisition/    # Scraping, headless fetch, angles LLM
â”‚   â”œâ”€â”€ ai_analyze.py
â”‚   â”œâ”€â”€ headless_browser_analyzer.py
â”‚   â”œâ”€â”€ scraper.py
â”‚   â””â”€â”€ angles/
â”‚       â””â”€â”€ angle_runner.py
â”œâ”€â”€ scripts/               # CLI scripts
â”‚   â””â”€â”€ nest.py
â”œâ”€â”€ util/                  # Backward compatibility shims
â”‚   â””â”€â”€ __init__.py        # Re-exports from integrations/
â”œâ”€â”€ API.py                 # Compatibility entrypoint
â””â”€â”€ tests/                 # Test suite
    â””â”€â”€ test_parity.py
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
- `util/newsApi.py` â†’ `integrations/news_api.py`
- `util/DuckDuckApi.py` â†’ `integrations/duckduckgo_api.py`
- `util/sdg.py` â†’ `integrations/scrapingdog_api.py`
- `util/LumenApi.py` â†’ `integrations/lumen_api.py`

Backward compatibility maintained via `util/__init__.py` shims.

### 5. Content acquisition package
Data processing modules live under `content_acquisition/`:
- `ai_analyze.py` â†’ `content_acquisition/ai_analyze.py`
- `headless_browser_analyzer.py` â†’ `content_acquisition/headless_browser_analyzer.py`
- `scraper.py` â†’ `content_acquisition/scraper.py`
- `angles/angle_runner.py` â†’ `content_acquisition/angles/angle_runner.py`

### 6. Application Factory
- **`app/app_factory.py`**: Centralized Flask app creation with blueprint registration
- **`API.py`**: Compatibility entrypoint that uses the new app factory

## Dependency Flow

```
routes â†’ services â†’ content_acquisition/integrations
         â†“
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
   - `from util.newsApi import ...` â†’ `from integrations.news_api import ...`
   - `from ai_analyze import ...` â†’ `from content_acquisition.ai_analyze import ...`
   - `from headless_browser_analyzer import ...` â†’ `from content_acquisition.headless_browser_analyzer import ...`

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

