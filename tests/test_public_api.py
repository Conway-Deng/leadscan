from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import pytest

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


def test_post_audit_success():
    audit_runner = MagicMock(return_value=make_ok_payload("https://example.com"))
    app = public_api.create_app(audit_runner=audit_runner)
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"
    assert "Retry-After" not in response.headers
    assert response.json() == make_ok_payload("https://example.com")


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

    app = public_api.create_app(
        envelope_limiter=envelope_limiter,
        audit_limiter=audit_limiter,
        gate=gate,
        audit_runner=fake_runner,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com/test"})
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
        content=b'{"url":"https://example.com"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_missing_content_type():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        content=b'{"url":"https://example.com"}',
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
        content=b'{"url":"https://example.com"}',
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

    huge_body = b'{"url":"' + (b"a" * 5000) + b'"}'
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

    response = client.post("/api/audit", json={"not_url": "https://example.com"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_extra_json_keys():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post(
        "/api/audit",
        json={"url": "https://example.com", "admin": True},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_non_string_url():
    app = public_api.create_app()
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": 12345})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_api.INVALID_REQUEST}


def test_valid_body_preserves_url_exact_whitespace():
    passed_url = []
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_url.append(u) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post("/api/audit", json={"url": "   example.com   "})
    assert passed_url == ["   example.com   "]


def test_default_client_identity_ignores_spoofed_headers():
    passed_keys = []
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json={"url": "https://example.com"},
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
    app = public_api.create_app(
        trusted_client_header="CF-Connecting-IP",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json={"url": "https://example.com"},
        headers={"CF-Connecting-IP": "203.0.113.9"},
    )

    assert passed_keys == ["203.0.113.9"]


def test_configured_trusted_header_rejects_comma_chain():
    passed_keys = []
    app = public_api.create_app(
        trusted_client_header="X-Forwarded-For",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json={"url": "https://example.com"},
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )

    assert len(passed_keys) == 1
    assert passed_keys[0] in ("testclient", "127.0.0.1", "<unknown>")
    assert "203.0.113.9" not in passed_keys[0]


def test_malformed_trusted_ip_falls_back_to_peer():
    passed_keys = []
    app = public_api.create_app(
        trusted_client_header="X-Real-IP",
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json={"url": "https://example.com"},
        headers={"X-Real-IP": "not-an-ip-address"},
    )

    assert len(passed_keys) == 1
    assert passed_keys[0] in ("testclient", "127.0.0.1", "<unknown>")


def test_trusted_header_read_from_env_var(monkeypatch):
    monkeypatch.setenv("LEADSCAN_TRUSTED_CLIENT_IP_HEADER", "X-Custom-Client-IP")
    passed_keys = []
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: passed_keys.append(k) or make_ok_payload(u),
    )
    client = TestClient(app)

    client.post(
        "/api/audit",
        json={"url": "https://example.com"},
        headers={"X-Custom-Client-IP": "198.51.100.22"},
    )

    assert passed_keys == ["198.51.100.22"]


def test_service_rate_limited_response():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {
            "ok": False,
            "code": public_audit.RATE_LIMITED,
            "retry_after": 25,
        }
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "25"
    assert response.json() == {
        "ok": False,
        "code": public_audit.RATE_LIMITED,
        "retry_after": 25,
    }


def test_service_busy_response():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.BUSY}
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "1"
    assert response.json() == {"ok": False, "code": public_audit.BUSY}


def test_service_invalid_url_response():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.INVALID_URL}
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "code": public_audit.INVALID_URL}


def test_service_audit_timeout_response():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.AUDIT_TIMEOUT}
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 504
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}


def test_service_audit_failed_response():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": public_audit.AUDIT_FAILED}
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_service_unexpected_exception_fails_closed():
    def exploding_runner(u, k, l, g):
        raise RuntimeError("Internal DB connection failed on host 10.0.0.99")

    app = public_api.create_app(audit_runner=exploding_runner)
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
    assert response.status_code == 500
    assert response.json() == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert "10.0.0.99" not in response.text
    assert "Internal DB" not in response.text


def test_unknown_service_code_fails_closed_to_500():
    app = public_api.create_app(
        audit_runner=lambda u, k, l, g: {"ok": False, "code": "some_alien_code"}
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
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

    response = client.post("/api/audit", json={"url": "https://example.com"})
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

    response = client.post("/api/audit", json={"url": "https://example.com"})
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

    client.post("/api/audit", json={"url": "https://example.com"})
    audit_runner.assert_not_called()


def test_fastapi_lifespan_calls_begin_draining_on_shutdown():
    ws = public_api.WorkerState()
    app = public_api.create_app(worker_state=ws)

    assert ws.accepting is True
    with TestClient(app) as client:
        assert ws.accepting is True
        res = client.post("/api/audit", json={"url": "https://example.com"})

    # On context exit, lifespan shutdown hook drains worker
    assert ws.accepting is False


def test_invalid_audit_wait_seconds_raises_value_error():
    for bad in (0, -1, 0.0, -10.5, False, True, "105"):
        with pytest.raises(ValueError):
            public_api.create_app(audit_wait_seconds=bad)


def test_http_timeout_while_audit_runner_blocks():
    import threading

    event = threading.Event()

    def blocking_runner(submitted_url, client_key, audit_limiter, gate):
        event.wait(timeout=2.0)
        return make_ok_payload(submitted_url)

    app = public_api.create_app(
        audit_runner=blocking_runner,
        audit_wait_seconds=0.05,
    )
    client = TestClient(app)

    try:
        response = client.post("/api/audit", json={"url": "https://example.com"})
        assert response.status_code == 504
        assert response.json() == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}
    finally:
        event.set()


def test_http_timeout_covers_body_parsing(monkeypatch):
    import asyncio

    async def slow_read_audit_url(request):
        await asyncio.sleep(0.1)
        return "https://example.com"

    monkeypatch.setattr(public_api, "_read_audit_url", slow_read_audit_url)

    audit_runner = MagicMock()
    app = public_api.create_app(
        audit_runner=audit_runner,
        audit_wait_seconds=0.03,
    )
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
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

    app = public_api.create_app(audit_runner=oversized_runner)
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
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

    app = public_api.create_app(audit_runner=bad_runner)
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "https://example.com"})
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
