import importlib.util
import unittest
from unittest.mock import patch
from fastapi import BackgroundTasks


class CoreWebVitalsRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_router_request_normalizes_plain_domain_url(self):
        from app.api.routers.cwv import CoreWebVitalsRequest

        payload = CoreWebVitalsRequest(url="example.com", strategy="desktop")

        self.assertEqual(payload.url, "https://example.com")

    def test_router_request_normalizes_batch_urls_without_scheme(self):
        from app.api.routers.cwv import CoreWebVitalsRequest

        payload = CoreWebVitalsRequest(
            scan_mode="batch",
            batch_urls=["example.com", "https://example.org/path"],
        )

        self.assertEqual(payload.batch_urls, ["https://example.com", "https://example.org/path"])

    async def test_route_queues_single_task(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")
        from app.api.routes import CoreWebVitalsRequest, create_core_web_vitals, get_task_result

        fake_result = {
            "task_type": "core_web_vitals",
            "url": "https://example.com/",
            "results": {
                "strategy": "desktop",
                "summary": {
                    "performance_score": 90,
                    "core_web_vitals_status": "good",
                },
                "metrics": {},
                "recommendations": [],
            },
        }

        with patch("app.api.routes.check_core_web_vitals", return_value=fake_result):
            payload = CoreWebVitalsRequest(url="example.com", strategy="desktop")
            response = await create_core_web_vitals(payload, BackgroundTasks())

        self.assertEqual(response.get("status"), "PENDING")
        task_id = str(response.get("task_id", ""))
        self.assertTrue(task_id.startswith("cwv-"))
        stored = get_task_result(task_id)
        self.assertIsNotNone(stored)
        self.assertEqual((stored or {}).get("task_type"), "core_web_vitals")
        self.assertEqual((stored or {}).get("status"), "PENDING")

    async def test_route_queues_batch_task(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")
        from app.api.routes import CoreWebVitalsRequest, create_core_web_vitals, get_task_result

        payload = CoreWebVitalsRequest(
            scan_mode="batch",
            strategy="desktop",
            batch_urls=["example.com", "https://example.org/"],
        )
        response = await create_core_web_vitals(payload, BackgroundTasks())

        self.assertEqual(response.get("status"), "PENDING")
        task_id = str(response.get("task_id", ""))
        self.assertTrue(task_id.startswith("cwv-"))
        stored = get_task_result(task_id)
        self.assertIsNotNone(stored)
        self.assertEqual((stored or {}).get("task_type"), "core_web_vitals")
        self.assertEqual((stored or {}).get("status"), "PENDING")

    async def test_route_rejects_batch_over_limit(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")
        from app.api.routes import CoreWebVitalsRequest, create_core_web_vitals
        from fastapi import HTTPException

        payload = CoreWebVitalsRequest(
            scan_mode="batch",
            strategy="desktop",
            batch_urls=[f"https://example{i}.com/" for i in range(11)],
        )
        with self.assertRaises(HTTPException) as ctx:
            await create_core_web_vitals(payload, BackgroundTasks())
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("максимум 10", str(ctx.exception.detail))

    async def test_route_rejects_competitor_mode_with_single_url(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")
        from app.api.routes import CoreWebVitalsRequest, create_core_web_vitals
        from fastapi import HTTPException

        payload = CoreWebVitalsRequest(
            scan_mode="batch",
            strategy="desktop",
            competitor_mode=True,
            batch_urls=["https://example.com/"],
        )
        with self.assertRaises(HTTPException) as ctx:
            await create_core_web_vitals(payload, BackgroundTasks())
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("минимум 2 URL", str(ctx.exception.detail))

    async def test_router_competitor_batch_task_completes(self):
        from app.api.routers.cwv import CoreWebVitalsRequest, create_core_web_vitals
        from app.api.routers._task_store import get_task_result

        def fake_scan(url, strategy="desktop", use_proxy=False, combined=False):
            return {
                "task_type": "core_web_vitals",
                "url": url,
                "results": {
                    "strategy": strategy,
                    "source": "pagespeed_insights_api",
                    "summary": {
                        "performance_score": 90 if "example.com" in url else 95,
                        "core_web_vitals_status": "good",
                    },
                    "metrics": {
                        "lcp": {"field_value_ms": 1800},
                        "inp": {"field_value_ms": 120},
                        "cls": {"field_value": 0.03},
                    },
                    "categories": {"performance": 90},
                    "diagnostics": {},
                    "analysis": {"risk_level": "low"},
                    "opportunities": [],
                    "recommendations": [],
                    "action_plan": [],
                },
            }

        background = BackgroundTasks()
        payload = CoreWebVitalsRequest(
            url="example.com",
            scan_mode="batch",
            competitor_mode=True,
            batch_urls=["example.com", "wikipedia.org"],
        )

        with patch("app.api.routers.cwv.check_core_web_vitals", side_effect=fake_scan):
            response = await create_core_web_vitals(payload, background)
            self.assertEqual(response.get("status"), "PENDING")
            self.assertEqual(len(background.tasks), 1)
            background.tasks[0].func(*background.tasks[0].args, **background.tasks[0].kwargs)

        task_id = str(response.get("task_id"))
        stored = get_task_result(task_id)
        self.assertEqual((stored or {}).get("status"), "SUCCESS")
        self.assertEqual((((stored or {}).get("result") or {}).get("results") or {}).get("mode"), "competitor")

    async def test_competitor_aggregator_builds_comparison_mode(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")
        from app.api.routes import _build_core_web_vitals_competitor_result

        sites = [
            {
                "url": "https://primary.example/",
                "status": "success",
                "summary": {"performance_score": 82, "core_web_vitals_status": "needs_improvement"},
                "metrics": {
                    "lcp": {"field_value_ms": 2100},
                    "inp": {"field_value_ms": 130},
                    "cls": {"field_value": 0.07},
                },
                "analysis": {"risk_level": "medium"},
                "opportunities": [{"id": "unused-javascript", "title": "Reduce unused JavaScript", "group": "javascript"}],
                "action_plan": [{"priority": "P1", "action": "Optimize JS"}],
                "recommendations": ["P1: Optimize JS"],
            },
            {
                "url": "https://competitor-a.example/",
                "status": "success",
                "summary": {"performance_score": 90, "core_web_vitals_status": "good"},
                "metrics": {
                    "lcp": {"field_value_ms": 1700},
                    "inp": {"field_value_ms": 95},
                    "cls": {"field_value": 0.03},
                },
                "analysis": {"risk_level": "low"},
                "opportunities": [{"id": "unused-javascript", "title": "Reduce unused JavaScript", "group": "javascript"}],
                "recommendations": ["Reduce unused JavaScript"],
            },
        ]

        result = _build_core_web_vitals_competitor_result(strategy="desktop", source="pagespeed_insights_api", sites=sites)
        self.assertEqual(result.get("mode"), "competitor")
        self.assertEqual(((result.get("summary") or {}).get("primary_url")), "https://primary.example/")
        self.assertEqual(len(result.get("comparison_rows") or []), 1)
        self.assertIn("benchmark", result)
        self.assertIn("gaps_for_primary", result)
        self.assertIn("strengths_of_primary", result)


if __name__ == "__main__":
    unittest.main()
