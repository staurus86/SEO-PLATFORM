"""Site Audit Pro orchestration service."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .adapter import SiteAuditProAdapter
from .artifacts import SiteProArtifactStore

ProgressCallback = Optional[Callable[[int, str, Optional[Dict[str, Any]]], None]]
_stats_lock = threading.RLock()
_compaction_stats: Dict[str, Any] = {
    "compactions_total": 0,
    "original_bytes_total": 0,
    "stored_bytes_total": 0,
    "bytes_saved_total": 0,
    "last_compacted_at": None,
}


def _estimate_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _record_compaction(*, original_size: int, compacted_size: int) -> None:
    with _stats_lock:
        _compaction_stats["compactions_total"] = int(_compaction_stats["compactions_total"]) + 1
        _compaction_stats["original_bytes_total"] = int(_compaction_stats["original_bytes_total"]) + int(original_size)
        _compaction_stats["stored_bytes_total"] = int(_compaction_stats["stored_bytes_total"]) + int(compacted_size)
        _compaction_stats["bytes_saved_total"] = int(_compaction_stats["bytes_saved_total"]) + max(0, int(original_size) - int(compacted_size))
        _compaction_stats["last_compacted_at"] = datetime.now(timezone.utc).isoformat()


def get_site_pro_compaction_stats() -> Dict[str, Any]:
    with _stats_lock:
        stats = dict(_compaction_stats)
    stats["inline_limits"] = {
        "issues": SiteAuditProService._int_setting("SITE_AUDIT_PRO_INLINE_ISSUES_LIMIT", 200),
        "semantic_linking_map": SiteAuditProService._int_setting("SITE_AUDIT_PRO_INLINE_SEMANTIC_LIMIT", 200),
        "pages": SiteAuditProService._int_setting("SITE_AUDIT_PRO_INLINE_PAGES_LIMIT", 500),
    }
    return stats


class SiteAuditProService:
    def __init__(self) -> None:
        self.adapter = SiteAuditProAdapter()

    def run(
        self,
        *,
        url: str,
        task_id: str,
        mode: str = "quick",
        max_pages: int = 5,
        batch_mode: bool = False,
        batch_urls: Optional[List[str]] = None,
        extended_hreflang_checks: bool = False,
        progress_callback: ProgressCallback = None,
        use_proxy: bool = False,
    ) -> Dict[str, Any]:
        def notify(progress: int, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
            if callable(progress_callback):
                progress_callback(progress, message, meta)

        selected_mode = "full" if mode == "full" else "quick"
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()

        notify(5, "Preparing Site Audit Pro")
        if batch_mode:
            notify(20, "Collecting batch URL scope")
        else:
            notify(20, "Collecting crawl scope")
        notify(45, "Running scoring pipeline")
        def _adapter_progress(progress: int, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
            notify(progress, message, meta)
        normalized = self.adapter.run(
            url=url,
            mode=selected_mode,
            max_pages=max_pages,
            batch_mode=batch_mode,
            batch_urls=batch_urls or [],
            extended_hreflang_checks=extended_hreflang_checks,
            progress_callback=_adapter_progress,
            use_proxy=use_proxy,
        )
        notify(75, "Building normalized report payload")
        public_results = self.adapter.to_public_results(normalized)
        notify(85, "Preparing deep artifacts")
        self._attach_chunked_artifacts(
            task_id=task_id,
            mode=selected_mode,
            public_results=public_results,
        )
        notify(95, "Finalizing Site Audit Pro result")
        duration_ms = int((time.perf_counter() - t0) * 1000)

        summary = public_results.get("summary", {}) if isinstance(public_results, dict) else {}

        return {
            "task_type": "site_audit_pro",
            "url": url,
            "mode": selected_mode,
            "batch_mode": bool(batch_mode),
            "batch_urls_count": len(batch_urls or []),
            "extended_hreflang_checks": bool(extended_hreflang_checks),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": public_results,
            "meta": {
                "task_id": task_id,
                "service": "site_pro_service_v0",
                "started_at": started_at,
                "duration_ms": duration_ms,
                "pages_scanned": summary.get("total_pages", 0),
                "issues_total": summary.get("issues_total", 0),
            },
        }

    def _attach_chunked_artifacts(self, *, task_id: str, mode: str, public_results: Dict[str, Any]) -> None:
        """
        Persist heavy deep arrays as chunked JSONL and attach manifest to results.
        This keeps task payload predictable while preserving full details for download.
        """
        if not isinstance(public_results, dict):
            return

        pipeline = public_results.get("pipeline", {}) or {}
        issues = public_results.get("issues", []) or []
        semantic = pipeline.get("semantic_linking_map", []) or []
        pages = public_results.get("pages", []) or []

        # In quick mode we still emit artifacts when payload arrays are non-trivial.
        should_emit = (mode == "full") or (len(issues) > 100) or (len(semantic) > 100) or (len(pages) > 200)
        if not should_emit:
            return

        store = SiteProArtifactStore(task_id=task_id)
        manifest = {
            "task_id": task_id,
            "base_dir": str(store.root_dir),
            "chunks": [],
        }

        issue_rows = [
            {
                "url": row.get("url"),
                "severity": row.get("severity"),
                "code": row.get("code"),
                "title": row.get("title"),
                "details": row.get("details"),
            }
            for row in issues
        ]
        semantic_rows = [
            {
                "source_url": row.get("source_url"),
                "target_url": row.get("target_url"),
                "topic": row.get("topic"),
                "reason": row.get("reason"),
            }
            for row in semantic
        ]
        page_rows = [
            {
                "url": row.get("url"),
                "status_code": row.get("status_code"),
                "health_score": row.get("health_score"),
                "topic_label": row.get("topic_label"),
                "recommendation": row.get("recommendation"),
            }
            for row in pages
        ]

        manifest["chunks"].append(store.write_chunked_jsonl(name="issues", rows=issue_rows, chunk_size=500))
        manifest["chunks"].append(store.write_chunked_jsonl(name="semantic_map", rows=semantic_rows, chunk_size=500))
        manifest["chunks"].append(store.write_chunked_jsonl(name="pages", rows=page_rows, chunk_size=1000))

        results_artifacts = public_results.setdefault("artifacts", {})
        results_artifacts["chunk_manifest"] = manifest
        self._compact_inline_payload(public_results)

    @staticmethod
    def _int_setting(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            try:
                from app.config import settings

                raw = getattr(settings, name, None)
            except Exception:
                raw = None
        try:
            value = int(raw if raw is not None else default)
        except Exception:
            value = int(default)
        return max(1, value)

    @staticmethod
    def _limit_list(values: Any, limit: int) -> tuple[Any, int]:
        if not isinstance(values, list):
            return values, 0
        if len(values) <= limit:
            return values, 0
        return values[:limit], len(values) - limit

    def _compact_page_row(self, row: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, int]]:
        compacted = dict(row or {})
        omitted: Dict[str, int] = {}

        for key, limit in (
            ("filler_phrases", 12),
            ("ai_markers_list", 12),
            ("top_keywords", 12),
            ("top_terms", 12),
            ("near_duplicate_urls", 10),
            ("semantic_links", 20),
            ("broken_internal_targets", 20),
        ):
            limited, removed = self._limit_list(compacted.get(key), limit)
            compacted[key] = limited
            if removed > 0:
                omitted[key] = removed

        density_map = compacted.get("keyword_density_profile")
        if isinstance(density_map, dict) and len(density_map) > 20:
            ordered = sorted(
                density_map.items(),
                key=lambda item: float(item[1] or 0),
                reverse=True,
            )[:20]
            compacted["keyword_density_profile"] = dict(ordered)
            omitted["keyword_density_profile"] = len(density_map) - 20

        if omitted:
            compacted["_storage_meta"] = {
                "payload_compacted": True,
                "omitted_counts": omitted,
            }
        return compacted, omitted

    def _compact_semantic_row(self, row: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, int]]:
        compacted = dict(row or {})
        omitted: Dict[str, int] = {}
        for key, limit in (
            ("supporting_urls", 8),
            ("source_terms", 8),
            ("target_terms", 8),
        ):
            limited, removed = self._limit_list(compacted.get(key), limit)
            compacted[key] = limited
            if removed > 0:
                omitted[key] = removed
        if omitted:
            compacted["_storage_meta"] = {
                "payload_compacted": True,
                "omitted_counts": omitted,
            }
        return compacted, omitted

    def _compact_inline_payload(self, public_results: Dict[str, Any]) -> None:
        """
        Keep task payload small when chunk artifacts are available.
        Full records stay downloadable via manifest links.
        """
        original_size = _estimate_bytes(public_results)
        issues_limit = self._int_setting("SITE_AUDIT_PRO_INLINE_ISSUES_LIMIT", 200)
        semantic_limit = self._int_setting("SITE_AUDIT_PRO_INLINE_SEMANTIC_LIMIT", 200)
        pages_limit = self._int_setting("SITE_AUDIT_PRO_INLINE_PAGES_LIMIT", 500)

        issues = list(public_results.get("issues", []) or [])
        pages = list(public_results.get("pages", []) or [])
        pipeline = public_results.get("pipeline", {}) or {}
        semantic = list(pipeline.get("semantic_linking_map", []) or [])

        omitted = {
            "issues": max(0, len(issues) - issues_limit),
            "semantic_linking_map": max(0, len(semantic) - semantic_limit),
            "pages": max(0, len(pages) - pages_limit),
        }

        public_results["issues"] = issues[:issues_limit]
        compacted_pages = []
        page_nested_omitted = 0
        for row in pages[:pages_limit]:
            compacted_row, row_omitted = self._compact_page_row(row)
            compacted_pages.append(compacted_row)
            page_nested_omitted += sum(row_omitted.values())
        compacted_semantic = []
        semantic_nested_omitted = 0
        for row in semantic[:semantic_limit]:
            compacted_row, row_omitted = self._compact_semantic_row(row)
            compacted_semantic.append(compacted_row)
            semantic_nested_omitted += sum(row_omitted.values())

        public_results["pages"] = compacted_pages
        pipeline["semantic_linking_map"] = compacted_semantic
        public_results["pipeline"] = pipeline

        artifacts = public_results.setdefault("artifacts", {})
        artifacts["payload_compacted"] = any(v > 0 for v in omitted.values()) or page_nested_omitted > 0 or semantic_nested_omitted > 0
        artifacts["inline_limits"] = {
            "issues": issues_limit,
            "semantic_linking_map": semantic_limit,
            "pages": pages_limit,
        }
        artifacts["omitted_counts"] = omitted
        if page_nested_omitted > 0 or semantic_nested_omitted > 0:
            nested = dict(artifacts.get("nested_omitted_counts") or {})
            if page_nested_omitted > 0:
                nested["pages"] = page_nested_omitted
            if semantic_nested_omitted > 0:
                nested["semantic_linking_map"] = semantic_nested_omitted
            artifacts["nested_omitted_counts"] = nested

        if artifacts["payload_compacted"]:
            _record_compaction(
                original_size=original_size,
                compacted_size=_estimate_bytes(public_results),
            )
