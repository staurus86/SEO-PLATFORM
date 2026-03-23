import shutil
import unittest
from pathlib import Path

from app.core.ops_observability import get_ops_observability_stats
from app.reports.docx_generator import DOCXGenerator
from app.reports.xlsx_generator import XLSXGenerator


class ExportObservabilityTests(unittest.TestCase):
    def test_docx_and_xlsx_save_update_export_observability(self):
        before = get_ops_observability_stats()["exports"]
        temp_dir = Path("tests") / ".tmp_export_observability"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "url": "https://example.com",
            "results": {
                "robots_txt_found": True,
                "status_code": 200,
                "quality_score": 91,
                "quality_grade": "A",
                "production_ready": True,
                "quick_status": "ok",
                "content_length": 120,
                "lines_count": 8,
                "user_agents": 1,
                "disallow_rules": 1,
                "allow_rules": 1,
                "sitemaps": ["https://example.com/sitemap.xml"],
                "hosts": ["example.com"],
                "crawl_delays": {},
                "clean_params": [],
                "severity_counts": {"critical": 0, "warning": 1, "info": 1},
                "warning_issues": ["Missing explicit GPTBot rule"],
                "info_issues": ["Robots file is reachable"],
                "recommendations": ["Add explicit AI bot policy."],
            },
        }

        try:
            docx = DOCXGenerator()
            docx.reports_dir = str(temp_dir)
            xlsx = XLSXGenerator()
            xlsx.reports_dir = str(temp_dir)

            docx_path = docx.generate_robots_report("export-obs-docx", payload)
            xlsx_path = xlsx.generate_robots_report("export-obs-xlsx", payload)

            self.assertTrue(Path(docx_path).exists())
            self.assertTrue(Path(xlsx_path).exists())

            after = get_ops_observability_stats()["exports"]
            self.assertGreaterEqual(after["generation_ms"]["count"], before["generation_ms"]["count"] + 2)
            self.assertGreaterEqual(after["file_size_bytes"]["count"], before["file_size_bytes"]["count"] + 2)
            self.assertGreater(after["file_size_bytes"]["max_bytes"], 0)
            self.assertIn(after["file_size_bytes"]["last_format"], {"docx", "xlsx"})
            self.assertIn("recent_15m", after["generation_ms"])
            self.assertIn("recent_60m", after["file_size_bytes"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
