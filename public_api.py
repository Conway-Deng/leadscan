"""
public_api.py
-------------
Thin, framework-isolated FastAPI transport layer for the hosted public audit service.

SECURITY & ARCHITECTURE NOTES:
1. Transport Layer Isolation:
   This module provides strictly the HTTP boundary. All business logic, URL safety,
   and scanner execution are delegated to `public_audit.py`.
2. Multi-Tier Admission Protection:
   - Malformed/unwanted HTTP traffic is rate-limited by a fast envelope limiter
     before parsing the JSON body.
   - Valid audit attempts are subject to a separate service-level audit limiter.
3. Trusted Client Identity:
   - By default, client identity is derived ONLY from the socket peer (`request.client.host`).
   - Inbound forwarding headers (e.g. X-Forwarded-For, X-Real-IP, CF-Connecting-IP) are
     completely ignored unless an operator explicitly configures a single trusted header.
   - A trusted client header must ONLY be configured when the reverse proxy / ingress
     guarantees that client-supplied copies of that header are overwritten or stripped.
4. Resource & Size Boundaries:
   - Max request body size: 4 KiB.
   - Max generated report HTML: 256 KiB UTF-8.
   - Max serialized response body: 384 KiB.
   - HTTP response wait timeout: 105 seconds by default bounding body read and audit scan.
5. Timeout Semantics & Concurrency Safety:
   - `asyncio.wait_for()` bounds the HTTP client wait time for request-body parsing and audit execution
     but does NOT terminate the underlying synchronous worker thread.
   - Inbound lead persistence begins only after successful audit completion; once persistence begins,
     the request waits for persistence to finish.
   - SQLite has its own bounded busy timeout in `SQLiteLeadStore`.
   - A timed-out audit request never begins lead persistence.
   - Because the ConcurrencyGate slot is acquired inside `public_audit.run_public_audit()`,
     the concurrency slot is retained until the thread finishes and exits.
   - Timed-out requests do NOT free concurrency capacity early.
6. Worker Draining & Lifespan:
   - Draining workers permanently reject new audit attempts with 503 (BUSY, Retry-After: 1).
   - FastAPI lifespan hook sets draining state upon graceful worker shutdown.
7. Single-Process Concurrency Model:
   - Uvicorn should run with `--workers 1` per service instance because rate limiters,
     concurrency gates, and draining state are process-local in-memory structures.
   - Horizontal scaling is achieved by running multiple service instances behind edge rate limiting.
   - Production deployments still require edge rate limiting and Task 8A network egress restrictions.
8. No Unauthenticated Open API Surface:
   FastAPI automatic documentation (/docs, /redoc, /openapi.json) is disabled.
"""

import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
import os
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import lead_capture
import public_audit
from public_limits import ConcurrencyGate, SlidingWindowRateLimiter


MAX_REQUEST_BODY_BYTES = 4096

ENVELOPE_RATE_LIMIT = 30
ENVELOPE_RATE_WINDOW_SECONDS = 60

AUDIT_RATE_LIMIT = 5
AUDIT_RATE_WINDOW_SECONDS = 60

MAX_CLIENTS = 4096
MAX_CONCURRENT_AUDITS = 2

AUDIT_HTTP_WAIT_SECONDS = 105.0
MAX_RESPONSE_BODY_BYTES = 384 * 1024

INVALID_REQUEST = "invalid_request"
LEAD_CAPTURE_FAILED = "lead_capture_failed"


class InvalidRequest(ValueError):
    """Raised when an incoming HTTP request violates protocol or body constraints."""
    pass


class WorkerState:
    """
    Thread-safe worker lifecycle state for graceful draining.
    """

    def __init__(self):
        self._accepting = True
        self._lock = threading.Lock()

    @property
    def accepting(self):
        with self._lock:
            return self._accepting

    def begin_draining(self):
        with self._lock:
            self._accepting = False


def _client_identity(request, trusted_header=None):
    """
    Derive the client IP string for rate limiting.

    Defaults strictly to socket peer host. If trusted_header is configured,
    validates the single IP value from that header or falls back to peer host.
    """
    peer_host = "<unknown>"
    if request.client and request.client.host:
        peer_host = str(request.client.host).strip() or "<unknown>"

    if not trusted_header:
        return peer_host

    header_val = request.headers.get(trusted_header)
    if not header_val:
        return peer_host

    cleaned = header_val.strip()
    if not cleaned or "," in cleaned or len(cleaned) > 64:
        return peer_host

    try:
        ip = ipaddress.ip_address(cleaned)
        return str(ip)
    except ValueError:
        return peer_host


def _validate_contact_name(raw_name):
    if not isinstance(raw_name, str):
        raise InvalidRequest("Invalid contact_name")
    cleaned = raw_name.strip()
    if "\x00" in cleaned or len(cleaned) > lead_capture.MAX_CONTACT_NAME_LENGTH:
        raise InvalidRequest("Invalid contact_name")
    return cleaned


def _validate_email(raw_email):
    if not isinstance(raw_email, str):
        raise InvalidRequest("Invalid email")
    cleaned = raw_email.strip()
    if not cleaned or "\x00" in cleaned or len(cleaned) > lead_capture.MAX_EMAIL_LENGTH:
        raise InvalidRequest("Invalid email")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in cleaned):
        raise InvalidRequest("Invalid email")
    if cleaned.count("@") != 1:
        raise InvalidRequest("Invalid email")
    local_part, domain_part = cleaned.split("@")
    if not local_part or not domain_part:
        raise InvalidRequest("Invalid email")
    if len(local_part) > 64 or len(domain_part) > 253:
        raise InvalidRequest("Invalid email")
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        raise InvalidRequest("Invalid email")
    if domain_part.startswith(".") or domain_part.endswith(".") or ".." in domain_part:
        raise InvalidRequest("Invalid email")
    return cleaned


async def _read_audit_request(request):
    """
    Safely stream and parse the JSON request body within strict size limits.

    Returns:
        tuple: (submitted_url, contact_name, email)

    Raises:
        InvalidRequest: If Content-Type, Content-Length, body size, or schema is invalid.
    """
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";")[0].strip().lower()
    if media_type != "application/json":
        raise InvalidRequest("Invalid Content-Type")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            cl = int(content_length.strip())
            if cl < 0 or cl > MAX_REQUEST_BODY_BYTES:
                raise InvalidRequest("Content-Length exceeds maximum")
        except (ValueError, TypeError):
            raise InvalidRequest("Invalid Content-Length")

    accumulated = bytearray()
    async for chunk in request.stream():
        accumulated.extend(chunk)
        if len(accumulated) > MAX_REQUEST_BODY_BYTES:
            raise InvalidRequest("Request body exceeds maximum size")

    try:
        body_text = accumulated.decode("utf-8")
        data = json.loads(body_text)
    except Exception:
        raise InvalidRequest("Malformed JSON body")

    if not isinstance(data, dict):
        raise InvalidRequest("JSON payload must be an object")

    keys = set(data.keys())
    if keys != {"url", "contact_name", "email"}:
        raise InvalidRequest("Invalid JSON payload keys")

    url = data["url"]
    if not isinstance(url, str):
        raise InvalidRequest("'url' must be a string")

    contact_name = _validate_contact_name(data["contact_name"])
    email = _validate_email(data["email"])
    return url, contact_name, email


_STATUS_BY_CODE = {
    public_audit.OK: 200,
    public_audit.RATE_LIMITED: 429,
    public_audit.BUSY: 503,
    public_audit.INVALID_URL: 400,
    public_audit.AUDIT_TIMEOUT: 504,
    public_audit.AUDIT_FAILED: 500,
    LEAD_CAPTURE_FAILED: 500,
}


def _generic_failed_response():
    """Generate standard 500 audit_failed response with security headers."""
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "code": public_audit.AUDIT_FAILED,
        },
        headers={"Cache-Control": "no-store"},
    )


def _audit_response(result):
    """
    Map service result dictionary to standard JSONResponse with security headers
    and response size bound verification.
    """
    if not isinstance(result, dict) or result.get("code") not in _STATUS_BY_CODE:
        return _generic_failed_response()

    code = result["code"]
    status_code = _STATUS_BY_CODE[code]
    headers = {"Cache-Control": "no-store"}

    if code == public_audit.RATE_LIMITED:
        retry_after = result.get("retry_after")
        if isinstance(retry_after, int) and retry_after > 0:
            headers["Retry-After"] = str(retry_after)
    elif code == public_audit.BUSY:
        headers["Retry-After"] = "1"

    try:
        response = JSONResponse(
            status_code=status_code,
            content=result,
            headers=headers,
        )
    except Exception:
        return _generic_failed_response()

    if len(response.body) > MAX_RESPONSE_BODY_BYTES:
        return _generic_failed_response()

    return response


def _invalid_request_response():
    """
    Generate standard 400 invalid_request error response.
    """
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "code": INVALID_REQUEST,
        },
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    *,
    envelope_limiter=None,
    audit_limiter=None,
    gate=None,
    audit_runner=None,
    trusted_client_header=None,
    worker_state=None,
    audit_wait_seconds=AUDIT_HTTP_WAIT_SECONDS,
    lead_store=None,
):
    """
    Application factory for the public audit FastAPI service.
    """
    if isinstance(audit_wait_seconds, bool) or not isinstance(audit_wait_seconds, (int, float)) or audit_wait_seconds <= 0:
        raise ValueError(f"audit_wait_seconds must be a number > 0, got {audit_wait_seconds!r}")
    audit_wait_seconds = float(audit_wait_seconds)

    worker_state = worker_state or WorkerState()

    envelope_limiter = envelope_limiter or SlidingWindowRateLimiter(
        ENVELOPE_RATE_LIMIT,
        ENVELOPE_RATE_WINDOW_SECONDS,
        max_clients=MAX_CLIENTS,
    )

    audit_limiter = audit_limiter or SlidingWindowRateLimiter(
        AUDIT_RATE_LIMIT,
        AUDIT_RATE_WINDOW_SECONDS,
        max_clients=MAX_CLIENTS,
    )

    gate = gate or ConcurrencyGate(MAX_CONCURRENT_AUDITS)
    audit_runner = audit_runner or public_audit.run_public_audit

    if lead_store is None:
        raw_lead_db_path = os.getenv("LEADSCAN_LEAD_DB_PATH", "")
        lead_db_path = raw_lead_db_path.strip()
        if lead_db_path:
            lead_store = lead_capture.SQLiteLeadStore(lead_db_path)

    if trusted_client_header is None:
        raw_header = os.getenv("LEADSCAN_TRUSTED_CLIENT_IP_HEADER", "")
        trusted_client_header = raw_header.strip() or None
    elif isinstance(trusted_client_header, str):
        trusted_client_header = trusted_client_header.strip() or None

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            worker_state.begin_draining()

    app = FastAPI(
        title="LeadScan Public Audit",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.post("/api/audit")
    async def audit_endpoint(request: Request):
        client_key = _client_identity(
            request,
            trusted_header=trusted_client_header,
        )

        if not worker_state.accepting:
            return _audit_response({
                "ok": False,
                "code": public_audit.BUSY,
            })

        allowed, retry_after = envelope_limiter.allow(client_key)
        if not allowed:
            return _audit_response({
                "ok": False,
                "code": public_audit.RATE_LIMITED,
                "retry_after": retry_after,
            })

        async def execute_request():
            submitted_url, contact_name, email = await _read_audit_request(request)

            if lead_store is None:
                return (
                    {
                        "ok": False,
                        "code": LEAD_CAPTURE_FAILED,
                    },
                    None,
                )

            audit_result = await asyncio.to_thread(
                audit_runner,
                submitted_url,
                client_key,
                audit_limiter,
                gate,
            )

            if (
                isinstance(audit_result, dict)
                and audit_result.get("ok") is True
                and audit_result.get("code") == public_audit.OK
            ):
                return audit_result, (contact_name, email, submitted_url)

            return audit_result, None

        try:
            result, capture_data = await asyncio.wait_for(
                execute_request(),
                timeout=audit_wait_seconds,
            )
        except InvalidRequest:
            return _invalid_request_response()
        except asyncio.TimeoutError:
            result = {
                "ok": False,
                "code": public_audit.AUDIT_TIMEOUT,
            }
            capture_data = None
        except Exception:
            result = {
                "ok": False,
                "code": public_audit.AUDIT_FAILED,
            }
            capture_data = None

        if capture_data is not None:
            contact_name, email, submitted_url = capture_data
            try:
                await asyncio.to_thread(
                    lead_store.save_lead,
                    contact_name=contact_name,
                    email=email,
                    website_url=submitted_url,
                )
            except Exception:
                result = {
                    "ok": False,
                    "code": LEAD_CAPTURE_FAILED,
                }

        return _audit_response(result)

    return app


app = create_app()
