"""
Tests for the second-page check, parked-site detection, the journal and the
exclusion list. These are the parts added after the first hardening pass.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

import detect  # noqa: E402
import leadscan  # noqa: E402
import runner  # noqa: E402
import scoring  # noqa: E402


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# Finding the contact page
# ---------------------------------------------------------------------------

def test_the_contact_link_is_found():
    html = ("<a href='/about'>About</a>"
            "<a href='/contact'>Contact us</a>"
            "<a href='/blog'>Blog</a>")
    found = detect.find_contact_links(soup_of(html), "https://studio.sg/")
    assert found == ["https://studio.sg/contact"]


def test_a_booking_link_counts_as_a_contact_link():
    html = "<a href='/book-a-consultation'>Book a consultation</a>"
    found = detect.find_contact_links(soup_of(html), "https://studio.sg/")
    assert found == ["https://studio.sg/book-a-consultation"]


def test_the_link_text_is_used_when_the_path_says_nothing():
    html = "<a href='/hubungi-kami'>Get in touch</a>"
    found = detect.find_contact_links(soup_of(html), "https://studio.sg/")
    assert found == ["https://studio.sg/hubungi-kami"]


def test_the_privacy_page_is_not_a_contact_page():
    """It holds the word 'contact', but there is no enquiry form on it."""
    html = "<a href='/privacy-policy'>Contact the DPO about privacy</a>"
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == []


def test_an_external_link_is_not_followed():
    """A Fresha booking link is already a capture method. Do not leave the site."""
    html = "<a href='https://fresha.com/studio'>Book now</a>"
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == []


def test_a_shallow_path_beats_a_deep_one():
    html = ("<a href='/blog/2024/how-to-contact-us'>contact</a>"
            "<a href='/contact'>contact</a>")
    found = detect.find_contact_links(soup_of(html), "https://studio.sg/")
    assert found[0] == "https://studio.sg/contact"


def test_the_home_page_is_never_returned_as_its_own_contact_page():
    html = "<a href='/'>Contact</a>"
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == []


def test_mailto_and_tel_links_are_not_pages_to_fetch():
    html = "<a href='mailto:a@b.sg'>Contact</a><a href='tel:+6561234567'>Contact</a>"
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == []


# ---------------------------------------------------------------------------
# Merging a second page
# ---------------------------------------------------------------------------

def home_findings(**overrides):
    base = detect.analyze("<html><body><p>hello</p></body></html>",
                          "https://studio.sg/", 1.0)
    base.update(overrides)
    return base


def test_a_form_on_the_contact_page_clears_the_biggest_pain():
    """This is the false positive the whole feature exists to remove."""
    home = home_findings()
    assert home["can_capture_lead"] is False

    contact = detect.read_second_page(
        "<form action='/enquiry'><input type='email' name='email'></form>",
        "https://studio.sg/contact")
    merged = detect.merge_second_page(home, contact)

    assert merged["can_capture_lead"] is True
    assert any("/contact" in method for method in merged["capture_methods"])


def test_a_second_page_can_only_add_evidence():
    """A contact page without a pixel does not mean the home page had none."""
    home = home_findings(ad_tags=["Meta Pixel"], has_ad_tags=True)
    merged = detect.merge_second_page(
        home, detect.read_second_page("<p>nothing here</p>", "https://studio.sg/contact"))
    assert merged["has_ad_tags"] is True
    assert merged["ad_tags"] == ["Meta Pixel"]


def test_speed_and_https_stay_as_measured_on_the_home_page():
    """The home page is the landing page, so that page is judged."""
    home = home_findings(is_slow=True, load_seconds=9.0, is_https=False)
    merged = detect.merge_second_page(
        home, detect.read_second_page("<p>fast</p>", "https://studio.sg/contact"))
    assert merged["is_slow"] is True
    assert merged["load_seconds"] == 9.0
    assert merged["is_https"] is False


def test_an_email_on_the_contact_page_is_picked_up():
    home = home_findings()
    contact = detect.read_second_page(
        "<a href='mailto:hello@studio.com.sg'>mail us</a>", "https://studio.sg/contact")
    merged = detect.merge_second_page(home, contact)
    assert "hello@studio.com.sg" in merged["emails"]


def test_merging_nothing_changes_nothing():
    home = home_findings()
    assert detect.merge_second_page(home, None) == home
    assert detect.merge_second_page(home, {}) == home


# ---------------------------------------------------------------------------
# Parked and unpublished sites
# ---------------------------------------------------------------------------

def test_a_domain_for_sale_page_is_reported_as_parked():
    html = "<html><body><h1>coolbreeze.sg</h1><p>This domain is for sale.</p></body></html>"
    assert detect.find_parked_markers(soup_of(html), html)
    assert detect.analyze(html, "https://coolbreeze.sg", 1.0)["is_parked"] is True


def test_a_coming_soon_page_is_reported_as_parked():
    html = "<html><body><p>Website coming soon</p></body></html>"
    assert detect.find_parked_markers(soup_of(html), html)


def test_a_real_site_is_not_reported_as_parked():
    html = ("<html><body><h1>Quiet Interiors</h1>"
            "<p>We design HDB and condo homes across Singapore.</p>"
            "<a href='/contact'>Contact</a></body></html>")
    assert detect.find_parked_markers(soup_of(html), html) == []


def test_a_script_mentioning_a_marker_does_not_condemn_the_site():
    """Only the visible text is read, never a script or a comment."""
    html = ("<html><body><h1>Quiet Interiors</h1>"
            "<script>var msg = 'this domain is for sale';</script>"
            "<p>We design homes.</p></body></html>")
    assert detect.find_parked_markers(soup_of(html), html) == []


def test_a_parked_site_gets_its_own_opening_line():
    findings = detect.analyze(
        "<html><body><p>This domain is for sale</p></body></html>",
        "https://coolbreeze.sg", 1.0)
    result = scoring.score_website_lead(findings, 11)
    assert result["warm"] is True
    assert "placeholder page" in result["hook"]
    # It must not claim a broken funnel, and it must not claim ad spend.
    assert "no form" not in result["hook"]
    assert "paid ads" not in result["hook"]


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------

def test_the_journal_records_and_replays(tmp_path):
    path = str(tmp_path / "run.jsonl")
    journal = runner.Journal(path)
    assert journal.done_keys() == {}
    journal.append("pid:A", {"name": "Alpha", "score": 80})
    journal.append("pid:B", {"name": "Beta", "score": 40})
    replayed = journal.done_keys()
    assert set(replayed) == {"pid:A", "pid:B"}
    assert replayed["pid:A"]["name"] == "Alpha"


def test_a_half_written_last_line_is_ignored(tmp_path):
    """A run killed mid-write must not stop the next run from starting."""
    path = tmp_path / "run.jsonl"
    path.write_text(json.dumps({"_key": "pid:A", "name": "Alpha"}) + "\n"
                    + '{"_key": "pid:B", "na', encoding="utf-8")
    assert set(runner.Journal(str(path)).done_keys()) == {"pid:A"}


def test_a_journal_with_no_path_is_harmless():
    journal = runner.Journal(None)
    journal.append("pid:A", {"name": "Alpha"})
    assert journal.done_keys() == {}


def test_the_business_key_prefers_the_place_id():
    assert runner.business_key({"place_id": "ChIJ1", "name": "A"}) == "pid:ChIJ1"
    assert "Alpha" in runner.business_key({"name": "Alpha", "website": "https://a.sg"}).title()


def test_run_audits_replays_the_journal_without_a_browser(tmp_path):
    """Every firm is already recorded, so no browser is ever started."""
    path = str(tmp_path / "run.jsonl")
    runner.Journal(path).append("pid:A", {"name": "Alpha", "score": 70,
                                          "warm": True, "disqualified": False,
                                          "tier": "hot"})
    rows = runner.run_audits([{"place_id": "A", "name": "Alpha"}],
                             log=lambda m: None, journal_path=path)
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"
    assert "_key" not in rows[0]


# ---------------------------------------------------------------------------
# The exclusion list
# ---------------------------------------------------------------------------

def test_an_already_called_firm_is_dropped(tmp_path):
    path = tmp_path / "called.csv"
    path.write_text("name,website,phone\nFt2 (Ubi),https://ft2.com.sg,+65 6123 4567\n",
                    encoding="utf-8")
    businesses = [
        # Same company, different branch name and phone format.
        {"name": "Ft2 (Woodlands)", "website": "", "phone": "6123 4567", "place_id": "Z"},
        {"name": "Other Studio", "website": "https://other.sg", "phone": "+65 6999 9999",
         "place_id": "Y"},
    ]
    kept, dropped = leadscan._apply_exclusions(businesses, str(path))
    assert dropped == 1
    assert [b["name"] for b in kept] == ["Other Studio"]


def test_an_empty_exclusion_file_drops_nothing(tmp_path):
    path = tmp_path / "called.csv"
    path.write_text("name,website,phone\n", encoding="utf-8")
    businesses = [{"name": "Alpha", "website": "https://a.sg", "phone": "", "place_id": "A"}]
    kept, dropped = leadscan._apply_exclusions(businesses, str(path))
    assert dropped == 0
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def _fake_requests(monkeypatch, text, status=200):
    import robots as robots_module
    robots_module.clear()
    import requests

    def fake_get(url, **kwargs):
        return _FakeResponse(text, status)

    monkeypatch.setattr(requests, "get", fake_get)
    return robots_module


def test_a_disallowed_path_is_refused(monkeypatch):
    module = _fake_requests(monkeypatch, "User-agent: *\nDisallow: /\n")
    assert module.may_fetch("https://blocked.sg/") is False


def test_an_allowed_path_is_permitted(monkeypatch):
    module = _fake_requests(monkeypatch, "User-agent: *\nDisallow: /admin\n")
    assert module.may_fetch("https://open.sg/") is True
    assert module.may_fetch("https://open.sg/admin/x") is False


def test_a_missing_robots_file_means_yes(monkeypatch):
    """Silence is permission. That is what the standard says."""
    module = _fake_requests(monkeypatch, "", status=404)
    assert module.may_fetch("https://nofile.sg/") is True


def test_a_broken_robots_file_means_yes(monkeypatch):
    module = _fake_requests(monkeypatch, "\x00\x01 not a robots file")
    assert module.may_fetch("https://junk.sg/") is True


def test_the_file_is_read_once_per_host(monkeypatch):
    import robots as robots_module
    import requests
    robots_module.clear()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse("User-agent: *\nDisallow: /admin\n")

    monkeypatch.setattr(requests, "get", fake_get)
    robots_module.may_fetch("https://once.sg/a")
    robots_module.may_fetch("https://once.sg/b")
    robots_module.may_fetch("https://once.sg/c")
    assert len(calls) == 1


def test_a_blocked_site_is_never_scored_on_evidence_it_did_not_give():
    result = scoring.score_website_lead({}, 12, error="blocked by robots.txt")
    assert result["warm"] is False
    assert result["score"] == 0
    assert "did not look" in result["hook"]
    # It must not claim a broken funnel or ad spend from an unread page.
    assert "no form" not in result["hook"]
    assert "paid ads" not in result["hook"]
