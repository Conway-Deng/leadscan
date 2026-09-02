"""Real Chromium proof for the browser, detection, scoring and worker path."""

import json
import os
import sys
import threading
import urllib.parse
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

import config  # noqa: E402
import robots  # noqa: E402
import runner  # noqa: E402
import serve  # noqa: E402

SITE_DIR = os.path.join(ROOT, "site")
PRODUCTION_WORKER_ORIGIN = "https://leadscan-9fsy.onrender.com"
PRODUCTION_AUDIT_URL = f"{PRODUCTION_WORKER_ORIGIN}/api/audit"


def assert_only_local_and_mocked_worker_requests(request_urls):
    for request_url in request_urls:
        if request_url.startswith(("http://", "https://")):
            parsed = urllib.parse.urlparse(request_url)
            if parsed.hostname != "127.0.0.1":
                assert request_url == PRODUCTION_AUDIT_URL, f"Unexpected external request: {request_url}"


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


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


@pytest.fixture
def local_frontend_url():
    handler = partial(QuietStaticHandler, directory=SITE_DIR)
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture(scope="module")
def frontend_chromium():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


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


def test_public_frontend_success_flow_in_real_chromium(
    local_frontend_url,
    frontend_chromium,
):
    page = frontend_chromium.new_page()
    try:
        captured = {}
        captured_urls = []
        page.on("request", lambda req: captured_urls.append(req.url))

        report_html = (
            "<!doctype html>"
            "<html><body>"
            '<h1 id="mock-report">Mock LeadScan report</h1>'
            "<p>Browser E2E report body.</p>"
            "</body></html>"
        )

        def handle_audit(route):
            req = route.request
            captured["method"] = req.method
            captured["url"] = req.url
            captured["headers"] = req.headers
            captured["post_data"] = req.post_data_json
            response_payload = {
                "ok": True,
                "code": "ok",
                "result": {
                    "url": "https://example.com",
                    "final_url": "https://example.com/home",
                    "score": 87,
                    "tier": "strong",
                    "hook": "A concrete browser-test opening line.",
                    "report_html": report_html,
                },
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(response_payload),
            )

        page.route(PRODUCTION_AUDIT_URL, handle_audit)

        page.goto(local_frontend_url)
        assert page.title() == "LeadScan — Free Website Review"

        url_input = page.locator("#website-url")
        url_input.fill("  example.com  ")

        name_input = page.locator("#contact-name")
        name_input.fill("  Alice Owner  ")

        email_input = page.locator("#contact-email")
        email_input.fill("alice@example.com")

        submit_btn = page.locator("#audit-submit")
        submit_btn.click()

        status_el = page.locator("#audit-status")
        expect(status_el).to_have_text("Review complete.")

        # Request verification
        assert captured["method"] == "POST"
        assert captured["url"] == PRODUCTION_AUDIT_URL
        assert captured["post_data"] == {
            "url": "example.com",
            "contact_name": "Alice Owner",
            "email": "alice@example.com",
        }
        content_type_header = {k.lower(): v for k, v in captured["headers"].items()}.get("content-type", "")
        assert "application/json" in content_type_header

        # Success DOM verification
        expect(page.locator("#audit-form")).to_be_hidden()
        expect(page.locator("#audit-result")).to_be_visible()
        expect(page.locator("#result-score")).to_have_text("87")
        expect(page.locator("#result-url")).to_have_text("https://example.com/home")
        assert page.locator("#result-tier").count() == 0
        assert page.locator("#result-hook").count() == 0

        # Report srcdoc + iframe render verification
        iframe_srcdoc = page.eval_on_selector("#report-preview", "el => el.srcdoc")
        assert iframe_srcdoc == report_html

        frame = page.frame_locator("#report-preview")
        expect(frame.locator("#mock-report")).to_have_text("Mock LeadScan report")

        # Reset flow verification
        page.locator("#audit-reset").click()
        expect(page.locator("#audit-form")).to_be_visible()
        expect(page.locator("#audit-result")).to_be_hidden()
        expect(url_input).to_have_value("")
        expect(url_input).to_be_enabled()
        expect(name_input).to_have_value("")
        expect(name_input).to_be_enabled()
        expect(email_input).to_have_value("")
        expect(email_input).to_be_enabled()
        expect(submit_btn).to_be_enabled()
        expect(submit_btn).to_have_text("Get my free review")
        expect(status_el).to_have_text("")
        assert page.eval_on_selector("#report-preview", "el => el.srcdoc") == ""
        expect(page.locator("#result-url")).to_have_text("")
        assert page.evaluate("document.activeElement === document.getElementById('website-url')") is True

        # External network check
        assert_only_local_and_mocked_worker_requests(captured_urls)
    finally:
        page.close()


def test_public_frontend_rate_limit_flow_in_real_chromium(
    local_frontend_url,
    frontend_chromium,
):
    page = frontend_chromium.new_page()
    try:
        captured_urls = []
        page.on("request", lambda req: captured_urls.append(req.url))

        def handle_rate_limited(route):
            response_payload = {
                "ok": False,
                "code": "rate_limited",
            }
            route.fulfill(
                status=429,
                headers={
                    "Access-Control-Expose-Headers": "Retry-After",
                    "Retry-After": "17",
                },
                content_type="application/json",
                body=json.dumps(response_payload),
            )

        page.route(PRODUCTION_AUDIT_URL, handle_rate_limited)

        page.goto(local_frontend_url)
        url_input = page.locator("#website-url")
        url_input.fill("example.com")

        name_input = page.locator("#contact-name")
        name_input.fill("Alice Owner")

        email_input = page.locator("#contact-email")
        email_input.fill("alice@example.com")

        submit_btn = page.locator("#audit-submit")
        submit_btn.click()

        status_el = page.locator("#audit-status")
        expect(status_el).to_contain_text("Too many review requests have been made.")
        expect(status_el).to_contain_text("17 seconds")

        # Error recovery UI assertions
        expect(page.locator("#audit-form")).to_be_visible()
        expect(page.locator("#audit-result")).to_be_hidden()
        expect(url_input).to_be_enabled()
        expect(name_input).to_be_enabled()
        expect(email_input).to_be_enabled()
        expect(submit_btn).to_be_enabled()
        expect(submit_btn).to_have_text("Get my free review")
        expect(url_input).to_have_value("example.com")
        expect(name_input).to_have_value("Alice Owner")
        expect(email_input).to_have_value("alice@example.com")

        # External network check
        assert_only_local_and_mocked_worker_requests(captured_urls)
    finally:
        page.close()


def test_public_frontend_lead_capture_failure_recovers_in_real_chromium(
    local_frontend_url,
    frontend_chromium,
):
    page = frontend_chromium.new_page()
    try:
        captured_urls = []
        page.on("request", lambda req: captured_urls.append(req.url))

        def handle_capture_failed(route):
            response_payload = {
                "ok": False,
                "code": "lead_capture_failed",
            }
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps(response_payload),
            )

        page.route(PRODUCTION_AUDIT_URL, handle_capture_failed)

        page.goto(local_frontend_url)
        url_input = page.locator("#website-url")
        url_input.fill("example.com")

        name_input = page.locator("#contact-name")
        name_input.fill("Alice Owner")

        email_input = page.locator("#contact-email")
        email_input.fill("alice@example.com")

        submit_btn = page.locator("#audit-submit")
        submit_btn.click()

        status_el = page.locator("#audit-status")
        expect(status_el).to_contain_text("We could not save your contact details.")

        # Error recovery UI assertions
        expect(page.locator("#audit-form")).to_be_visible()
        expect(page.locator("#audit-result")).to_be_hidden()
        expect(url_input).to_be_enabled()
        expect(name_input).to_be_enabled()
        expect(email_input).to_be_enabled()
        expect(submit_btn).to_be_enabled()
        expect(submit_btn).to_have_text("Get my free review")
        expect(url_input).to_have_value("example.com")
        expect(name_input).to_have_value("Alice Owner")
        expect(email_input).to_have_value("alice@example.com")
        expect(page.locator("#result-score")).to_have_text("")

        # External network check
        assert_only_local_and_mocked_worker_requests(captured_urls)
    finally:
        page.close()


def test_public_frontend_configured_worker_origin_in_real_chromium(
    local_frontend_url,
    frontend_chromium,
):
    page = frontend_chromium.new_page()
    try:
        captured = {}
        captured_urls = []
        page.on("request", lambda req: captured_urls.append(req.url))

        report_html = (
            "<!doctype html>"
            "<html><body>"
            '<h1 id="mock-report">Mock Cross-Origin LeadScan report</h1>'
            "<p>Browser cross-origin E2E report body.</p>"
            "</body></html>"
        )

        def handle_cross_origin_audit(route):
            req = route.request
            captured["method"] = req.method
            captured["url"] = req.url
            captured["headers"] = req.headers
            captured["post_data"] = req.post_data_json
            response_payload = {
                "ok": True,
                "code": "ok",
                "result": {
                    "url": "https://example.com",
                    "final_url": "https://example.com/home",
                    "score": 87,
                    "tier": "strong",
                    "hook": "A concrete cross-origin opening line.",
                    "report_html": report_html,
                },
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(response_payload),
            )

        page.route("https://worker.example/api/audit", handle_cross_origin_audit)

        page.goto(local_frontend_url)
        assert page.title() == "LeadScan — Free Website Review"

        # Update meta leadscan-api-origin to https://worker.example/ before submitting
        page.eval_on_selector(
            'meta[name="leadscan-api-origin"]',
            'el => el.setAttribute("content", "https://worker.example/")',
        )

        url_input = page.locator("#website-url")
        url_input.fill("example.com")

        name_input = page.locator("#contact-name")
        name_input.fill("Alice Owner")

        email_input = page.locator("#contact-email")
        email_input.fill("alice@example.com")

        submit_btn = page.locator("#audit-submit")
        submit_btn.click()

        status_el = page.locator("#audit-status")
        expect(status_el).to_have_text("Review complete.")

        # Intercepted cross-origin request verification
        assert captured["method"] == "POST"
        assert captured["url"] == "https://worker.example/api/audit"
        assert captured["post_data"] == {
            "url": "example.com",
            "contact_name": "Alice Owner",
            "email": "alice@example.com",
        }
        content_type_header = {k.lower(): v for k, v in captured["headers"].items()}.get("content-type", "")
        assert "application/json" in content_type_header

        # Success DOM verification
        expect(page.locator("#audit-form")).to_be_hidden()
        expect(page.locator("#audit-result")).to_be_visible()
        expect(page.locator("#result-score")).to_have_text("87")
        expect(page.locator("#result-url")).to_have_text("https://example.com/home")
        assert page.locator("#result-tier").count() == 0
        assert page.locator("#result-hook").count() == 0

        # Report srcdoc + iframe render verification
        iframe_srcdoc = page.eval_on_selector("#report-preview", "el => el.srcdoc")
        assert iframe_srcdoc == report_html

        frame = page.frame_locator("#report-preview")
        expect(frame.locator("#mock-report")).to_have_text("Mock Cross-Origin LeadScan report")

        # Verify invalid configured origin fails closed without sending fetch
        page.locator("#audit-reset").click()
        expect(page.locator("#audit-form")).to_be_visible()

        page.eval_on_selector(
            'meta[name="leadscan-api-origin"]',
            'el => el.setAttribute("content", "https://invalid.example/non-root-path")',
        )
        url_input.fill("example.com")
        email_input.fill("alice@example.com")
        submit_btn.click()

        expect(status_el).to_contain_text("temporarily unavailable")
        expect(page.locator("#audit-form")).to_be_visible()
        expect(url_input).to_be_enabled()
        expect(email_input).to_be_enabled()
        expect(submit_btn).to_be_enabled()

        # External network check: only 127.0.0.1 and intercepted https://worker.example/api/audit allowed
        for req_url in captured_urls:
            if req_url.startswith(("http://", "https://")):
                parsed = urllib.parse.urlparse(req_url)
                if parsed.hostname != "127.0.0.1":
                    assert req_url == "https://worker.example/api/audit", f"Unexpected external request: {req_url}"
    finally:
        page.close()
