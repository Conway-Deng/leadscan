"""
checks.py
---------
The audit of ONE business, from end to end.

    render the site  ->  detect what is on it  ->  score it against the ICP

The three parts live in separate files so that each one can be tested:
    browser.py  opens the page          (needs Playwright)
    detect.py   reads the page          (pure, testable)
    scoring.py  judges the business     (pure, testable)

This file only joins them together and builds the output row.
"""

import detect
import scoring
import config
import verify


def audit_business(browser, business, cache=None, deep=True):
    """
    Full pipeline for one business that has a website.

    `deep` follows the first contact link on the site. Most small firms keep
    the enquiry form on /contact and use the home page for pictures, so a scan
    of the home page alone reports "cannot capture a lead" for a firm that
    can. That false positive is worse than a missed lead, because the caller
    opens with a statement the prospect knows is wrong.
    """
    website = (business.get("website") or "").strip()

    findings, error, final_url = _render_and_detect(browser, website, cache)

    if deep and findings and not findings.get("can_capture_lead"):
        findings = _check_contact_pages(browser, findings, cache)

    # If Google lists an Instagram or TikTok page as the "website", treat it as
    # the social profile. This must happen BEFORE the social marketing test,
    # which is the fault in the earlier version: the patch ran after the test,
    # so a firm whose only web presence is Instagram was never counted as
    # marketing on social.
    if findings:
        lowered = website.lower()
        if "instagram.com" in lowered and not findings.get("instagram"):
            findings["instagram"] = website
        if "tiktok.com" in lowered and not findings.get("tiktok"):
            findings["tiktok"] = website
        findings["markets_on_social"] = bool(
            findings.get("instagram") or findings.get("tiktok")
        )

    # Best-effort follower count. Used only to skip an influencer-run firm.
    follower_count = None
    instagram = findings.get("instagram") if findings else ""
    if instagram:
        follower_count = _cached_followers(browser, instagram, cache)

    verdict = scoring.score_website_lead(
        findings or {}, business.get("review_count"), follower_count, error=error
    )
    return _row(business, findings, verdict, follower_count,
                status=error or "ok", final_url=final_url)


def audit_social_only(browser, business, cache=None, region=None):
    """
    Pipeline for a business with NO website. There is no site to render, so the
    Instagram or TikTok profile is found with a web search instead.
    """
    region = region or config.SOCIAL_SEARCH_REGION
    key = f"{business.get('name', '')}|{region}"
    socials = cache.get("social", key) if cache else None
    if socials is None:
        socials = browser.find_social(business.get("name", ""), region)
        if cache:
            cache.put("social", key, socials)

    follower_count = None
    if socials.get("instagram"):
        follower_count = _cached_followers(browser, socials["instagram"], cache)
    if follower_count is None and socials.get("tiktok"):
        follower_count = _cached_followers(browser, socials["tiktok"], cache)

    verdict = scoring.score_social_only_lead(
        socials, follower_count, business.get("review_count")
    )
    findings = {
        "instagram": socials.get("instagram", ""),
        "tiktok": socials.get("tiktok", ""),
        "facebook": "",
    }
    return _row(business, findings, verdict, follower_count,
                status="social-only", final_url="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_and_detect(browser, website, cache):
    """Give back (findings dict or None, error string or None, final url)."""
    if not website:
        return None, "no website", ""

    cached = cache.get("render", website) if cache else None
    if cached is not None:
        return cached.get("findings"), cached.get("error"), cached.get("final_url", "")

    html, final_url, load_seconds, error = browser.render(website)
    findings = None
    if not error:
        findings = detect.analyze(html, final_url, load_seconds, config.SLOW_SECONDS)

    if cache:
        cache.put("render", website,
                  {"findings": findings, "error": error, "final_url": final_url})
    return findings, error, final_url


def _check_contact_pages(browser, findings, cache, limit=2):
    """
    Follow the contact links found on the home page and fold in what they show.

    The search stops as soon as a capture method is found, because one is
    enough to prove the firm can take an enquiry.
    """
    for url in (findings.get("contact_links") or [])[:limit]:
        extra = cache.get("page", url) if cache else None
        if extra is None:
            html, _final_url, _load, error = browser.render(url)
            if error or not html:
                # Store the failure too, so a dead contact link is not fetched
                # again on the next run.
                if cache:
                    cache.put("page", url, {})
                continue
            # Only the part that can change the verdict is kept, not the page.
            extra = detect.read_second_page(html, url)
            if cache:
                cache.put("page", url, extra)
        if not extra:
            continue
        findings = detect.merge_second_page(findings, extra)
        if findings.get("can_capture_lead"):
            break
    return findings


def _cached_followers(browser, profile_url, cache):
    cached = cache.get("followers", profile_url) if cache else None
    if cached is not None:
        return cached.get("count")
    count = browser.followers(profile_url)
    if cache:
        cache.put("followers", profile_url, {"count": count})
    return count


def _row(business, findings, verdict, follower_count, status, final_url):
    findings = findings or {}
    # Grade the address before it reaches the caller. A shared inbox is still
    # useful; a template placeholder is not.
    best_email = verify.best(findings.get("emails"))
    return {
        # The full findings ride along so a prospect report can be built later
        # without opening the website again. report.py ignores this key.
        "_findings": findings,
        "score": verdict["score"],
        "tier": verdict["tier"],
        "warm": verdict["warm"],
        "disqualified": verdict["disqualified"],
        "name": business.get("name", ""),
        "phone": business.get("phone", ""),
        "address": business.get("address", ""),
        "review_count": business.get("review_count"),
        "rating": business.get("rating"),
        "opening_hours": " | ".join(business.get("opening_hours") or []),
        "instagram_followers": follower_count,
        "instagram": findings.get("instagram", ""),
        "facebook": findings.get("facebook", ""),
        "tiktok": findings.get("tiktok", ""),
        "email": best_email.get("address", "") if best_email else "",
        "email_grade": best_email.get("grade", "") if best_email else "",
        "ad_tags": ", ".join(findings.get("ad_tags", [])),
        "capture_methods": ", ".join(findings.get("capture_methods", [])),
        "load_seconds": findings.get("load_seconds"),
        "hook": verdict["hook"],
        "reasons": "; ".join(verdict.get("reasons", [])),
        "website": business.get("website", ""),
        "final_url": final_url,
        "status": status,
    }


# Kept so an older script that imports these names still works.
Browser = None      # replaced below at import time
analyze = detect.analyze


def _late_import():
    """Import the browser only when Playwright is really needed."""
    global Browser
    from browser import Browser as _Browser
    Browser = _Browser
    return _Browser
