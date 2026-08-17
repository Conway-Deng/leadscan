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

# One parser per host, so `robots.txt` is fetched once per site and not once
# per page.
_CACHE = {}
_CACHE_LIMIT = 2000


def clear():
    _CACHE.clear()


def _robots_url(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, None
    return f"{parts.scheme}://{parts.netloc}/robots.txt", parts.netloc.lower()


def may_fetch(url, user_agent="*", timeout=8):
    """
    True when `robots.txt` allows this URL.

    A missing, unreadable or broken `robots.txt` means yes. That is what the
    standard says: silence is permission. Only an explicit Disallow is a no.
    """
    robots_url, host = _robots_url(url)
    if not robots_url:
        return True

    if host in _CACHE:
        parser = _CACHE[host]
    else:
        parser = _read(robots_url, timeout)
        if len(_CACHE) < _CACHE_LIMIT:
            _CACHE[host] = parser
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

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.parse(response.text.splitlines())
    except Exception:
        return None
    return parser
