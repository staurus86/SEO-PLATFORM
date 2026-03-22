import unittest


class LlmQueueCompactionTests(unittest.TestCase):
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
