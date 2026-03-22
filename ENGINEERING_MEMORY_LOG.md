# Engineering Memory Log

Updated: 2026-03-22

## Done

### Runtime and Infrastructure

- `8c5b6d9` Improved runtime stability and memory bounds.
- Reduced client polling pressure on results pages.
- Added watchdog-style polling when WebSocket is healthy.
- Ensured timers and WebSocket handles are disposed on page exit.
- Added memory bounds for in-memory fallback stores in:
  - `app/api/routers/_task_store.py`
  - `app/core/progress.py`
- Fixed `PLAYWRIGHT_BROWSERS_PATH` default to `/ms-playwright`.

### Ops and Railway Stability

- `e17d001` Added `/api/ops/status` and hardened worker startup.
- Removed runtime Playwright installation from `entrypoint.sh` for `llm-worker`.
- Added operational visibility for:
  - Redis health
  - worker heartbeat
  - queue depth
  - process RSS
  - task/progress memory stats

### Redis Partitioning

- `56d495e` Partitioned Redis workloads and hardened ops visibility.
- Added dedicated env support for:
  - `TASK_STORE_REDIS_URL`
  - `PROGRESS_REDIS_URL`
  - `RATE_LIMIT_REDIS_URL`
  - `LLM_CRAWLER_REDIS_URL`
  - `CELERY_BROKER_URL`
  - `CELERY_RESULT_BACKEND`
- Added prefixes:
  - `TASK_STORE_REDIS_PREFIX`
  - `PROGRESS_REDIS_PREFIX`
  - `RATE_LIMIT_REDIS_PREFIX`
  - `LLM_CRAWLER_REDIS_PREFIX`

### Maintenance / Cleanup

- `8b651a1` Added maintenance mode and cleanup runner.
- Added `SERVICE_MODE=maintenance`.
- Added `scripts/run_maintenance.py` for:
  - stale report artifact cleanup
  - expired LLM job cleanup
  - memory cleanup
  - optional GC

### Frontend Decomposition

- `8b651a1` Extracted batch renderer into `app/static/js/task-progress-batch.js`.
- `f4136a4` Extracted unified renderer into `app/static/js/task-progress-unified.js`.
- `2ecb120` Extracted runtime helpers into `app/static/js/task-progress-runtime.js`.
- `task-progress.js` is now smaller and less fragile.

### Robots Architecture Split

- `f4136a4` Extracted network / redirect / gzip layer into `app/api/routers/robots_fetch.py`.
- `409643f` Extracted parser / analysis layer into `app/api/routers/robots_analysis.py`.
- `app/api/routers/robots.py` remains a compatibility facade.

### Generic Task / Progress Payload Compaction

- `b1ce01d` Added storage-level compaction for heavy task payloads in `app/api/routers/_task_store.py`.
- Large payloads now drop only heavy debug/blob fields such as:
  - `raw_html`
  - `rendered_html`
  - `page_source`
  - `dom_html`
  - `full_html`
  - `network_log`
  - `raw_response`
- Long `console_log.errors` / `console_log.warnings` are truncated.
- Added `storage_meta` with:
  - original bytes
  - stored bytes
  - removed fields
  - removed field count

- Added progress payload compaction in `app/core/progress.py`.
- Heavy `extra` fields are compacted before storing in Redis or memory fallback.

- Added settings:
  - `TASK_STORE_COMPACT_THRESHOLD_BYTES`
  - `PROGRESS_COMPACT_THRESHOLD_BYTES`

### LLM Crawler Payload Compaction

- `b1ce01d` Strengthened `app/tools/llmCrawler/queue.py`.
- LLM job record compaction now trims:
  - `content_segments`
  - `segment_tree`
  - `main_content_nodes`
  - `noise_nodes`
  - `chunk_ranking_debug`
  - `recommendation_diagnostics.{strengths,warnings,actions}`
- Existing `include_raw_html` / `include_rendered_html` logic remains respected.
- Added `storage_meta` counters for omitted debug sections.

### Site Audit Pro Payload Compaction

- Existing chunk manifest flow was already in place.
- `a660498` added nested payload compaction for inline `Site Audit Pro` results.
- Inline `pages` now trim only heavy nested fields:
  - `filler_phrases`
  - `ai_markers_list`
  - `top_keywords`
  - `top_terms`
  - `near_duplicate_urls`
  - `semantic_links`
  - `broken_internal_targets`
  - `keyword_density_profile`
- Inline `pipeline.semantic_linking_map` now trims:
  - `supporting_urls`
  - `source_terms`
  - `target_terms`
- Added nested compaction metadata:
  - row `_storage_meta`
  - `artifacts.nested_omitted_counts`

### Regression Coverage Added

- Added / extended tests for:
  - task payload compaction
  - progress payload compaction
  - LLM queue compaction
  - Site Audit Pro artifact compaction
  - browser critical flows
  - unified UI guards
  - robots / sitemap regressions

## Current Stable State

- Redis health and prefixes are visible in `/api/ops/status`.
- `llm-worker` startup no longer depends on runtime browser installation.
- Task, progress, LLM, and Site Audit Pro payloads are more memory-bounded.
- Major frontend and robots monoliths have been partially decomposed.

## Remaining Work

### Highest Priority

1. Add payload-size metrics to `/api/ops/status`.
   - Show compaction counters and recent compacted byte totals.
   - Expose task/progress/llm/site-pro compaction activity.

2. Add logical Redis DB split in Railway.
   - Move from prefix-only isolation to separate DB indexes.
   - Keep current env structure; only change values.

3. Add queue / duration / payload observability.
   - Track queue wait time.
   - Track result size before/after compaction.
   - Track export generation duration.

### Site Audit Pro

4. Consider export-aware deep artifact loading.
   - XLSX currently depends on inline `pages`.
   - If full pages are loaded from manifest for export, inline payload can be reduced further.

5. Add explicit payload size guards for `Site Audit Pro` before `create_task_result`.
   - Current compaction is good, but no tool-specific size budget is enforced yet.

### LLM Crawler

6. Continue targeted compaction in LLM results.
   - Safely reduce:
     - `structured_data`
     - `detectors`
     - `quality_gates.checks`
     - `recommendation_diagnostics`
   - Only if UI / DOCX / HTML reports do not depend on full detail.

7. Add worker-side metrics for:
   - compressed vs uncompressed job size
   - compaction hit rate
   - dropped debug sections

### Frontend

8. Continue splitting `app/static/js/task-progress.js`.
   - Remaining target: per-tool renderers and shared builders / formatters.

9. Add artifact-aware UI hints when payload is compacted.
   - Not only `Site Pro`, but also task-store-level `storage_meta`.

### Robots / Backend Architecture

10. Continue splitting `robots.py` facade.
   - Candidate next steps:
     - sitemap discovery helpers
     - bot-check logic
     - router-only facade

### Maintenance / Ops

11. Add scheduled maintenance verification.
   - Log deleted file counts
   - log expired jobs cleanup counts
   - optional retention dashboards

12. Add post-deploy operational smoke for:
   - `/health`
   - `/api/ops/status`
   - batch flow
   - unified flow
   - one export

## Railway Manual Follow-up

- Keep these env vars in `web` and `Worker LLM`:
  - `TASK_STORE_REDIS_URL=${{REDIS_URL}}`
  - `PROGRESS_REDIS_URL=${{REDIS_URL}}`
  - `RATE_LIMIT_REDIS_URL=${{REDIS_URL}}`
  - `LLM_CRAWLER_REDIS_URL=${{REDIS_URL}}`
  - `CELERY_BROKER_URL=${{REDIS_URL}}`
  - `CELERY_RESULT_BACKEND=${{REDIS_URL}}`
  - `TASK_STORE_REDIS_PREFIX=task`
  - `PROGRESS_REDIS_PREFIX=task_progress`
  - `RATE_LIMIT_REDIS_PREFIX=ratelimit`
  - `LLM_CRAWLER_REDIS_PREFIX=llmCrawler`

- Keep this env var in `Worker LLM`:
  - `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`

- Maintenance service should use:
  - `SERVICE_MODE=maintenance`
  - optional `MAINTENANCE_STALE_REPORT_DAYS=7`
  - optional `MAINTENANCE_FORCE_GC=1`
