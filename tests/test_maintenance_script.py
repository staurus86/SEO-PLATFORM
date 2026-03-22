import unittest
from unittest.mock import patch


class MaintenanceScriptTests(unittest.TestCase):
    def test_run_maintenance_returns_expected_sections(self):
        from scripts.run_maintenance import run_maintenance

        with patch("scripts.run_maintenance.prune_stale_report_artifacts", return_value={"deleted_files": 2}), \
             patch("scripts.run_maintenance.cleanup_expired_jobs") as cleanup_jobs_mock, \
             patch("scripts.run_maintenance.run_memory_cleanup_now", return_value={"gc": {"collected_objects": 1}}), \
             patch("scripts.run_maintenance.queue_depth", return_value=0), \
             patch("scripts.run_maintenance.get_worker_heartbeat", return_value={"updatedAt": "2026-03-22T00:00:00+00:00"}):
            payload = run_maintenance(stale_days=7, force_gc=True)

        cleanup_jobs_mock.assert_called_once()
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["artifacts"]["deleted_files"], 2)
        self.assertIn("memory", payload)
        self.assertIn("llm_queue_depth", payload)
        self.assertIn("llm_worker_heartbeat", payload)


if __name__ == "__main__":
    unittest.main()
