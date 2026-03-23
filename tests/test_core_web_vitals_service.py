import unittest
from unittest.mock import patch

import requests

from app.tools.core_web_vitals.service_v1 import run_core_web_vitals


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class CoreWebVitalsServiceTests(unittest.TestCase):
    @patch("app.tools.core_web_vitals.service_v1.requests.get")
    def test_runs_and_parses_metrics(self, mock_get):
        payload = {
            "lighthouseResult": {
                "categories": {"performance": {"score": 0.82}},
                "audits": {
                    "largest-contentful-paint": {"numericValue": 2900},
                    "interaction-to-next-paint": {"numericValue": 180},
                    "cumulative-layout-shift": {"numericValue": 0.12},
                    "first-contentful-paint": {"numericValue": 1700},
                    "server-response-time": {"numericValue": 620},
                    "unused-css-rules": {
                        "score": 0.2,
                        "scoreDisplayMode": "numeric",
                        "title": "Reduce unused CSS",
                        "displayValue": "120 KiB",
                        "description": "Unused styles were found",
                    },
                },
            },
            "loadingExperience": {
                "metrics": {
                    "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2700},
                    "INTERACTION_TO_NEXT_PAINT": {"percentile": 220},
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 18},
                }
            },
        }
        mock_get.return_value = _FakeResponse(200, payload)

        result = run_core_web_vitals(url="example.com", strategy="mobile", api_key="token")
        self.assertEqual(result.get("task_type"), "core_web_vitals")
        self.assertEqual(result.get("url"), "https://example.com/")
        r = result.get("results", {})
        self.assertEqual(r.get("strategy"), "mobile")
        self.assertEqual(r.get("mode"), "single")
        self.assertEqual((r.get("summary") or {}).get("performance_score"), 82)
        self.assertIn((r.get("summary") or {}).get("risk_level"), {"low", "medium", "high"})
        self.assertIsNotNone((r.get("summary") or {}).get("health_index"))
        self.assertTrue(str((r.get("summary") or {}).get("grade") or "").strip())
        self.assertIn("performance", (r.get("categories") or {}))
        self.assertIn("accessibility", (r.get("categories") or {}))
        self.assertIn("best_practices", (r.get("categories") or {}))
        self.assertIn("seo", (r.get("categories") or {}))
        self.assertEqual(((r.get("metrics") or {}).get("lcp") or {}).get("status"), "needs_improvement")
        self.assertEqual(((r.get("metrics") or {}).get("inp") or {}).get("status"), "needs_improvement")
        self.assertEqual(((r.get("metrics") or {}).get("cls") or {}).get("status"), "needs_improvement")
        self.assertTrue(len(r.get("opportunities") or []) >= 1)
        first_opp = (r.get("opportunities") or [{}])[0]
        self.assertIn(str(first_opp.get("priority") or ""), {"critical", "high", "medium"})
        self.assertTrue(str(first_opp.get("group") or "").strip())
        self.assertTrue(len(r.get("recommendations") or []) >= 1)
        self.assertIsInstance(r.get("action_plan"), list)
        self.assertIsInstance(r.get("analysis"), dict)
        self.assertIsInstance(r.get("diagnostics"), dict)
        self.assertIsInstance(r.get("resource_summary"), dict)
        self.assertIsInstance(r.get("third_party"), dict)
        self.assertTrue((r.get("api") or {}).get("has_key"))

    @patch("app.tools.core_web_vitals.service_v1.requests.get")
    def test_retries_after_timeout(self, mock_get):
        payload = {
            "lighthouseResult": {
                "categories": {"performance": {"score": 0.9}},
                "audits": {
                    "largest-contentful-paint": {"numericValue": 2100},
                    "interaction-to-next-paint": {"numericValue": 140},
                    "cumulative-layout-shift": {"numericValue": 0.08},
                    "first-contentful-paint": {"numericValue": 1200},
                    "server-response-time": {"numericValue": 450},
                },
            },
            "loadingExperience": {"metrics": {}},
        }
        mock_get.side_effect = [
            requests.Timeout("Read timed out"),
            _FakeResponse(200, payload),
        ]

        result = run_core_web_vitals(url="example.com", strategy="desktop")
        r = result.get("results", {})
        self.assertEqual((r.get("summary") or {}).get("performance_score"), 90)
        self.assertGreaterEqual((r.get("api") or {}).get("retries_used", 0), 1)

    @patch("app.tools.core_web_vitals.service_v1.requests.get")
    def test_raises_on_api_error(self, mock_get):
        mock_get.return_value = _FakeResponse(429, {"error": {"message": "quota exceeded"}})
        with self.assertRaises(RuntimeError) as ctx:
            run_core_web_vitals(url="https://example.com", strategy="desktop")
        self.assertIn("лимита API", str(ctx.exception))

    @patch("app.tools.core_web_vitals.service_v1.requests.get")
    def test_retries_after_retryable_psi_document_timeout_error(self, mock_get):
        payload = {
            "lighthouseResult": {
                "categories": {"performance": {"score": 0.91}},
                "audits": {
                    "largest-contentful-paint": {"numericValue": 1900},
                    "interaction-to-next-paint": {"numericValue": 120},
                    "cumulative-layout-shift": {"numericValue": 0.04},
                    "first-contentful-paint": {"numericValue": 1100},
                    "server-response-time": {"numericValue": 420},
                },
            },
            "loadingExperience": {"metrics": {}},
        }
        mock_get.side_effect = [
            _FakeResponse(
                400,
                {
                    "error": {
                        "message": (
                            "Lighthouse returned error: FAILED_DOCUMENT_REQUEST. "
                            "Make sure you are testing the correct URL. (Details: net::ERR_TIMED_OUT)"
                        )
                    }
                },
            ),
            _FakeResponse(200, payload),
        ]

        result = run_core_web_vitals(url="example.com", strategy="desktop")
        r = result.get("results", {})
        self.assertEqual((r.get("summary") or {}).get("performance_score"), 91)
        self.assertGreaterEqual((r.get("api") or {}).get("retries_used", 0), 1)

    @patch("app.tools.core_web_vitals.service_v1.requests.get")
    def test_humanizes_document_timeout_error(self, mock_get):
        mock_get.return_value = _FakeResponse(
            400,
            {
                "error": {
                    "message": (
                        "Lighthouse returned error: FAILED_DOCUMENT_REQUEST. "
                        "Make sure you are testing the correct URL. (Details: net::ERR_TIMED_OUT)"
                    )
                }
            },
        )

        with self.assertRaises(RuntimeError) as ctx:
            run_core_web_vitals(url="https://example.com", strategy="desktop")

        self.assertIn("не смог загрузить страницу", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
