"""
browser.py
----------
The Playwright wrapper. It opens a real headless browser so that scripts which
load a pixel or build a form with JavaScript are actually seen.

WHAT CHANGED FROM THE EARLIER VERSION
  * One retry with a longer timeout. Several real sites needed more than 15
    seconds and were written down as "broken". A slow site is a defect, but a
    dead site is a different sales conversation, so the two must not be mixed.
  * Each site gets a polite delay, so a sweep does not hammer small hosts.
  * Shutdown closes every resource even when one close call fails.
  * The follower count is read from the og:description meta tag only, not from
    the whole page, so a random number in the page body cannot be mistaken for
    a follower count.
"""

import re
import time
import urllib.parse

from playwright.sync_api import sync_playwright

import config
import deadlines
import detect
import robots
import url_safety

# Instagram and TikTok put the follower count in the og:description meta tag.
_OG_DESCRIPTION = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]*content=["\']([^"\']{0,400})',
    re.I,
)
_FOLLOWERS_IN_TEXT = re.compile(r"([\d.,]+\s*[KMkm]?)\s*Followers", re.I)

_BROWSERLESS_HOSTS = frozenset({
    "production-sfo.browserless.io",
    "production-lon.browserless.io",
    "production-ams.browserless.io",
})
_BROWSERLESS_SESSION_TIMEOUT_MS = 120_000


class BrowserlessConfigurationError(RuntimeError):
    """Raised when optional Browserless settings are incomplete or unsafe."""


class BrowserlessConnectionError(RuntimeError):
    """Sanitized remote-browser startup failure that never includes the token."""


def _browserless_cdp_url(endpoint, token):
    """Return a restricted Browserless WebSocket URL, or None for local mode."""
    endpoint = (endpoint or "").strip()
    token = (token or "").strip()
    if not endpoint and not token:
        return None
    if not endpoint or not token:
        raise BrowserlessConfigurationError(
            "Browserless endpoint and token must be configured together"
        )

    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise BrowserlessConfigurationError("Invalid Browserless endpoint") from None

    if (
        parsed.scheme.lower() not in {"https", "wss"}
        or (parsed.hostname or "").lower() not in _BROWSERLESS_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise BrowserlessConfigurationError("Invalid Browserless endpoint")

    host = parsed.hostname.lower()
    query = urllib.parse.urlencode({
        "token": token,
        "timeout": str(_BROWSERLESS_SESSION_TIMEOUT_MS),
    })
    return urllib.parse.urlunsplit(("wss", host, "", query, ""))


def _host_of(url):
    """The host part of a URL, used to keep the polite delay per server."""
    try:
        return urllib.parse.urlparse(url or "").netloc.lower()
    except ValueError:
        return ""


def parse_count(text):
    """Turn '1,234' or '12.3K' or '1.1M' into an integer. None when unreadable."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace(" ", "")
    multiplier = 1
    if text[-1:].lower() == "k":
        multiplier, text = 1_000, text[:-1]
    elif text[-1:].lower() == "m":
        multiplier, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def followers_from_html(html):
    """Read a follower count out of the og:description meta tag."""
    match = _OG_DESCRIPTION.search(html or "")
    if not match:
        return None
    found = _FOLLOWERS_IN_TEXT.search(match.group(1))
    return parse_count(found.group(1)) if found else None


class Browser:
    """One browser for the whole run. Faster and more stable than one per site."""

    def __init__(
        self,
        polite_delay=None,
        log=None,
        respect_robots=None,
        enforce_public_browser_requests=False,
        public_resolver=None,
    ):
        self.polite_delay = (
            config.POLITE_DELAY_SECONDS if polite_delay is None else polite_delay
        )
        self.log = log or (lambda message: None)
        self.respect_robots = (
            config.RESPECT_ROBOTS if respect_robots is None else respect_robots
        )
        self.enforce_public_browser_requests = bool(enforce_public_browser_requests)
        self.public_resolver = public_resolver
        self.deadline = None
        self._last_hit_by_host = {}

    def set_deadline(self, deadline):
        """Set the current business budget; direct callers may still pass one."""
        self.deadline = deadline

    def __enter__(self):
        remote_url = _browserless_cdp_url(
            config.BROWSERLESS_ENDPOINT,
            config.BROWSERLESS_TOKEN,
        )
        self._playwright = sync_playwright().start()
        context_kwargs = {
            "user_agent": config.USER_AGENT,
            "viewport": {"width": 1366, "height": 900},
            "locale": "en-SG",
        }
        if self.enforce_public_browser_requests:
            context_kwargs["service_workers"] = "block"

        try:
            if remote_url is None:
                self.browser = self._playwright.chromium.launch(headless=True)
            else:
                self.browser = self._playwright.chromium.connect_over_cdp(remote_url)
            self._remote_browser = remote_url is not None
            # Always create LeadScan's own context. In public mode this is what
            # preserves service-worker blocking instead of inheriting an
            # unguarded remote default context.
            self.context = self.browser.new_context(**context_kwargs)
            if self.enforce_public_browser_requests:
                self._install_public_websocket_guard()
        except Exception:
            self._close_resources()
            if remote_url is not None:
                raise BrowserlessConnectionError(
                    "Remote browser connection failed"
                ) from None
            raise
        return self

    def _block_public_websocket(self, websocket):
        """
        Keep routed WebSockets entirely inside Playwright in public mode.

        Intentionally do NOT call connect_to_server(), because a routed
        WebSocket does not connect to the real server unless that method
        is called.

        Also do NOT call websocket.close() here. Real sync-mode verification
        showed that closing synchronously from this callback can deadlock
        while page navigation is in progress.
        """
        return None

    def _install_public_websocket_guard(self):
        self.context.route_web_socket(
            re.compile(r"^wss?://", re.I),
            self._block_public_websocket,
        )

    def _new_page(self):
        page = self.context.new_page()
        if self.enforce_public_browser_requests:
            self._install_public_cdp_guard(page)
        return page

    def _handle_public_cdp_request(self, session, params):
        request_id = params["requestId"]
        url = params["request"]["url"]

        if not url.lower().startswith(("http://", "https://")):
            session.send(
                "Fetch.continueRequest",
                {"requestId": request_id},
            )
            return

        try:
            url_safety.resolve_public_url(
                url,
                resolver=self.public_resolver,
            )
        except url_safety.UnsafeURL:
            session.send(
                "Fetch.failRequest",
                {
                    "requestId": request_id,
                    "errorReason": "BlockedByClient",
                },
            )
            return

        session.send(
            "Fetch.continueRequest",
            {"requestId": request_id},
        )

    def _install_public_cdp_guard(self, page):
        """
        Attach a Chromium CDP session to intercept all HTTP(S) requests via Fetch.

        SECURITY NOTE:
        The public Browser mode:
        * validates Chromium HTTP(S) requests with CDP Fetch;
        * re-checks redirect hops;
        * protects normal subresources;
        * disables service workers;
        * routes all ws:// / wss:// locally without connecting to the server;
        * public-safe robots fetching is separately enforced.

        REMAINING LIMITATION:
        * DNS validation vs actual socket connection retains a DNS-rebinding / TOCTOU boundary;
        * Production deployment MUST restrict network egress to private/internal/metadata destinations
          as defense in depth.
        """
        session = self.context.new_cdp_session(page)
        session.on(
            "Fetch.requestPaused",
            lambda params: self._handle_public_cdp_request(session, params),
        )
        session.send(
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
        return session

    def __exit__(self, *exc):
        self._close_resources()
        return False

    def _close_resources(self):
        # Close each resource on its own. A failure in one must not leak the
        # others, which was the fault in the earlier one-line version.
        for close in (
            getattr(self, "context", None),
            getattr(self, "browser", None),
        ):
            try:
                if close is not None:
                    close.close()
            except Exception:
                pass
        try:
            self._playwright.stop()
        except Exception:
            pass

    def _wait_politely(self, url="", deadline=None):
        """
        Wait before hitting the SAME host again.

        The delay used to be global, so a sweep of 200 different sites paid 200
        seconds of waiting for no reason: no single host was under any load.
        The courtesy that matters is not hitting one server twice in quick
        succession, which is exactly what the contact-page check does.
        """
        budget = deadline or self.deadline
        if budget:
            budget.check()
        host = _host_of(url)
        last = self._last_hit_by_host.get(host, 0.0)
        gap = time.time() - last
        if gap < self.polite_delay:
            wait = self.polite_delay - gap
            if budget:
                wait = budget.cap_seconds(wait)
            time.sleep(wait)
            if budget:
                budget.check()
        self._last_hit_by_host[host] = time.time()

    # -----------------------------------------------------------------
    # Page rendering
    # -----------------------------------------------------------------
    def _read_remote_robots_text(self, robots_url, timeout, deadline=None):
        """Fetch a bounded robots body through the guarded remote browser."""
        page = self._new_page()
        try:
            if deadline:
                deadline.check()
            timeout_ms = max(1, int(float(timeout) * 1000))
            if deadline:
                timeout_ms = deadline.cap_milliseconds(timeout_ms)
            response = page.goto(
                robots_url,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            if deadline:
                deadline.check()
            if response is None or response.status != 200:
                return None

            headers = response.all_headers()
            content_length = headers.get("content-length")
            if content_length is not None:
                try:
                    if int(str(content_length).strip()) > robots.MAX_PUBLIC_ROBOTS_BYTES:
                        return None
                except (TypeError, ValueError):
                    pass

            body = response.body()
            if not isinstance(body, (bytes, bytearray)):
                return None
            if len(body) > robots.MAX_PUBLIC_ROBOTS_BYTES:
                return None
            text = bytes(body).decode("utf-8", errors="replace")
            return page.url, text
        except deadlines.AuditDeadlineExceeded:
            raise
        except Exception:
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def render(self, url, deadline=None):
        """
        Load a page fully. Give back (html, final_url, load_seconds, error).

        The first try uses the normal timeout. If it times out, one more try
        uses a longer timeout, because a slow site is not a dead site.
        """
        budget = deadline or self.deadline
        if budget:
            budget.check()
        if not url or not url.strip():
            return None, None, None, "no website"
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if self.respect_robots:
            robots_timeout = budget.cap_seconds(8) if budget else 8
            remote_public_robots = (
                getattr(self, "_remote_browser", False)
                and self.enforce_public_browser_requests
            )
            robots_kwargs = {
                "timeout": robots_timeout,
                "public_only": self.enforce_public_browser_requests,
                "resolver": (
                    self.public_resolver
                    if self.enforce_public_browser_requests
                    else None
                ),
            }
            if remote_public_robots:
                robots_kwargs["text_fetcher"] = (
                    lambda robots_url, timeout: self._read_remote_robots_text(
                        robots_url,
                        timeout,
                        deadline=budget,
                    )
                )
            try:
                allowed = robots.may_fetch(
                    url,
                    **robots_kwargs,
                )
            except url_safety.UnsafeURL:
                return None, url, None, "unsafe URL blocked"

            if not allowed:
                return None, url, None, "blocked by robots.txt"

        timeouts = [config.NAV_TIMEOUT_MS] + [config.RETRY_TIMEOUT_MS] * config.RENDER_RETRIES
        last_error = "unreachable"
        for attempt, timeout in enumerate(timeouts):
            if budget:
                budget.check()
            self._wait_politely(url, deadline=budget)
            html, final_url, load, error = self._render_once(
                url, timeout, deadline=budget)
            if error is None:
                return html, final_url, load, None
            last_error = error
            # Only a timeout is worth a second try. A DNS failure or a 404 will
            # not change, so stop and save the time.
            if "timeout" not in error.lower():
                break
            if attempt < len(timeouts) - 1:
                self.log(f"      retry after timeout: {url}")
        return None, url, None, last_error

    def _render_once(self, url, timeout_ms, deadline=None):
        page = self._new_page()
        try:
            start = time.time()
            if deadline:
                timeout_ms = deadline.cap_milliseconds(timeout_ms)
            response = page.goto(url, timeout=timeout_ms, wait_until="load")
            if deadline:
                deadline.check()
            settle_ms = int(config.SETTLE_SECONDS * 1000)
            if deadline:
                settle_ms = deadline.cap_milliseconds(settle_ms)
            if settle_ms:
                page.wait_for_timeout(settle_ms)
            if deadline:
                deadline.check()
            load = round(time.time() - start, 2)
            if response is not None and response.status >= 400:
                return None, page.url, load, f"http {response.status}"
            return page.content(), page.url, load, None
        except deadlines.AuditDeadlineExceeded:
            raise
        except Exception as error:
            message = str(error).splitlines()[0][:80]
            if "Timeout" in message or "timeout" in message:
                return None, url, None, f"timeout after {timeout_ms // 1000}s"
            return None, url, None, f"unreachable ({message})"
        finally:
            try:
                page.close()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Social profiles
    # -----------------------------------------------------------------
    def followers(self, profile_url, deadline=None):
        """
        Best-effort follower count. Instagram and TikTok often block a logged-out
        visitor, so None is a common and correct answer, not a fault. The
        reliable "quiet" signal is the Google review count.
        """
        budget = deadline or self.deadline
        if budget:
            budget.check()
        if not profile_url:
            return None
        self._wait_politely(profile_url, deadline=budget)
        page = self._new_page()
        try:
            timeout = (budget.cap_milliseconds(config.NAV_TIMEOUT_MS)
                       if budget else config.NAV_TIMEOUT_MS)
            page.goto(profile_url, timeout=timeout,
                      wait_until="domcontentloaded")
            if budget:
                budget.check()
            return followers_from_html(page.content())
        except deadlines.AuditDeadlineExceeded:
            raise
        except Exception:
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def find_social(self, name, region=None, deadline=None):
        """
        Find a no-website business's Instagram or TikTok with a web search.

        DuckDuckGo's plain-HTML endpoint is used because Google blocks a scraped
        search. DuckDuckGo wraps each result URL in a redirect and encodes it,
        so the page is decoded first and then scanned for a real profile link.
        """
        budget = deadline or self.deadline
        if budget:
            budget.check()
        region = region or config.SOCIAL_SEARCH_REGION
        query = f"{name} {region} instagram tiktok".strip()
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        self._wait_politely(url, deadline=budget)
        page = self._new_page()
        try:
            timeout = (budget.cap_milliseconds(config.NAV_TIMEOUT_MS)
                       if budget else config.NAV_TIMEOUT_MS)
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if budget:
                budget.check()
            html = urllib.parse.unquote(page.content())
        except deadlines.AuditDeadlineExceeded:
            raise
        except Exception:
            return {}
        finally:
            try:
                page.close()
            except Exception:
                pass

        found = {}
        for network in ("instagram", "tiktok"):
            link = detect.first_profile(html, network)
            if link:
                found[network] = link
        return found
