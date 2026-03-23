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

        with patch("app.core.task_cleanup.prune_stale_report_artifacts", return_value=fake_summary["artifacts"]), \
             patch("app.tools.llmCrawler.queue.cleanup_expired_jobs") as cleanup_jobs_mock, \
             patch("app.core.memory_guard.run_memory_cleanup_now", return_value=fake_summary["memory"]), \
             patch("app.api.routers.tasks.queue_depth", return_value=fake_summary["llm_queue_depth"]), \
             patch("app.api.routers.tasks.get_worker_heartbeat", return_value=fake_summary["llm_worker_heartbeat"]):
            payload = await run_maintenance_now(_request(), days=9, force_gc=False)

        cleanup_jobs_mock.assert_called_once()
        self.assertEqual(payload, fake_summary)
