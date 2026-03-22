import os
import socket
import subprocess
import sys
import time
import unittest
import json
from pathlib import Path

import requests


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.ok:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Server did not become ready: {url}")


class BrowserCriticalFlowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if __import__("importlib").util.find_spec("playwright") is None:
            raise unittest.SkipTest("playwright is not installed in this environment")

        cls.root = Path(__file__).resolve().parents[1]
        cls.artifacts_dir = cls.root / "tests" / ".artifacts" / "browser-smoke"
        if cls.artifacts_dir.exists():
            for child in cls.artifacts_dir.iterdir():
                if child.is_file():
                    child.unlink()
        else:
            cls.artifacts_dir.mkdir(parents=True, exist_ok=True)
        cls.port = _free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(cls.root)
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--log-level",
                "warning",
            ],
            cwd=str(cls.root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        try:
            _wait_for_http(f"{cls.base_url}/health")
        except Exception:
            cls.server.terminate()
            cls.server.wait(timeout=10)
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server", None):
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()

    def _with_browser(self, callback):
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context()
                context.route("https://cdn.jsdelivr.net/**", lambda route: route.fulfill(status=200, body="window.Chart = window.Chart || function(){};"))
                context.route("https://unpkg.com/**", lambda route: route.fulfill(status=200, body="window.lucide = { createIcons: function(){} };"))
                try:
                    callback(context)
                finally:
                    context.close()
                    browser.close()
        except PlaywrightError as exc:
            raise unittest.SkipTest(f"Playwright browser runtime unavailable: {exc}") from exc

    def test_unified_batch_and_sitepro_critical_flows(self):
        def run(context):
            self._exercise_unified_flow(context, capture_visual=True)
            self._exercise_batch_flow(context)
            self._exercise_sitepro_batch_flow(context)
            self._exercise_llm_v2_visual_flow(context)

        self._with_browser(run)

    def _set_theme(self, page, theme: str):
        page.evaluate(
            """(theme) => {
                localStorage.setItem('ds-theme', theme);
                document.documentElement.setAttribute('data-theme', theme);
            }""",
            theme,
        )

    def _screenshot(self, page, name: str):
        target = self.artifacts_dir / name
        page.screenshot(path=str(target), full_page=True)
        self.assertTrue(target.exists(), f"Screenshot was not created: {target}")
        self.assertGreater(target.stat().st_size, 0, f"Screenshot is empty: {target}")

    def _exercise_unified_flow(self, context, capture_visual: bool = False):
        task_id = "unified-e2e-1"

        unified_result = {
            "task_id": task_id,
            "status": "SUCCESS",
            "task_type": "unified_audit",
            "url": "https://example.com/",
            "overall_score": 84.2,
            "overall_grade": "B",
            "tools_run": 7,
            "tools_failed": 0,
            "duration_ms": 1240,
            "scores": {
                "onpage": 81,
                "render": 76,
                "mobile_friendly": 92,
                "bot_accessibility": 88,
                "redirect": 73,
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
                "render": {"results": {"summary": {"quality_score": 76}}},
                "mobile": {"results": {"score": 92}},
                "bot_check": {"results": {"summary": {"indexable_bots": 7, "blocked_bots": 0}}},
                "redirect": {"results": {"summary": {"quality_score": 73}}},
                "cwv": {"results": {"combined": True, "mobile": {"summary": {"performance_score": 84}}, "desktop": {"summary": {"performance_score": 96}}}},
            },
        }

        context.route(
            f"{self.base_url}/api/tasks/unified-audit",
            lambda route: route.fulfill(status=200, content_type="application/json", body=f'{{"task_id":"{task_id}","status":"PENDING"}}'),
        )
        context.route(
            f"{self.base_url}/api/tasks/{task_id}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "task_id": task_id,
                        "status": "SUCCESS",
                        "task_type": "unified_audit",
                        "progress": 100,
                        "status_message": "Done",
                        "result": unified_result,
                    }
                ),
            ),
        )

        page = context.new_page()
        page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        page.fill("#unified-audit-url", "https://example.com/")
        page.click("#full-audit-card button[type='submit']")
        page.wait_for_url(f"{self.base_url}/results/{task_id}", timeout=10000)
        page.wait_for_selector("text=Unified Full SEO Audit", timeout=10000)
        page.wait_for_selector("text=Общая оценка", timeout=10000)
        if capture_visual:
            self._set_theme(page, "light")
            page.wait_for_timeout(150)
            self._screenshot(page, "unified-results-light.png")
            self._set_theme(page, "dark")
            page.wait_for_timeout(150)
            self._screenshot(page, "unified-results-dark.png")
        page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        page.wait_for_selector("text=История задач", timeout=10000)
        page.wait_for_selector("text=Full SEO Audit", timeout=10000)
        page.close()

    def _exercise_batch_flow(self, context):
        task_id = "batch-e2e-1"
        batch_result = {
            "task_id": task_id,
            "status": "SUCCESS",
            "task_type": "batch_onpage",
            "results": {
                "summary": {
                    "tool": "onpage",
                    "total_urls": 2,
                    "success": 2,
                    "errors": 0,
                },
                "items": [
                    {"url": "https://example.com/", "status": "success", "result": {"score": 82, "summary": {"score": 82}, "issues": []}},
                    {"url": "https://example.com/about", "status": "success", "result": {"score": 77, "summary": {"score": 77}, "issues": []}},
                ],
            },
        }

        context.route(
            f"{self.base_url}/api/tasks/batch",
            lambda route: route.fulfill(status=200, content_type="application/json", body=f'{{"task_id":"{task_id}","status":"PENDING"}}'),
        )
        context.route(
            f"{self.base_url}/api/tasks/{task_id}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "task_id": task_id,
                        "status": "SUCCESS",
                        "task_type": "batch_onpage",
                        "progress": 100,
                        "status_message": "Done",
                        "result": batch_result,
                    }
                ),
            ),
        )

        page = context.new_page()
        page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        page.select_option("#batch-tool", "onpage")
        page.fill("#batch-urls", "https://example.com/\nhttps://example.com/about")
        page.click("#batch-mode-card button[type='submit']")
        page.wait_for_url(f"{self.base_url}/results/{task_id}", timeout=10000)
        page.wait_for_selector("text=Batch Mode", timeout=10000)
        page.wait_for_selector("text=https://example.com/about", timeout=10000)
        page.close()

    def _exercise_sitepro_batch_flow(self, context):
        task_id = "sitepro-e2e-1"
        sitepro_result = {
            "task_id": task_id,
            "status": "SUCCESS",
            "task_type": "site_audit_pro",
            "url": "https://example.com/",
            "batch_mode": True,
            "results": {
                "mode": "full",
                "summary": {
                    "total_pages": 2,
                    "total_issues": 1,
                    "critical_issues": 0,
                    "warning_issues": 1,
                    "info_issues": 0,
                    "score": 81,
                },
                "pages": [
                    {"url": "https://example.com/", "title": "Home", "status_code": 200, "recommendation": "Fix title"},
                    {"url": "https://example.com/about", "title": "About", "status_code": 200, "recommendation": "Add schema"},
                ],
                "issues": [
                    {"code": "title_length", "severity": "warning", "url": "https://example.com/"}
                ],
                "pipeline": {"metrics": {}},
                "artifacts": {"batch_mode": True, "batch_urls_requested": 2},
            },
        }

        context.route(
            f"{self.base_url}/api/tasks/site-audit-pro",
            lambda route: route.fulfill(status=200, content_type="application/json", body=f'{{"task_id":"{task_id}","status":"PENDING"}}'),
        )
        context.route(
            f"{self.base_url}/api/tasks/{task_id}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "task_id": task_id,
                        "status": "SUCCESS",
                        "task_type": "site_audit_pro",
                        "progress": 100,
                        "status_message": "Done",
                        "result": sitepro_result,
                    }
                ),
            ),
        )

        page = context.new_page()
        page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        page.select_option("#site-pro-scan-mode", "batch")
        page.wait_for_selector("#site-pro-batch-urls:visible", timeout=5000)
        page.fill("#site-pro-batch-urls", "https://example.com/\nhttps://example.com/about")
        page.click("#site-pro-card button[type='submit']")
        page.wait_for_url(f"{self.base_url}/results/{task_id}", timeout=10000)
        page.wait_for_selector("text=Site Audit Pro", timeout=10000)
        page.wait_for_selector("text=Страницы", timeout=10000)
        page.close()

    def _exercise_llm_v2_visual_flow(self, context):
        job_id = "llm-v2-visual-1"
        llm_payload = {
            "status": "done",
            "progress": 100,
            "status_message": "Done",
            "result": {
                "final_url": "https://example.com/page",
                "score": {"total": 72, "top_issues": ["Missing schema"]},
                "projected_score_after_fixes": 88,
                "projected_score_waterfall": {
                    "baseline": 72,
                    "steps": [{"label": "Schema coverage", "delta": 12, "value": 84}],
                    "target": 88,
                },
                "citation_probability": 68,
                "eeat_score": {"status": "not_evaluated", "reason": "feature_disabled", "score": None},
                "trust_signal_score": 40,
                "ai_understanding": {
                    "topic": "Industrial vacuum meter calibration",
                    "score": 78,
                    "topic_confidence": 74,
                    "topic_fallback_used": True,
                    "content_clarity_status": "evaluated",
                    "content_clarity": 71,
                    "entities": ["Vacuum meter"],
                    "intent": "informational",
                },
                "discoverability": {"discoverability_score": 61, "click_depth_estimate": 3},
                "ai_answer_preview": {
                    "question": "What is this page about?",
                    "answer": "The page explains vacuum meter calibration and maintenance.",
                    "confidence": 66,
                },
                "nojs": {
                    "content": {
                        "main_text_length": 1800,
                        "main_text_preview": "Vacuum meter guide",
                        "main_content_ratio": 0.71,
                        "boilerplate_ratio": 0.29,
                        "chunks": [{"idx": 1, "text": "Chunk text 1"}, {"idx": 2, "text": "Chunk text 2"}],
                    },
                    "schema": {"coverage_score": 25, "jsonld_types": []},
                    "resources": {"cookie_wall": False, "paywall": False, "login_wall": False, "csp_strict": False, "mixed_content_count": 0},
                    "signals": {"author_present": True},
                },
                "rendered": {"content": {"main_text_length": 2400}, "render_debug": {"console_errors": [], "failed_requests": []}},
                "bot_matrix": [{"profile": "gptbot", "allowed": True, "reason": "ok"}],
                "metrics_bytes": {"html_bytes": 24000, "text_bytes": 1800, "text_html_ratio": 0.075, "main_content_ratio": 0.71, "boilerplate_ratio": 0.29},
                "quality_profile": {
                    "status": "stable",
                    "profile_id": "article-v1",
                    "coverage_ratio": 0.84,
                    "avg_detector_confidence": 0.76,
                    "retrieval_confidence": 0.71,
                    "retrieval_variance": 0.08,
                    "citation_calibration_error": 0.03,
                    "drift_flags": [],
                },
                "quality_gates": {
                    "status": "pass",
                    "passed": 6,
                    "total": 6,
                    "checks": [
                        {"metric": "page_type_accuracy", "value": 0.9, "threshold": 0.8, "pass": True},
                        {"metric": "citation_pass_rate", "value": 0.82, "threshold": 0.75, "pass": True},
                    ],
                },
                "detector_calibration": {"profile_id": "article-v1", "downgraded_count": 0},
                "recommendations": [
                    {
                        "priority": "P1",
                        "area": "schema",
                        "title": "Add JSON-LD",
                        "expected_lift": "+8..12",
                        "evidence": ["No JSON-LD types found"],
                    }
                ],
                "snippet_library": {"jsonld_organization": "<script>...</script>"},
                "llm_ingestion": {"status": "evaluated", "avg_chunk_quality": 57, "chunks_total": 12, "chunks_survive_1024": 6},
                "js_dependency": {"status": "executed", "score": 34, "risk": "medium", "reason": ""},
                "llm_simulation": {"citation_probability": 64, "reason": "Good coverage"},
                "entity_graph": {"organizations": ["Example Inc."]},
                "main_content_confidence": {"level": "high", "reasons": ["clean article structure"]},
                "page_type": "article",
                "page_type_confidence": 81,
                "diff": {"missing": ["schema markup"]},
            },
        }

        context.route(
            f"{self.base_url}/api/tools/llm-crawler/jobs/{job_id}",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(llm_payload)),
        )

        page = context.new_page()
        page.set_viewport_size({"width": 1440, "height": 1600})
        page.goto(f"{self.base_url}/llm-crawler/results/{job_id}", wait_until="domcontentloaded")
        page.wait_for_selector("text=AI Visibility Overview", timeout=10000)
        self._set_theme(page, "light")
        page.wait_for_timeout(150)
        self._screenshot(page, "llm-v2-light.png")
        self._set_theme(page, "dark")
        page.wait_for_timeout(150)
        self._screenshot(page, "llm-v2-dark.png")
        page.close()
