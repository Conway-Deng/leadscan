"""Real Chromium proof for the browser, detection, scoring and worker path."""

import os
import sys
import threading
import urllib.parse
from http.server import HTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

import config  # noqa: E402
import robots  # noqa: E402
import runner  # noqa: E402
import serve  # noqa: E402


@pytest.fixture
def local_fixture_url():
    server = HTTPServer(("127.0.0.1", 0), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/javascript"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_chromium_executes_javascript_through_worker_pipeline(
        local_fixture_url, monkeypatch):
    monkeypatch.setattr(config, "POLITE_DELAY_SECONDS", 0)

    def unexpected_robots_request(_url):
        raise AssertionError("robots.txt must not be fetched for this fixture")

    monkeypatch.setattr(robots, "may_fetch", unexpected_robots_request)
    assert "<form" not in serve.JAVASCRIPT.lower()
    assert "http://" not in serve.JAVASCRIPT.lower()
    assert "https://" not in serve.JAVASCRIPT.lower()

    rows = runner.run_audits(
        [{
            "place_id": "browser-integration",
            "name": "JavaScript Capture Studio",
            "website": local_fixture_url,
            "phone": "",
            "review_count": 12,
        }],
        workers=1,
        deep=False,
        respect_robots=False,
        log=lambda _message: None,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ok"
    final_url = urllib.parse.urlparse(row["final_url"])
    fixture_url = urllib.parse.urlparse(local_fixture_url)
    assert final_url.hostname == "127.0.0.1"
    assert final_url.port == fixture_url.port
    assert row["_findings"]["can_capture_lead"] is True
    assert "contact form" in row["_findings"]["capture_methods"]
    assert "contact form" in row["capture_methods"]
