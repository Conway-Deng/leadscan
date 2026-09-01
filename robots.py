"""
robots.py
---------
Reads `robots.txt` and decides whether a page may be fetched.

WHY THIS IS HERE
LeadScan opens a few hundred websites in an automated loop. That is a crawler,
whatever the user agent says, and a crawler that ignores `robots.txt` is badly
behaved. Most small-business sites either have no `robots.txt` or allow
everything, so in practice this changes almost nothing — but the cases where it
does change something are the cases where it matters.

WHAT HAPPENS WHEN A SITE SAYS NO
The firm is not dropped. It stays in the output with the status
`blocked by robots.txt`, so the caller can see it exists and open the site by
hand. It is simply never scored on evidence that was not collected. Guessing a
verdict from an unread page would break the honesty rule that the whole scoring
model rests on.

`--ignore-robots` turns the check off. Think before you use it.
"""

import urllib.parse
import urllib.robotparser

import url_safety

# One parser per host and mode, so `robots.txt` is fetched once per site and not once
# per page, keeping public and default modes isolated.
_CACHE = {}
_CACHE_LIMIT = 2000
MAX_PUBLIC_ROBOTS_BYTES = 512 * 1024


def clear():
    _CACHE.clear()


def _robots_url(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, None
    return f"{parts.scheme}://{parts.netloc}/robots.txt", parts.netloc.lower()


def may_fetch(
    url,
    user_agent="*",
    timeout=8,
    public_only=False,
    resolver=None,
    text_fetcher=None,
):
    """
    True when `robots.txt` allows this URL.

    A missing, unreadable or broken `robots.txt` means yes. That is what the
    standard says: silence is permission. Only an explicit Disallow is a no.
    """
    robots_url, host = _robots_url(url)
    if not robots_url:
        return True

    if public_only:
        url_safety.resolve_public_url(robots_url, resolver=resolver)

    cache_key = (host, bool(public_only))
    if cache_key in _CACHE:
        parser = _CACHE[cache_key]
    else:
        if public_only and text_fetcher is not None:
            fetched = text_fetcher(robots_url, timeout)
            if fetched is None:
                parser = None
            else:
                final_url, text = fetched
                parser = _parser_from_text(final_url, text)
        elif public_only:
            parser = _read_public(robots_url, timeout, resolver=resolver)
        else:
            parser = _read(robots_url, timeout)
        if len(_CACHE) < _CACHE_LIMIT:
            _CACHE[cache_key] = parser

    if parser is None:
        return True

    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


def _read(robots_url, timeout):
    """Give back a parser, or None when the file cannot be read."""
    import requests
    try:
        response = requests.get(robots_url, timeout=timeout,
                                headers={"User-Agent": "LeadScan"})
    except Exception:
        return None
    # 404 and 401 both mean "no rules". A 5xx means the server is unwell, and
    # the polite reading of the standard is to stay out, but treating a broken
    # server as a total ban would make the tool unusable, so it is allowed.
    if response.status_code != 200 or not response.text:
        return None

    return _parser_from_text(robots_url, response.text)


def _parser_from_text(robots_url, text):
    """Build the shared robots parser from an already-fetched text body."""
    if not text:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.parse(text.splitlines())
    except Exception:
        return None
    return parser


def _read_bounded_public_body(
    response,
    max_bytes=MAX_PUBLIC_ROBOTS_BYTES,
):
    """
    Stream and decode response body safely within an upper byte limit.

    Rejects oversized responses via Content-Length or streaming byte count.
    """
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            cl_val = int(str(content_length).strip())
            if cl_val >= 0 and cl_val > max_bytes:
                return None
        except (ValueError, TypeError):
            pass

    data = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if chunk:
            data.extend(chunk)
            if len(data) > max_bytes:
                return None

    return data.decode("utf-8", errors="replace")


def _read_public(
    robots_url,
    timeout,
    resolver=None,
    getter=None,
    max_redirects=5,
):
    """
    Fetch robots.txt safely in public/hosted mode with redirect validation.

    SECURITY NOTE:
    Public robots mode:
    * validates every redirect hop before connecting;
    * disables automatic redirects;
    * rejects any hostname resolving to a non-global IP;
    * bounds response bodies to MAX_PUBLIC_ROBOTS_BYTES.
    However, DNS validation vs actual socket connection retains the same DNS TOCTOU
    boundary as the browser layer, so restricted network egress remains required.
    """
    import requests

    fetch = getter or requests.get
    current_url = robots_url
    redirects = 0

    while True:
        url_safety.resolve_public_url(
            current_url,
            resolver=resolver,
        )

        try:
            response = fetch(
                current_url,
                timeout=timeout,
                headers={"User-Agent": "LeadScan"},
                allow_redirects=False,
                stream=True,
            )
        except (requests.RequestException, OSError):
            return None

        try:
            if response.status_code in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > max_redirects:
                    return None
                location = response.headers.get("Location")
                if not location:
                    return None
                current_url = urllib.parse.urljoin(current_url, location.strip())
                continue

            if response.status_code != 200:
                return None

            text = _read_bounded_public_body(response)
            if not text:
                return None

            return _parser_from_text(current_url, text)
        finally:
            response.close()
