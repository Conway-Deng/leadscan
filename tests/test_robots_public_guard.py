import socket
from unittest.mock import MagicMock
import pytest
import requests

import robots
import url_safety


@pytest.fixture(autouse=True)
def clean_cache():
    robots.clear()
    yield
    robots.clear()


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        text="",
        headers=None,
        body=None,
    ):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        if body is None:
            self._body = (text or "").encode("utf-8")
        else:
            self._body = body
        self.closed = False
        self.iter_content_called = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=64 * 1024):
        self.iter_content_called = True
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def make_fake_resolver(ip_map):
    """Map hostnames to lists of IP strings for testing."""
    def resolver(host, port, family=0, type=0, *args, **kwargs):
        if host in ip_map:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 80))
                for ip in ip_map[host]
            ]
        raise socket.gaierror(f"unexpected host: {host}")
    return resolver


def test_default_mode_remains_unrestricted(monkeypatch):
    """Default mode (public_only=False) preserves existing unrestricted behavior."""
    read_called = []

    def fake_read(robots_url, timeout):
        read_called.append(robots_url)
        return None

    monkeypatch.setattr(robots, "_read", fake_read)

    allowed = robots.may_fetch("http://127.0.0.1/test", public_only=False)
    assert allowed is True
    assert read_called == ["http://127.0.0.1/robots.txt"]


def test_public_safe_direct_robots_fetch(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    robots_text = "User-agent: *\nDisallow: /admin\n"

    def fake_get(url, timeout=8, headers=None, allow_redirects=False, stream=False):
        assert allow_redirects is False
        assert stream is True
        return FakeResponse(200, text=robots_text)

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver) is True
    assert robots.may_fetch("https://example.com/admin", public_only=True, resolver=resolver) is False


def test_getter_receives_allow_redirects_false(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse(200, text="")

    monkeypatch.setattr(requests, "get", fake_get)

    robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver)
    assert len(calls) == 1
    assert calls[0].get("allow_redirects") is False
    assert calls[0].get("stream") is True



def test_safe_relative_redirect(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    fetched_urls = []

    def fake_get(url, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/robots.txt":
            return FakeResponse(302, headers={"Location": "/real-robots.txt"})
        if url == "https://example.com/real-robots.txt":
            return FakeResponse(200, text="User-agent: *\nDisallow: /secret\n")
        return FakeResponse(404)

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/secret", public_only=True, resolver=resolver) is False
    assert robots.may_fetch("https://example.com/open", public_only=True, resolver=resolver) is True
    assert fetched_urls == ["https://example.com/robots.txt", "https://example.com/real-robots.txt"]


def test_safe_cross_host_public_redirect(monkeypatch):
    resolver = make_fake_resolver({
        "example.com": ["93.184.216.34"],
        "cdn.example.net": ["93.184.216.35"],
    })
    fetched_urls = []

    def fake_get(url, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/robots.txt":
            return FakeResponse(301, headers={"Location": "https://cdn.example.net/robots.txt"})
        if url == "https://cdn.example.net/robots.txt":
            return FakeResponse(200, text="User-agent: *\nDisallow: /blocked\n")
        return FakeResponse(404)

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/blocked", public_only=True, resolver=resolver) is False
    assert fetched_urls == ["https://example.com/robots.txt", "https://cdn.example.net/robots.txt"]


def test_private_redirect_target_raises_and_prevents_connection(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    fetched_urls = []

    def fake_get(url, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/robots.txt":
            return FakeResponse(302, headers={"Location": "http://127.0.0.1/private"})
        return FakeResponse(200, text="")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(url_safety.UnsafeURL):
        robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver)

    assert "http://127.0.0.1/private" not in fetched_urls
    assert fetched_urls == ["https://example.com/robots.txt"]


def test_dns_private_redirect_hostname_raises_and_prevents_connection(monkeypatch):
    resolver = make_fake_resolver({
        "example.com": ["93.184.216.34"],
        "private.example": ["10.0.0.5"],
    })
    fetched_urls = []

    def fake_get(url, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/robots.txt":
            return FakeResponse(302, headers={"Location": "https://private.example/robots.txt"})
        return FakeResponse(200, text="")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(url_safety.UnsafeURL):
        robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver)

    assert "https://private.example/robots.txt" not in fetched_urls
    assert fetched_urls == ["https://example.com/robots.txt"]


def test_mixed_public_and_private_dns_redirect_raises_unsafe_url(monkeypatch):
    resolver = make_fake_resolver({
        "example.com": ["93.184.216.34"],
        "mixed.example": ["93.184.216.34", "10.0.0.5"],
    })
    fetched_urls = []

    def fake_get(url, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/robots.txt":
            return FakeResponse(302, headers={"Location": "https://mixed.example/robots.txt"})
        return FakeResponse(200, text="")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(url_safety.UnsafeURL):
        robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver)

    assert "https://mixed.example/robots.txt" not in fetched_urls


def test_initial_private_url_raises_and_prevents_connection(monkeypatch):
    resolver = make_fake_resolver({"local.example": ["127.0.0.1"]})
    get_mock = MagicMock()
    monkeypatch.setattr(requests, "get", get_mock)

    with pytest.raises(url_safety.UnsafeURL):
        robots.may_fetch("https://local.example/page", public_only=True, resolver=resolver)

    get_mock.assert_not_called()


def test_broken_redirect_with_no_location_fails_open(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})

    def fake_get(url, **kwargs):
        return FakeResponse(302, headers={})

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver) is True


def test_redirect_exhaustion_fails_open(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    fetch_count = [0]

    def fake_get(url, **kwargs):
        fetch_count[0] += 1
        return FakeResponse(302, headers={"Location": f"/redirect-{fetch_count[0]}"})

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver) is True
    # Initial + 5 redirects = 6 attempts total before returning None
    assert fetch_count[0] == 6


def test_ordinary_network_failure_fails_open(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})

    def fake_get(url, **kwargs):
        raise requests.exceptions.ConnectionError("Connection reset")

    monkeypatch.setattr(requests, "get", fake_get)

    assert robots.may_fetch("https://example.com/page", public_only=True, resolver=resolver) is True


def test_public_cache_is_separate_from_default_cache(monkeypatch):
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})

    default_get_called = []
    public_get_called = []

    def fake_read(robots_url, timeout):
        default_get_called.append(robots_url)
        return None

    def fake_get_public(url, **kwargs):
        public_get_called.append(url)
        return FakeResponse(200, text="User-agent: *\nDisallow: /admin\n")

    monkeypatch.setattr(robots, "_read", fake_read)
    monkeypatch.setattr(requests, "get", fake_get_public)

    # 1. Populate default cache
    robots.may_fetch("https://example.com/admin", public_only=False)
    assert len(default_get_called) == 1
    assert len(public_get_called) == 0

    # 2. Call public mode - must NOT use default cache!
    allowed = robots.may_fetch("https://example.com/admin", public_only=True, resolver=resolver)
    assert allowed is False
    assert len(public_get_called) == 1


def test_public_cache_revalidates_initial_hostname(monkeypatch):
    """
    Populate a public-mode parser cache using a global fake resolver.
    Then call again with resolver now returning 10.0.0.5.
    Must raise UnsafeURL even though the parser is cached.
    """
    resolver_state = {"ips": ["93.184.216.34"]}

    def dynamic_resolver(host, port, family=0, type=0, *args, **kwargs):
        if host == "example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 80))
                for ip in resolver_state["ips"]
            ]
        raise socket.gaierror(f"unexpected host: {host}")

    def fake_get(url, **kwargs):
        return FakeResponse(200, text="User-agent: *\nDisallow: /admin\n")

    monkeypatch.setattr(requests, "get", fake_get)

    # 1. Initial call with global IP -> parses and caches
    allowed = robots.may_fetch("https://example.com/page", public_only=True, resolver=dynamic_resolver)
    assert allowed is True

    # 2. Re-resolve host to private IP -> must raise UnsafeURL despite cached parser
    resolver_state["ips"] = ["10.0.0.5"]
    with pytest.raises(url_safety.UnsafeURL):
        robots.may_fetch("https://example.com/page", public_only=True, resolver=dynamic_resolver)


def test_exact_limit_acceptance():
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    base_content = b"User-agent: *\nDisallow: /blocked\n"
    padding = b"# " + b"a" * (robots.MAX_PUBLIC_ROBOTS_BYTES - len(base_content) - 3) + b"\n"
    body = base_content + padding
    assert len(body) == robots.MAX_PUBLIC_ROBOTS_BYTES

    fake_resp = FakeResponse(200, body=body)

    def fake_get(url, **kwargs):
        return fake_resp

    parser = robots._read_public(
        "https://example.com/robots.txt",
        timeout=8,
        resolver=resolver,
        getter=fake_get,
    )
    assert parser is not None
    assert parser.can_fetch("*", "https://example.com/blocked") is False
    assert fake_resp.closed is True


def test_oversized_content_length_early_reject():
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    fake_resp = FakeResponse(
        200,
        text="User-agent: *\nDisallow: /blocked\n",
        headers={"Content-Length": str(robots.MAX_PUBLIC_ROBOTS_BYTES + 1)},
    )

    def fake_get(url, **kwargs):
        return fake_resp

    result = robots._read_public(
        "https://example.com/robots.txt",
        timeout=8,
        resolver=resolver,
        getter=fake_get,
    )
    assert result is None
    assert fake_resp.iter_content_called is False
    assert fake_resp.closed is True


def test_oversized_chunked_response():
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    body = b"x" * (robots.MAX_PUBLIC_ROBOTS_BYTES + 1)
    fake_resp = FakeResponse(200, body=body)

    def fake_get(url, **kwargs):
        return fake_resp

    result = robots._read_public(
        "https://example.com/robots.txt",
        timeout=8,
        resolver=resolver,
        getter=fake_get,
    )
    assert result is None
    assert fake_resp.iter_content_called is True
    assert fake_resp.closed is True


def test_malformed_content_length():
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    fake_resp = FakeResponse(
        200,
        text="User-agent: *\nDisallow: /secret\n",
        headers={"Content-Length": "not-a-number"},
    )

    def fake_get(url, **kwargs):
        return fake_resp

    parser = robots._read_public(
        "https://example.com/robots.txt",
        timeout=8,
        resolver=resolver,
        getter=fake_get,
    )
    assert parser is not None
    assert fake_resp.iter_content_called is True
    assert parser.can_fetch("*", "https://example.com/secret") is False
    assert fake_resp.closed is True


def test_redirect_response_closed():
    resolver = make_fake_resolver({"example.com": ["93.184.216.34"]})
    resp1 = FakeResponse(302, headers={"Location": "/robots-final.txt"})
    resp2 = FakeResponse(200, text="User-agent: *\nDisallow: /secret\n")

    def fake_get(url, **kwargs):
        if url == "https://example.com/robots.txt":
            return resp1
        if url == "https://example.com/robots-final.txt":
            return resp2
        return FakeResponse(404)

    parser = robots._read_public(
        "https://example.com/robots.txt",
        timeout=8,
        resolver=resolver,
        getter=fake_get,
    )
    assert parser is not None
    assert parser.can_fetch("*", "https://example.com/secret") is False
    assert resp1.closed is True
    assert resp2.closed is True
