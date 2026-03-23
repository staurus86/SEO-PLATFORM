import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from starlette.requests import Request
from fastapi import HTTPException


def _request(headers=None, query_string=""):
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), str(value).encode("latin-1")))
    return Request({"type": "http", "method": "GET", "path": "/api/ops/status", "headers": raw_headers, "query_string": query_string.encode("latin-1")})


class OpsStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_ops_status_denies_access_when_token_required(self):
        from app.api.routers.tasks import ops_status

        with patch("app.api.routers.tasks.settings.OPS_ACCESS_TOKEN", "secret", create=True):
            with self.assertRaises(HTTPException) as ctx:
                await ops_status(_request())

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_ops_status_reports_healthy_redis_and_worker(self):
        from app.api.routers.tasks import ops_status

        fake_heartbeat = {"updatedAt": datetime.now(timezone.utc).isoformat(), "worker": 1, "queue_depth": 0}

        with patch("app.api.routers.tasks.get_task_store_redis", return_value=object(), create=True), \
             patch("app.api.routers.tasks.get_worker_heartbeat", return_value=fake_heartbeat), \
             patch("app.api.routers.tasks.queue_depth", return_value=3), \
             patch("app.api.routers.tasks.get_process_memory_snapshot", return_value={"rss_mb": 64}, create=True), \
             patch("app.api.routers.tasks.get_memory_guard_status", return_value={"idle_seconds": 5}, create=True), \
             patch("app.api.routers.tasks.get_task_store_compaction_stats", return_value={"compactions_total": 2}, create=True), \
             patch("app.api.routers.tasks.get_llm_crawler_compaction_stats", return_value={"compactions_total": 3}, create=True), \
             patch("app.api.routers.tasks.get_site_pro_compaction_stats", return_value={"compactions_total": 4}, create=True), \
             patch("app.api.routers.tasks.get_ops_observability_stats", return_value={"tasks": {"queue_wait_ms": {"count": 5}}}, create=True), \
             patch("app.api.routers.tasks.get_task_store_memory_stats", return_value={"items_total": 2}, create=True), \
             patch("app.api.routers.tasks.progress_tracker") as progress_tracker_mock:
            progress_tracker_mock.get_memory_stats.return_value = {"items_total": 1}
            progress_tracker_mock.get_compaction_stats.return_value = {"compactions_total": 1}

            payload = await ops_status(_request())

        self.assertEqual(payload["status"], "healthy")
        self.assertTrue(payload["redis"]["ok"])
        self.assertTrue(payload["llm_worker"]["healthy"])
        self.assertEqual(payload["llm_worker"]["queue_depth"], 3)
        self.assertTrue(str(payload["redis"]["urls"]["task_store"]).startswith("redis://"))
        self.assertEqual(payload["payload_compaction"]["task_store"]["compactions_total"], 2)
        self.assertEqual(payload["payload_compaction"]["progress"]["compactions_total"], 1)
        self.assertEqual(payload["payload_compaction"]["llm_crawler"]["compactions_total"], 3)
        self.assertEqual(payload["payload_compaction"]["site_audit_pro"]["compactions_total"], 4)
        self.assertEqual(payload["observability"]["tasks"]["queue_wait_ms"]["count"], 5)

    async def test_ops_status_reports_degraded_when_redis_unavailable(self):
        from app.api.routers.tasks import ops_status

        with patch("app.api.routers.tasks.get_task_store_redis", return_value=None, create=True), \
             patch("app.api.routers.tasks.get_worker_heartbeat", return_value=None), \
             patch("app.api.routers.tasks.queue_depth", return_value=0), \
             patch("app.api.routers.tasks.get_process_memory_snapshot", return_value={"rss_mb": 64}, create=True), \
             patch("app.api.routers.tasks.get_memory_guard_status", return_value={"idle_seconds": 5}, create=True), \
             patch("app.api.routers.tasks.get_task_store_compaction_stats", return_value={"compactions_total": 0}, create=True), \
             patch("app.api.routers.tasks.get_llm_crawler_compaction_stats", return_value={"compactions_total": 0}, create=True), \
             patch("app.api.routers.tasks.get_site_pro_compaction_stats", return_value={"compactions_total": 0}, create=True), \
             patch("app.api.routers.tasks.get_ops_observability_stats", return_value={"tasks": {"queue_wait_ms": {"count": 0}}}, create=True), \
             patch("app.api.routers.tasks.get_task_store_memory_stats", return_value={"items_total": 2}, create=True), \
             patch("app.api.routers.tasks.progress_tracker") as progress_tracker_mock:
            progress_tracker_mock.get_memory_stats.return_value = {"items_total": 1}
            progress_tracker_mock.get_compaction_stats.return_value = {"compactions_total": 0}
            payload = await ops_status(_request())

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["redis"]["ok"])
        self.assertFalse(payload["llm_worker"]["healthy"])


if __name__ == "__main__":
    unittest.main()
