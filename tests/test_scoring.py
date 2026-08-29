"""Tests for scoring.py -- the ICP judge. Pure, so no browser is needed."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import scoring  # noqa: E402


def findings(**overrides):
    base = {
        "can_capture_lead": True,
        "has_mobile_viewport": True,
        "is_https": True,
        "is_slow": False,
        "load_seconds": 1.0,
        "has_ad_tags": False,
        "ad_tags": [],
        "analytics_tags": [],
        "measures_only": False,
        "markets_on_social": False,
        "instagram": "",
        "tiktok": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The honesty rule
# ---------------------------------------------------------------------------

def test_no_ad_tag_means_no_claim_about_ads():
    """The old hook told every firm 'You may be spending on ads'. That is a guess."""
    result = scoring.score_website_lead(findings(can_capture_lead=False), 12)
    assert "ads" not in result["hook"].lower()
    assert "spending" not in result["hook"].lower()
    assert result["tier"] == scoring.TIER_COOL


def test_installed_ad_tag_names_exactly_what_was_seen():
    result = scoring.score_website_lead(
        findings(can_capture_lead=False, has_ad_tags=True, ad_tags=["Meta Pixel"]), 12
    )
    assert "Meta Pixel" in result["hook"]
    assert "installed" in result["hook"]
    assert "running paid ads" not in result["hook"].lower()
    assert result["tier"] == scoring.TIER_HOT


def test_analytics_only_is_recorded_but_never_makes_a_lead_hot():
    result = scoring.score_website_lead(
        findings(can_capture_lead=False, measures_only=True,
                 analytics_tags=["Google Analytics 4"]), 12
    )
    assert result["tier"] == scoring.TIER_COOL
    assert any("analytics only" in reason for reason in result["reasons"])


def test_social_marketing_makes_a_lead_warm_not_hot():
    result = scoring.score_website_lead(
        findings(can_capture_lead=False, markets_on_social=True,
                 instagram="https://www.instagram.com/studio.sg"), 12
    )
    assert result["tier"] == scoring.TIER_WARM
    assert "Instagram" in result["hook"]


# ---------------------------------------------------------------------------
# Disqualifiers and edge cases
# ---------------------------------------------------------------------------

def test_influencer_is_disqualified():
    result = scoring.score_website_lead(findings(can_capture_lead=False), 12,
                                        follower_count=config.INFLUENCER_FOLLOWERS)
    assert result["disqualified"] is True
    assert result["warm"] is False


def test_established_firm_with_a_site_error_is_not_a_lead():
    result = scoring.score_website_lead({}, config.ESTABLISHED_REVIEWS + 1,
                                        error="timeout after 20s")
    assert result["warm"] is False
    assert "temporary fault" in result["hook"]


def test_small_firm_with_a_site_error_is_a_lead():
    result = scoring.score_website_lead({}, 8, error="http 503")
    assert result["warm"] is True
    assert result["score"] == 60


def test_no_website_never_claims_ad_spend():
    result = scoring.score_website_lead({}, 5, error="no website")
    assert result["warm"] is True
    assert "ad" not in result["hook"].lower().replace("head", "")


def test_solid_funnel_is_not_a_lead():
    result = scoring.score_website_lead(
        findings(has_ad_tags=True, ad_tags=["Meta Pixel"]), 10
    )
    assert result["warm"] is False
    assert result["tier"] == ""
    assert "Lower priority" in result["hook"]


def test_unknown_review_count_does_not_crash_or_award_points():
    result = scoring.score_website_lead(findings(can_capture_lead=False), None)
    assert result["score"] == 40


def test_established_firm_loses_points():
    quiet = scoring.score_website_lead(findings(can_capture_lead=False), 5)
    busy = scoring.score_website_lead(findings(can_capture_lead=False), 400)
    assert quiet["score"] > busy["score"]


def test_score_is_capped_between_zero_and_one_hundred():
    result = scoring.score_website_lead(
        findings(can_capture_lead=False, has_mobile_viewport=False,
                 is_https=False, is_slow=True, load_seconds=12.0,
                 has_ad_tags=True, ad_tags=["Meta Pixel"]), 1
    )
    assert 0 <= result["score"] <= 100


# ---------------------------------------------------------------------------
# The no-website tier
# ---------------------------------------------------------------------------

def test_social_only_sweet_spot():
    result = scoring.score_social_only_lead(
        {"instagram": "https://www.instagram.com/tiny.sg"}, 800, 11
    )
    assert result["warm"] is True
    assert result["tier"] == scoring.TIER_WARM
    assert result["score"] == 80


def test_social_only_too_big_is_skipped():
    result = scoring.score_social_only_lead(
        {"instagram": "https://www.instagram.com/big.sg"},
        config.SOCIAL_ONLY_MAX_FOLLOWERS + 1, 11
    )
    assert result["disqualified"] is True


def test_social_only_with_nothing_found():
    result = scoring.score_social_only_lead({}, None, 4)
    assert result["warm"] is True
    assert "invisible" in result["hook"]


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def test_hot_sorts_above_a_higher_scoring_warm():
    rows = [
        {"tier": scoring.TIER_WARM, "score": 95, "name": "warm"},
        {"tier": scoring.TIER_HOT, "score": 55, "name": "hot"},
        {"tier": scoring.TIER_COOL, "score": 99, "name": "cool"},
    ]
    rows.sort(key=scoring.sort_key)
    assert [r["name"] for r in rows] == ["hot", "warm", "cool"]


# ---------------------------------------------------------------------------
# The opening line must make sense
# ---------------------------------------------------------------------------

def test_the_consequence_matches_the_defect_that_is_named():
    """'No HTTPS padlock, so nobody can get in touch' is not a sentence a
    prospect accepts. Each defect carries its own consequence."""
    result = scoring.score_website_lead(findings(is_https=False), 12)
    assert "HTTPS padlock" in result["hook"]
    assert "get in touch" not in result["hook"]
    assert "warns your visitors" in result["hook"]


def test_the_capture_defect_uses_the_channel_consequence():
    result = scoring.score_website_lead(
        findings(can_capture_lead=False, has_ad_tags=True, ad_tags=["Meta Pixel"]), 12
    )
    assert "people who arrive on the page have no clear next step" in result["hook"]


def test_a_slow_page_says_so():
    result = scoring.score_website_lead(findings(is_slow=True, load_seconds=8.1), 12)
    assert "8.1s" in result["hook"]
    assert "leave before they see anything" in result["hook"]


def test_the_hook_holds_no_command_line_instructions():
    """The hook is read aloud on a call. It must not contain a CLI flag."""
    for result in (
        scoring.score_website_lead({}, 5, error="no website"),
        scoring.score_website_lead(findings(can_capture_lead=False), 5),
        scoring.score_social_only_lead({}, None, 5),
    ):
        assert "--" not in result["hook"]
