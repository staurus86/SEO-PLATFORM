import unittest


class TaskPayloadCompactionTests(unittest.TestCase):
    def test_task_store_compacts_heavy_debug_fields_when_payload_exceeds_threshold(self):
        from app.api.routers import _task_store

        payload = {
            "task_id": "big-task",
            "status": "SUCCESS",
            "result": {
                "variants": [
                    {
                        "console_log": {
                            "error_count": 12,
                            "warning_count": 8,
                            "errors": [f"err-{i}" for i in range(12)],
                            "warnings": [f"warn-{i}" for i in range(8)],
                        },
                        "raw_html": "x" * 900000,
                        "rendered_html": "y" * 900000,
                    }
                ]
            },
        }

        compacted = _task_store._compact_task_payload("big-task", payload)

        self.assertTrue(compacted["storage_meta"]["payload_compacted"])
        variant = compacted["result"]["variants"][0]
        self.assertNotIn("raw_html", variant)
        self.assertNotIn("rendered_html", variant)
        self.assertEqual(len(variant["console_log"]["errors"]), 5)
        self.assertEqual(len(variant["console_log"]["warnings"]), 5)
        self.assertGreater(compacted["storage_meta"]["original_bytes"], compacted["storage_meta"]["stored_bytes"])

    def test_task_store_keeps_small_payload_unchanged(self):
        from app.api.routers import _task_store

        payload = {"task_id": "small-task", "status": "SUCCESS", "result": {"message": "ok"}}
        compacted = _task_store._compact_task_payload("small-task", payload)
        self.assertEqual(compacted, payload)


class ProgressPayloadCompactionTests(unittest.TestCase):
    def test_progress_tracker_compacts_heavy_extra_fields(self):
        from app.core.progress import ProgressTracker

        tracker = ProgressTracker()
        extra = {
            "phase": "render",
            "rendered_html": "z" * 200000,
            "nested": {"raw_html": "q" * 200000},
        }

        compacted = tracker._compact_extra(extra)

        self.assertNotIn("rendered_html", compacted)
        self.assertNotIn("raw_html", compacted["nested"])
        self.assertTrue(compacted["_storage_meta"]["payload_compacted"])


if __name__ == "__main__":
    unittest.main()
