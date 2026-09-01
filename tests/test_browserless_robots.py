from unittest.mock import MagicMock

import pytest

import browser as browser_module
import robots
import url_safety


FAKE_TOKEN = "obviously-fake-browserless-token"
TARGET_URL = "https://example.com/private"
ROBOTS_URL = "https://example.com/robots.txt"


@pytest.fixture(autouse=True)
def clear_robots_cache():
    robots.clear()
    yield
    robots.clear()


def _allow_public_resolution(monkeypatch):
    monkeypatch.setattr(
        url_safety,
        "resolve_public_url",
        lambda url, resolver=None: (url, ("93.184.216.34",)),
    )


def _remote_browser_with_page(*, status=200, text="User-agent: *\nAllow: /"):
    active = browser_module.Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=True,
    )
    active._remote_browser = True
    active.context = MagicMock()
    page = active.context.new_page.return_value
    response = page.goto.return_value
    response.status = status
    response.all_headers.return_value = {}
    response.body.return_value = text.encode("utf-8")
    page.url = ROBOTS_URL
    return active, page, response


def test_local_default_mode_keeps_existing_direct_public_robots_path(monkeypatch):
    active = browser_module.Browser(
        polite_delay=0,
        respect_robots=True,
        enforce_public_browser_requests=True,
    )
    active._remote_browser = False
    calls = []

    def direct_may_fetch(url, **kwargs):
        calls.append((url, kwargs))
        assert "text_fetcher" not in kwargs
        return False

    monkeypatch.setattr(robots, "may_fetch", direct_may_fetch)

    result = active.render(TARGET_URL)

    assert result == (None, TARGET_URL, None, "blocked by robots.txt")
    assert len(calls) == 1


def test_remote_public_mode_never_calls_local_public_http_fetch(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, _, _ = _remote_browser_with_page()
    monkeypatch.setattr(
        robots,
        "_read_public",
        lambda *args, **kwargs: pytest.fail("local robots HTTP must not run"),
    )
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: ("<html></html>", url, 0.1, None),
    )

    result = active.render(TARGET_URL)

    assert result == ("<html></html>", TARGET_URL, 0.1, None)


def test_remote_robots_page_receives_existing_cdp_guard(monkeypatch):
    active, page, _ = _remote_browser_with_page()
    guarded = []
    monkeypatch.setattr(
        active,
        "_install_public_cdp_guard",
        lambda guarded_page: guarded.append(guarded_page),
    )

    fetched = active._read_remote_robots_text(ROBOTS_URL, timeout=8)

    assert fetched == (ROBOTS_URL, "User-agent: *\nAllow: /")
    assert guarded == [page]


def test_remote_explicit_disallow_still_blocks_main_navigation(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, page, _ = _remote_browser_with_page(
        text="User-agent: *\nDisallow: /private",
    )
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda *args, **kwargs: pytest.fail("main page must stay blocked"),
    )

    result = active.render(TARGET_URL)

    assert result == (None, TARGET_URL, None, "blocked by robots.txt")
    page.close.assert_called_once()


def test_remote_missing_robots_allows_main_navigation(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, page, _ = _remote_browser_with_page(status=404)
    main_calls = []
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: (
            main_calls.append((url, deadline)) or ("<html>main</html>", url, 0.2, None)
        ),
    )

    result = active.render(TARGET_URL)

    assert result == ("<html>main</html>", TARGET_URL, 0.2, None)
    assert main_calls == [(TARGET_URL, None)]
    page.close.assert_called_once()


def test_remote_oversized_robots_body_is_not_parsed(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, page, response = _remote_browser_with_page()
    response.body.return_value = b"x" * (robots.MAX_PUBLIC_ROBOTS_BYTES + 1)
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: ("<html>main</html>", url, 0.2, None),
    )

    result = active.render(TARGET_URL)

    assert result[0] == "<html>main</html>"
    page.close.assert_called_once()


def test_remote_declared_oversized_robots_skips_body_transfer(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, page, response = _remote_browser_with_page()
    response.all_headers.return_value = {
        "content-length": str(robots.MAX_PUBLIC_ROBOTS_BYTES + 1),
    }
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: ("<html>main</html>", url, 0.2, None),
    )

    result = active.render(TARGET_URL)

    assert result[0] == "<html>main</html>"
    response.body.assert_not_called()
    page.close.assert_called_once()


def test_remote_navigation_failure_is_sanitized_and_page_always_closes(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, page, _ = _remote_browser_with_page()
    page.goto.side_effect = RuntimeError(f"remote endpoint token={FAKE_TOKEN}")
    logs = []
    active.log = logs.append
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: ("<html>main</html>", url, 0.2, None),
    )

    result = active.render(TARGET_URL)

    assert result == ("<html>main</html>", TARGET_URL, 0.2, None)
    assert FAKE_TOKEN not in str(result)
    assert logs == []
    page.close.assert_called_once()


def test_remote_allowed_robots_keeps_main_navigation_arguments(monkeypatch):
    _allow_public_resolution(monkeypatch)
    active, _, _ = _remote_browser_with_page()
    deadline = MagicMock()
    deadline.cap_seconds.return_value = 7
    deadline.cap_milliseconds.side_effect = lambda value: value
    main_calls = []
    monkeypatch.setattr(
        active,
        "_render_once",
        lambda url, timeout, deadline=None: (
            main_calls.append((url, timeout, deadline))
            or ("<html>main</html>", url, 0.3, None)
        ),
    )

    result = active.render(TARGET_URL, deadline=deadline)

    assert result == ("<html>main</html>", TARGET_URL, 0.3, None)
    assert main_calls == [(TARGET_URL, browser_module.config.NAV_TIMEOUT_MS, deadline)]
