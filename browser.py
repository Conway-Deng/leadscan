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
import detect

# Instagram and TikTok put the follower count in the og:description meta tag.
_OG_DESCRIPTION = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]*content=["\']([^"\']{0,400})',
    re.I,
)
_FOLLOWERS_IN_TEXT = re.compile(r"([\d.,]+\s*[KMkm]?)\s*Followers", re.I)


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

    def __init__(self, polite_delay=None, log=None):
        self.polite_delay = (
            config.POLITE_DELAY_SECONDS if polite_delay is None else polite_delay
        )
        self.log = log or (lambda message: None)
        self._last_hit_by_host = {}

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-SG",
        )
        return self

    def __exit__(self, *exc):
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
        return False

    def _wait_politely(self, url=""):
        """
        Wait before hitting the SAME host again.

        The delay used to be global, so a sweep of 200 different sites paid 200
        seconds of waiting for no reason: no single host was under any load.
        The courtesy that matters is not hitting one server twice in quick
        succession, which is exactly what the contact-page check does.
        """
        host = _host_of(url)
        last = self._last_hit_by_host.get(host, 0.0)
        gap = time.time() - last
        if gap < self.polite_delay:
            time.sleep(self.polite_delay - gap)
        self._last_hit_by_host[host] = time.time()

    # -----------------------------------------------------------------
    # Page rendering
    # -----------------------------------------------------------------
    def render(self, url):
        """
        Load a page fully. Give back (html, final_url, load_seconds, error).

        The first try uses the normal timeout. If it times out, one more try
        uses a longer timeout, because a slow site is not a dead site.
        """
        if not url or not url.strip():
            return None, None, None, "no website"
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        timeouts = [config.NAV_TIMEOUT_MS] + [config.RETRY_TIMEOUT_MS] * config.RENDER_RETRIES
        last_error = "unreachable"
        for attempt, timeout in enumerate(timeouts):
            self._wait_politely(url)
            html, final_url, load, error = self._render_once(url, timeout)
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

    def _render_once(self, url, timeout_ms):
        page = self.context.new_page()
        try:
            start = time.time()
            response = page.goto(url, timeout=timeout_ms, wait_until="load")
            page.wait_for_timeout(int(config.SETTLE_SECONDS * 1000))
            load = round(time.time() - start, 2)
            if response is not None and response.status >= 400:
                return None, page.url, load, f"http {response.status}"
            return page.content(), page.url, load, None
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
    def followers(self, profile_url):
        """
        Best-effort follower count. Instagram and TikTok often block a logged-out
        visitor, so None is a common and correct answer, not a fault. The
        reliable "quiet" signal is the Google review count.
        """
        if not profile_url:
            return None
        self._wait_politely(profile_url)
        page = self.context.new_page()
        try:
            page.goto(profile_url, timeout=config.NAV_TIMEOUT_MS,
                      wait_until="domcontentloaded")
            return followers_from_html(page.content())
        except Exception:
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def find_social(self, name, region=None):
        """
        Find a no-website business's Instagram or TikTok with a web search.

        DuckDuckGo's plain-HTML endpoint is used because Google blocks a scraped
        search. DuckDuckGo wraps each result URL in a redirect and encodes it,
        so the page is decoded first and then scanned for a real profile link.
        """
        region = region or config.SOCIAL_SEARCH_REGION
        query = f"{name} {region} instagram tiktok".strip()
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        self._wait_politely(url)
        page = self.context.new_page()
        try:
            page.goto(url, timeout=config.NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            html = urllib.parse.unquote(page.content())
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
