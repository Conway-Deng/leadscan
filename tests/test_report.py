"""Tests for report.py, cache.py, browser parsing, and adlibrary.py."""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adlibrary  # noqa: E402
import cache as cache_module  # noqa: E402
import compatibility  # noqa: E402
import config  # noqa: E402
import report  # noqa: E402
import scoring  # noqa: E402


def lead(name="Alpha", tier="hot", score=80, phone="+65 6123 4567", **extra):
    row = {"name": name, "tier": tier, "score": score, "phone": phone,
           "hook": "A Meta Pixel is installed on your site.", "warm": True,
           "disqualified": False, "website": "https://alpha.sg",
           "instagram": "", "facebook": "", "tiktok": "", "email": "",
           "review_count": 12, "rating": 4.5, "instagram_followers": None,
           "ad_tags": "Meta Pixel", "capture_methods": "", "address": "",
           "reasons": "", "status": "ok"}
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Spreadsheet safety
# ---------------------------------------------------------------------------

def test_a_formula_looking_name_is_neutralised():
    """A cell starting with '=' is executed by Excel. That is an attack path."""
    assert report.safe_cell("=HYPERLINK(\"http://evil\")").startswith("'=")
    assert report.safe_cell("+1234").startswith("'+")
    assert report.safe_cell("-cmd").startswith("'-")
    assert report.safe_cell("@SUM(A1)").startswith("'@")


def test_a_normal_value_is_untouched():
    assert report.safe_cell("Quiet Studio") == "Quiet Studio"
    assert report.safe_cell(42) == 42
    assert report.safe_cell(None) == ""


def test_csv_output_neutralises_a_formula_name(tmp_path):
    path = str(tmp_path / "out.csv")
    report.write_csv([lead(name="=cmd|'/c calc'!A1")], path)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"].startswith("'=")


def test_csv_output_persists_pages_checked(tmp_path):
    path = str(tmp_path / "out.csv")
    pages = "https://studio.sg/quote | https://studio.sg/contact"
    report.write_csv([lead(pages_checked=pages)], path)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        assert "pages_checked" in reader.fieldnames
        rows = list(reader)
    assert rows[0]["pages_checked"] == pages


# ---------------------------------------------------------------------------
# Lead selection and ordering
# ---------------------------------------------------------------------------

def test_selection_drops_disqualified_and_cold_rows():
    rows = [
        lead(name="hot"),
        lead(name="skip", disqualified=True),
        lead(name="cold", warm=False),
    ]
    kept = report.select_leads(rows, want=10)
    assert [r["name"] for r in kept] == ["hot"]


def test_selection_puts_hot_first():
    rows = [lead(name="warm", tier=scoring.TIER_WARM, score=99),
            lead(name="hot", tier=scoring.TIER_HOT, score=50)]
    kept = report.select_leads(rows, want=10)
    assert kept[0]["name"] == "hot"


def test_selection_respects_want():
    rows = [lead(name=f"firm{i}", score=100 - i) for i in range(10)]
    assert len(report.select_leads(rows, want=3)) == 3


def test_cool_rows_are_hidden_unless_asked_for():
    rows = [lead(name="hot"), lead(name="cool", tier=scoring.TIER_COOL)]
    assert [r["name"] for r in report.select_leads(rows, 10)] == ["hot"]
    assert len(report.select_leads(rows, 10, include_cool=True)) == 2


def test_cool_rows_are_shown_when_they_are_all_there_is():
    """An empty sheet is worse than a sheet of cool leads."""
    rows = [lead(name="cool", tier=scoring.TIER_COOL)]
    assert len(report.select_leads(rows, 10)) == 1


# ---------------------------------------------------------------------------
# HTML call sheet
# ---------------------------------------------------------------------------

def test_html_escapes_a_hostile_business_name(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([lead(name="<script>alert(1)</script>")], path)
    page = open(path, encoding="utf-8").read()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_html_makes_the_phone_dialable(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([lead(phone="+65 6123 4567")], path)
    assert 'href="tel:+6561234567"' in open(path, encoding="utf-8").read()


def test_html_handles_a_missing_phone(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([lead(phone="")], path)
    assert "no phone listed" in open(path, encoding="utf-8").read()


def test_html_handles_an_empty_list(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([], path)
    assert "No leads matched" in open(path, encoding="utf-8").read()


def test_write_all_produces_every_format(tmp_path):
    written = report.write_all([lead()], str(tmp_path / "leads.csv"), stamp="now")
    assert os.path.exists(written["csv"])
    assert os.path.exists(written["html"])
    if "xlsx" in written:
        assert os.path.exists(written["xlsx"])


def test_lead_state_key_stability_across_score_and_tier():
    row_hot = lead(name="Studio", website="https://studio.sg", score=90, tier="hot")
    row_cool = lead(name="Studio", website="https://studio.sg", score=10, tier="cool")
    assert report._lead_state_key(row_hot) == report._lead_state_key(row_cool)


def test_lead_state_key_website_normalization():
    row1 = lead(website="https://STUDIO.sg/")
    row2 = lead(website="  https://studio.sg/  ")
    assert report._lead_state_key(row1) == report._lead_state_key(row2)


def test_lead_state_key_phone_fallback_normalization():
    row1 = lead(website="", phone="+65 6123 4567")
    row2 = lead(website="", phone="65-6123-4567")
    assert report._lead_state_key(row1) == report._lead_state_key(row2)


def test_lead_state_key_name_fallback_normalization():
    row1 = lead(website="", phone="", name="Quiet   Studio")
    row2 = lead(website="", phone="", name="quiet studio")
    assert report._lead_state_key(row1) == report._lead_state_key(row2)


def test_lead_state_key_distinct_for_different_businesses():
    key1 = report._lead_state_key(lead(website="https://studio-a.sg"))
    key2 = report._lead_state_key(lead(website="https://studio-b.sg"))
    assert key1 != key2


def test_html_includes_data_lead_key_without_raw_sensitive_data(tmp_path):
    path = str(tmp_path / "out.html")
    phone = "+65 6123 4567"
    report.write_html([lead(website="", phone=phone, name="Alpha Studio")], path)
    page = open(path, encoding="utf-8").read()
    assert 'data-lead-key="lead-' in page
    # Verify raw phone number is not directly in data-lead-key attribute
    assert f'data-lead-key="{phone}"' not in page
    assert 'data-lead-key="61234567"' not in page


def test_html_call_sheet_contains_lead_state_persistence_script(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([lead(name="Alpha Studio", website="https://alpha.sg")], path)
    page = open(path, encoding="utf-8").read()

    # A. Storage namespace
    assert "leadscan.called.v1" in page

    # B. Stable key accessed from DOM
    assert "dataset.leadKey" in page

    # C. Restore behavior
    assert "called[key]" in page
    assert "box.checked = true" in page
    assert "lead.classList.add('done')" in page

    # D. Save checked state
    assert "called[key] = true" in page

    # E. Uncheck removes state
    assert "delete called[key]" in page

    # F. Safe JSON and storage error handling
    assert "JSON.parse" in page
    assert "JSON.stringify" in page
    assert "try" in page
    assert "catch" in page


def test_html_call_sheet_contains_outcome_selector_per_lead(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([
        lead(name="Alpha Studio", website="https://alpha.sg"),
        lead(name="Beta Design", website="https://beta.sg"),
    ], path)
    page = open(path, encoding="utf-8").read()

    # A. One outcome selector per lead card
    assert page.count('<article class="lead"') == 2
    assert page.count('<select class="outcome"') == 2

    # B. Exact options
    assert '<option value="">No outcome</option>' in page
    assert '<option value="follow-up">Follow up</option>' in page
    assert '<option value="interested">Interested</option>' in page
    assert '<option value="not-interested">Not interested</option>' in page

    # C. Accessible label contains business name
    assert 'aria-label="Contact outcome for Alpha Studio"' in page
    assert 'aria-label="Contact outcome for Beta Design"' in page

    # D. Selector is placed inside the card
    assert '<div class="side">' in page
    assert '<select class="outcome"' in page

    # E. Print CSS hides outcome selector
    assert ".outcome { display:none }" in page or ".outcome {display:none}" in page

    # F. Existing called persistence remains intact
    assert "leadscan.called.v1" in page
    assert "dataset.leadKey" in page


def test_html_call_sheet_contains_outcome_persistence_script(tmp_path):
    path = str(tmp_path / "out.html")
    report.write_html([lead(name="Alpha Studio", website="https://alpha.sg")], path)
    page = open(path, encoding="utf-8").read()

    # A. New namespace
    assert "leadscan.outcome.v1" in page

    # B. Allowed values validated
    assert "follow-up" in page
    assert "interested" in page
    assert "not-interested" in page

    # C. Restoration uses stable lead key
    assert "dataset.leadKey" in page

    # D. Restore logic assigns saved value
    assert "select.value = outcomes[key]" in page

    # E. Change handler saves
    assert "outcomes[key] = val" in page or "outcomes[key] =" in page

    # F. Selecting empty outcome removes key
    assert "delete outcomes[key]" in page

    # G. Safe storage handling
    assert "JSON.parse" in page
    assert "JSON.stringify" in page
    assert "try" in page
    assert "catch" in page

    # H. Existing called persistence still present
    assert "leadscan.called.v1" in page
    assert "delete called[key]" in page

    # I. Outcome handler does NOT modify .done or checkbox
    outcome_script = page[page.find("document.querySelectorAll('.outcome')"):]
    assert ".classList" not in outcome_script
    assert ".checked" not in outcome_script


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_current_version_cache_stores_and_reads_back(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    assert store.get("render", "https://a.test") is None
    store.put("render", "https://a.test", {"error": None})
    assert store.get("render", "https://a.test") == {"error": None}
    with open(store._path("render", "https://a.test"), encoding="utf-8") as handle:
        assert json.load(handle)["_pipeline_version"] == config.PIPELINE_SCHEMA_VERSION


def test_incompatible_cache_records_are_ignored_without_being_deleted(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    store.put("render", "https://a.test", {"error": None})
    path = store._path("render", "https://a.test")

    with open(path, encoding="utf-8") as handle:
        current = json.load(handle)
    for version in (None, config.PIPELINE_SCHEMA_VERSION - 1):
        incompatible = dict(current)
        if version is None:
            incompatible.pop("_pipeline_version")
        else:
            incompatible["_pipeline_version"] = version
        path_obj = tmp_path / os.path.basename(path)
        path_obj.write_text(json.dumps(incompatible), encoding="utf-8")
        before = path_obj.read_text(encoding="utf-8")
        assert store.get("render", "https://a.test") is None
        store.put("render", "https://a.test", {"error": "fresh"})
        assert path_obj.read_text(encoding="utf-8") == before


def test_corrupt_cache_record_is_ignored_without_being_rewritten(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    path = store._path("render", "https://a.test")
    corrupt = "{half-written"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(corrupt)

    assert store.get("render", "https://a.test") is None
    store.put("render", "https://a.test", {"error": None})
    assert open(path, encoding="utf-8").read() == corrupt


def test_robots_policies_do_not_share_render_cache_evidence(tmp_path):
    obey = cache_module.Cache(directory=str(tmp_path), ttl_hours=1,
                              respect_robots=True)
    obey.put("render", "https://a.test", {"findings": {"is_slow": False}})
    path = obey._path("render", "https://a.test")
    before = open(path, encoding="utf-8").read()

    ignore = cache_module.Cache(directory=str(tmp_path), ttl_hours=1,
                                respect_robots=False)
    assert ignore.get("render", "https://a.test") is None
    assert open(path, encoding="utf-8").read() == before


def test_slow_threshold_changes_reject_cached_is_slow_evidence(tmp_path,
                                                               monkeypatch):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    store.put("render", "https://a.test", {"findings": {"is_slow": False}})
    path = store._path("render", "https://a.test")
    before = open(path, encoding="utf-8").read()

    monkeypatch.setattr(config, "SLOW_SECONDS", config.SLOW_SECONDS + 1)
    changed = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    assert changed.get("render", "https://a.test") is None
    assert open(path, encoding="utf-8").read() == before


def test_cache_fingerprints_use_distinct_paths_without_overwriting(tmp_path,
                                                                   monkeypatch):
    first = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    first.put("render", "https://a.test", {"version": "A"})
    first_path = first._path("render", "https://a.test")
    first_contents = open(first_path, encoding="utf-8").read()

    monkeypatch.setattr(config, "SLOW_SECONDS", config.SLOW_SECONDS + 1)
    second = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    second.put("render", "https://a.test", {"version": "B"})
    second_path = second._path("render", "https://a.test")

    assert first_path != second_path
    assert os.path.exists(first_path)
    assert os.path.exists(second_path)
    assert first.get("render", "https://a.test") == {"version": "A"}
    assert second.get("render", "https://a.test") == {"version": "B"}
    assert open(first_path, encoding="utf-8").read() == first_contents


def _fingerprints():
    return (compatibility.cache_fingerprint(),
            compatibility.run_fingerprint())


def test_region_or_language_changes_invalidate_cache_and_run_fingerprints(
        monkeypatch):
    original = _fingerprints()
    monkeypatch.setattr(config, "REGION_CODE", config.REGION_CODE + "-changed")
    changed = _fingerprints()
    assert changed[0] != original[0]
    assert changed[1] != original[1]


def test_social_search_region_changes_invalidate_cache_and_run_fingerprints(
        monkeypatch):
    original = _fingerprints()
    monkeypatch.setattr(config, "SOCIAL_SEARCH_REGION",
                        config.SOCIAL_SEARCH_REGION + " changed")
    changed = _fingerprints()
    assert changed[0] != original[0]
    assert changed[1] != original[1]


def test_render_setting_changes_invalidate_cache_and_run_fingerprints(
        monkeypatch):
    original = _fingerprints()
    monkeypatch.setattr(config, "NAV_TIMEOUT_MS", config.NAV_TIMEOUT_MS + 1)
    changed = _fingerprints()
    assert changed[0] != original[0]
    assert changed[1] != original[1]


def test_places_legacy_mode_invalidates_cache_and_run_fingerprints(monkeypatch):
    original = _fingerprints()
    replacement = "0" if compatibility.places_legacy_enabled() else "1"
    monkeypatch.setenv("LEADSCAN_PLACES_LEGACY", replacement)
    changed = _fingerprints()
    assert changed[0] != original[0]
    assert changed[1] != original[1]


def test_cache_expires(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=0)
    store.put("render", "https://a.test", {"error": None})
    assert store.get("render", "https://a.test") is None


def test_a_disabled_cache_stores_nothing(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), enabled=False)
    store.put("render", "https://a.test", {"x": 1})
    assert store.get("render", "https://a.test") is None


def test_namespaces_do_not_collide(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    store.put("render", "key", "A")
    store.put("social", "key", "B")
    assert store.get("render", "key") == "A"
    assert store.get("social", "key") == "B"


# ---------------------------------------------------------------------------
# Follower parsing (pure part of browser.py)
# ---------------------------------------------------------------------------

def test_follower_count_is_read_from_the_meta_tag_only():
    import browser
    html = ('<meta property="og:description" content="12.3K Followers, 300 '
            'Following - See photos">' '<body>Call 90210 Followers now</body>')
    assert browser.followers_from_html(html) == 12_300


def test_follower_count_is_none_when_the_meta_tag_is_absent():
    import browser
    assert browser.followers_from_html("<body>4,000 Followers</body>") is None


def test_parse_count_handles_every_shape():
    import browser
    assert browser.parse_count("1,234") == 1234
    assert browser.parse_count("12.3K") == 12300
    assert browser.parse_count("1.1M") == 1_100_000
    assert browser.parse_count("") is None
    assert browser.parse_count("abc") is None


# ---------------------------------------------------------------------------
# Meta Ad Library coverage
# ---------------------------------------------------------------------------

def test_singapore_is_reported_as_not_covered():
    warning = adlibrary.coverage_warning("SG")
    assert warning is not None
    assert "EU and UK" in warning


def test_an_eu_country_is_covered():
    assert adlibrary.coverage_warning("DE") is None


def test_an_uncovered_country_is_never_reported_as_no_ads(monkeypatch):
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "fake")
    result = adlibrary.has_live_ads("Quiet Studio", "SG")
    assert result["checked"] is False
    assert result["live_ads"] is False   # and `checked` False means "unknown"
