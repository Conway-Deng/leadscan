"""Regression tests for evidence-accurate advertising-tag wording."""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_report  # noqa: E402
import checks  # noqa: E402
import detect  # noqa: E402
import outreach  # noqa: E402
import report  # noqa: E402
import scoring  # noqa: E402


BANNED_CLAIMS = (
    "running paid ads",
    "currently paying for traffic",
    "already paying",
    "paid traffic",
    "traffic you pay for",
    "losing money every day",
    "costing you money every day",
)


def assert_no_live_campaign_claim(text):
    lowered = text.lower()
    for claim in BANNED_CLAIMS:
        assert claim not in lowered


def page(head=""):
    return ("<html><head>" + head + "<meta name='viewport' content='width=device-width'>"
            "</head><body><p>Studio</p></body></html>")


def scored_tag(html, expected_tag):
    findings = detect.analyze(html, "https://studio.test", 1.0)
    assert findings["has_ad_tags"] is True
    assert expected_tag in findings["ad_tags"]
    result = scoring.score_website_lead(findings, 12)
    assert expected_tag in result["hook"]
    assert "installed" in result["hook"]
    assert_no_live_campaign_claim(result["hook"])


def test_google_ads_tag_confirms_only_installed_infrastructure():
    scored_tag(page("<script>gtag('config', 'AW-123456789')</script>"),
               "Google Ads tag")


def test_meta_pixel_confirms_only_installed_infrastructure():
    scored_tag(page("<script>fbq('init', '1234567890')</script>"), "Meta Pixel")


def test_tiktok_pixel_confirms_only_installed_infrastructure():
    scored_tag(page("<script>ttq.load('CABC123')</script>"), "TikTok Pixel")


def test_analytics_only_page_produces_no_advertising_claim():
    findings = detect.analyze(
        page("<script>gtag('config', 'G-ABCDEF1234')</script>"),
        "https://studio.test", 1.0)
    assert findings["has_ad_tags"] is False
    assert findings["measures_only"] is True
    result = scoring.score_website_lead(findings, 12)
    assert result["tier"] == scoring.TIER_COOL
    assert "advertis" not in result["hook"].lower()
    assert_no_live_campaign_claim(result["hook"])


def test_page_without_ad_tag_produces_no_advertising_claim():
    findings = detect.analyze(page(), "https://studio.test", 1.0)
    assert findings["has_ad_tags"] is False
    result = scoring.score_website_lead(findings, 12)
    assert "advertis" not in result["hook"].lower()
    assert_no_live_campaign_claim(result["hook"])


def test_legacy_cached_tag_findings_score_like_fresh_findings():
    fresh = detect.analyze(
        page("<script>fbq('init', '1234567890')</script>"),
        "https://studio.test", 1.0)
    legacy = dict(fresh)
    legacy["spends_on_ads"] = legacy.pop("has_ad_tags")

    class LegacyRenderCache:
        def get(self, namespace, key):
            assert namespace == "render"
            assert key == "https://studio.test"
            return {"findings": legacy, "error": None,
                    "final_url": "https://studio.test"}

    class BrowserMustNotRun:
        def render(self, _url):
            raise AssertionError("a cache hit must not render the site")

    cached, error, final_url = checks._render_and_detect(
        BrowserMustNotRun(), "https://studio.test", LegacyRenderCache())
    assert error is None
    assert final_url == "https://studio.test"
    assert cached["has_ad_tags"] is True
    assert "has_ad_tags" not in legacy

    cached_result = scoring.score_website_lead(cached, 12)
    fresh_result = scoring.score_website_lead(fresh, 12)
    assert fresh_result["score"] == 75
    assert fresh_result["tier"] == scoring.TIER_HOT
    assert fresh_result["warm"] is True
    fields = ("score", "tier", "warm", "hook")
    assert {key: cached_result[key] for key in fields} == {
        key: fresh_result[key] for key in fields
    }
    assert "Meta Pixel is installed" in cached_result["hook"]
    assert_no_live_campaign_claim(cached_result["hook"])

    unsupported_claim_only = checks._normalise_cached_render_findings(
        {"spends_on_ads": True, "ad_tags": []})
    assert unsupported_claim_only["has_ad_tags"] is False


def test_every_generated_surface_uses_evidence_accurate_tag_wording(tmp_path):
    findings = detect.analyze(
        page("<script>fbq('init', '1234567890')</script>"),
        "https://studio.test", 1.0)
    verdict = scoring.score_website_lead(findings, 12)
    row = {
        "name": "Studio", "phone": "+65 6111 1111", "website": "https://studio.test",
        "email": "hello@studio.test", "review_count": 12, "rating": 4.5,
        "instagram_followers": None, "email_grade": "personal",
        "ad_tags": ", ".join(findings["ad_tags"]), "capture_methods": "",
        "hook": verdict["hook"], "tier": verdict["tier"], "score": verdict["score"],
        "warm": verdict["warm"], "disqualified": False, "instagram": "",
        "facebook": "", "tiktok": "", "address": "Singapore",
        "opening_hours": "", "reasons": "; ".join(verdict["reasons"]),
        "status": "ok", "_findings": findings,
    }
    outreach.add_drafts([row])

    call_sheet_path = tmp_path / "call-sheet.html"
    report.write_html([row], str(call_sheet_path))
    prospect_report = audit_report.build(row, findings)

    crm_path = tmp_path / "crm.csv"
    outreach.write_crm_csv([row], str(crm_path), only_with_email=False)
    with open(crm_path, newline="", encoding="utf-8-sig") as handle:
        crm_row = next(csv.DictReader(handle))

    surfaces = (
        row["hook"], row["whatsapp_message"], row["email_subject"],
        row["email_body"], call_sheet_path.read_text(encoding="utf-8"),
        prospect_report, crm_row["custom_observation"],
        crm_row["custom_consequence"],
    )
    for text in surfaces:
        assert_no_live_campaign_claim(text)

    for text in (row["hook"], row["whatsapp_message"], row["email_body"],
                 call_sheet_path.read_text(encoding="utf-8"), prospect_report,
                 crm_row["custom_observation"]):
        assert "Meta Pixel" in text
