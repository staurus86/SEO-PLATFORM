import argparse
import sys
from typing import Iterable

import requests


def _check(session: requests.Session, url: str, expected: int = 200, contains: Iterable[str] | None = None) -> tuple[bool, str]:
    try:
        response = session.get(url, timeout=20)
    except Exception as exc:
        return False, f"GET {url} failed: {exc}"

    if response.status_code != expected:
        return False, f"GET {url} returned {response.status_code}, expected {expected}"

    body = response.text
    for needle in contains or []:
        if needle not in body:
            return False, f"GET {url} missing marker: {needle}"
    return True, f"GET {url} -> {response.status_code}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-deploy smoke checks for SEO Tools Platform.")
    parser.add_argument("--base-url", required=True, help="Base deployment URL, e.g. https://seo-tools-platform.up.railway.app")
    parser.add_argument("--llm", action="store_true", help="Also validate LLM crawler results v2 assets/template markers.")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = requests.Session()

    checks = [
        (f"{base}/health", 200, ['"status":"healthy"']),
        (f"{base}/", 200, ["Full SEO Audit", "Batch Mode", "Site Audit Pro", "/static/js/app.js?v="]),
        (f"{base}/api/docs", 200, ["Swagger UI"]),
    ]

    if args.llm:
        checks.append((f"{base}/llm-crawler/results/demo", 200, ["/static/css/llm-crawler-v2.css?v=", "/static/js/llm-crawler-v2.js?v="]))

    failures = []
    for url, expected, contains in checks:
        ok, message = _check(session, url, expected=expected, contains=contains)
        print(("OK   " if ok else "FAIL ") + message)
        if not ok:
            failures.append(message)

    if failures:
        print("\nSmoke check failed.")
        return 1

    print("\nSmoke check passed.")
    print("Manual follow-up still recommended: Full SEO Audit submit, Batch Mode redirect, Site Audit Pro submit, export download, LLM dark mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
