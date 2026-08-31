import socket
import pytest

import url_safety
from url_safety import UnsafeURL, prepare_public_url, resolve_public_url


def _make_fake_resolver(ip_list):
    """Build a fake socket.getaddrinfo resolver returning specific IP strings."""
    def resolver(host, port, family=0, type=0):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
            for ip in ip_list
        ]
    return resolver


# ---------------------------------------------------------------------------
# Task 8A-1: Pure URL validation tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_input, expected",
    [
        ("https://example.com", "https://example.com"),
        ("http://example.com/path?q=1", "http://example.com/path?q=1"),
        ("example.com", "https://example.com"),
        ("https://123.com", "https://123.com"),
        ("https://8.8.8.8", "https://8.8.8.8"),
        ("http://example.com:80/path", "http://example.com:80/path"),
        ("https://example.com:443/path", "https://example.com:443/path"),
        ("https://example.com:80/path", "https://example.com:80/path"),
        ("http://example.com:443/path", "http://example.com:443/path"),
    ],
)
def test_prepare_public_url_accepts_valid_inputs(raw_input, expected):
    assert prepare_public_url(raw_input) == expected



@pytest.mark.parametrize(
    "empty_val",
    [
        "",
        "   ",
        None,
    ],
)
def test_prepare_public_url_rejects_empty_inputs(empty_val):
    with pytest.raises(UnsafeURL):
        prepare_public_url(empty_val)


@pytest.mark.parametrize(
    "unsafe_scheme",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "data:text/plain,test",
        "gopher://127.0.0.1",
    ],
)
def test_prepare_public_url_rejects_unsafe_schemes(unsafe_scheme):
    with pytest.raises(UnsafeURL):
        prepare_public_url(unsafe_scheme)


@pytest.mark.parametrize(
    "with_credentials",
    [
        "http://user@example.com",
        "http://user:pass@example.com",
    ],
)
def test_prepare_public_url_rejects_credentials(with_credentials):
    with pytest.raises(UnsafeURL):
        prepare_public_url(with_credentials)


@pytest.mark.parametrize(
    "local_host",
    [
        "http://localhost",
        "http://api.localhost",
        "http://printer.local",
        "http://service.internal",
        "http://router.lan",
        "http://server.home",
        "http://router.home.arpa",
    ],
)
def test_prepare_public_url_rejects_local_hostnames(local_host):
    with pytest.raises(UnsafeURL):
        prepare_public_url(local_host)


@pytest.mark.parametrize(
    "unsafe_ipv4",
    [
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://224.0.0.1",
        "http://239.255.255.250",
    ],
)
def test_prepare_public_url_rejects_unsafe_ipv4_literals(unsafe_ipv4):
    with pytest.raises(UnsafeURL):
        prepare_public_url(unsafe_ipv4)


@pytest.mark.parametrize(
    "unsafe_ipv6",
    [
        "http://[::1]",
        "http://[fc00::1]",
        "http://[fe80::1]",
        "http://[ff02::1]",
        "http://[ff0e::1]",
        "http://[fec0::1]",
    ],
)
def test_prepare_public_url_rejects_unsafe_ipv6_literals(unsafe_ipv6):
    with pytest.raises(UnsafeURL):
        prepare_public_url(unsafe_ipv6)


@pytest.mark.parametrize(
    "numeric_trick",
    [
        "http://2130706433",
        "http://0177.0.0.1",
        "http://0x7f000001",
    ],
)
def test_prepare_public_url_rejects_numeric_host_tricks(numeric_trick):
    with pytest.raises(UnsafeURL):
        prepare_public_url(numeric_trick)


@pytest.mark.parametrize(
    "invalid_port",
    [
        "https://example.com:99999",
        "https://example.com:notaport",
    ],
)
def test_prepare_public_url_rejects_invalid_ports(invalid_port):
    with pytest.raises(UnsafeURL):
        prepare_public_url(invalid_port)


def test_prepare_public_url_does_not_perform_dns_lookup(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        raise AssertionError("DNS lookup was attempted by prepare_public_url!")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    result = prepare_public_url("https://example.com")
    assert result == "https://example.com"


# ---------------------------------------------------------------------------
# Task 8A-2: DNS-aware resolution tests
# ---------------------------------------------------------------------------

def test_resolve_public_url_accepts_public_ipv4():
    resolver = _make_fake_resolver(["93.184.216.34"])
    url, ips = resolve_public_url("https://example.com", resolver=resolver)
    assert url == "https://example.com"
    assert ips == ("93.184.216.34",)


def test_resolve_public_url_accepts_multiple_public_addresses():
    resolver = _make_fake_resolver(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    url, ips = resolve_public_url("https://example.com", resolver=resolver)
    assert url == "https://example.com"
    assert ips == ("2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34")


def test_resolve_public_url_deduplicates_addresses():
    resolver = _make_fake_resolver(["93.184.216.34", "93.184.216.34"])
    url, ips = resolve_public_url("https://example.com", resolver=resolver)
    assert url == "https://example.com"
    assert ips == ("93.184.216.34",)


def test_resolve_public_url_rejects_private_ipv4():
    resolver = _make_fake_resolver(["10.0.0.5"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://private.example.com", resolver=resolver)


def test_resolve_public_url_rejects_link_local_metadata_ipv4():
    resolver = _make_fake_resolver(["169.254.169.254"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://meta.example.com", resolver=resolver)


def test_resolve_public_url_rejects_loopback_ipv4():
    resolver = _make_fake_resolver(["127.0.0.1"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://local.example.com", resolver=resolver)


def test_resolve_public_url_rejects_multicast_and_site_local_dns():
    for bad_ip in ("224.0.0.1", "ff02::1", "fec0::1"):
        resolver = _make_fake_resolver([bad_ip])
        with pytest.raises(UnsafeURL):
            resolve_public_url("https://bad.example.com", resolver=resolver)


def test_resolve_public_url_rejects_private_ipv6():
    resolver = _make_fake_resolver(["fc00::5"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://v6private.example.com", resolver=resolver)


def test_resolve_public_url_rejects_link_local_ipv6():
    resolver = _make_fake_resolver(["fe80::1"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://v6local.example.com", resolver=resolver)


def test_resolve_public_url_rejects_mixed_public_and_private_dns():
    # If ANY resolved address is private, the entire hostname must be rejected
    resolver = _make_fake_resolver(["93.184.216.34", "10.0.0.5"])
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://mixed.example.com", resolver=resolver)


def test_resolve_public_url_rejects_dns_failure():
    def failing_resolver(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(UnsafeURL):
        resolve_public_url("https://nonexistent.example.com", resolver=failing_resolver)


def test_resolve_public_url_rejects_empty_dns_result():
    def empty_resolver(*args, **kwargs):
        return []

    with pytest.raises(UnsafeURL):
        resolve_public_url("https://empty.example.com", resolver=empty_resolver)


def test_resolve_public_url_rejects_malformed_resolver_ip():
    def malformed_resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 80))]

    with pytest.raises(UnsafeURL):
        resolve_public_url("https://malformed.example.com", resolver=malformed_resolver)


def test_resolve_public_url_global_literal_skips_dns():
    def failing_resolver(*args, **kwargs):
        raise AssertionError("Resolver should not be called for IP literals!")

    url, ips = resolve_public_url("https://8.8.8.8", resolver=failing_resolver)
    assert url == "https://8.8.8.8"
    assert ips == ("8.8.8.8",)


# ---------------------------------------------------------------------------
# Task 9B-0: Direct _is_public_unicast_address helper test
# ---------------------------------------------------------------------------

def test_is_public_unicast_address_classification():
    import ipaddress
    from url_safety import _is_public_unicast_address

    true_cases = [
        "8.8.8.8",
        "2606:4700:4700::1111",
    ]
    for ip_str in true_cases:
        addr = ipaddress.ip_address(ip_str)
        assert _is_public_unicast_address(addr) is True

    false_cases = [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "ff02::1",
        "fec0::1",
        "fc00::1",
        "fe80::1",
    ]
    for ip_str in false_cases:
        addr = ipaddress.ip_address(ip_str)
        assert _is_public_unicast_address(addr) is False


# ---------------------------------------------------------------------------
# Task 9C-2: Explicit destination port restriction tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "disallowed_url",
    [
        "https://example.com:0",
        "https://example.com:1",
        "https://example.com:22",
        "https://example.com:53",
        "https://example.com:81",
        "https://example.com:3000",
        "https://example.com:8000",
        "https://example.com:8080",
        "https://example.com:8443",
        "https://example.com:65535",
    ],
)
def test_prepare_public_url_rejects_disallowed_explicit_ports(disallowed_url):
    with pytest.raises(UnsafeURL):
        prepare_public_url(disallowed_url)


def test_prepare_public_url_ipv4_literal_ports():
    assert prepare_public_url("https://8.8.8.8:443") == "https://8.8.8.8:443"
    assert prepare_public_url("http://8.8.8.8:80") == "http://8.8.8.8:80"
    with pytest.raises(UnsafeURL):
        prepare_public_url("https://8.8.8.8:8443")
    with pytest.raises(UnsafeURL):
        prepare_public_url("http://8.8.8.8:8080")


def test_prepare_public_url_ipv6_literal_ports():
    assert prepare_public_url("https://[2606:4700:4700::1111]:443/") == "https://[2606:4700:4700::1111]:443/"
    with pytest.raises(UnsafeURL):
        prepare_public_url("https://[2606:4700:4700::1111]:8443/")


def test_resolve_public_url_rejects_disallowed_port_before_dns():
    def throwing_resolver(*args, **kwargs):
        raise AssertionError("resolver should not run")

    with pytest.raises(UnsafeURL):
        resolve_public_url("https://example.com:8443", resolver=throwing_resolver)


def test_allowed_public_ports_constant():
    assert url_safety._ALLOWED_PUBLIC_PORTS == {80, 443}
