"""
checks.py
---------
The brain. For one business it:
  1. Opens the site in a REAL headless browser (Playwright) so JavaScript-loaded
     pixels and forms are actually seen -- fixes the v1 false-negatives.
  2. Detects: can they capture a lead? do they spend on ads? social links?
  3. Scores against the ICP (Ideal Customer Profile):
        quiet business + already runs ads + broken funnel   = HOT lead
        quiet business + posts on IG/TikTok + broken funnel = HOT lead
            (organic angle: they market on the exact channel Nixon works in,
             but the following leaks because the site can't capture it)
        big social following (influencer-run)               = DISQUALIFY
  4. Writes a plain-English "hook" (the opening line for the call).

WHY the ICP shift matters: an influencer-run firm already owns an audience, so
they don't need Nixon. The perfect lead is a QUIET firm that pays for ads but
can't convert -- they have the problem and no other way to solve it.
"""

import re
import time
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

NAV_TIMEOUT = 15000  # ms per page. Slow-to-load is itself a red flag.

# Social networks we look for links to, and how to spot a "real" profile link.
SOCIAL_HOSTS = {
    "instagram": "instagram.com",
    "facebook": "facebook.com",
    "tiktok": "tiktok.com",
    "linkedin": "linkedin.com",
}


# --------------------------------------------------------------------------
# Browser lifecycle -- one browser reused for the whole run (fast + stable).
# --------------------------------------------------------------------------
class Browser:
    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
        )
        return self

    def __exit__(self, *a):
        try:
            self.context.close(); self.browser.close(); self._pw.stop()
        except Exception:
            pass

    def render(self, url):
        """Load a page fully. Returns (html, final_url, load_seconds, error)."""
        if not url:
            return None, None, None, "no website"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page = self.context.new_page()
        try:
            start = time.time()
            resp = page.goto(url, timeout=NAV_TIMEOUT, wait_until="load")
            time.sleep(1.2)  # let late-firing scripts (pixels) run
            load = round(time.time() - start, 2)
            if resp and resp.status >= 400:
                return None, page.url, load, f"http {resp.status}"
            html = page.content()
            return html, page.url, load, None
        except Exception as e:
            msg = str(e).splitlines()[0][:60]
            return None, url, None, f"unreachable ({msg})"
        finally:
            page.close()

    def instagram_followers(self, ig_url):
        """
        BEST-EFFORT Instagram follower count. Instagram blocks logged-out access
        often, so this returns None a lot -- that's expected, not a bug. The
        reliable 'quiet' signal is Google review_count; this is a bonus.
        """
        if not ig_url:
            return None
        page = self.context.new_page()
        try:
            page.goto(ig_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            html = page.content()
            # IG puts "1,234 Followers, ..." in the og:description meta tag.
            m = re.search(r'([\d.,]+[KMkm]?)\s+Followers', html)
            return _parse_count(m.group(1)) if m else None
        except Exception:
            return None
        finally:
            page.close()

    def tiktok_followers(self, tt_url):
        """
        BEST-EFFORT TikTok follower count, same idea as instagram_followers.
        TikTok also puts "1234 Followers" in its og:description, but blocks bots
        a lot -- returns None often, and that's fine.
        """
        if not tt_url:
            return None
        page = self.context.new_page()
        try:
            page.goto(tt_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            html = page.content()
            m = re.search(r'([\d.,]+[KMkm]?)\s+Followers', html)
            return _parse_count(m.group(1)) if m else None
        except Exception:
            return None
        finally:
            page.close()

    def find_social(self, name, region="Singapore"):
        """
        Find a no-website business's IG/TikTok by web-searching for it.

        We use DuckDuckGo's plain-HTML endpoint because Google blocks scraped
        searches. DDG wraps each result URL in a redirect and URL-encodes it, so
        we unquote the whole page first, then regex out the first real IG/TikTok
        PROFILE link. Best-effort: returns {} when nothing convincing is found.
        """
        query = f"{name} {region} instagram tiktok".strip()
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        page = self.context.new_page()
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            html = urllib.parse.unquote(page.content())
        except Exception:
            return {}
        finally:
            page.close()

        found = {}
        ig = _first_profile(html, "instagram")
        tt = _first_profile(html, "tiktok")
        if ig:
            found["instagram"] = ig
        if tt:
            found["tiktok"] = tt
        return found


# Path fragments that are NOT a profile (posts, reels, search, share, help...).
_NON_PROFILE = ("/p/", "/reel/", "/reels/", "/explore", "/tags/", "/tag/",
                "/accounts", "/about", "/help", "/legal", "/share", "intent/",
                "/hashtag/", "/discover", "/music/", "/video/", "/@/")


def _first_profile(html, network):
    """
    Pull the first real IG or TikTok profile URL out of a blob of HTML.
    IG profiles look like instagram.com/<handle>; TikTok like tiktok.com/@<handle>.
    """
    if network == "instagram":
        pattern = r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?'
    else:  # tiktok
        pattern = r'https?://(?:www\.)?tiktok\.com/(@[A-Za-z0-9_.]+)/?'
    for m in re.finditer(pattern, html):
        full = m.group(0)
        low = full.lower()
        if any(bad in low for bad in _NON_PROFILE):
            continue
        handle = m.group(1).strip("/").lower()
        # Guard against catching the network's own utility pages as a "handle".
        if handle in ("explore", "accounts", "about", "p", "reel", "reels",
                      "directory", "tv", "stories", "@"):
            continue
        return full
    return None


def _parse_count(text):
    """Turn '1,234' or '12.3K' or '1.1M' into an integer."""
    text = text.strip().replace(",", "")
    mult = 1
    if text[-1:].lower() == "k":
        mult, text = 1_000, text[:-1]
    elif text[-1:].lower() == "m":
        mult, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Page analysis
# --------------------------------------------------------------------------
def analyze(html, final_url, load_seconds):
    soup = BeautifulSoup(html, "html.parser")
    lowered = html.lower()

    has_form = bool(soup.find("form"))
    has_phone_link = "tel:" in lowered
    has_email_link = "mailto:" in lowered
    booking_tools = ["calendly", "acuity", "cal.com", "hubspot", "typeform",
                     "jotform", "book now", "schedule a", "get a quote", "free quote",
                     "consultation", "book a call", "start your project",
                     "work with us", "inquire", "let's talk"]
    has_booking = any(t in lowered for t in booking_tools)
    can_capture_lead = has_form or has_booking or has_phone_link or has_email_link

    has_meta_pixel = ("fbq(" in lowered) or ("connect.facebook.net" in lowered)
    has_google_tag = ("gtag(" in lowered) or ("googletagmanager.com" in lowered) \
        or ("google-analytics.com" in lowered)
    spends_on_ads = has_meta_pixel or has_google_tag

    has_mobile_viewport = bool(soup.find("meta", attrs={"name": "viewport"}))
    is_https = bool(final_url and final_url.startswith("https://"))
    is_slow = load_seconds is not None and load_seconds > 5

    socials = _extract_socials(soup)

    # "Markets on social" = they push content on IG/TikTok -- the exact channel
    # Nixon works in. A business doing this but with a broken funnel is leaking
    # organic effort, so it's a warm lead even if it never touches paid ads. We
    # scope this to IG/TikTok (not Facebook), because a lone FB link is usually a
    # dormant legacy page, not active content marketing.
    markets_on_social = bool(socials.get("instagram") or socials.get("tiktok"))

    return {
        "can_capture_lead": can_capture_lead,
        "has_meta_pixel": has_meta_pixel,
        "has_google_tag": has_google_tag,
        "spends_on_ads": spends_on_ads,
        "markets_on_social": markets_on_social,
        "has_mobile_viewport": has_mobile_viewport,
        "is_https": is_https,
        "is_slow": is_slow,
        "load_seconds": load_seconds,
        "instagram": socials.get("instagram", ""),
        "facebook": socials.get("facebook", ""),
        "tiktok": socials.get("tiktok", ""),
    }


# Links that live on a social platform but are NOT a business profile: the
# platform's own corporate/dev/help/share pages. Without this filter we'd grab
# e.g. "about.instagram.com" or "developers.facebook.com" out of a page footer
# and mistake it for the business's account.
_FB_NOISE = ("developers.", "business.", "l.facebook", "about.", "help.",
             "/sharer", "/plugins", "/docs", "/policies", "/tr", "/legal",
             "/login", "/dialog")


def _first_facebook(html):
    """First real Facebook page/profile URL, skipping platform noise."""
    for m in re.finditer(r'https?://(?:www\.|[a-z-]+\.)?facebook\.com/[^\s"\'<>]+', html):
        full, low = m.group(0), m.group(0).lower()
        if any(bad in low for bad in _FB_NOISE):
            continue
        return full.rstrip('/",\'')
    return None


def _extract_socials(soup):
    """
    Pull the first REAL profile link for each network. Uses _first_profile (which
    already rejects the platform's utility pages) for IG/TikTok, and a noise-
    filtered scan for Facebook. Fixes the footer-link bug where the platform's own
    corporate links (about.instagram.com, developers.facebook.com) were captured.
    """
    html = str(soup)
    found = {}
    ig = _first_profile(html, "instagram")
    if ig:
        found["instagram"] = ig
    tt = _first_profile(html, "tiktok")
    if tt:
        found["tiktok"] = tt
    fb = _first_facebook(html)
    if fb:
        found["facebook"] = fb
    return found


# --------------------------------------------------------------------------
# Scoring against the ICP
# --------------------------------------------------------------------------
INFLUENCER_FOLLOWERS = 20_000  # at/above this we assume they don't need help

# A firm with this many Google reviews is established. If ITS site merely errors
# (503/timeout/cert), that's almost certainly a transient blip -- not a genuinely
# broken funnel worth calling. Small firms that error are more likely truly broken.
ESTABLISHED_REVIEWS = 60

# Tighter bar for the no-website tier: a social-only micro-business over this is
# big enough to run its own thing, so it isn't a fit for Nixon. Deliberately much
# lower than INFLUENCER_FOLLOWERS -- these firms have no site, so a few thousand
# followers is already "established" for them.
SOCIAL_ONLY_MAX_FOLLOWERS = 3_000


def score_and_hook(findings, review_count, follower_count, error=None):
    """
    Returns (score, warm bool, disqualified bool, hook str).
    warm = worth calling. disqualified = influencer-run, skip.
    """
    # --- Influencer disqualifier (the SG Interior Design case) ---
    if follower_count is not None and follower_count >= INFLUENCER_FOLLOWERS:
        return 0, False, True, (
            f"SKIP -- ~{follower_count:,} IG followers. They already own an audience, "
            f"so they don't need lead-gen help."
        )

    # --- No website AT ALL: a different situation from a broken site. We must
    #     NOT claim "your ad clicks land on nothing" -- we have no evidence they
    #     run ads. It's an honest "you're invisible online" lead, and only warm if
    #     they're small enough to plausibly need help. (--social-only enriches
    #     these with their real IG/TikTok + follower count.) ---
    if error == "no website":
        quiet = review_count is None or review_count <= 30
        score = 40 if quiet else 15
        return score, quiet, False, (
            "No website at all -- you're leaning on word-of-mouth and Google, so "
            "there's nothing to turn an interested person into a booking. "
            "(Check their IG/TikTok with --social-only.)"
        )

    # --- Real broken/dead site. Strong lead ONLY if the firm is small. A big,
    #     established firm (many reviews) that momentarily 503s/times-out is almost
    #     certainly a transient blip, not a broken funnel -- don't waste the call. ---
    if error:
        if review_count is not None and review_count > ESTABLISHED_REVIEWS:
            return 10, False, False, (
                f"Site errored ({error}) but they have {review_count} reviews -- "
                f"likely a transient blip on an established firm, not a real lead."
            )
        return 60, True, False, (
            f"Their site is down/broken ({error}). If they're paying for ads or "
            f"posting on social, every click lands on a dead page."
        )

    score = 0
    pains = []

    if not findings["can_capture_lead"]:
        score += 40
        pains.append("no form, booking, or click-to-call on the page")
    if not findings["has_mobile_viewport"]:
        score += 15
        pains.append("the site isn't built for mobile")
    if not findings["is_https"]:
        score += 10
        pains.append("no HTTPS padlock, which scares buyers off")
    if findings["is_slow"]:
        score += 15
        pains.append(f"the page took {findings['load_seconds']}s to load")

    # --- Do they invest in getting attention? Two ways in: paid ads, OR
    #     organic content on IG/TikTok. Either one means a real audience is
    #     flowing to a funnel that (per the pains above) can't hold it. Paid
    #     scores a touch higher because it proves cash is already going out the
    #     door every day it stays broken. ---
    runs_ads = findings["spends_on_ads"]
    markets_on_social = findings.get("markets_on_social", False)
    if runs_ads:
        score += 15
        ad_tool = "Meta Pixel" if findings["has_meta_pixel"] else "Google tag"
        channel_note = f"You're running paid ads ({ad_tool} is on your site)"
        leak = "that ad traffic has nowhere to convert"
    elif markets_on_social:
        score += 10
        chans = [c for c, on in (("Instagram", findings.get("instagram")),
                                 ("TikTok", findings.get("tiktok"))) if on]
        channel = " and ".join(chans) or "social media"
        # Name the audience if we managed to read a follower count (best-effort).
        aud = (f"your ~{follower_count:,} {chans[0]} followers"
               if follower_count and chans else "the people you reach")
        channel_note = f"You're building an audience on {channel}"
        leak = f"{aud} have no clear next step"
    else:
        channel_note = "You may be spending on ads without tracking"
        leak = "that traffic has nowhere to convert"

    # --- The QUIET signal: low reviews = small firm that likely needs help ---
    quiet_note = ""
    if review_count is not None:
        if review_count <= 30:
            score += 20
            quiet_note = f" You've only got {review_count} Google reviews, so you're still under the radar."
        elif review_count <= 100:
            score += 5
        else:
            score -= 10  # established, plenty of traction already

    score = max(0, min(score, 100))

    # Warm = there's a real problem AND they show they invest in getting seen --
    # by paid ads, by organic social, or by being a quiet firm still worth a call.
    warm = bool(pains) and (
        runs_ads or markets_on_social or (review_count is not None and review_count <= 30)
    )

    if not pains:
        hook = f"{channel_note} -- and your funnel looks solid. Lower priority."
    else:
        hook = f"{channel_note} -- but {pains[0]}, so {leak}.{quiet_note}"

    return score, warm, False, hook


def audit_business(browser, biz):
    """Full pipeline for one business dict. Returns a flat result row."""
    html, final_url, load, error = browser.render(biz["website"])
    findings = {} if error else analyze(html, final_url, load)

    # If Google's "website" for them is really their IG/TikTok page, treat that as
    # the social handle directly (don't rely on scraping the profile as a site).
    web = (biz.get("website") or "").lower()
    if findings:
        if "instagram.com" in web and not findings.get("instagram"):
            findings["instagram"] = biz["website"]
        if "tiktok.com" in web and not findings.get("tiktok"):
            findings["tiktok"] = biz["website"]

    # Best-effort follower count only if we found an Instagram link.
    follower_count = None
    ig = findings.get("instagram") if findings else ""
    if ig:
        follower_count = browser.instagram_followers(ig)

    score, warm, disq, hook = score_and_hook(findings, biz.get("review_count"),
                                              follower_count, error=error)
    # Tier: "hot" = we can SEE they pay for ads (pixel/tag) and the funnel leaks.
    # Everything else warm is "warm". Nixon calls hot first.
    tier = ""
    if warm and not disq:
        tier = "hot" if (findings and findings.get("spends_on_ads")) else "warm"
    return {
        "score": score, "tier": tier, "warm": warm, "disqualified": disq,
        "name": biz["name"], "phone": biz.get("phone", ""),
        "review_count": biz.get("review_count"),
        "instagram_followers": follower_count,
        "instagram": findings.get("instagram", "") if findings else "",
        "facebook": findings.get("facebook", "") if findings else "",
        "tiktok": findings.get("tiktok", "") if findings else "",
        "hook": hook, "website": biz["website"], "final_url": final_url,
        "status": error or "ok",
    }


# --------------------------------------------------------------------------
# The no-website tier: businesses that run entirely off IG/TikTok.
# No site to render, so we score purely on "they have zero funnel" + audience.
# --------------------------------------------------------------------------
def score_social_only(name, socials, follower_count, review_count):
    """
    Score a business that has NO website, only social. Returns
    (score, warm bool, disqualified bool, hook str).

    WHY these are hot: with no website at all, there is literally nothing to turn
    a follower or a Google search into a booking -- the funnel isn't broken, it's
    absent. The one disqualifier is size: over SOCIAL_ONLY_MAX_FOLLOWERS they're
    big enough to sell for themselves and don't need Nixon.
    """
    # --- Too big for this tier: they already own an audience ---
    if follower_count is not None and follower_count > SOCIAL_ONLY_MAX_FOLLOWERS:
        return 0, False, True, (
            f"SKIP -- ~{follower_count:,} followers with no website. Big enough to "
            f"run their own lead-gen, so not a fit."
        )

    chans = [c for c, on in (("Instagram", socials.get("instagram")),
                             ("TikTok", socials.get("tiktok"))) if on]

    # --- Couldn't find any socials either: invisible online ---
    if not chans:
        score = 55
        if review_count is not None and review_count <= 30:
            score += 15
        return min(score, 100), True, False, (
            f"No website, and we couldn't find an active IG or TikTok for you -- "
            f"you're basically invisible online, so any word-of-mouth just evaporates."
        )

    # --- The sweet spot: real (small) social presence, zero funnel ---
    channel = " and ".join(chans)
    score = 70
    if follower_count is not None:
        aud = f"Your ~{follower_count:,} {chans[0]} followers have nowhere to go --"
    else:
        aud = "Anyone who finds you on there has no next step --"
    if review_count is not None and review_count <= 30:
        score += 10
    score = min(score, 100)

    hook = (
        f"Everything runs off your {channel} and there's no website at all. "
        f"{aud} no site, no form, no way to book you."
    )
    return score, True, False, hook


def audit_social_only(browser, biz, region="Singapore"):
    """
    Pipeline for a no-website business: web-search its IG/TikTok, best-effort read
    a follower count, then score. Returns the SAME row schema as audit_business.
    """
    socials = browser.find_social(biz["name"], region)

    # Best-effort follower count: prefer Instagram, fall back to TikTok.
    follower_count = None
    if socials.get("instagram"):
        follower_count = browser.instagram_followers(socials["instagram"])
    if follower_count is None and socials.get("tiktok"):
        follower_count = browser.tiktok_followers(socials["tiktok"])

    score, warm, disq, hook = score_social_only(
        biz["name"], socials, follower_count, biz.get("review_count")
    )
    # No-website firms can't show a pixel, so they're never "hot" -- always warm.
    tier = "warm" if (warm and not disq) else ""
    return {
        "score": score, "tier": tier, "warm": warm, "disqualified": disq,
        "name": biz["name"], "phone": biz.get("phone", ""),
        "review_count": biz.get("review_count"),
        "instagram_followers": follower_count,
        "instagram": socials.get("instagram", ""),
        "facebook": "",
        "tiktok": socials.get("tiktok", ""),
        "hook": hook, "website": "", "final_url": "",
        "status": "social-only",
    }
