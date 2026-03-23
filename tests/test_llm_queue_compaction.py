import unittest


class LlmQueueCompactionTests(unittest.TestCase):
    def test_llm_job_observability_tracks_queue_wait_and_runtime(self):
        from app.core.ops_observability import get_ops_observability_stats
        from app.tools.llmCrawler.queue import create_job_record, save_job_record, update_job_record

        before = get_ops_observability_stats()["llm_jobs"]
        job = create_job_record(
            job_id="llm-obs-job",
            request_id="req-1",
            requested_url="https://example.com",
            options={},
        )
        save_job_record(job)
        update_job_record("llm-obs-job", status="running", progress=5)
        update_job_record("llm-obs-job", status="done", progress=100, duration_ms=123, result={"ok": True})
        after = get_ops_observability_stats()["llm_jobs"]

        self.assertGreaterEqual(after["queue_wait_ms"]["count"], before["queue_wait_ms"]["count"] + 1)
        self.assertGreaterEqual(after["run_duration_ms"]["count"], before["run_duration_ms"]["count"] + 1)
        self.assertGreaterEqual(after["end_to_end_ms"]["count"], before["end_to_end_ms"]["count"] + 1)
        self.assertGreaterEqual(after["result_payload_bytes"]["count"], before["result_payload_bytes"]["count"] + 1)
        self.assertIn("recent_15m", after["queue_wait_ms"])
        self.assertIn("recent_60m", after["end_to_end_ms"])
        self.assertIn("recent_15m", after["result_payload_bytes"])

    def test_compaction_stats_accumulate(self):
        from app.tools.llmCrawler.queue import _truncate_heavy_fields, get_compaction_stats

        before = get_compaction_stats()["compactions_total"]
        _truncate_heavy_fields(
            {
                "jobId": "llm-job-stats",
                "result": {
                    "options": {"include_raw_html": False, "include_rendered_html": False},
                    "content_segments": [{"id": i} for i in range(50)],
                    "nojs": {"raw_html": "x" * 1000},
                },
            }
        )
        after = get_compaction_stats()

        self.assertGreaterEqual(after["compactions_total"], before + 1)
        self.assertGreater(after["bytes_saved_total"], 0)

    def test_truncate_heavy_fields_limits_debug_arrays(self):
        from app.tools.llmCrawler.queue import _truncate_heavy_fields

        job = {
            "jobId": "llm-job",
            "result": {
                "options": {"include_raw_html": False, "include_rendered_html": False},
                "content_segments": [{"id": i} for i in range(50)],
                "segment_tree": [{"id": i} for i in range(80)],
                "main_content_nodes": list(range(100)),
                "noise_nodes": list(range(150)),
                "chunk_ranking_debug": [{"idx": i, "score": i / 100} for i in range(40)],
                "recommendation_diagnostics": {
                    "strengths": [f"s{i}" for i in range(20)],
                    "warnings": [f"w{i}" for i in range(20)],
                    "actions": [f"a{i}" for i in range(20)],
                },
                "segmentation": {
                    "content_segments": [{"id": i} for i in range(50)],
                    "segment_tree": [{"id": i} for i in range(80)],
                    "main_content_nodes": list(range(100)),
                    "noise_nodes": list(range(150)),
                },
                "nojs": {"raw_html": "x" * 1000, "content": {"readability_text": "y" * 250000}},
                "rendered": {"rendered_html": "z" * 1000, "content": {"trafilatura_text": "q" * 250000}},
            },
        }

        compacted = _truncate_heavy_fields(job)
        result = compacted["result"]

        self.assertEqual(len(result["content_segments"]), 20)
        self.assertEqual(len(result["segment_tree"]), 30)
        self.assertEqual(len(result["main_content_nodes"]), 40)
        self.assertEqual(len(result["noise_nodes"]), 60)
        self.assertEqual(len(result["chunk_ranking_debug"]), 20)
        self.assertEqual(len(result["recommendation_diagnostics"]["strengths"]), 10)
        self.assertNotIn("raw_html", result["nojs"])
        self.assertNotIn("rendered_html", result["rendered"])
        self.assertLessEqual(len(result["nojs"]["content"]["readability_text"]), 200000)
        self.assertLessEqual(len(result["rendered"]["content"]["trafilatura_text"]), 200000)
        self.assertTrue(result["storage_meta"]["content_segments_omitted"] > 0)
        self.assertTrue(result["storage_meta"]["segmentation_segment_tree_omitted"] > 0)


if __name__ == "__main__":
    unittest.main()
