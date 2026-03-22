import unittest
from unittest.mock import patch
from datetime import datetime, timezone


class OpsStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_ops_status_reports_healthy_redis_and_worker(self):
        from app.api.routers.tasks import ops_status

        fake_heartbeat = {"updatedAt": datetime.now(timezone.utc).isoformat(), "worker": 1, "queue_depth": 0}

        with patch("app.api.routers.tasks.get_task_store_redis", return_value=object(), create=True), \
             patch("app.api.routers.tasks.get_worker_heartbeat", return_value=fake_heartbeat), \
             patch("app.api.routers.tasks.queue_depth", return_value=3), \
             patch("app.api.routers.tasks.get_process_memory_snapshot", return_value={"rss_mb": 64}, create=True), \
             patch("app.api.routers.tasks.get_memory_guard_status", return_value={"idle_seconds": 5}, create=True), \
             patch("app.api.routers.tasks.get_task_store_memory_stats", return_value={"items_total": 2}, create=True), \
             patch("app.api.routers.tasks.progress_tracker") as progress_tracker_mock:
            progress_tracker_mock.get_memory_stats.return_value = {"items_total": 1}

            payload = await ops_status()

        self.assertEqual(payload["status"], "healthy")
        self.assertTrue(payload["redis"]["ok"])
        self.assertTrue(payload["llm_worker"]["healthy"])
        self.assertEqual(payload["llm_worker"]["queue_depth"], 3)
        self.assertTrue(str(payload["redis"]["urls"]["task_store"]).startswith("redis://"))

    async def test_ops_status_reports_degraded_when_redis_unavailable(self):
        from app.api.routers.tasks import ops_status

        with patch("app.api.routers.tasks.get_task_store_redis", return_value=None, create=True), \
             patch("app.api.routers.tasks.get_worker_heartbeat", return_value=None), \
             patch("app.api.routers.tasks.queue_depth", return_value=0), \
             patch("app.api.routers.tasks.get_process_memory_snapshot", return_value={"rss_mb": 64}, create=True), \
             patch("app.api.routers.tasks.get_memory_guard_status", return_value={"idle_seconds": 5}, create=True), \
             patch("app.api.routers.tasks.get_task_store_memory_stats", return_value={"items_total": 2}, create=True), \
             patch("app.api.routers.tasks.progress_tracker") as progress_tracker_mock:
            progress_tracker_mock.get_memory_stats.return_value = {"items_total": 1}
            payload = await ops_status()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["redis"]["ok"])
        self.assertFalse(payload["llm_worker"]["healthy"])


if __name__ == "__main__":
    unittest.main()
