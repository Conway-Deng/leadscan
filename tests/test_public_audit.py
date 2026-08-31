from unittest.mock import MagicMock
import pytest

import audit_report
import checks
import deadlines
import public_audit
from public_limits import SlidingWindowRateLimiter, ConcurrencyGate
import url_safety


class FakeBrowserContext:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args):
        self.exited = True


def make_ok_row(url):
    return {
        "status": "ok",
        "name": url,
        "website": url,
        "final_url": f"{url}/home",
        "score": 75,
        "tier": "good",
        "hook": "Strong digital footprint",
        "email": "owner@example.com",
        "phone": "+65 1234 5678",
        "address": "123 Main St",
        "_findings": {"speed": "fast", "mobile": "yes"},
    }


def test_rate_limit_checked_first_and_skips_validation_and_gate(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (False, 45)

    gate = MagicMock(spec=ConcurrencyGate)
    browser_factory = MagicMock()

    url_prep_called = []
    monkeypatch.setattr(
        url_safety,
        "prepare_public_url",
        lambda u: url_prep_called.append(u),
    )

    res = public_audit.run_public_audit(
        "invalid url garbage",
        "client-1",
        limiter,
        gate,
        browser_factory=browser_factory,
    )

    assert res == {
        "ok": False,
        "code": public_audit.RATE_LIMITED,
        "retry_after": 45,
    }
    limiter.allow.assert_called_once_with("client-1")
    assert len(url_prep_called) == 0
    gate.try_acquire.assert_not_called()
    browser_factory.assert_not_called()


def test_none_url_returns_invalid_url_without_gate():
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = MagicMock(spec=ConcurrencyGate)

    res = public_audit.run_public_audit(
        None,
        "client-1",
        limiter,
        gate,
    )

    assert res == {"ok": False, "code": public_audit.INVALID_URL}
    limiter.allow.assert_called_once_with("client-1")
    gate.try_acquire.assert_not_called()


def test_empty_or_whitespace_url_returns_invalid_url():
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = MagicMock(spec=ConcurrencyGate)

    for empty in ("", "   ", "\t\n"):
        res = public_audit.run_public_audit(
            empty,
            "client-1",
            limiter,
            gate,
        )
        assert res == {"ok": False, "code": public_audit.INVALID_URL}
    gate.try_acquire.assert_not_called()


def test_url_exceeding_max_length_returns_invalid_url():
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = MagicMock(spec=ConcurrencyGate)

    long_url = "https://example.com/" + ("a" * 2050)
    res = public_audit.run_public_audit(
        long_url,
        "client-1",
        limiter,
        gate,
    )

    assert res == {"ok": False, "code": public_audit.INVALID_URL}
    gate.try_acquire.assert_not_called()


def test_bare_domain_pre_normalized_to_https(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    passed_kwargs = {}
    def fake_factory(**kwargs):
        passed_kwargs.update(kwargs)
        return FakeBrowserContext(**kwargs)

    scanned_business = []
    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        scanned_business.append(business)
        return make_ok_row(business["website"])

    monkeypatch.setattr(checks, "audit_business", fake_audit)
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: "<html>report</html>")

    res = public_audit.run_public_audit(
        "example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=fake_factory,
    )

    assert res["ok"] is True
    assert res["code"] == public_audit.OK
    assert scanned_business[0]["website"] == "https://example.com"
    assert res["result"]["url"] == "https://example.com"


def test_obvious_unsafe_url_rejected_before_gate():
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = MagicMock(spec=ConcurrencyGate)

    for unsafe in ("http://127.0.0.1", "http://localhost:8080", "ftp://example.com"):
        res = public_audit.run_public_audit(
            unsafe,
            "client-1",
            limiter,
            gate,
        )
        assert res == {"ok": False, "code": public_audit.INVALID_URL}

    gate.try_acquire.assert_not_called()


def test_busy_gate_returns_busy_without_creating_browser():
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)

    gate = MagicMock(spec=ConcurrencyGate)
    gate.try_acquire.return_value = False

    browser_factory = MagicMock()

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=browser_factory,
    )

    assert res == {"ok": False, "code": public_audit.BUSY}
    gate.try_acquire.assert_called_once()
    browser_factory.assert_not_called()


def test_browser_forced_public_safe_and_scanner_args(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    factory_calls = []
    def fake_factory(**kwargs):
        factory_calls.append(kwargs)
        return FakeBrowserContext(**kwargs)

    scan_calls = []
    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        scan_calls.append({
            "browser": browser,
            "business": business,
            "cache": cache,
            "deep": deep,
            "deadline": deadline,
        })
        return make_ok_row(business["website"])

    monkeypatch.setattr(checks, "audit_business", fake_audit)
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: "<html>report</html>")

    fake_deadline = MagicMock()

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=fake_factory,
        deadline_factory=lambda: fake_deadline,
    )

    assert res["ok"] is True
    assert factory_calls == [{
        "respect_robots": True,
        "enforce_public_browser_requests": True,
    }]
    assert len(scan_calls) == 1
    assert scan_calls[0]["business"] == {
        "name": "https://example.com",
        "website": "https://example.com",
        "phone": "",
        "review_count": None,
        "place_id": "",
    }
    assert scan_calls[0]["cache"] is None
    assert scan_calls[0]["deep"] is True
    assert scan_calls[0]["deadline"] is fake_deadline


def test_successful_result_is_deliberately_small(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: "<html report>")

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert set(res.keys()) == {"ok", "code", "result"}
    result_data = res["result"]
    assert set(result_data.keys()) == {"url", "final_url", "score", "tier", "hook", "report_html"}
    assert result_data["url"] == "https://example.com"
    assert result_data["final_url"] == "https://example.com/home"
    assert result_data["score"] == 75
    assert result_data["tier"] == "good"
    assert result_data["hook"] == "Strong digital footprint"
    assert result_data["report_html"] == "<html report>"

    # Ensure sensitive/unwanted fields are stripped
    assert "_findings" not in result_data
    assert "email" not in result_data
    assert "phone" not in result_data
    assert "address" not in result_data


def test_report_builder_receives_exact_stamp(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    builder_calls = []
    def fake_build(row, findings=None, brand_info=None, stamp=None):
        builder_calls.append((row, findings, stamp))
        return "<html>report</html>"

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    monkeypatch.setattr(audit_report, "build", fake_build)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
        stamp="2026-08-31",
    )

    assert res["ok"] is True
    assert len(builder_calls) == 1
    assert builder_calls[0][2] == "2026-08-31"


def test_scanner_non_ok_status_returns_audit_failed(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        return {"status": "unreachable", "error": "connection failed"}

    monkeypatch.setattr(checks, "audit_business", fake_audit)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_deadline_exceeded_returns_audit_timeout(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        raise deadlines.AuditDeadlineExceeded("Timeout")

    monkeypatch.setattr(checks, "audit_business", fake_audit)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert res == {"ok": False, "code": public_audit.AUDIT_TIMEOUT}


def test_unsafe_url_during_scan_returns_invalid_url(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        raise url_safety.UnsafeURL("DNS resolved to private IP 10.0.0.1")

    monkeypatch.setattr(checks, "audit_business", fake_audit)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert res == {"ok": False, "code": public_audit.INVALID_URL}
    assert "10.0.0.1" not in str(res)


def test_unexpected_exception_returns_audit_failed_without_leak(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    def fake_audit(browser, business, cache=None, deep=True, deadline=None):
        raise RuntimeError("Secret internal database connection string failed!")

    monkeypatch.setattr(checks, "audit_business", fake_audit)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert "Secret" not in str(res)


def test_report_builder_exception_returns_audit_failed(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))

    def fake_build(row, findings=None, brand_info=None, stamp=None):
        raise ValueError("Corrupt template")

    monkeypatch.setattr(audit_report, "build", fake_build)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )

    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}


def test_gate_release_guarantees(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)

    # 1. Success path
    gate = ConcurrencyGate(1)
    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    monkeypatch.setattr(audit_report, "build", lambda r, f=None, b=None, s=None: "<html report>")
    public_audit.run_public_audit("https://example.com", "c1", limiter, gate, browser_factory=FakeBrowserContext)
    assert gate.in_flight == 0

    # 2. Scanner non-ok
    gate = ConcurrencyGate(1)
    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: {"status": "error"})
    public_audit.run_public_audit("https://example.com", "c1", limiter, gate, browser_factory=FakeBrowserContext)
    assert gate.in_flight == 0

    # 3. Timeout
    gate = ConcurrencyGate(1)
    monkeypatch.setattr(checks, "audit_business", MagicMock(side_effect=deadlines.AuditDeadlineExceeded("timeout")))
    public_audit.run_public_audit("https://example.com", "c1", limiter, gate, browser_factory=FakeBrowserContext)
    assert gate.in_flight == 0

    # 4. UnsafeURL
    gate = ConcurrencyGate(1)
    monkeypatch.setattr(checks, "audit_business", MagicMock(side_effect=url_safety.UnsafeURL("unsafe")))
    public_audit.run_public_audit("https://example.com", "c1", limiter, gate, browser_factory=FakeBrowserContext)
    assert gate.in_flight == 0

    # 5. Unexpected Exception
    gate = ConcurrencyGate(1)
    monkeypatch.setattr(checks, "audit_business", MagicMock(side_effect=RuntimeError("crash")))
    public_audit.run_public_audit("https://example.com", "c1", limiter, gate, browser_factory=FakeBrowserContext)
    assert gate.in_flight == 0


def test_no_filesystem_output_during_audit(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: "<html>report</html>")

    def forbidden_open(*args, **kwargs):
        raise AssertionError(f"open() called unexpectedly: {args}")

    monkeypatch.setattr("builtins.open", forbidden_open)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )
    assert res["ok"] is True


def test_report_html_within_size_limit_succeeds(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    valid_report = "a" * (public_audit.MAX_REPORT_HTML_BYTES)
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: valid_report)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )
    assert res["ok"] is True
    assert res["result"]["report_html"] == valid_report
    assert gate.in_flight == 0


def test_report_html_exceeding_byte_limit_returns_audit_failed_and_releases_gate(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    oversized_report = "a" * (public_audit.MAX_REPORT_HTML_BYTES + 1)
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: oversized_report)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )
    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert gate.in_flight == 0


def test_non_string_report_builder_returns_audit_failed_and_releases_gate(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: {"invalid": "object"})

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )
    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert gate.in_flight == 0


def test_report_html_utf8_multibyte_boundary(monkeypatch):
    limiter = MagicMock(spec=SlidingWindowRateLimiter)
    limiter.allow.return_value = (True, 0)
    gate = ConcurrencyGate(1)

    monkeypatch.setattr(checks, "audit_business", lambda b, biz, cache, deep, deadline: make_ok_row(biz["website"]))
    # 'é' is 2 bytes in UTF-8. 150,000 characters is < 256k characters, but 300,000 bytes > 256 KiB.
    multibyte_report = "é" * 150000
    monkeypatch.setattr(audit_report, "build", lambda row, findings=None, brand_info=None, stamp=None: multibyte_report)

    res = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
        browser_factory=FakeBrowserContext,
    )
    assert res == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert gate.in_flight == 0
