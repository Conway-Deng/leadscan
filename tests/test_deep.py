"""
Tests for the second-page check, parked-site detection, the journal and the
exclusion list. These are the parts added after the first hardening pass.
"""

import json
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

import detect  # noqa: E402
import compatibility  # noqa: E402
import config  # noqa: E402
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


def test_contact_links_normalise_protocol_case_and_fragments():
    html = ("<a href='//studio.sg/contact#form'>Contact</a>"
            "<a href='HTTPS://studio.sg/contact#map'>Contact map</a>")
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == [
        "https://studio.sg/contact"]


def test_non_http_and_malformed_contact_links_are_never_fetched():
    html = ("<a href='javascript:openContact()'>Contact</a>"
            "<a href='data:text/html,contact'>Contact</a>"
            "<a href='file:///contact'>Contact</a>"
            "<a href='http://[broken/contact'>Contact</a>")
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == []


def test_bare_and_www_hosts_are_equivalent_only_for_local_contact_pages():
    html = ("<a href='https://www.studio.sg/contact'>Contact</a>"
            "<a href='https://unrelated.sg/contact'>External contact</a>")
    assert detect.find_contact_links(soup_of(html), "https://studio.sg/") == [
        "https://www.studio.sg/contact"]


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

def _journal_identity(business):
    return (runner.business_key(business),
            compatibility.business_fingerprint(business))


def _install_fake_audit(monkeypatch, audited):
    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    browser_module = types.ModuleType("browser")
    browser_module.Browser = FakeBrowser
    monkeypatch.setitem(sys.modules, "browser", browser_module)

    def fake_audit(_browser, business, cache=None, deep=True, deadline=None):
        audited.append(business.copy())
        return {"name": business["name"], "score": 70, "tier": "hot",
                "warm": True, "disqualified": False}

    monkeypatch.setattr(runner.checks, "audit_business", fake_audit)


def _install_browser(monkeypatch, browser_class):
    browser_module = types.ModuleType("browser")
    browser_module.Browser = browser_class
    monkeypatch.setitem(sys.modules, "browser", browser_module)


def test_current_version_journal_records_and_replays(tmp_path):
    path = str(tmp_path / "run.jsonl")
    alpha = {"place_id": "A", "name": "Alpha"}
    beta = {"place_id": "B", "name": "Beta"}
    journal = runner.Journal(path)
    assert journal.done_keys() == {}
    journal.append("pid:A", {"name": "Alpha", "score": 80}, business=alpha)
    journal.append("pid:B", {"name": "Beta", "score": 40}, business=beta)
    replayed = journal.done_keys()
    assert set(replayed) == {_journal_identity(alpha), _journal_identity(beta)}
    assert replayed[_journal_identity(alpha)]["name"] == "Alpha"
    assert (replayed[_journal_identity(alpha)]["_pipeline_version"]
            == config.PIPELINE_SCHEMA_VERSION)


def test_a_half_written_last_line_is_ignored(tmp_path):
    """A run killed mid-write must not stop the next run from starting."""
    path = tmp_path / "run.jsonl"
    business = {"place_id": "A", "name": "Alpha"}
    valid = {"_key": "pid:A", "name": "Alpha",
             "_pipeline_version": config.PIPELINE_SCHEMA_VERSION,
             "_run_fingerprint": compatibility.run_fingerprint(),
             "_business_fingerprint": compatibility.business_fingerprint(business)}
    path.write_text("not json\n" + json.dumps(valid) + "\n"
                    + '{"_key": "pid:B", "na', encoding="utf-8")
    assert set(runner.Journal(str(path)).done_keys()) == {_journal_identity(business)}


def test_a_journal_with_no_path_is_harmless():
    journal = runner.Journal(None)
    journal.append("pid:A", {"name": "Alpha"})
    assert journal.done_keys() == {}


def test_the_business_key_prefers_the_place_id():
    assert runner.business_key({"place_id": "ChIJ1", "name": "A"}) == "pid:ChIJ1"
    assert "Alpha" in runner.business_key({"name": "Alpha", "website": "https://a.sg"}).title()


def test_run_audits_replays_current_journal_without_a_browser(tmp_path):
    """Every firm is already recorded, so no browser is ever started."""
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha"}
    runner.Journal(path).append("pid:A", {"name": "Alpha", "score": 70,
                                          "warm": True, "disqualified": False,
                                          "tier": "hot"}, business=business)
    rows = runner.run_audits([business],
                             log=lambda m: None, journal_path=path)
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"
    internal = {"_key", "_pipeline_version", "_run_fingerprint",
                "_business_fingerprint"}
    assert internal.isdisjoint(rows[0])


def test_a_scoring_threshold_change_invalidates_journal_replay(tmp_path,
                                                               monkeypatch):
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha", "review_count": 12}
    runner.Journal(path).append("pid:A", {"name": "Alpha"}, business=business)
    monkeypatch.setattr(config, "QUIET_REVIEWS", config.QUIET_REVIEWS + 1)
    assert runner.Journal(path).done_keys() == {}


def test_deep_and_shallow_runs_do_not_share_journal_results(tmp_path):
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha"}
    deep = compatibility.run_fingerprint(deep=True)
    runner.Journal(path, deep).append("pid:A", {"name": "Alpha"},
                                      business=business)
    shallow = compatibility.run_fingerprint(deep=False)
    assert runner.Journal(path, shallow).done_keys() == {}


def test_robots_policies_do_not_share_journal_results(tmp_path):
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha"}
    obey = compatibility.run_fingerprint(respect_robots=True)
    runner.Journal(path, obey).append("pid:A", {"name": "Alpha"},
                                      business=business)
    ignore = compatibility.run_fingerprint(respect_robots=False)
    assert runner.Journal(path, ignore).done_keys() == {}


def test_social_and_website_runs_do_not_share_journal_results(tmp_path):
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha"}
    website = compatibility.run_fingerprint(social_only=False)
    runner.Journal(path, website).append("pid:A", {"name": "Alpha"},
                                         business=business)
    social = compatibility.run_fingerprint(social_only=True)
    assert runner.Journal(path, social).done_keys() == {}


def test_changed_business_input_is_reaudited_for_the_same_place_id(tmp_path,
                                                                  monkeypatch):
    path = str(tmp_path / "run.jsonl")
    old = {"place_id": "A", "name": "Alpha", "review_count": 12,
           "website": "https://alpha.sg", "rating": 4.2}
    current = dict(old, review_count=13)
    runner.Journal(path).append("pid:A", {"name": "stale"}, business=old)
    audited = []
    _install_fake_audit(monkeypatch, audited)

    rows = runner.run_audits([current], workers=1, log=lambda _message: None,
                             journal_path=path)

    assert audited == [current]
    assert rows[0]["name"] == "Alpha"
    assert len((tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_no_cache_mode_bypasses_journal_replay_but_appends_recovery_rows(
        tmp_path, monkeypatch):
    path = str(tmp_path / "run.jsonl")
    business = {"place_id": "A", "name": "Alpha"}
    runner.Journal(path).append("pid:A", {"name": "stale"}, business=business)
    audited = []
    _install_fake_audit(monkeypatch, audited)

    rows = runner.run_audits([business], workers=1, log=lambda _message: None,
                             journal_path=path, resume_journal=False)

    assert audited == [business]
    assert rows[0]["name"] == "Alpha"
    assert len((tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_no_cache_cli_disables_cache_reads_and_journal_replay(tmp_path,
                                                              monkeypatch):
    source = tmp_path / "businesses.csv"
    source.write_text("place_id,name,website\nA,Alpha,https://alpha.sg\n",
                      encoding="utf-8")
    captured = {}

    def fake_audit_all(_businesses, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(leadscan, "audit_all", fake_audit_all)
    monkeypatch.chdir(tmp_path)
    result = leadscan.main(["--input", str(source), "--no-cache",
                            "--out", str(tmp_path / "leads.csv")])

    assert result == 0
    assert captured["resume_journal"] is False
    assert captured["cache"].enabled is False
    help_text = leadscan.build_parser().format_help()
    assert "journal replay" in help_text


def test_missing_and_old_version_journal_rows_are_reaudited(tmp_path, monkeypatch):
    path = tmp_path / "run.jsonl"
    old = config.PIPELINE_SCHEMA_VERSION - 1
    path.write_text(
        json.dumps({"_key": "pid:A", "name": "stale missing version"}) + "\n"
        + json.dumps({"_key": "pid:B", "name": "stale old version",
                      "_pipeline_version": old}) + "\n",
        encoding="utf-8")

    audited = []
    _install_fake_audit(monkeypatch, audited)
    businesses = [{"place_id": "A", "name": "Alpha"},
                  {"place_id": "B", "name": "Beta"}]
    rows = runner.run_audits(
        businesses,
        workers=1, log=lambda _message: None, journal_path=str(path))

    assert audited == businesses
    assert [row["name"] for row in rows] == ["Alpha", "Beta"]
    current = runner.Journal(str(path)).done_keys()
    assert set(current) == {_journal_identity(business) for business in businesses}
    assert all(record["_pipeline_version"] == config.PIPELINE_SCHEMA_VERSION
               for record in current.values())
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4


# ---------------------------------------------------------------------------
# Fatal worker failures
# ---------------------------------------------------------------------------

def test_every_browser_worker_failing_to_start_is_fatal(monkeypatch):
    class BrokenBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("browser executable missing")

        def __exit__(self, *_args):
            return None

    _install_browser(monkeypatch, BrokenBrowser)
    businesses = [{"place_id": str(index), "name": f"Firm {index}"}
                  for index in range(3)]

    with pytest.raises(runner.AuditRunError,
                       match="No audit workers could start"):
        runner.run_audits(businesses, workers=3, log=lambda _message: None)


def test_one_worker_may_fail_if_another_finishes_every_business(monkeypatch):
    starts = {"count": 0}
    start_lock = threading.Lock()

    class SometimesBrokenBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            with start_lock:
                starts["count"] += 1
                attempt = starts["count"]
            if attempt == 1:
                raise RuntimeError("one browser failed")
            return self

        def __exit__(self, *_args):
            return None

    _install_browser(monkeypatch, SometimesBrokenBrowser)
    audited = []

    def fake_audit(_browser, business, cache=None, deep=True, deadline=None):
        audited.append(business["place_id"])
        return {"name": business["name"], "score": 70, "tier": "hot",
                "warm": True, "disqualified": False}

    monkeypatch.setattr(runner.checks, "audit_business", fake_audit)
    businesses = [{"place_id": str(index), "name": f"Firm {index}"}
                  for index in range(4)]

    rows = runner.run_audits(businesses, workers=2, log=lambda _message: None)

    assert len(rows) == 4
    assert sorted(audited) == ["0", "1", "2", "3"]


def test_unprocessed_businesses_are_fatal_and_completed_journal_rows_survive(
        tmp_path, monkeypatch):
    audited = []
    _install_fake_audit(monkeypatch, audited)
    path = str(tmp_path / "run.jsonl")
    businesses = [{"place_id": "A", "name": "Alpha"},
                  {"place_id": "B", "name": "Beta"}]

    def failing_progress_log(message):
        if message.startswith("  ["):
            raise RuntimeError("progress output failed")

    with pytest.raises(runner.AuditRunError, match="1 business.*unprocessed"):
        runner.run_audits(businesses, workers=1, log=failing_progress_log,
                          journal_path=path)

    records = runner.Journal(path).done_keys()
    assert len(records) == 1
    assert next(iter(records.values()))["name"] == "Alpha"
    assert len((tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_per_business_failure_remains_an_isolated_error_row(monkeypatch):
    audited = []
    _install_fake_audit(monkeypatch, audited)

    def sometimes_fails(_browser, business, cache=None, deep=True, deadline=None):
        if business["place_id"] == "A":
            raise RuntimeError("site-specific render failure")
        return {"name": business["name"], "score": 70, "tier": "hot",
                "warm": True, "disqualified": False}

    monkeypatch.setattr(runner.checks, "audit_business", sometimes_fails)
    rows = runner.run_audits(
        [{"place_id": "A", "name": "Alpha"},
         {"place_id": "B", "name": "Beta"}],
        workers=1, log=lambda _message: None)

    assert len(rows) == 2
    assert rows[0]["status"] == "scan failed"
    assert "site-specific render failure" in rows[0]["reasons"]
    assert rows[1]["name"] == "Beta"


def test_cli_fatal_audit_writes_no_outputs(tmp_path, monkeypatch, capsys):
    source = tmp_path / "businesses.csv"
    source.write_text("place_id,name,website\nA,Alpha,https://alpha.sg\n",
                      encoding="utf-8")
    output = tmp_path / "leads.csv"
    reports = tmp_path / "reports"
    crm = tmp_path / "crm.csv"

    def fatal_audit(*_args, **_kwargs):
        raise runner.AuditRunError(
            "No audit workers could start. Check the browser installation and retry.")

    monkeypatch.setattr(leadscan, "audit_all", fatal_audit)
    result = leadscan.main([
        "--input", str(source), "--no-cache", "--out", str(output),
        "--reports", str(reports), "--crm", str(crm),
    ])

    assert result != 0
    assert "Audit failed: No audit workers could start" in capsys.readouterr().out
    assert not output.exists()
    assert not output.with_suffix(".html").exists()
    assert not output.with_suffix(".xlsx").exists()
    assert not reports.exists()
    assert not crm.exists()


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
