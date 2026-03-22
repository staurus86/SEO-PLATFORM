"""Verify PLAYWRIGHT_BROWSERS_PATH is configured correctly for containerised runtime."""

import os
import subprocess
import textwrap

import pytest


def test_railway_toml_has_playwright_browsers_path():
    """railway.toml must export PLAYWRIGHT_BROWSERS_PATH so the web service finds installed browsers."""
    toml_path = os.path.join(os.path.dirname(__file__), os.pardir, "railway.toml")
    content = open(toml_path).read()
    assert "PLAYWRIGHT_BROWSERS_PATH" in content, (
        "railway.toml is missing PLAYWRIGHT_BROWSERS_PATH in [deploy.env]. "
        "Without it the web service will look in the default ~/.cache path "
        "instead of /ms-playwright where browsers are installed."
    )


def test_entrypoint_exports_playwright_path_for_web():
    """entrypoint.sh must export PLAYWRIGHT_BROWSERS_PATH before starting uvicorn."""
    sh_path = os.path.join(os.path.dirname(__file__), os.pardir, "entrypoint.sh")
    content = open(sh_path).read()
    # The export must appear in the else (web) branch, not only in llm-worker
    web_section = content.split("Starting in WEB mode")[-1]
    assert "PLAYWRIGHT_BROWSERS_PATH" in web_section, (
        "entrypoint.sh does not export PLAYWRIGHT_BROWSERS_PATH in web mode. "
        "Playwright will fall back to ~/.cache and fail to find browsers."
    )


def test_entrypoint_does_not_install_playwright_at_runtime_for_llm_worker():
    """llm-worker should fail fast when browsers are missing instead of installing them at runtime."""
    sh_path = os.path.join(os.path.dirname(__file__), os.pardir, "entrypoint.sh")
    content = open(sh_path).read()
    llm_section = content.split('elif [ "$SERVICE_MODE" = "llm-worker" ]; then')[-1].split("else")[0]
    assert "python -m playwright install" not in llm_section, (
        "entrypoint.sh still installs Playwright browsers at runtime in llm-worker mode. "
        "This slows deployments and makes worker startup unstable on Railway."
    )
    assert "Runtime install is disabled for stability." in llm_section


def test_dockerfile_installs_to_ms_playwright():
    """Dockerfile must set ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright and install browsers there."""
    dockerfile = os.path.join(os.path.dirname(__file__), os.pardir, "Dockerfile")
    content = open(dockerfile).read()
    assert "ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in content
    assert "playwright install" in content
