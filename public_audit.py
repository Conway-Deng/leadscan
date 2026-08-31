"""
public_audit.py
---------------
Framework-independent service core for hosted, single-URL public audits.

SECURITY & ARCHITECTURE NOTES:
1. Framework Independence:
   This module implements the core business logic and safety pipeline for public audits
   without tying to any specific HTTP framework (FastAPI, Flask, Starlette).
2. Trusted Client Identity:
   The caller/HTTP adapter is responsible for deriving a trusted, sanitized client key
   (e.g., verified reverse-proxy client IP) before passing it to this service.
3. Mandatory Task 8A Safety:
   Audits run exclusively through Browser instances configured with
   `enforce_public_browser_requests=True` and `respect_robots=True`. Public callers
   cannot disable or bypass these boundaries.
4. Admission Defense-in-Depth & Resource Bounds:
   - Rate limiting and concurrency controls are process-local guards against resource exhaustion.
   - Generated report HTML is bounded to 256 KiB UTF-8.
   - Production deployments still require edge rate limiting and egress
     network filtering against internal/metadata IP ranges.
"""

import datetime

import audit_report
from browser import Browser
import checks
import config
import deadlines
import url_safety


OK = "ok"
RATE_LIMITED = "rate_limited"
BUSY = "busy"
INVALID_URL = "invalid_url"
AUDIT_TIMEOUT = "audit_timeout"
AUDIT_FAILED = "audit_failed"

MAX_URL_LENGTH = 2048
MAX_REPORT_HTML_BYTES = 256 * 1024


def _business_for_url(url):
    """
    Construct the minimal business dict required by checks.audit_business().
    """
    return {
        "name": url,
        "website": url,
        "phone": "",
        "review_count": None,
        "place_id": "",
    }


def run_public_audit(
    submitted_url,
    client_key,
    limiter,
    gate,
    *,
    browser_factory=Browser,
    deadline_factory=None,
    stamp=None,
):
    """
    Execute a single-URL audit with rate limiting, input validation, and concurrency gating.

    Returns:
        dict: Structured response dictionary with 'ok', 'code', and optional 'result' or 'retry_after'.
    """
    # 1. Rate limit decision FIRST
    allowed, retry_after = limiter.allow(client_key)
    if not allowed:
        return {
            "ok": False,
            "code": RATE_LIMITED,
            "retry_after": retry_after,
        }

    # 2. Simple input shape and length validation
    if submitted_url is None:
        return {
            "ok": False,
            "code": INVALID_URL,
        }

    raw = str(submitted_url).strip()
    if not raw or len(raw) > MAX_URL_LENGTH:
        return {
            "ok": False,
            "code": INVALID_URL,
        }

    # 3. Pre-normalize URL without DNS lookups
    try:
        prepared_url = url_safety.prepare_public_url(raw)
    except url_safety.UnsafeURL:
        return {
            "ok": False,
            "code": INVALID_URL,
        }

    # 4. Acquire global concurrency gate
    if not gate.try_acquire():
        return {
            "ok": False,
            "code": BUSY,
        }

    # 5. Execute audit inside guaranteed release boundary
    try:
        make_deadline = deadline_factory or (
            lambda: deadlines.Deadline(config.BUSINESS_TIMEOUT_SECONDS)
        )
        deadline = make_deadline()

        with browser_factory(
            respect_robots=True,
            enforce_public_browser_requests=True,
        ) as browser:
            row = checks.audit_business(
                browser,
                _business_for_url(prepared_url),
                cache=None,
                deep=True,
                deadline=deadline,
            )

        if not isinstance(row, dict) or row.get("status") != "ok":
            return {
                "ok": False,
                "code": AUDIT_FAILED,
            }

        findings = row.get("_findings") or {}
        stamp_val = stamp if stamp is not None else datetime.date.today().isoformat()
        report_html = audit_report.build(
            row,
            findings,
            stamp=stamp_val,
        )

        if not isinstance(report_html, str):
            return {
                "ok": False,
                "code": AUDIT_FAILED,
            }

        if len(report_html.encode("utf-8")) > MAX_REPORT_HTML_BYTES:
            return {
                "ok": False,
                "code": AUDIT_FAILED,
            }

        return {
            "ok": True,
            "code": OK,
            "result": {
                "url": prepared_url,
                "final_url": row.get("final_url") or "",
                "score": row.get("score"),
                "tier": row.get("tier") or "",
                "hook": row.get("hook") or "",
                "report_html": report_html,
            },
        }

    except deadlines.AuditDeadlineExceeded:
        return {
            "ok": False,
            "code": AUDIT_TIMEOUT,
        }
    except url_safety.UnsafeURL:
        return {
            "ok": False,
            "code": INVALID_URL,
        }
    except Exception:
        return {
            "ok": False,
            "code": AUDIT_FAILED,
        }
    finally:
        gate.release()
