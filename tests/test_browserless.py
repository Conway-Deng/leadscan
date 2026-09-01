from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock

import pytest

import browser as browser_module
import config
import public_audit
from public_limits import ConcurrencyGate, SlidingWindowRateLimiter


FAKE_TOKEN = "fake/token?with&reserved+characters="
SFO_ENDPOINT = "https://production-sfo.browserless.io"


def _fake_playwright(monkeypatch, *, connect_error=None):
    playwright = MagicMock()
    local_browser = MagicMock(name="local_browser")
    remote_browser = MagicMock(name="remote_browser")
    local_browser.new_context.return_value = MagicMock(name="local_context")
    remote_browser.new_context.return_value = MagicMock(name="remote_context")
    playwright.chromium.launch.return_value = local_browser
    if connect_error is None:
        playwright.chromium.connect_over_cdp.return_value = remote_browser
    else:
        playwright.chromium.connect_over_cdp.side_effect = connect_error

    manager = MagicMock()
    manager.start.return_value = playwright
    monkeypatch.setattr(browser_module, "sync_playwright", lambda: manager)
    return playwright, local_browser, remote_browser


def _set_browserless(monkeypatch, endpoint="", token=""):
    monkeypatch.setattr(config, "BROWSERLESS_ENDPOINT", endpoint)
    monkeypatch.setattr(config, "BROWSERLESS_TOKEN", token)


def test_no_browserless_configuration_keeps_local_launch(monkeypatch):
    _set_browserless(monkeypatch)
    playwright, local_browser, _ = _fake_playwright(monkeypatch)

    with browser_module.Browser():
        pass

    playwright.chromium.launch.assert_called_once_with(headless=True)
    playwright.chromium.connect_over_cdp.assert_not_called()
    local_browser.new_context.assert_called_once()


def test_browserless_configuration_selects_connect_over_cdp(monkeypatch):
    _set_browserless(monkeypatch, SFO_ENDPOINT, FAKE_TOKEN)
    playwright, _, remote_browser = _fake_playwright(monkeypatch)

    with browser_module.Browser():
        pass

    playwright.chromium.launch.assert_not_called()
    playwright.chromium.connect_over_cdp.assert_called_once()
    connection_url = playwright.chromium.connect_over_cdp.call_args.args[0]
    parsed = urlsplit(connection_url)
    assert parsed.scheme == "wss"
    assert parsed.hostname == "production-sfo.browserless.io"
    assert parse_qs(parsed.query) == {
        "token": [FAKE_TOKEN],
        "timeout": ["120000"],
    }
    remote_browser.new_context.assert_called_once()


def test_browserless_token_is_encoded_and_never_logged(monkeypatch):
    _set_browserless(monkeypatch, SFO_ENDPOINT, FAKE_TOKEN)
    encoded_url = browser_module._browserless_cdp_url(SFO_ENDPOINT, FAKE_TOKEN)
    assert "fake%2Ftoken%3Fwith%26reserved%2Bcharacters%3D" in encoded_url
    assert FAKE_TOKEN not in encoded_url
    assert "proxy" not in parse_qs(urlsplit(encoded_url).query)

    logs = []
    _fake_playwright(
        monkeypatch,
        connect_error=RuntimeError(f"connection failed: {encoded_url}"),
    )
    with pytest.raises(browser_module.BrowserlessConnectionError) as caught:
        with browser_module.Browser(log=logs.append):
            pass

    assert str(caught.value) == "Remote browser connection failed"
    assert FAKE_TOKEN not in str(caught.value)
    assert FAKE_TOKEN not in repr(caught.value)
    assert logs == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.example",
        "https://production-sfo.browserless.io.attacker.example",
        "https://production-sfo.browserless.io/other/path",
        "https://production-sfo.browserless.io?token=attacker-controlled",
        "http://production-sfo.browserless.io",
    ],
)
def test_arbitrary_or_malformed_browserless_endpoint_is_rejected(endpoint):
    with pytest.raises(browser_module.BrowserlessConfigurationError):
        browser_module._browserless_cdp_url(endpoint, FAKE_TOKEN)


@pytest.mark.parametrize(
    ("endpoint", "token"),
    [
        (SFO_ENDPOINT, ""),
        ("", FAKE_TOKEN),
    ],
)
def test_incomplete_browserless_configuration_fails_safely(endpoint, token):
    with pytest.raises(
        browser_module.BrowserlessConfigurationError,
        match="configured together",
    ):
        browser_module._browserless_cdp_url(endpoint, token)


def test_remote_connection_failure_is_normal_public_audit_failure(monkeypatch):
    _set_browserless(monkeypatch, SFO_ENDPOINT, FAKE_TOKEN)
    encoded_url = browser_module._browserless_cdp_url(SFO_ENDPOINT, FAKE_TOKEN)
    _fake_playwright(
        monkeypatch,
        connect_error=RuntimeError(f"could not connect to {encoded_url}"),
    )
    limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60)
    gate = ConcurrencyGate(1)

    result = public_audit.run_public_audit(
        "https://example.com",
        "client-1",
        limiter,
        gate,
    )

    assert result == {"ok": False, "code": public_audit.AUDIT_FAILED}
    assert FAKE_TOKEN not in str(result)
    assert gate.in_flight == 0


def test_remote_public_mode_preserves_all_context_and_page_guards(monkeypatch):
    _set_browserless(monkeypatch, "wss://production-ams.browserless.io/", FAKE_TOKEN)
    playwright, _, remote_browser = _fake_playwright(monkeypatch)
    remote_context = remote_browser.new_context.return_value
    remote_page = remote_context.new_page.return_value
    remote_session = remote_context.new_cdp_session.return_value

    with browser_module.Browser(enforce_public_browser_requests=True) as active:
        assert active._new_page() is remote_page

    playwright.chromium.connect_over_cdp.assert_called_once()
    context_kwargs = remote_browser.new_context.call_args.kwargs
    assert context_kwargs == {
        "user_agent": config.USER_AGENT,
        "viewport": {"width": 1366, "height": 900},
        "locale": "en-SG",
        "service_workers": "block",
    }
    remote_context.route_web_socket.assert_called_once()
    remote_context.new_cdp_session.assert_called_once_with(remote_page)
    remote_session.on.assert_called_once()
    assert remote_session.on.call_args.args[0] == "Fetch.requestPaused"
    remote_session.send.assert_called_once_with(
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
