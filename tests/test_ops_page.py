import importlib.util
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class OpsPageTests(unittest.TestCase):
    def test_ops_page_renders_dashboard_shell(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")

        from app.main import app

        client = TestClient(app)
        response = client.get("/ops")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ops Status Dashboard", response.text)
        self.assertIn("ops-observability-table", response.text)
        self.assertIn("/static/js/ops-status.js", response.text)
        self.assertIn("ops-memory-cleanup-btn", response.text)
        self.assertIn("ops-artifacts-cleanup-btn", response.text)
        self.assertIn("ops-maintenance-run-btn", response.text)
        self.assertIn("ops-action-result", response.text)
        self.assertIn("ops-action-history", response.text)
        self.assertIn("ops-raw-snapshot", response.text)
        self.assertIn("ops-copy-snapshot-btn", response.text)

    def test_ops_page_requires_token_when_configured(self):
        if importlib.util.find_spec("multipart") is None:
            self.skipTest("python-multipart is not installed in this environment")

        from app.main import app

        with patch("app.main.settings.OPS_ACCESS_TOKEN", "secret", create=True):
            client = TestClient(app)
            denied = client.get("/ops")
            allowed = client.get("/ops?ops_token=secret")

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("ops_token", allowed.headers.get("set-cookie", ""))
