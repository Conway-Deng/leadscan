"""
adlibrary.py
------------
Optional check against the Meta Ad Library API (`ads_archive`).

READ THIS BEFORE YOU SPEND A DAY ON IT
-------------------------------------
The handoff note plans a "Meta Ad Library gate" as the real proof that a firm
has a LIVE advertisement, and estimates about one day of work for the Meta app
and the identity check.

That day will not produce the result you want in Singapore.

The `ads_archive` endpoint returns COMMERCIAL advertisements only when
`ad_reached_countries` names an EU member state or the United Kingdom. That
coverage exists because the EU Digital Services Act forces Meta to archive
every advertisement shown to a person in the EU. Everywhere else, including
Singapore, the endpoint returns advertisements about politics and social issues
only. A Singapore aesthetic clinic that runs Instagram advertisements to a
Singapore audience does not appear in the API at all.

So:

  * For an EU or UK sweep, this module works and gives real proof of a live
    advertisement. Use it.
  * For a Singapore sweep, the API cannot answer the question. The public Ad
    Library web page can, but reading it with a script breaks the Meta terms of
    service, and the risk falls on your account, so this module does not do it.

WHAT TO USE INSTEAD FOR SINGAPORE
The advertisement tag test in `detect.py` is the practical substitute. It was
made much stricter in this version: a Google Ads conversion identifier (AW-),
a Meta Pixel init call, a TikTok pixel, a LinkedIn insight tag and similar all
count, while Google Analytics on its own no longer counts. A site with a Meta
Pixel and a Google Ads tag but no booking form is a strong lead, and you can
say what you saw without any guesswork.

Enable this module with META_AD_LIBRARY_TOKEN in .env.
"""

import os

import requests

API_URL = "https://graph.facebook.com/v21.0/ads_archive"

# Countries where the API returns commercial advertisements.
COMMERCIAL_COVERAGE = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "GB",
}


def coverage_warning(country_code):
    """Give back a warning string, or None when the country is covered."""
    if country_code.upper() in COMMERCIAL_COVERAGE:
        return None
    return (
        f"The Meta Ad Library API returns commercial ads for EU and UK "
        f"audiences only. For {country_code.upper()} it returns political and "
        f"social-issue ads only, so a normal business will never be found. "
        f"Rely on the ad-tag test in detect.py instead."
    )


def is_enabled():
    return bool(os.getenv("META_AD_LIBRARY_TOKEN"))


def has_live_ads(page_name, country_code="SG", token=None, limit=5, session=None):
    """
    Ask the Ad Library whether a page runs an active advertisement.

    Give back a dict:
        {"checked": bool, "live_ads": bool, "count": int, "note": str}

    `checked` is False when the token is missing or the country is not covered.
    A False `checked` value must never be read as "they run no ads".
    """
    token = token or os.getenv("META_AD_LIBRARY_TOKEN")
    if not token:
        return {"checked": False, "live_ads": False, "count": 0,
                "note": "no META_AD_LIBRARY_TOKEN set"}

    warning = coverage_warning(country_code)
    if warning:
        return {"checked": False, "live_ads": False, "count": 0, "note": warning}

    caller = session or requests
    try:
        response = caller.get(
            API_URL,
            params={
                "access_token": token,
                "search_terms": page_name,
                "ad_reached_countries": f'["{country_code.upper()}"]',
                "ad_active_status": "ACTIVE",
                "ad_type": "ALL",
                "limit": limit,
                "fields": "id,page_name,ad_delivery_start_time",
            },
            timeout=20,
        )
        if response.status_code >= 400:
            return {"checked": False, "live_ads": False, "count": 0,
                    "note": f"API error {response.status_code}: {response.text[:160]}"}
        data = response.json().get("data", [])
    except Exception as error:  # network, JSON, anything
        return {"checked": False, "live_ads": False, "count": 0,
                "note": f"request failed: {str(error)[:120]}"}

    # The search is by keyword, so confirm the page name really matches.
    wanted = _simplify(page_name)
    matched = [ad for ad in data if wanted and wanted in _simplify(ad.get("page_name", ""))]
    return {
        "checked": True,
        "live_ads": bool(matched),
        "count": len(matched),
        "note": f"{len(matched)} active ad(s) found" if matched else "no active ads found",
    }


def _simplify(text):
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())
