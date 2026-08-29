"""Tests for report.py, cache.py, browser parsing, and adlibrary.py."""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adlibrary  # noqa: E402
import cache as cache_module  # noqa: E402
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


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_stores_and_reads_back(tmp_path):
    store = cache_module.Cache(directory=str(tmp_path), ttl_hours=1)
    assert store.get("render", "https://a.test") is None
    store.put("render", "https://a.test", {"error": None})
    assert store.get("render", "https://a.test") == {"error": None}


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
