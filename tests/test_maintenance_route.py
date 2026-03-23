import unittest
from unittest.mock import patch
from starlette.requests import Request


def _request():
    return Request({"type": "http", "method": "POST", "path": "/api/maintenance/run", "headers": []})


class MaintenanceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_maintenance_route_delegates_to_script(self):
        from app.api.routers.tasks import run_maintenance_now

        fake_summary = {
            "status": "SUCCESS",
            "artifacts": {"deleted_files": 3},
            "memory": {"gc": {"collected_objects": 1}},
            "llm_queue_depth": 0,
            "llm_worker_heartbeat": {"updatedAt": "2026-03-23T12:00:00+00:00"},
        }

        with patch("scripts.run_maintenance.run_maintenance", return_value=fake_summary) as run_mock:
            payload = await run_maintenance_now(_request(), days=9, force_gc=False)

        run_mock.assert_called_once_with(stale_days=9, force_gc=False)
        self.assertEqual(payload, fake_summary)
