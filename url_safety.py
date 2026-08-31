"""
url_safety.py
-------------
Pure, network-free URL validation boundary and DNS-aware host validation
for public audit inputs.

SECURITY BOUNDARY NOTE:
1. prepare_public_url() is only the first syntax/literal validation layer.
2. resolve_public_url() resolves domain hostnames and verifies that every
   returned IP address is a public unicast address (rejecting private, loopback,
   link-local, multicast, and deprecated IPv6 site-local addresses).
3. resolve_public_url() is still NOT sufficient by itself for a hosted browser
   service, because:
   - HTTP redirects can point to another hostname;
   - Page subresources can request other internal/private hostnames;
   - DNS can change between validation and browser connection (DNS rebinding / TOCTOU).
   The later network execution layer must validate every outbound navigation
   and request, and production deployments must use restricted network egress
   as defense in depth.
"""

import ipaddress
import re
import socket
import urllib.parse


class UnsafeURL(ValueError):
    """Raised when a URL fails pure public safety or DNS validation."""
    pass


_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PUBLIC_PORTS = {80, 443}
_LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".home.arpa",
)
_NUMERIC_PART_RE = re.compile(r"^(?:0x[0-9a-fA-F]+|\d+)$")


def _is_public_unicast_address(addr):
    """
    Determine whether an ipaddress.IPv4Address or IPv6Address is a public unicast address.

    Requires global routability and explicitly rejects multicast as well as
    deprecated IPv6 site-local (fec0::/10) ranges.
    """
    return (
        addr.is_global
        and not addr.is_multicast
        and not getattr(addr, "is_site_local", False)
    )


def prepare_public_url(value):
    """
    Validate and normalize a user-supplied URL for public audits.

    Performs syntax validation, scheme restriction, credential rejection,
    local hostname rejection, literal IP classification, and port verification
    without performing any DNS lookups or network calls.
    """
    if value is None:
        raw = ""
    else:
        raw = str(value).strip()

    if not raw:
        raise UnsafeURL("URL cannot be empty")

    if "://" not in raw:
        prepared = "https://" + raw
    else:
        prepared = raw

    try:
        parts = urllib.parse.urlsplit(prepared)
    except ValueError as exc:
        raise UnsafeURL(f"Invalid URL structure: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURL(f"Unsupported URL scheme: {parts.scheme!r}")

    host = parts.hostname
    if not host:
        raise UnsafeURL("URL must include a valid hostname")

    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("URL credentials are not permitted")

    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL(f"Invalid port: {exc}") from exc

    if port is not None and port not in _ALLOWED_PUBLIC_PORTS:
        raise UnsafeURL(f"URL port is not permitted: {port}")


    host_lower = host.lower()

    if host_lower == "localhost" or host_lower.endswith(_LOCAL_HOST_SUFFIXES):
        raise UnsafeURL(f"Local and internal hostnames are not permitted: {host_lower}")

    # Check if host is a valid IP literal
    try:
        addr = ipaddress.ip_address(host_lower)
    except ValueError:
        addr = None

    if addr is not None:
        if not _is_public_unicast_address(addr):
            raise UnsafeURL(f"Non-global IP literal is not permitted: {host_lower}")
        return prepared

    # Check for alternative numeric IP representations (octal, hex, dword, etc.)
    dotted_parts = host_lower.split(".")
    if dotted_parts and all(_NUMERIC_PART_RE.match(part) for part in dotted_parts):
        raise UnsafeURL(f"Non-canonical numeric host is not permitted: {host_lower}")

    return prepared


def resolve_public_url(value, resolver=None):
    """
    Validate a public URL and ensure its hostname resolves exclusively to public unicast IPs.

    Returns:
        tuple: (prepared_url, tuple_of_sorted_unique_canonical_ip_strings)

    Raises:
        UnsafeURL: If syntax validation fails, resolution fails, or any resolved
                   IP address is non-public (private, loopback, link-local, multicast,
                   deprecated site-local, etc.).
    """
    prepared = prepare_public_url(value)
    parts = urllib.parse.urlsplit(prepared)
    host = parts.hostname

    # If the host is already a valid IP literal, prepare_public_url() already verified it is global
    try:
        addr = ipaddress.ip_address(host)
        return prepared, (str(addr),)
    except ValueError:
        pass

    dns_func = resolver or socket.getaddrinfo

    try:
        records = dns_func(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise UnsafeURL(f"DNS resolution failed for {host}: {exc}") from exc

    if not records:
        raise UnsafeURL(f"DNS produced zero address records for {host}")

    resolved_ips = set()
    for record in records:
        try:
            sockaddr = record[4]
            ip_str = sockaddr[0]
            addr = ipaddress.ip_address(ip_str)
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafeURL(f"Invalid DNS address record for {host}: {exc}") from exc

        if not _is_public_unicast_address(addr):
            raise UnsafeURL(f"Hostname {host} resolved to non-global IP: {addr}")

        resolved_ips.add(str(addr))

    if not resolved_ips:
        raise UnsafeURL(f"DNS produced zero usable address records for {host}")

    return prepared, tuple(sorted(resolved_ips))
