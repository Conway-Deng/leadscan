"""Tests for detect.py -- the page reader. No browser, no network."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import detect  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402


def page(body, head=""):
    return f"<html><head>{head}</head><body>{body}</body></html>"


# ---------------------------------------------------------------------------
# Advertising infrastructure: distinct from analytics and live-campaign proof
# ---------------------------------------------------------------------------

def test_google_analytics_alone_is_not_advertising_infrastructure():
    """The old version called this an ad-spender. Almost every site has GA."""
    html = page("", head="<script src='https://www.googletagmanager.com/gtag/js?id=G-ABC1234567'></script>")
    assert detect.find_ad_tags(html) == []
    assert detect.find_analytics_tags(html)
    assert detect.analyze(html, "https://x.test", 1.0)["has_ad_tags"] is False


def test_google_ads_conversion_tag_is_advertising_infrastructure():
    """AW- is a Google Ads id. It looks like G- but is not analytics."""
    html = page("", head="<script src='https://www.googletagmanager.com/gtag/js?id=AW-123456789'></script>")
    assert "Google Ads tag" in detect.find_ad_tags(html)
    assert detect.analyze(html, "https://x.test", 1.0)["has_ad_tags"] is True


def test_meta_pixel_is_advertising_infrastructure():
    html = page("", head="<script>fbq('init', '1234567890');</script>")
    assert "Meta Pixel" in detect.find_ad_tags(html)


def test_tiktok_pixel_is_advertising_infrastructure():
    html = page("", head="<script>ttq.load('CABC123');</script>")
    assert "TikTok Pixel" in detect.find_ad_tags(html)


def test_gtm_alone_is_analytics_not_ads():
    html = page("", head="<script src='https://www.googletagmanager.com/gtm.js?id=GTM-XYZ'></script>")
    assert detect.find_ad_tags(html) == []
    assert "Google Tag Manager" in detect.find_analytics_tags(html)


def test_analyze_flags_measures_only():
    html = page("", head="<script>gtag('config','G-ABCDEF1234');</script>")
    findings = detect.analyze(html, "https://x.test", 1.0)
    assert findings["measures_only"] is True


# ---------------------------------------------------------------------------
# Lead capture
# ---------------------------------------------------------------------------

def test_the_word_consultation_is_not_lead_capture():
    """The old version matched any page containing the word 'consultation'."""
    html = page("<p>Book a free consultation with our designers today.</p>")
    soup = BeautifulSoup(html, "html.parser")
    assert detect.find_capture_methods(soup, html) == []


def test_contact_form_counts():
    html = page("<form action='/enquiry'><input name='email' type='email'>"
                "<textarea name='message'></textarea></form>")
    soup = BeautifulSoup(html, "html.parser")
    assert "contact form" in detect.find_capture_methods(soup, html)


def test_search_form_does_not_count():
    html = page("<form class='search-form'><input name='s' type='text'></form>")
    soup = BeautifulSoup(html, "html.parser")
    assert detect.find_capture_methods(soup, html) == []


def test_newsletter_form_does_not_count():
    html = page("<form id='newsletter-signup'><input name='email' type='email'></form>")
    soup = BeautifulSoup(html, "html.parser")
    assert detect.find_capture_methods(soup, html) == []


def test_whatsapp_link_counts():
    html = page("<a href='https://wa.me/6591234567'>Chat with us</a>")
    soup = BeautifulSoup(html, "html.parser")
    assert "WhatsApp link" in detect.find_capture_methods(soup, html)


def test_tel_link_counts():
    html = page("<a href='tel:+6561234567'>Call us</a>")
    soup = BeautifulSoup(html, "html.parser")
    assert "click-to-call" in detect.find_capture_methods(soup, html)


def test_calendly_counts_without_a_form_element():
    html = page("<div class='calendly-inline-widget' "
                "data-url='https://calendly.com/studio/intro'></div>")
    soup = BeautifulSoup(html, "html.parser")
    assert "Calendly" in detect.find_capture_methods(soup, html)


# ---------------------------------------------------------------------------
# Social profiles
# ---------------------------------------------------------------------------

def test_facebook_xml_namespace_is_rejected():
    """The known defect: an XML namespace was reported as a Facebook page."""
    html = ('<html xmlns:fb="http://www.facebook.com/2008/fbml">'
            '<body>Elm &amp; Line</body></html>')
    assert detect.first_profile(html, "facebook") is None


def test_facebook_page_starting_with_tr_is_kept():
    """The old noise list held '/tr', so any page name starting 'tr' was lost."""
    html = page("<a href='https://www.facebook.com/trendyrenovations'>us</a>")
    assert detect.first_profile(html, "facebook") == \
        "https://www.facebook.com/trendyrenovations"


def test_facebook_pixel_endpoint_is_rejected():
    html = page("<img src='https://www.facebook.com/tr?id=123&ev=PageView'>")
    assert detect.first_profile(html, "facebook") is None


def test_developer_subdomain_is_rejected():
    html = page("<a href='https://developers.facebook.com/docs'>docs</a>")
    assert detect.first_profile(html, "facebook") is None


def test_instagram_post_link_is_not_a_profile():
    html = page("<a href='https://www.instagram.com/p/CxYz123/'>a post</a>")
    assert detect.first_profile(html, "instagram") is None


def test_instagram_profile_is_found():
    html = page("<a href='https://www.instagram.com/studio.sg/'>follow</a>")
    assert detect.first_profile(html, "instagram") == \
        "https://www.instagram.com/studio.sg"


def test_protocol_relative_instagram_profile_is_normalised():
    html = page("<a href='//www.instagram.com/studio.sg/#bio'>follow</a>")
    assert detect.first_profile(html, "instagram") == \
        "https://www.instagram.com/studio.sg"


def test_non_http_profile_scheme_is_not_promoted_to_https():
    html = page("<a href='ftp://www.instagram.com/studio.sg/'>follow</a>")
    assert detect.first_profile(html, "instagram") is None


def test_tiktok_profile_is_normalised():
    html = page("<a href='https://www.tiktok.com/@renovate.sg?lang=en'>tiktok</a>")
    assert detect.first_profile(html, "tiktok") == "https://www.tiktok.com/@renovate.sg"


# ---------------------------------------------------------------------------
# Email extraction
# ---------------------------------------------------------------------------

def test_emails_are_found_and_noise_is_dropped():
    html = page("<a href='mailto:hello@studio.com.sg'>mail</a>"
                "<img src='logo@2x.png'>"
                "<script>Sentry.init({dsn:'https://abc@sentry.io/1'})</script>")
    found = detect.find_emails(html)
    assert "hello@studio.com.sg" in found
    assert not any("sentry" in address for address in found)


# ---------------------------------------------------------------------------
# The whole page report
# ---------------------------------------------------------------------------

def test_analyze_on_a_broken_funnel_site():
    html = page(
        "<p>Award-winning interior design. Call us for a consultation.</p>"
        "<a href='https://www.instagram.com/quietstudio.sg'>IG</a>",
        head="<script>fbq('init','999');</script>",
    )
    findings = detect.analyze(html, "http://quiet.test", 7.4, slow_seconds=5.0)
    assert findings["can_capture_lead"] is False
    assert findings["has_ad_tags"] is True
    assert findings["markets_on_social"] is True
    assert findings["has_mobile_viewport"] is False
    assert findings["is_https"] is False
    assert findings["is_slow"] is True
