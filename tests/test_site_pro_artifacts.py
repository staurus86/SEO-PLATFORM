import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools.site_pro.service import SiteAuditProService


class SiteProArtifactsTests(unittest.TestCase):
    def test_full_mode_emits_chunk_manifest_and_files(self):
        service = SiteAuditProService()
        task_id = "sitepro-artifacts-test"
        chunk_root = Path("reports_output") / "site_pro" / task_id
        legacy_chunk_root = Path("site_pro") / task_id
        if chunk_root.exists():
            shutil.rmtree(chunk_root, ignore_errors=True)
        if legacy_chunk_root.exists():
            shutil.rmtree(legacy_chunk_root, ignore_errors=True)

        public_payload = {
            "summary": {"total_pages": 2, "issues_total": 2},
            "pages": [
                {"url": "https://site.test", "status_code": 200, "health_score": 90, "topic_label": "home", "recommendation": "Keep"},
                {"url": "https://site.test/a", "status_code": 200, "health_score": 88, "topic_label": "a", "recommendation": "Fix alt"},
            ],
            "issues": [
                {"url": "https://site.test", "severity": "warning", "code": "x", "title": "X", "details": ""},
                {"url": "https://site.test/a", "severity": "info", "code": "y", "title": "Y", "details": ""},
            ],
            "pipeline": {
                "semantic_linking_map": [
                    {"source_url": "https://site.test", "target_url": "https://site.test/a", "topic": "home", "reason": "related"}
                ]
            },
            "artifacts": {},
        }

        with patch.object(service.adapter, "run", return_value=object()), patch.object(
            service.adapter, "to_public_results", return_value=public_payload
        ):
            result = service.run(url="https://site.test", task_id=task_id, mode="full", max_pages=5)

        chunk_manifest = (((result or {}).get("results") or {}).get("artifacts") or {}).get("chunk_manifest", {})
        self.assertEqual(chunk_manifest.get("task_id"), task_id)
        chunks = chunk_manifest.get("chunks", [])
        self.assertTrue(chunks)

        all_files = [f for chunk in chunks for f in (chunk.get("files") or [])]
        self.assertTrue(all_files)
        for meta in all_files:
            path = Path(meta.get("path", ""))
            self.assertTrue(path.exists(), msg=f"Chunk file does not exist: {path}")
            self.assertTrue(str(meta.get("download_url", "")).startswith("/api/site-pro-artifacts/"))

        shutil.rmtree(chunk_root, ignore_errors=True)
        shutil.rmtree(legacy_chunk_root, ignore_errors=True)

    def test_compacts_inline_payload_when_limits_exceeded(self):
        service = SiteAuditProService()
        task_id = "sitepro-artifacts-compact-test"
        chunk_root = Path("reports_output") / "site_pro" / task_id
        if chunk_root.exists():
            shutil.rmtree(chunk_root, ignore_errors=True)

        issues = [
            {"url": f"https://site.test/p{i}", "severity": "warning", "code": "x", "title": "X", "details": ""}
            for i in range(260)
        ]
        semantic_rows = [
            {"source_url": f"https://site.test/p{i}", "target_url": "https://site.test/hub", "topic": "t", "reason": "r"}
            for i in range(240)
        ]
        pages = [
            {"url": f"https://site.test/p{i}", "status_code": 200, "health_score": 90, "topic_label": "t", "recommendation": "keep"}
            for i in range(520)
        ]
        public_payload = {
            "summary": {"total_pages": 520, "issues_total": 260},
            "pages": pages,
            "issues": issues,
            "pipeline": {"semantic_linking_map": semantic_rows},
            "artifacts": {},
        }

        with patch.object(service.adapter, "run", return_value=object()), patch.object(
            service.adapter, "to_public_results", return_value=public_payload
        ):
            result = service.run(url="https://site.test", task_id=task_id, mode="full", max_pages=5)

        results = (result or {}).get("results", {}) or {}
        artifacts = results.get("artifacts", {}) or {}
        self.assertTrue(artifacts.get("payload_compacted"))
        self.assertEqual(len(results.get("issues", [])), 200)
        self.assertEqual(len(results.get("pages", [])), 500)
        self.assertEqual(len((results.get("pipeline", {}) or {}).get("semantic_linking_map", [])), 200)
        omitted = artifacts.get("omitted_counts", {})
        self.assertEqual(omitted.get("issues"), 60)
        self.assertEqual(omitted.get("pages"), 20)
        self.assertEqual(omitted.get("semantic_linking_map"), 40)

        shutil.rmtree(chunk_root, ignore_errors=True)

    def test_compacts_nested_page_and_semantic_fields(self):
        service = SiteAuditProService()
        public_payload = {
            "summary": {"total_pages": 1, "issues_total": 1},
            "pages": [
                {
                    "url": "https://site.test/p1",
                    "status_code": 200,
                    "health_score": 90,
                    "topic_label": "t",
                    "recommendation": "keep",
                    "filler_phrases": [f"f{i}" for i in range(30)],
                    "ai_markers_list": [f"a{i}" for i in range(30)],
                    "top_keywords": [f"k{i}" for i in range(30)],
                    "top_terms": [f"t{i}" for i in range(30)],
                    "near_duplicate_urls": [f"https://site.test/d{i}" for i in range(30)],
                    "semantic_links": [{"target_url": f"https://site.test/x{i}"} for i in range(30)],
                    "broken_internal_targets": [f"https://site.test/b{i}" for i in range(30)],
                    "keyword_density_profile": {f"kw{i}": i / 10 for i in range(40)},
                }
            ],
            "issues": [{"url": "https://site.test/p1", "severity": "warning", "code": "x", "title": "X", "details": ""}],
            "pipeline": {
                "semantic_linking_map": [
                    {
                        "source_url": "https://site.test/p1",
                        "target_url": "https://site.test/hub",
                        "topic": "t",
                        "reason": "r",
                        "supporting_urls": [f"https://site.test/s{i}" for i in range(20)],
                        "source_terms": [f"src{i}" for i in range(20)],
                        "target_terms": [f"dst{i}" for i in range(20)],
                    }
                ]
            },
            "artifacts": {"chunk_manifest": {"chunks": []}},
        }

        service._compact_inline_payload(public_payload)
        page = public_payload["pages"][0]
        semantic_row = public_payload["pipeline"]["semantic_linking_map"][0]
        nested = public_payload["artifacts"]["nested_omitted_counts"]

        self.assertEqual(len(page["filler_phrases"]), 12)
        self.assertEqual(len(page["ai_markers_list"]), 12)
        self.assertEqual(len(page["top_keywords"]), 12)
        self.assertEqual(len(page["top_terms"]), 12)
        self.assertEqual(len(page["near_duplicate_urls"]), 10)
        self.assertEqual(len(page["semantic_links"]), 20)
        self.assertEqual(len(page["broken_internal_targets"]), 20)
        self.assertEqual(len(page["keyword_density_profile"]), 20)
        self.assertTrue(page["_storage_meta"]["payload_compacted"])
        self.assertEqual(len(semantic_row["supporting_urls"]), 8)
        self.assertEqual(len(semantic_row["source_terms"]), 8)
        self.assertEqual(len(semantic_row["target_terms"]), 8)
        self.assertTrue(semantic_row["_storage_meta"]["payload_compacted"])
        self.assertGreater(nested["pages"], 0)
        self.assertGreater(nested["semantic_linking_map"], 0)


if __name__ == "__main__":
    unittest.main()
