import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.memory_guard import run_memory_cleanup_now
from app.core.task_cleanup import prune_stale_report_artifacts
from app.tools.llmCrawler.queue import cleanup_expired_jobs, queue_depth, get_worker_heartbeat


def run_maintenance(*, stale_days: int, force_gc: bool) -> Dict[str, Any]:
    artifacts = prune_stale_report_artifacts(max_age_days=stale_days)
    cleanup_expired_jobs()
    memory = run_memory_cleanup_now(force_gc=force_gc)
    heartbeat = get_worker_heartbeat()
    queue_size = queue_depth()
    return {
        "status": "SUCCESS",
        "artifacts": artifacts,
        "memory": memory,
        "llm_queue_depth": queue_size,
        "llm_worker_heartbeat": heartbeat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run periodic maintenance for SEO Tools Platform.")
    parser.add_argument("--stale-days", type=int, default=7, help="Delete report artifacts older than this many days.")
    parser.add_argument("--force-gc", action="store_true", help="Force Python GC after registered cleanups.")
    args = parser.parse_args()

    summary = run_maintenance(stale_days=max(1, int(args.stale_days)), force_gc=bool(args.force_gc))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
