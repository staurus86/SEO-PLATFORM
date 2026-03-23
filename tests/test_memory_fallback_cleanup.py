import time
import unittest
from unittest.mock import patch

from app.api.routers import _task_store
from app.config import settings
from app.core.progress import ProgressTracker


class TaskStoreMemoryCleanupTests(unittest.TestCase):
    def setUp(self):
        _task_store.task_results_memory.clear()
        _task_store._task_updated_at.clear()
        _task_store._task_last_access_at.clear()
        _task_store._task_payload_size_bytes.clear()

    def tearDown(self):
        _task_store.task_results_memory.clear()
        _task_store._task_updated_at.clear()
        _task_store._task_last_access_at.clear()
        _task_store._task_payload_size_bytes.clear()

    @patch("app.api.routers._task_store.get_redis_client", return_value=None)
    def test_aggressive_idle_cleanup_removes_terminal_tasks(self, _redis_mock):
        with patch.object(settings, "TASK_STORE_MEMORY_MAX_ITEMS", 100, create=True), \
             patch.object(settings, "TASK_STORE_MEMORY_TTL_SEC", 7200, create=True), \
             patch.object(settings, "TASK_STORE_IDLE_KEEP_SEC", 60, create=True):
            _task_store.create_task_pending("task-running", "clusterizer", "keywords:1")
            _task_store.create_task_pending("task-done", "clusterizer", "keywords:1")
            _task_store.update_task_state("task-done", status="SUCCESS", result={"ok": True})

            # Simulate stale terminal task in idle period.
            _task_store._task_last_access_at["task-done"] = time.time() - 3600
            summary = _task_store.cleanup_task_results_memory(idle_seconds=3600, aggressive=True)

            self.assertIsNotNone(_task_store.get_task_result("task-running"))
            self.assertIsNone(_task_store.get_task_result("task-done"))
            self.assertGreaterEqual(summary["removed_idle"], 1)

    @patch("app.api.routers._task_store.get_redis_client", return_value=None)
    def test_task_store_byte_budget_evicts_large_terminal_payloads_first(self, _redis_mock):
        with patch.object(settings, "TASK_STORE_MEMORY_MAX_ITEMS", 100, create=True), \
             patch.object(settings, "TASK_STORE_MEMORY_MAX_BYTES", 1024, create=True):
            _task_store.create_task_pending("task-active", "clusterizer", "keywords:1")
            _task_store.create_task_pending("task-heavy", "clusterizer", "keywords:1")
            _task_store.update_task_state("task-heavy", status="SUCCESS", result={"blob": "x" * 5000})

            self.assertIsNotNone(_task_store.get_task_result("task-active"))
            self.assertIsNone(_task_store.get_task_result("task-heavy"))
            self.assertLessEqual(_task_store.get_task_store_memory_stats()["bytes_total"], 1024)

    @patch("app.api.routers._task_store.get_redis_client", return_value=None)
    def test_task_timestamps_track_running_and_completion(self, _redis_mock):
        from app.core.ops_observability import get_ops_observability_stats

        before = get_ops_observability_stats()["tasks"]
        _task_store.create_task_pending("task-progress", "redirect_checker", "https://example.com")
        pending = _task_store.get_task_result("task-progress")

        self.assertIsNotNone(pending.get("created_at"))
        self.assertIsNotNone(pending.get("updated_at"))
        self.assertIsNone(pending.get("started_at"))
        self.assertIsNone(pending.get("completed_at"))

        _task_store.update_task_state("task-progress", status="RUNNING", progress=10)
        running = _task_store.get_task_result("task-progress")

        self.assertIsNotNone(running.get("started_at"))
        self.assertIsNotNone(running.get("updated_at"))
        self.assertIsNone(running.get("completed_at"))

        _task_store.update_task_state("task-progress", status="SUCCESS", progress=100, result={"ok": True})
        finished = _task_store.get_task_result("task-progress")

        self.assertIsNotNone(finished.get("started_at"))
        self.assertIsNotNone(finished.get("updated_at"))
        self.assertIsNotNone(finished.get("completed_at"))
        after = get_ops_observability_stats()["tasks"]
        self.assertGreaterEqual(after["queue_wait_ms"]["count"], before["queue_wait_ms"]["count"] + 1)
        self.assertGreaterEqual(after["run_duration_ms"]["count"], before["run_duration_ms"]["count"] + 1)
        self.assertGreaterEqual(after["end_to_end_ms"]["count"], before["end_to_end_ms"]["count"] + 1)
        self.assertGreaterEqual(after["result_payload_bytes"]["count"], before["result_payload_bytes"]["count"] + 1)
        self.assertIn("recent_15m", after["queue_wait_ms"])
        self.assertIn("recent_60m", after["run_duration_ms"])
        self.assertIn("recent_15m", after["result_payload_bytes"])


class ProgressMemoryCleanupTests(unittest.TestCase):
    def test_progress_ttl_cleanup_removes_expired_entries(self):
        tracker = ProgressTracker()
        tracker._redis_next_retry_ts = time.time() + 3600

        with patch.object(settings, "PROGRESS_MEMORY_TTL_SEC", 30, create=True), \
             patch.object(settings, "PROGRESS_MEMORY_MAX_ITEMS", 100, create=True), \
             patch.object(settings, "PROGRESS_IDLE_KEEP_SEC", 60, create=True):
            tracker.update_progress("progress-1", current=1, total=10, message="start")
            tracker._memory_updated_at["progress-1"] = time.time() - 600

            summary = tracker.cleanup_memory(idle_seconds=0.0, aggressive=False)

            self.assertIsNone(tracker.get_progress("progress-1"))
            self.assertGreaterEqual(summary["removed_expired"], 1)

    def test_progress_byte_budget_evicts_old_entries(self):
        tracker = ProgressTracker()
        tracker._redis_next_retry_ts = time.time() + 3600

        with patch.object(settings, "PROGRESS_MEMORY_MAX_ITEMS", 100, create=True), \
             patch.object(settings, "PROGRESS_MEMORY_MAX_BYTES", 600, create=True):
            tracker.update_progress("progress-old", current=1, total=10, message="x" * 5000)
            tracker._memory_last_access_at["progress-old"] = time.time() - 100
            tracker.update_progress("progress-new", current=2, total=10, message="ok")

            self.assertIsNone(tracker.get_progress("progress-old"))
            self.assertIsNotNone(tracker.get_progress("progress-new"))
            self.assertLessEqual(tracker.get_memory_stats()["bytes_total"], 1024)


if __name__ == "__main__":
    unittest.main()
