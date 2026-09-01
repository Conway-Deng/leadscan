import asyncio
import sqlite3
import threading
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import pytest

import lead_capture
import public_api
import public_audit
from public_limits import ConcurrencyGate, SlidingWindowRateLimiter


def make_ok_payload(url="https://example.com"):
    return {
        "ok": True,
        "code": public_audit.OK,
        "result": {
            "url": url,
            "final_url": f"{url}/home",
            "score": 80,
            "tier": "good",
            "hook": "Strong digital footprint",
            "report_html": "<html>report</html>",
        },
    }


def make_capture_body(
    url="https://example.com",
    contact_name="Test User",
    email="test@example.com",
):
    return {
        "url": url,
        "contact_name": contact_name,
        "email": email,
    }


def test_post_audit_success():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(audit_runner=audit_runner, lead_store=lead_store)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"
    assert "Retry-After" not in response.headers
    assert response.json() == make_ok_payload("https://example.com")
    lead_store.save_lead.assert_called_once_with(
        contact_name="Test User",
        email="test@example.com",
        website_url="https://example.com",
    )


def test_audit_runner_receives_exact_arguments():
    captured = {}

    def fake_runner(submitted_url, client_key, audit_limiter, gate):
        captured["url"] = submitted_url
        captured["client_key"] = client_key
        captured["audit_limiter"] = audit_limiter
        captured["gate"] = gate
        return make_ok_payload(submitted_url)

    envelope_limiter = MagicMock(spec=SlidingWindowRateLimiter)
    envelope_limiter.allow.return_value = (True, 0)
    audit_limiter = MagicMock(spec=SlidingWindowRateLimiter)
    gate = MagicMock(spec=ConcurrencyGate)
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        envelope_limiter=envelope_limiter,
        audit_limiter=audit_limiter,
        gate=gate,
        audit_runner=fake_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com/test"))
    assert response.status_code == 200
    assert captured["url"] == "https://example.com/test"
    assert captured["client_key"] in ("testclient", "127.0.0.1", "<unknown>")
    assert captured["audit_limiter"] is audit_limiter
    assert captured["gate"] is gate


def test_envelope_limiter_runs_before_body_parsing():
    envelope_limiter = MagicMock(spec=SlidingWindowRateLimiter)
    envelope_limiter.allow.return_value = (False, 17)

    audit_runner = MagicMock()
    app = public_api.create_app(
        envelope_limiter=envelope_limiter,
        audit_runner=audit_runner,
    )
    client = TestClient(app)

    # Send completely broken raw content with wrong content-type
    response = client.post(
        "/api/audit",
        content=b"this is not json and exceeds parsing rules",
        headers={"Content-Type": "text/plain"},
    )

    # Must be 429 (envelope rate limit), NOT 400 (invalid request)
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "17"
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.json() == {
        "ok": False,
        "code": public_audit.RATE_LIMITED,
        "retry_after": 17,
    }
    audit_runner.assert_not_called()


def test_malformed_json_after_envelope_admission():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_wrong_content_type():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        content=b'{"url":"https://example.com","contact_name":"Alice","email":"alice@example.com"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_missing_content_type():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        content=b'{"url":"https://example.com","contact_name":"Alice","email":"alice@example.com"}',
        headers={},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_oversized_content_length():
    audit_runner = MagicMock()
    app = public_api.create_app(audit_runner=audit_runner)
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        content=b'{"url":"https://example.com","contact_name":"Alice","email":"alice@example.com"}',
        headers={
            "Content-Type": "application/json",
            "Content-Length": "10000",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}
    audit_runner.assert_not_called()


def test_oversized_streamed_body():
    audit_runner = MagicMock()
    app = public_api.create_app(audit_runner=audit_runner)
    client = TestClient(app)

    huge_body = b'{"url":"' + (b"a" * 5000) + b'","contact_name":"Alice","email":"alice@example.com"}'
    response = client.post(
        "/api/audit",
        content=huge_body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}
    audit_runner.assert_not_called()


def test_missing_url_key():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post("/api/audit", json={"not_url": "https://example.com", "contact_name": "A", "email": "a@example.com"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_extra_json_keys():
    app = public_api.create_app()
    client = TestClient(app)

    # Missing contact keys with extra key
    response = client.post(
        "/api/audit",
        json={"url": "https://example.com", "admin": True},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}

    # Capture with extra key
    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
            "extra": 1,
        },
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_non_string_url():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(lead_store=lead_store)
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": 12345,
            "contact_name": "Test User",
            "email": "test@example.com",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_valid_body_preserves_url_exact_whitespace():
    passed_url = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_url.append(u) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post("/api/audit", json=make_capture_body("   example.com   "))
    assert passed_url == ["   example.com   "]


def test_default_client_identity_ignores_spoofed_headers():
    passed_keys = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
        headers={
            "X-Forwarded-For": "1.2.3.4",
            "X-Real-IP": "5.6.7.8",
            "CF-Connecting-IP": "9.10.11.12",
        },
    )

    assert len(passed_keys) == 1
    assert passed_keys[0] in ("testclient", "127.0.0.1", "<unknown>")
    assert passed_keys[0] not in ("1.2.3.4", "5.6.7.8", "9.10.11.12")


def test_configured_trusted_single_ip_header_works():
    passed_keys = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        trusted_client_header="CF-Connecting-IP",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
        headers={"CF-Connecting-IP": "203.0.113.9"},
    )

    assert passed_keys == ["203.0.113.9"]


def test_configured_trusted_header_rejects_comma_chain():
    passed_keys = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        trusted_client_header="X-Forwarded-For",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )

    assert len(passed_keys) == 1
    assert passed_keys[0] in ("testclient", "127.0.0.1", "<unknown>")
    assert "203.0.113.9" not in passed_keys[0]


def test_malformed_trusted_ip_falls_back_to_peer():
    passed_keys = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        trusted_client_header="X-Real-IP",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
        headers={"X-Real-IP": "not-an-ip-address"},
    )

    assert len(passed_keys) == 1
    assert passed_keys[0] in ("testclient", "127.0.0.1", "<unknown>")


def test_trusted_header_read_from_env_var(monkeypatch):
    monkeypatch.setenv("LEADSCAN_TRUSTED_CLIENT_IP_HEADER", "X-Custom-Client-IP")
    passed_keys = []
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
        lead_store=lead_store,
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
        headers={"X-Custom-Client-IP": "198.51.100.22"},
    )

    assert passed_keys == ["198.51.100.22"]


def test_service_rate_limited_response():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {
            "ok": False,
            "code": public_audit.RATE_LIMITED,
            "retry_after": 25,
        },
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "25"
    assert response.json() == {
        "ok": False,
        "code": public_audit.RATE_LIMITED,
        "retry_after": 25,
    }


def test_service_busy_response():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.BUSY},
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "1"
    assert response.json() == {"ok": False, "code": public_audit.BUSY}


def test_service_invalid_url_response():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.INVALID_URL},
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_audit.INVALID_URL}


def test_service_audit_timeout_response():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.AUDIT_TIMEOUT},
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 504
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}


def test_service_audit_failed_response():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.AUDIT_FAILED},
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_service_unexpected_exception_fails_closed():
    def exploding_runner(u, k, l, g):
        raise RuntimeError("Internal DB connection failed on host 10.0.0.99")

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(audit_runner=exploding_runner, lead_store=lead_store)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert "10.0.0.99" not in response.text
    assert "Internal DB" not in response.text


def test_unknown_service_code_fails_closed_to_500():
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": "some_alien_code"},
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_docs_and_openapi_endpoints_disabled():
    app = public_api.create_app()
    client = TestClient(app)

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_no_additional_public_routes():
    app = public_api.create_app()
    paths = [route.path for route in app.routes]
    assert paths == ["/api/audit"]


def test_worker_state_accepting_and_draining_idempotent():
    ws = public_api.WorkerState()
    assert ws.accepting is True

    ws.begin_draining()
    assert ws.accepting is False

    # Idempotent
    ws.begin_draining()
    assert ws.accepting is False


def test_draining_endpoint_returns_503_busy():
    ws = public_api.WorkerState()
    ws.begin_draining()

    app = public_api.create_app(worker_state=ws)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "1"
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.json() == {"ok": False, "code": public_audit.BUSY}


def test_draining_runs_before_envelope_limiter():
    ws = public_api.WorkerState()
    ws.begin_draining()

    envelope_limiter = MagicMock(spec=SlidingWindowRateLimiter)
    envelope_limiter.allow.side_effect = AssertionError("Envelope limiter must not be called when draining!")

    app = public_api.create_app(worker_state=ws, envelope_limiter=envelope_limiter)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 503
    assert response.json() == {"ok": False, "code": public_audit.BUSY}


def test_draining_runs_before_body_parsing():
    ws = public_api.WorkerState()
    ws.begin_draining()

    app = public_api.create_app(worker_state=ws)
    client = TestClient(app)

    # Malformed non-JSON body still yields 503 when draining
    response = client.post(
        "/api/audit",
        content=b"broken raw content",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 503
    assert response.json() == {"ok": False, "code": public_audit.BUSY}


def test_draining_does_not_call_audit_runner():
    ws = public_api.WorkerState()
    ws.begin_draining()

    audit_runner = MagicMock()
    app = public_api.create_app(worker_state=ws, audit_runner=audit_runner)
    client = TestClient(app)

    client.post("/api/audit", json=make_capture_body("https://example.com"))
    audit_runner.assert_not_called()


def test_fastapi_lifespan_calls_begin_draining_on_shutdown():
    ws = public_api.WorkerState()
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(worker_state=ws, lead_store=lead_store)

    assert ws.accepting is True
    with TestClient(app) as client:
        assert ws.accepting is True
        res = client.post("/api/audit", json=make_capture_body("https://example.com"))

    # On context exit, lifespan shutdown hook drains worker
    assert ws.accepting is False


def test_invalid_audit_wait_seconds_raises_value_error():
    for bad in (0, -1, 0.0, -10.5, False, True, "105"):
        with pytest.raises(ValueError):
            public_api.create_app(audit_wait_seconds=bad)


def test_http_timeout_while_audit_runner_blocks():
    event = threading.Event()

    def blocking_runner(submitted_url, client_key, audit_limiter, gate):
        event.wait(timeout=2.0)
        return make_ok_payload(submitted_url)

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=blocking_runner,
        lead_store=lead_store,
        audit_wait_seconds=0.05,
    )
    client = TestClient(app)

    try:
        response = client.post("/api/audit", json=make_capture_body("https://example.com"))
        assert response.status_code == 504
        assert response.json() == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}
    finally:
        event.set()


def test_http_timeout_covers_body_parsing(monkeypatch):
    async def slow_read_audit_request(request):
        await asyncio.sleep(0.1)
        return "https://example.com", "Alice", "alice@example.com"

    monkeypatch.setattr(public_api, "_read_audit_request", slow_read_audit_request)

    audit_runner = MagicMock()
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
        audit_wait_seconds=0.03,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 504
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}
    audit_runner.assert_not_called()


def test_oversized_service_response_fails_closed():
    def oversized_runner(submitted_url, client_key, audit_limiter, gate):
        huge_html = "x" * (public_api.MAX_RESPONSE_BODY_BYTES + 1024)
        return {
            "ok": True,
            "code": public_audit.OK,
            "result": {
                "url": submitted_url,
                "final_url": submitted_url,
                "score": 100,
                "tier": "good",
                "hook": "hook",
                "report_html": huge_html,
            },
        }

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(audit_runner=oversized_runner, lead_store=lead_store)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert len(response.content) <= public_api.MAX_RESPONSE_BODY_BYTES


def test_audit_response_serialization_failure_fails_closed():
    class Unserializable:
        pass

    def bad_runner(submitted_url, client_key, audit_limiter, gate):
        return {
            "ok": True,
            "code": public_audit.OK,
            "result": {"bad": Unserializable()},
        }

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    app = public_api.create_app(audit_runner=bad_runner, lead_store=lead_store)
    client = TestClient(app)

    response = client.post("/api/audit", json=make_capture_body("https://example.com"))
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_procfile_contents_and_single_worker():
    from pathlib import Path

    content = Path("Procfile").read_text(encoding="utf-8")
    expected_line = (
        "web: uvicorn public_api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-graceful-shutdown 120"
    )
    assert content.strip() == expected_line
    assert "--workers 1" in content
    assert "--reload" not in content
    assert "--workers 2" not in content
    assert "--workers 3" not in content
    assert "--workers 4" not in content


# ==============================================================================
# Task 9C-5B / 9C-5C1 Lead Capture Integration Tests
# ==============================================================================


def test_url_only_request_is_rejected_before_audit_or_capture():
    audit_runner = MagicMock()
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})

    assert response.status_code == 400
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}
    audit_runner.assert_not_called()
    lead_store.save_lead.assert_not_called()


def test_capture_request_saves_lead_after_successful_audit():
    audit_runner = MagicMock(return_value=make_ok_payload("   example.com   "))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.return_value = 42

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "   example.com   ",
            "contact_name": "  Alice Owner  ",
            "email": "  alice@example.com  ",
        },
    )

    assert response.status_code == 200
    assert response.json() == make_ok_payload("   example.com   ")
    assert audit_runner.call_args[0][0] == "   example.com   "
    lead_store.save_lead.assert_called_once_with(
        contact_name="Alice Owner",
        email="alice@example.com",
        website_url="   example.com   ",
    )


def test_save_happens_after_audit_call_order():
    events = []

    def fake_audit_runner(url, client_key, limiter, gate):
        events.append("audit")
        return make_ok_payload(url)

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.side_effect = lambda **kw: events.append("save") or 1

    app = public_api.create_app(
        audit_runner=fake_audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == 200
    assert events == ["audit", "save"]


@pytest.mark.parametrize(
    "error_code,expected_status",
    [
        (public_audit.INVALID_URL, 400),
        (public_audit.RATE_LIMITED, 429),
        (public_audit.BUSY, 503),
        (public_audit.AUDIT_TIMEOUT, 504),
        (public_audit.AUDIT_FAILED, 500),
    ],
)
def test_audit_failure_does_not_save_lead(error_code, expected_status):
    audit_runner = MagicMock(return_value={"ok": False, "code": error_code})
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == error_code
    lead_store.save_lead.assert_not_called()


def test_capture_request_without_store_fails_before_audit(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LEADSCAN_LEAD_DB_PATH", raising=False)
    audit_runner = MagicMock()

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=None,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "code": public_api.LEAD_CAPTURE_FAILED,
    }
    assert response.headers.get("Cache-Control") == "no-store"
    audit_runner.assert_not_called()


def test_capture_storage_failure_returns_generic_error_without_pii():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com/unique-path"))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.side_effect = lead_capture.LeadStoreError("Failed to store lead")

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    secret_name = "SuperSecretPerson"
    secret_email = "supersecretperson@classified.example.com"
    secret_url = "https://example.com/unique-path"

    response = client.post(
        "/api/audit",
        json={
            "url": secret_url,
            "contact_name": secret_name,
            "email": secret_email,
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "code": public_api.LEAD_CAPTURE_FAILED,
    }
    assert secret_name not in response.text
    assert secret_email not in response.text
    assert secret_url not in response.text
    assert "LeadStoreError" not in response.text


def test_unexpected_store_error_also_fails_closed():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.side_effect = RuntimeError("disk /secret/path failed")

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "code": public_api.LEAD_CAPTURE_FAILED,
    }
    assert "/secret/path" not in response.text


@pytest.mark.parametrize(
    "contact_name,email",
    [
        (123, "valid@example.com"),
        ("a" * 121, "valid@example.com"),
        ("bad\x00name", "valid@example.com"),
        ("Alice", 123),
        ("Alice", ""),
        ("Alice", "   "),
        ("Alice", "a" * 255),
        ("Alice", "bad\x00email@example.com"),
        ("Alice", "internal space@example.com"),
        ("Alice", "tab\temail@example.com"),
        ("Alice", "newline\n@example.com"),
        ("Alice", "noatsign.example.com"),
        ("Alice", "two@at@example.com"),
        ("Alice", "@example.com"),
        ("Alice", "user@"),
        ("Alice", ("a" * 65) + "@example.com"),
        ("Alice", ".leadingdot@example.com"),
        ("Alice", "trailingdot.@example.com"),
        ("Alice", "double..dot@example.com"),
        ("Alice", "user@.leadingdomaindot.com"),
        ("Alice", "user@trailingdomaindot.com."),
        ("Alice", "user@double..domain.com"),
    ],
)
def test_invalid_contact_fields_rejected_before_audit(contact_name, email):
    audit_runner = MagicMock()
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": contact_name,
            "email": email,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}
    audit_runner.assert_not_called()
    lead_store.save_lead.assert_not_called()
    if isinstance(contact_name, str) and contact_name:
        assert contact_name not in response.text
    if isinstance(email, str) and email:
        assert email not in response.text


@pytest.mark.parametrize(
    "partial_payload",
    [
        {"url": "https://example.com", "email": "a@example.com"},
        {"url": "https://example.com", "contact_name": "Alice"},
        {"contact_name": "Alice", "email": "a@example.com"},
    ],
)
def test_partial_contact_bodies_rejected(partial_payload):
    audit_runner = MagicMock()
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json=partial_payload)

    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}
    audit_runner.assert_not_called()
    lead_store.save_lead.assert_not_called()


def test_env_configured_lead_store_is_lazy_until_capture(tmp_path, monkeypatch):
    db_file = tmp_path / "private" / "leads.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LEADSCAN_LEAD_DB_PATH", str(db_file))

    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))

    app = public_api.create_app(audit_runner=audit_runner)
    assert not db_file.exists()

    client = TestClient(app)
    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Bob Owner",
            "email": "bob@example.com",
        },
    )

    assert response.status_code == 200
    assert db_file.exists()

    conn = sqlite3.connect(str(db_file))
    try:
        rows = conn.execute("SELECT contact_name, email, website_url, source FROM public_leads").fetchall()
        assert len(rows) == 1
        assert rows[0] == ("Bob Owner", "bob@example.com", "https://example.com", "public_audit_widget")
    finally:
        conn.close()


def test_env_whitespace_means_disabled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("LEADSCAN_LEAD_DB_PATH", "   ")
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))

    app = public_api.create_app(audit_runner=audit_runner)
    client = TestClient(app)

    # Capture request fails with 500 when store is disabled
    response = client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
    )
    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "code": public_api.LEAD_CAPTURE_FAILED,
    }
    audit_runner.assert_not_called()


def test_database_url_selects_postgres_store(monkeypatch):
    fake_store = MagicMock(spec=lead_capture.PostgresLeadStore)
    constructor = MagicMock(return_value=fake_store)
    monkeypatch.setattr(lead_capture, "PostgresLeadStore", constructor)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:fake-password@example.invalid/leadscan",
    )
    monkeypatch.delenv("LEADSCAN_LEAD_DB_PATH", raising=False)

    public_api.create_app()

    constructor.assert_called_once_with(
        "postgresql://user:fake-password@example.invalid/leadscan"
    )


def test_without_database_url_sqlite_path_selects_sqlite(monkeypatch, tmp_path):
    fake_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    constructor = MagicMock(return_value=fake_store)
    db_path = str(tmp_path / "leads.sqlite3")
    monkeypatch.setattr(lead_capture, "SQLiteLeadStore", constructor)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LEADSCAN_LEAD_DB_PATH", db_path)

    public_api.create_app()

    constructor.assert_called_once_with(db_path)


def test_database_url_takes_precedence_over_sqlite_path(monkeypatch, tmp_path):
    postgres_store = MagicMock(spec=lead_capture.PostgresLeadStore)
    postgres_constructor = MagicMock(return_value=postgres_store)
    sqlite_constructor = MagicMock()
    database_url = "postgresql://user:fake-password@example.invalid/leadscan"
    monkeypatch.setattr(lead_capture, "PostgresLeadStore", postgres_constructor)
    monkeypatch.setattr(lead_capture, "SQLiteLeadStore", sqlite_constructor)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv(
        "LEADSCAN_LEAD_DB_PATH",
        str(tmp_path / "unused.sqlite3"),
    )

    public_api.create_app()

    postgres_constructor.assert_called_once_with(database_url)
    sqlite_constructor.assert_not_called()


def test_failed_postgres_save_withholds_successful_audit_report():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    lead_store = MagicMock(spec=lead_capture.PostgresLeadStore)
    lead_store.save_lead.side_effect = lead_capture.LeadStoreError(
        "Failed to store lead"
    )
    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json=make_capture_body("https://example.com"),
    )

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "code": public_api.LEAD_CAPTURE_FAILED,
    }
    assert "report_html" not in response.text


def test_success_response_does_not_add_capture_metadata():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.return_value = 101

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"ok", "code", "result"}
    for forbidden in ["lead_id", "captured", "database", "email", "contact_name"]:
        assert forbidden not in data


def test_no_database_read_route():
    app = public_api.create_app()
    client = TestClient(app)

    assert client.get("/api/leads").status_code == 404
    assert client.post("/api/leads").status_code == 404


def test_audit_timeout_never_starts_late_lead_save():
    audit_started = threading.Event()
    release_audit = threading.Event()
    audit_finished = threading.Event()

    def slow_runner(submitted_url, client_key, audit_limiter, gate):
        audit_started.set()
        release_audit.wait(timeout=2.0)
        audit_finished.set()
        return make_ok_payload(submitted_url)

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=slow_runner,
        lead_store=lead_store,
        audit_wait_seconds=0.05,
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/api/audit",
            json={
                "url": "https://example.com",
                "contact_name": "Alice",
                "email": "alice@example.com",
            },
        )
        assert response.status_code == 504
        assert response.json() == {
            "ok": False,
            "code": public_audit.AUDIT_TIMEOUT,
        }
        assert audit_started.is_set()
        lead_store.save_lead.assert_not_called()
    finally:
        release_audit.set()

    assert audit_finished.wait(timeout=2.0)
    lead_store.save_lead.assert_not_called()


def test_capture_save_runs_after_audit_wait_boundary(monkeypatch):
    events = []

    async def tracked_wait_for(awaitable, timeout):
        events.append("wait_enter")
        try:
            result = await awaitable
            events.append("wait_exit")
            return result
        except Exception:
            events.append("wait_exit_error")
            raise

    monkeypatch.setattr(public_api.asyncio, "wait_for", tracked_wait_for)

    def fake_audit_runner(url, client_key, limiter, gate):
        events.append("audit")
        return make_ok_payload(url)

    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)
    lead_store.save_lead.side_effect = lambda **kw: events.append("save") or 1

    app = public_api.create_app(
        audit_runner=fake_audit_runner,
        lead_store=lead_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={
            "url": "https://example.com",
            "contact_name": "Alice",
            "email": "alice@example.com",
        },
    )

    assert response.status_code == 200
    assert events == ["wait_enter", "audit", "wait_exit", "save"]


# ==============================================================================
# Task 9C-5E-A CORS Support Tests
# ==============================================================================


def test_cors_is_disabled_when_no_allowed_origin_is_configured(monkeypatch):
    monkeypatch.delenv("LEADSCAN_ALLOWED_ORIGIN", raising=False)
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        headers={"Origin": "https://example.net"},
        json={"invalid": "payload"},
    )
    assert "Access-Control-Allow-Origin" not in response.headers


def test_exact_allowed_origin_receives_cors_preflight():
    app = public_api.create_app(allowed_origin="https://leadscan.example")
    client = TestClient(app)

    response = client.options(
        "/api/audit",
        headers={
            "Origin": "https://leadscan.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == "https://leadscan.example"
    allow_methods = response.headers.get("Access-Control-Allow-Methods", "")
    assert "POST" in allow_methods
    allow_headers = response.headers.get("Access-Control-Allow-Headers", "").lower()
    assert "content-type" in allow_headers
    assert response.headers.get("Access-Control-Allow-Credentials") not in ("true", True)
    assert response.headers.get("Access-Control-Allow-Origin") != "*"


def test_exact_allowed_origin_receives_cors_on_audit_response():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    lead_store = MagicMock(spec=lead_capture.SQLiteLeadStore)

    app = public_api.create_app(
        audit_runner=audit_runner,
        lead_store=lead_store,
        allowed_origin="https://leadscan.example",
    )
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        headers={"Origin": "https://leadscan.example"},
        json=make_capture_body("https://example.com"),
    )

    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == "https://leadscan.example"
    assert response.json() == make_ok_payload("https://example.com")
    lead_store.save_lead.assert_called_once_with(
        contact_name="Test User",
        email="test@example.com",
        website_url="https://example.com",
    )


def test_unlisted_origin_does_not_receive_cors_permission():
    app = public_api.create_app(allowed_origin="https://leadscan.example")
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        headers={"Origin": "https://evil.example"},
        json={"invalid": "payload"},
    )
    assert response.headers.get("Access-Control-Allow-Origin") != "https://evil.example"
    assert response.headers.get("Access-Control-Allow-Origin") != "*"


def test_allowed_origin_can_be_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("LEADSCAN_ALLOWED_ORIGIN", "  https://leadscan.example/  ")
    app = public_api.create_app()
    client = TestClient(app)

    response = client.options(
        "/api/audit",
        headers={
            "Origin": "https://leadscan.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") == "https://leadscan.example"


@pytest.mark.parametrize(
    "invalid_origin",
    [
        "*",
        "https://*.netlify.app",
        "http://example.net",
        "https://example.net/path",
        "https://example.net/?x=1",
        "https://example.net/#x",
        "https://user@example.net",
        "https://example.net,https://evil.example",
    ],
)
def test_invalid_allowed_origin_configuration_fails_closed(invalid_origin):
    with pytest.raises(ValueError):
        public_api.create_app(allowed_origin=invalid_origin)


def test_static_no_wildcard_cors_in_production_source():
    from pathlib import Path
    source = Path("public_api.py").read_text(encoding="utf-8")
    assert 'allow_origins=["*"]' not in source
    assert "allow_origins=['*']" not in source
    assert "LEADSCAN_ALLOWED_ORIGIN" in source
