from pathlib import Path
import re
from unittest.mock import MagicMock
import pytest

from browser import Browser
import robots
import url_safety


def test_browser_defaults_guard_to_off():
    b = Browser()
    assert b.enforce_public_browser_requests is False
    assert b.public_resolver is None


def test_handle_public_cdp_request_continues_on_valid_http_url(monkeypatch):
    b = Browser(enforce_public_browser_requests=True)
    called_with = []

    def fake_resolve(url, resolver=None):
        called_with.append((url, resolver))
        return url, ("93.184.216.34",)

    monkeypatch.setattr(url_safety, "resolve_public_url", fake_resolve)

    fake_session = MagicMock()
    params = {"requestId": "req-1", "request": {"url": "https://example.com/app.js"}}

    b._handle_public_cdp_request(fake_session, params)

    assert called_with == [("https://example.com/app.js", None)]
    fake_session.send.assert_called_once_with(
        "Fetch.continueRequest",
        {"requestId": "req-1"},
    )


def test_handle_public_cdp_request_fails_on_unsafe_url(monkeypatch):
    b = Browser(enforce_public_browser_requests=True)

    def fake_resolve(url, resolver=None):
        raise url_safety.UnsafeURL("Rejected")

    monkeypatch.setattr(url_safety, "resolve_public_url", fake_resolve)

    fake_session = MagicMock()
    params = {"requestId": "req-2", "request": {"url": "http://10.0.0.1/admin"}}

    b._handle_public_cdp_request(fake_session, params)

    fake_session.send.assert_called_once_with(
        "Fetch.failRequest",
        {
            "requestId": "req-2",
            "errorReason": "BlockedByClient",
        },
    )


def test_handle_public_cdp_request_forwards_injected_resolver(monkeypatch):
    custom_resolver = MagicMock()
    b = Browser(enforce_public_browser_requests=True, public_resolver=custom_resolver)
    received_resolvers = []

    def fake_resolve(url, resolver=None):
        received_resolvers.append(resolver)
        return url, ("93.184.216.34",)

    monkeypatch.setattr(url_safety, "resolve_public_url", fake_resolve)

    fake_session = MagicMock()
    params = {"requestId": "req-3", "request": {"url": "https://example.com/"}}

    b._handle_public_cdp_request(fake_session, params)

    assert received_resolvers == [custom_resolver]
    fake_session.send.assert_called_once_with(
        "Fetch.continueRequest",
        {"requestId": "req-3"},
    )


def test_handle_public_cdp_request_bypasses_non_http_schemes(monkeypatch):
    b = Browser(enforce_public_browser_requests=True)

    def fail_if_called(url, resolver=None):
        raise AssertionError("resolve_public_url must not be called for non-HTTP URLs!")

    monkeypatch.setattr(url_safety, "resolve_public_url", fail_if_called)

    for non_http_url in ("data:text/plain,test", "blob:https://example.com/uuid", "about:blank"):
        fake_session = MagicMock()
        params = {"requestId": "req-non-http", "request": {"url": non_http_url}}

        b._handle_public_cdp_request(fake_session, params)

        fake_session.send.assert_called_once_with(
            "Fetch.continueRequest",
            {"requestId": "req-non-http"},
        )


def test_install_public_cdp_guard_registers_listener_and_enables_fetch():
    b = Browser(enforce_public_browser_requests=True)
    fake_context = MagicMock()
    fake_page = MagicMock()
    fake_session = MagicMock()

    fake_context.new_cdp_session.return_value = fake_session
    b.context = fake_context

    session = b._install_public_cdp_guard(fake_page)

    assert session is fake_session
    fake_context.new_cdp_session.assert_called_once_with(fake_page)

    fake_session.on.assert_called_once()
    event_name, handler = fake_session.on.call_args[0]
    assert event_name == "Fetch.requestPaused"

    fake_session.send.assert_called_once_with(
        "Fetch.enable",
        {
            "patterns": [
                {
                    "urlPattern": "*",
                    "requestStage": "Request",
                }
            ]
        },
    )

    # Test invoking the registered callback manually
    fake_session.reset_mock()
    handler({"requestId": "req-cb", "request": {"url": "data:image/png;base64,..."}})
    fake_session.send.assert_called_once_with(
        "Fetch.continueRequest",
        {"requestId": "req-cb"},
    )


def test_new_page_installs_cdp_guard_when_enabled(monkeypatch):
    b = Browser(enforce_public_browser_requests=True)
    fake_context = MagicMock()
    fake_page = MagicMock()
    fake_context.new_page.return_value = fake_page
    b.context = fake_context

    installed_pages = []
    monkeypatch.setattr(b, "_install_public_cdp_guard", lambda p: installed_pages.append(p))

    page = b._new_page()

    assert page is fake_page
    fake_context.new_page.assert_called_once()
    assert installed_pages == [fake_page]


def test_new_page_skips_cdp_guard_when_disabled(monkeypatch):
    b = Browser(enforce_public_browser_requests=False)
    fake_context = MagicMock()
    fake_page = MagicMock()
    fake_context.new_page.return_value = fake_page
    b.context = fake_context

    installed_pages = []
    monkeypatch.setattr(b, "_install_public_cdp_guard", lambda p: installed_pages.append(p))

    page = b._new_page()

    assert page is fake_page
    fake_context.new_page.assert_called_once()
    assert installed_pages == []


def test_context_blocks_service_workers_and_installs_websocket_guard_when_enabled(monkeypatch):
    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context

    monkeypatch.setattr("browser.sync_playwright", lambda: MagicMock(start=lambda: mock_playwright))

    b = Browser(enforce_public_browser_requests=True)
    with b:
        mock_browser.new_context.assert_called_once()
        kwargs = mock_browser.new_context.call_args.kwargs
        assert kwargs.get("service_workers") == "block"
        mock_context.route.assert_not_called()
        mock_context.route_web_socket.assert_called_once_with(
            re.compile(r"^wss?://", re.I),
            b._block_public_websocket,
        )


def test_default_context_preserves_unrouted_behaviour(monkeypatch):
    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context

    monkeypatch.setattr("browser.sync_playwright", lambda: MagicMock(start=lambda: mock_playwright))

    b = Browser()
    with b:
        mock_browser.new_context.assert_called_once()
        kwargs = mock_browser.new_context.call_args.kwargs
        assert "service_workers" not in kwargs
        mock_context.route.assert_not_called()
        mock_context.route_web_socket.assert_not_called()


# ---------------------------------------------------------------------------
# Task 8A-4B: Browser robots wiring tests
# ---------------------------------------------------------------------------

def test_render_forwards_public_robots_settings(monkeypatch):
    fake_resolver = MagicMock()
    b = Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=True,
        public_resolver=fake_resolver,
    )
    calls = []

    def fake_may_fetch(url, timeout=8, public_only=False, resolver=None):
        calls.append({"url": url, "timeout": timeout, "public_only": public_only, "resolver": resolver})
        return False

    def unexpected_render_once(*args, **kwargs):
        raise AssertionError("_render_once must not be called when robots denies fetch!")

    monkeypatch.setattr(robots, "may_fetch", fake_may_fetch)
    monkeypatch.setattr(b, "_render_once", unexpected_render_once)

    result = b.render("https://example.com")

    assert len(calls) == 1
    assert calls[0]["url"] == "https://example.com"
    assert calls[0]["public_only"] is True
    assert calls[0]["resolver"] is fake_resolver
    assert result == (None, "https://example.com", None, "blocked by robots.txt")


def test_render_blocks_unsafe_robots_target(monkeypatch):
    b = Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=True,
    )

    def failing_may_fetch(url, timeout=8, public_only=False, resolver=None):
        raise url_safety.UnsafeURL("DNS resolved to private IP")

    def unexpected_render_once(*args, **kwargs):
        raise AssertionError("_render_once must not be called on UnsafeURL!")

    monkeypatch.setattr(robots, "may_fetch", failing_may_fetch)
    monkeypatch.setattr(b, "_render_once", unexpected_render_once)

    result = b.render("https://example.com")
    assert result == (None, "https://example.com", None, "unsafe URL blocked")


def test_render_default_mode_remains_non_public(monkeypatch):
    b = Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=False,
    )
    calls = []

    def fake_may_fetch(url, timeout=8, public_only=False, resolver=None):
        calls.append({"public_only": public_only, "resolver": resolver})
        return False

    monkeypatch.setattr(robots, "may_fetch", fake_may_fetch)

    result = b.render("https://example.com")
    assert len(calls) == 1
    assert calls[0]["public_only"] is False
    assert calls[0]["resolver"] is None
    assert result == (None, "https://example.com", None, "blocked by robots.txt")


def test_render_robots_disabled_does_not_call_robots(monkeypatch):
    b = Browser(
        polite_delay=0,
        respect_robots=False,
        enforce_public_browser_requests=True,
    )

    def unexpected_may_fetch(*args, **kwargs):
        raise AssertionError("robots.may_fetch must not be called when respect_robots is False!")

    def fake_render_once(url, timeout, deadline=None):
        return "<html></html>", url, 0.1, None

    monkeypatch.setattr(robots, "may_fetch", unexpected_may_fetch)
    monkeypatch.setattr(b, "_render_once", fake_render_once)

    html, final_url, load, error = b.render("https://example.com")
    assert html == "<html></html>"
    assert final_url == "https://example.com"
    assert error is None


def test_render_public_robots_success_proceeds_to_render(monkeypatch):
    b = Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=True,
    )

    def fake_may_fetch(url, timeout=8, public_only=False, resolver=None):
        return True

    def fake_render_once(url, timeout, deadline=None):
        return "<html><body>Hello</body></html>", url, 0.12, None

    monkeypatch.setattr(robots, "may_fetch", fake_may_fetch)
    monkeypatch.setattr(b, "_render_once", fake_render_once)

    html, final_url, load, error = b.render("https://example.com")
    assert html == "<html><body>Hello</body></html>"
    assert final_url == "https://example.com"
    assert load == 0.12
    assert error is None


# ---------------------------------------------------------------------------
# Task 8A-5C: Browser WebSocket routing guard tests
# ---------------------------------------------------------------------------

def test_block_public_websocket_performs_no_server_connection():
    b = Browser(enforce_public_browser_requests=True)

    fake_ws = MagicMock()
    fake_ws.connect_to_server.side_effect = AssertionError("connect_to_server must not be called!")
    fake_ws.close.side_effect = AssertionError("close must not be called from sync callback!")
    fake_ws.send.side_effect = AssertionError("send must not be called!")

    result = b._block_public_websocket(fake_ws)

    assert result is None
    fake_ws.connect_to_server.assert_not_called()
    fake_ws.close.assert_not_called()
    fake_ws.send.assert_not_called()


def test_install_public_websocket_guard_registers_context_route():
    b = Browser(enforce_public_browser_requests=True)
    fake_context = MagicMock()
    b.context = fake_context

    b._install_public_websocket_guard()

    fake_context.route_web_socket.assert_called_once()
    pattern, handler = fake_context.route_web_socket.call_args[0]

    assert pattern.search("ws://example.com/socket") is not None
    assert pattern.search("wss://example.com/socket") is not None
    assert pattern.search("http://example.com") is None
    assert pattern.search("https://example.com") is None

    assert handler == b._block_public_websocket


def test_requirements_declares_minimum_playwright_1_48():
    content = Path("requirements.txt").read_text(encoding="utf-8")
    assert "playwright>=1.48" in content
    assert "playwright>=1.44" not in content
