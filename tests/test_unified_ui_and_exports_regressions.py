import os
import shutil
import unittest
from pathlib import Path


class UnifiedUiGuards(unittest.TestCase):
    def test_task_progress_has_unified_type_and_localized_status_labels(self):
        root = Path(__file__).resolve().parents[1]
        main_js = (root / "app" / "static" / "js" / "task-progress.js").read_text(encoding="utf-8")
        batch_js = (root / "app" / "static" / "js" / "task-progress-batch.js").read_text(encoding="utf-8")
        template = (root / "app" / "templates" / "task_progress.html").read_text(encoding="utf-8")

        self.assertIn("unified_audit: 'Full SEO Audit'", main_js)
        self.assertIn("replace(/-/g, '_')", main_js)
        self.assertIn("PENDING: 'В очереди'", main_js)
        self.assertIn("RUNNING: 'В работе'", main_js)
        self.assertIn("SUCCESS: 'Готово'", main_js)
        self.assertIn("FAILURE: 'Ошибка'", main_js)
        self.assertIn("const cwvEntry = toolResults.cwv || toolResults.core_web_vitals || {}", main_js)
        self.assertIn("function _batchRenderSuccessDetails(item, toolType)", batch_js)
        self.assertIn("if (t.includes('robots')) {", batch_js)
        self.assertIn("title: `Batch ${_batchFriendlyToolLabel(toolType)}`", batch_js)
        self.assertIn('/static/js/task-progress-batch.js?v={{ app_version }}-{{ task_id }}', template)

    def test_history_has_friendly_unified_label(self):
        js_path = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "history.js"
        content = js_path.read_text(encoding="utf-8")

        self.assertIn("'unified-audit': 'Full SEO Audit'", content)

    def test_llm_v2_template_uses_versioned_assets_and_dark_theme_rules_exist(self):
        root = Path(__file__).resolve().parents[1]
        template_content = (root / "app" / "templates" / "llm_crawler_result_v2.html").read_text(encoding="utf-8")
        css_content = (root / "app" / "static" / "css" / "llm-crawler-v2.css").read_text(encoding="utf-8")

        self.assertIn("llm-crawler-v2.css?v={{ asset_version }}", template_content)
        self.assertIn("llm-crawler-v2.js?v={{ asset_version }}", template_content)
        self.assertIn('[data-theme="dark"]', css_content)
        self.assertIn('[data-theme="dark"] .llm-v2 .v2-btn-neutral', css_content)
        self.assertIn('[data-theme="dark"] .llm-v2 .panel', css_content)


class UnifiedExportRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_unified_export_routes_generate_xlsx_and_docx(self):
        if __import__("importlib").util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")

        from fastapi.responses import Response

        from app.api.routers.exports import get_unified_audit_docx, get_unified_audit_xlsx
        from app.api.routes import create_task_result
        from app.config import settings

        temp_dir = Path("tests") / ".tmp_unified_exports"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        original_reports_dir = settings.REPORTS_DIR
        try:
            settings.REPORTS_DIR = str(temp_dir)
            task_id = "unified-export-test"
            fake_result = {
                "task_type": "unified_audit",
                "url": "https://example.com/",
                "overall_score": 82.4,
                "overall_grade": "B",
                "tools_run": 7,
                "tools_failed": 0,
                "duration_ms": 1420,
                "scores": {
                    "onpage": 81,
                    "render": 77,
                    "mobile_friendly": 92,
                    "bot_accessibility": 88,
                    "redirect": 70,
                    "cwv_mobile": 84,
                    "cwv_desktop": 96,
                    "cwv_avg": 90,
                    "robots_ok": 100,
                },
                "dev_tasks": [
                    {
                        "priority": "P1",
                        "category": "SEO / Content",
                        "source_tool": "OnPage Audit",
                        "title": "Improve title tag",
                        "description": "Shorten title to fit SERP.",
                        "owner": "SEO",
                    }
                ],
                "errors": {},
                "results": {
                    "robots": {"results": {"robots_txt_found": True, "quality_score": 100, "sitemaps": ["https://example.com/sitemap.xml"]}},
                    "sitemap": {"results": {"valid": True, "quality_score": 100, "urls_count": 10, "sitemaps_scanned": 1}},
                    "onpage": {"results": {"score": 81}},
                    "render": {"results": {"summary": {"quality_score": 77}}},
                    "mobile": {"results": {"score": 92}},
                    "bot_check": {"results": {"summary": {"indexable_bots": 7, "blocked_bots": 0}}},
                    "redirect": {"results": {"summary": {"quality_score": 70}}},
                    "cwv": {"results": {"combined": True, "mobile": {"summary": {"performance_score": 84}}, "desktop": {"summary": {"performance_score": 96}}}},
                },
            }
            create_task_result(task_id, "unified_audit", "https://example.com/", fake_result)

            xlsx_response = await get_unified_audit_xlsx(task_id)
            self.assertIsInstance(xlsx_response, Response)
            self.assertEqual(
                xlsx_response.media_type,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.assertIn("unified_audit_example.com_", xlsx_response.headers.get("content-disposition", ""))
            self.assertGreater(len(xlsx_response.body), 0)

            docx_response = await get_unified_audit_docx(task_id)
            self.assertIsInstance(docx_response, Response)
            self.assertEqual(
                docx_response.media_type,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assertIn("unified_audit_example.com_", docx_response.headers.get("content-disposition", ""))
            self.assertGreater(len(docx_response.body), 0)
        finally:
            settings.REPORTS_DIR = original_reports_dir
            shutil.rmtree(temp_dir, ignore_errors=True)
