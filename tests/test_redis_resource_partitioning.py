import unittest


class RedisResourcePartitioningTests(unittest.TestCase):
    def test_task_store_uses_prefixed_key(self):
        from app.api.routers import _task_store
        from app.config import settings

        self.assertEqual(_task_store._task_key("demo"), f"{settings.TASK_STORE_REDIS_PREFIX}:demo")

    def test_llm_queue_uses_default_prefix_when_explicit_keys_empty(self):
        from app.tools.llmCrawler import queue
        from app.config import settings

        self.assertEqual(queue.queue_key(), f"{settings.LLM_CRAWLER_REDIS_PREFIX}:queue")
        self.assertEqual(queue.job_key("abc"), f"{settings.LLM_CRAWLER_REDIS_PREFIX}:job:abc")
        self.assertEqual(queue._llm_prefix(), settings.LLM_CRAWLER_REDIS_PREFIX)

    def test_progress_prefix_applied(self):
        from app.core.progress import ProgressTracker
        from app.config import settings

        tracker = ProgressTracker()
        self.assertEqual(tracker._get_key("demo"), f"{settings.PROGRESS_REDIS_PREFIX}:demo")

    def test_ops_status_masks_credentials(self):
        from app.api.routers.tasks import _mask_redis_url

        masked = _mask_redis_url("redis://default:supersecret@redis.railway.internal:6379/0")
        self.assertEqual(masked, "redis://default:***@redis.railway.internal:6379/0")


if __name__ == "__main__":
    unittest.main()
