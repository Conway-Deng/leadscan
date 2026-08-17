"""
detect.py
---------
Pure analysis of one page of HTML. No browser, no network, no state. Every
function here takes text and gives back a result, so the test suite can check
the logic without a browser.

Three questions get answered:

  1. Can this business capture a lead?  (a form, a booking tool, a tel: link)
  2. Do they pay for advertisements?    (an advertisement tag, not analytics)
  3. Which social profiles do they own? (a real profile, not a platform page)

WHY THE ADVERTISEMENT TEST CHANGED
The earlier version counted Google Analytics as proof of advertisement spend.
Almost every website has Google Analytics, so almost every lead became "hot"
and the tier meant nothing. Analytics shows that somebody measures traffic. It
does not show that somebody buys traffic. This module separates the two:

  * AD_PROOF      -- a conversion or retargeting tag. Money is going out.
  * ANALYTICS_ONLY-- a measurement tag. It proves nothing about spend.

The clearest single signal is the Google tag prefix. "AW-" is a Google Ads
conversion identifier. "G-" is a Google Analytics 4 identifier. They look
almost the same in the page source and mean completely different things.
"""

import re
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Advertisement proof
# ---------------------------------------------------------------------------
# Each entry is (label, regular expression). A match is proof that the business
# runs paid advertisements, or at least pays to retarget people.

AD_PROOF_PATTERNS = [
    ("Meta Pixel", re.compile(r"fbq\s*\(\s*['\"]init['\"]", re.I)),
    ("Meta Pixel", re.compile(r"connect\.facebook\.net/[^\"'\s]*/fbevents\.js", re.I)),
    ("Meta Pixel", re.compile(r"facebook\.com/tr\?[^\"'\s]*id=\d", re.I)),
    ("Google Ads tag", re.compile(r"\bAW-\d{6,}", re.I)),
    ("Google Ads tag", re.compile(r"google_conversion_id", re.I)),
    ("Google Ads tag", re.compile(r"googleads\.g\.doubleclick\.net", re.I)),
    ("Google Ads tag", re.compile(r"googleadservices\.com/pagead/conversion", re.I)),
    ("TikTok Pixel", re.compile(r"analytics\.tiktok\.com/i18n/pixel", re.I)),
    ("TikTok Pixel", re.compile(r"\bttq\.(?:load|track)\s*\(", re.I)),
    ("LinkedIn Insight tag", re.compile(r"snap\.licdn\.com/li\.lms-analytics", re.I)),
    ("Google remarketing", re.compile(r"googlesyndication\.com", re.I)),
    ("Microsoft Ads UET", re.compile(r"bat\.bing\.com/bat\.js", re.I)),
    ("Pinterest tag", re.compile(r"\bpintrk\s*\(", re.I)),
    ("Twitter/X pixel", re.compile(r"static\.ads-twitter\.com", re.I)),
    ("Snap Pixel", re.compile(r"sc-static\.net/scevent", re.I)),
]

# Measurement only. Never proof of spend.
ANALYTICS_PATTERNS = [
    ("Google Analytics 4", re.compile(r"\bG-[A-Z0-9]{6,}\b")),
    ("Google Analytics", re.compile(r"\bUA-\d{4,}-\d+\b")),
    ("Google Analytics", re.compile(r"google-analytics\.com/(?:analytics|ga|collect)", re.I)),
    ("Google Tag Manager", re.compile(r"googletagmanager\.com/gtm\.js", re.I)),
    ("Hotjar", re.compile(r"static\.hotjar\.com", re.I)),
    ("Microsoft Clarity", re.compile(r"clarity\.ms/tag", re.I)),
]


def find_ad_tags(html):
    """Give back the sorted list of advertisement tags found in the page."""
    return _match_labels(html, AD_PROOF_PATTERNS)


def find_analytics_tags(html):
    """Give back the sorted list of measurement-only tags found in the page."""
    return _match_labels(html, ANALYTICS_PATTERNS)


def _match_labels(html, patterns):
    found = []
    for label, pattern in patterns:
        if label in found:
            continue
        if pattern.search(html):
            found.append(label)
    return sorted(found)


# ---------------------------------------------------------------------------
# Lead capture
# ---------------------------------------------------------------------------
# The earlier version searched for words such as "consultation" anywhere in the
# page, and counted any "mailto:" as lead capture. Almost every website passed,
# so the largest score item (no lead capture, +40) almost never applied. This
# version looks for elements that really capture a lead.

# Hosted booking and form tools. A script or an iframe from one of these is a
# real capture path, even when the page has no <form> element of its own.
BOOKING_HOSTS = [
    ("Calendly", re.compile(r"calendly\.com", re.I)),
    ("Acuity", re.compile(r"acuityscheduling\.com|squarespacescheduling\.com", re.I)),
    ("Cal.com", re.compile(r"\bcal\.com/", re.I)),
    ("HubSpot form", re.compile(r"js\.hs(?:-scripts|forms)\.(?:com|net)", re.I)),
    ("Typeform", re.compile(r"typeform\.com", re.I)),
    ("Jotform", re.compile(r"jotform\.com", re.I)),
    ("Google Form", re.compile(r"docs\.google\.com/forms", re.I)),
    ("Setmore", re.compile(r"setmore\.com", re.I)),
    ("Vagaro", re.compile(r"vagaro\.com", re.I)),
    ("Fresha", re.compile(r"fresha\.com", re.I)),
    ("Tally", re.compile(r"tally\.so", re.I)),
    ("Chat widget", re.compile(r"tawk\.to|crisp\.chat|intercom\.io|widget\.manychat", re.I)),
]

# A WhatsApp click-to-chat link. In South East Asia this is the main way a small
# business takes an enquiry, so it counts as real lead capture.
WHATSAPP_PATTERN = re.compile(
    r"(?:wa\.me/\d|api\.whatsapp\.com/send|web\.whatsapp\.com/send|whatsapp://send)", re.I
)

# Input types that show a form really collects contact details.
CONTACT_INPUT_NAMES = re.compile(r"e-?mail|phone|tel|mobile|contact|name|enquir|inquir", re.I)

# Search, newsletter and login forms are not lead capture.
NOT_LEAD_FORM = re.compile(r"search|newsletter|subscribe|login|log-in|sign-?in|cart", re.I)


def find_capture_methods(soup, html):
    """
    Give back the list of real lead-capture methods on the page.

    A method counts when it can turn a visitor into a contactable person:
      * a contact form with at least one contact field
      * a hosted booking or form tool
      * a WhatsApp click-to-chat link
      * a tel: link
      * a mailto: link
    """
    methods = []

    if _has_contact_form(soup):
        methods.append("contact form")

    for label, pattern in BOOKING_HOSTS:
        if pattern.search(html):
            methods.append(label)
            break

    if WHATSAPP_PATTERN.search(html):
        methods.append("WhatsApp link")

    if _has_scheme_link(soup, "tel:"):
        methods.append("click-to-call")

    if _has_scheme_link(soup, "mailto:"):
        methods.append("email link")

    return methods


def _has_contact_form(soup):
    """True when a <form> collects contact details and is not search or login."""
    for form in soup.find_all("form"):
        marker = " ".join(
            str(form.get(attr, "")) for attr in ("id", "class", "name", "action")
        )
        if NOT_LEAD_FORM.search(marker):
            continue
        for field in form.find_all(["input", "textarea", "select"]):
            if field.name == "textarea":
                return True
            kind = (field.get("type") or "text").lower()
            if kind in ("hidden", "submit", "button", "image", "checkbox", "radio"):
                continue
            if kind in ("email", "tel"):
                return True
            marker = " ".join(
                str(field.get(attr, ""))
                for attr in ("name", "id", "placeholder", "aria-label")
            )
            if CONTACT_INPUT_NAMES.search(marker):
                return True
    return False


def _has_scheme_link(soup, scheme):
    for tag in soup.find_all("a", href=True):
        if tag["href"].strip().lower().startswith(scheme):
            return True
    return False


# ---------------------------------------------------------------------------
# Social profiles
# ---------------------------------------------------------------------------
# A page footer holds many links to a social platform that are not the
# business's own profile: the platform's own corporate pages, a share button, a
# developer document, an XML namespace. These must all be rejected.

# Path parts that are not a profile.
_NON_PROFILE_PATHS = (
    "/p/", "/reel/", "/reels/", "/explore", "/tags/", "/tag/", "/accounts",
    "/about", "/help", "/legal", "/share", "intent/", "/hashtag/", "/discover",
    "/music/", "/video/", "/@/", "/developer", "/policies", "/privacy",
    "/terms", "/2008/", "/2010/", "xmlns", "/tr?", "/plugins/", "/dialog/",
    "/sharer", "/login", "/signup", "/embed",
)

# Handles that belong to the platform, not to a business.
_RESERVED_HANDLES = {
    "explore", "accounts", "about", "p", "reel", "reels", "directory", "tv",
    "stories", "developers", "business", "help", "legal", "privacy", "terms",
    "policies", "pages", "groups", "events", "watch", "marketplace", "gaming",
    "sharer", "dialog", "plugins", "tr", "login", "signup", "home", "profile",
    "share", "search", "notes", "media", "photo", "story", "public", "people",
    "web", "l", "lm", "mbasic", "m", "www", "foundation", "creators", "ads",
    "instagram", "facebook", "tiktok", "meta", "graphapi", "connect",
}

_IG_PATTERN = re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?", re.I)
_TT_PATTERN = re.compile(r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{2,30})/?", re.I)
_FB_PATTERN = re.compile(
    r"https?://(?:www\.|web\.|m\.)?facebook\.com/([A-Za-z0-9_.\-]{2,60})/?", re.I
)

# Subdomains of a social platform that are never a business profile.
_BAD_SUBDOMAIN = re.compile(
    r"https?://(?:developers|business|about|help|l|lm|connect|graph|static|"
    r"scontent|external|api|partners|newsroom|investor|research|ai|opensource)\.",
    re.I,
)


def first_profile(html, network):
    """
    Pull the first real profile link for one network out of a blob of HTML.
    Give back None when nothing convincing is present.
    """
    patterns = {"instagram": _IG_PATTERN, "tiktok": _TT_PATTERN, "facebook": _FB_PATTERN}
    pattern = patterns[network]
    for match in pattern.finditer(html):
        full = match.group(0).rstrip("/\"',<>)")
        lowered = full.lower()
        if _BAD_SUBDOMAIN.match(lowered):
            continue
        if any(bad in lowered for bad in _NON_PROFILE_PATHS):
            continue
        handle = match.group(1).strip("/").lower()
        if handle in _RESERVED_HANDLES:
            continue
        # A pure number is a Facebook numeric page id and is acceptable, but a
        # bare year such as 2008 is namespace noise.
        if handle.isdigit() and len(handle) < 8:
            continue
        if network == "tiktok":
            return f"https://www.tiktok.com/@{match.group(1)}"
        return full
    return None


def extract_socials(html):
    """Give back a dict of the first real profile link for each network."""
    found = {}
    for network in ("instagram", "tiktok", "facebook"):
        link = first_profile(html, network)
        if link:
            found[network] = link
    return found


# ---------------------------------------------------------------------------
# Contact details on the page
# ---------------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)

# Addresses that belong to a platform or a tool, not to the business.
_EMAIL_NOISE = re.compile(
    r"(?:sentry|wixpress|example\.com|domain\.com|yourdomain|email\.com|"
    r"godaddy|squarespace|\.png|\.jpg|\.jpeg|\.gif|\.webp|\.svg|"
    r"no-?reply|sentry\.io|wordpress\.org|schema\.org)",
    re.I,
)


def find_emails(html, limit=3):
    """
    Give back up to `limit` plausible business email addresses from the page.
    This supports done-for-you outreach, which needs more than a phone number.
    """
    seen = []
    for match in _EMAIL_PATTERN.finditer(html):
        address = match.group(0).strip(".").lower()
        if _EMAIL_NOISE.search(address):
            continue
        if address in seen:
            continue
        seen.append(address)
        if len(seen) >= limit:
            break
    return seen


# ---------------------------------------------------------------------------
# The full page report
# ---------------------------------------------------------------------------

def analyze(html, final_url, load_seconds, slow_seconds=5.0):
    """Turn one page of HTML into the flat findings dict the scorer reads."""
    soup = BeautifulSoup(html, "html.parser")

    capture_methods = find_capture_methods(soup, html)
    ad_tags = find_ad_tags(html)
    analytics_tags = find_analytics_tags(html)
    socials = extract_socials(html)

    return {
        "capture_methods": capture_methods,
        "can_capture_lead": bool(capture_methods),
        # Only a real advertisement tag counts as spend.
        "ad_tags": ad_tags,
        "spends_on_ads": bool(ad_tags),
        # Analytics is kept for context. It never raises the tier.
        "analytics_tags": analytics_tags,
        "measures_only": bool(analytics_tags) and not ad_tags,
        "markets_on_social": bool(socials.get("instagram") or socials.get("tiktok")),
        "has_mobile_viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
        "is_https": bool(final_url and final_url.lower().startswith("https://")),
        "is_slow": load_seconds is not None and load_seconds > slow_seconds,
        "load_seconds": load_seconds,
        "emails": find_emails(html),
        "instagram": socials.get("instagram", ""),
        "facebook": socials.get("facebook", ""),
        "tiktok": socials.get("tiktok", ""),
    }
