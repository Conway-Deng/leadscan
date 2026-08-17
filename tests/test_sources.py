"""Tests for sources.py -- de-duplication, normalising, and the API mapping."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sources  # noqa: E402


def firm(name, phone="", website="", place_id="", reviews=None):
    return {"name": name, "phone": phone, "website": website,
            "place_id": place_id, "review_count": reviews}


# ---------------------------------------------------------------------------
# Phone normalising
# ---------------------------------------------------------------------------

def test_the_same_number_written_three_ways_matches():
    forms = ["+65 6123 4567", "6123 4567", "+6561234567", "65-6123-4567"]
    normalised = {sources.normalise_phone(f) for f in forms}
    assert len(normalised) == 1
    assert normalised.pop() == "61234567"


def test_empty_phone_is_safe():
    assert sources.normalise_phone("") == ""
    assert sources.normalise_phone(None) == ""


# ---------------------------------------------------------------------------
# Domain normalising
# ---------------------------------------------------------------------------

def test_root_domain_ignores_www_and_path():
    assert sources.root_domain("https://www.studio.com.sg/about") == "studio.com.sg"
    assert sources.root_domain("studio.com.sg") == "studio.com.sg"


def test_root_domain_keeps_two_label_suffixes_whole():
    assert sources.root_domain("https://shop.example.com.sg") == "example.com.sg"
    assert sources.root_domain("https://blog.example.com") == "example.com"


def test_root_domain_on_junk_is_safe():
    assert sources.root_domain("") == ""
    assert sources.root_domain(None) == ""


# ---------------------------------------------------------------------------
# De-duplication -- the known defect in the handoff note
# ---------------------------------------------------------------------------

def test_two_branches_with_one_phone_number_collapse():
    """'Ft2 (Ubi)' and 'Ft2 (Woodlands)' were both called. Same company."""
    firms = [
        firm("Ft2 (Ubi)", phone="+65 6123 4567", place_id="A", reviews=12),
        firm("Ft2 (Woodlands)", phone="6123 4567", place_id="B", reviews=40),
    ]
    kept, removed = sources.dedupe(firms)
    assert removed == 1
    assert len(kept) == 1
    # The record with more reviews is kept, because it is usually the main branch.
    assert kept[0]["name"] == "Ft2 (Woodlands)"


def test_same_website_different_place_id_collapses():
    firms = [
        firm("Studio A", website="https://studio.com.sg", place_id="A"),
        firm("Studio A Pte Ltd", website="https://www.studio.com.sg/home", place_id="B"),
    ]
    kept, removed = sources.dedupe(firms)
    assert removed == 1


def test_two_firms_both_linking_to_instagram_do_not_collapse():
    """A shared platform domain must never merge two different companies."""
    firms = [
        firm("Alpha", website="https://www.instagram.com/alpha", place_id="A"),
        firm("Beta", website="https://www.instagram.com/beta", place_id="B"),
    ]
    kept, removed = sources.dedupe(firms)
    assert removed == 0
    assert len(kept) == 2


def test_distinct_firms_are_all_kept():
    firms = [
        firm("Alpha", phone="+65 6111 1111", website="https://alpha.sg", place_id="A"),
        firm("Beta", phone="+65 6222 2222", website="https://beta.sg", place_id="B"),
        firm("Gamma", phone="+65 6333 3333", website="https://gamma.sg", place_id="C"),
    ]
    kept, removed = sources.dedupe(firms)
    assert removed == 0
    assert len(kept) == 3


def test_dedupe_keeps_first_seen_order():
    firms = [firm("A", place_id="1"), firm("B", place_id="2"), firm("C", place_id="3")]
    kept, _ = sources.dedupe(firms)
    assert [f["name"] for f in kept] == ["A", "B", "C"]


def test_records_with_no_identity_fall_back_to_the_name():
    firms = [firm("Same Name"), firm("Same  Name"), firm("Other")]
    kept, removed = sources.dedupe(firms)
    assert removed == 1
    assert len(kept) == 2


# ---------------------------------------------------------------------------
# Places API (New) mapping
# ---------------------------------------------------------------------------

def test_place_from_new_maps_every_field():
    raw = {
        "id": "ChIJabc",
        "displayName": {"text": "Quiet Studio", "languageCode": "en"},
        "formattedAddress": "1 Ubi Road, Singapore",
        "websiteUri": "https://quietstudio.com.sg",
        "nationalPhoneNumber": "6123 4567",
        "userRatingCount": 14,
        "rating": 4.8,
        "businessStatus": "OPERATIONAL",
    }
    mapped = sources.place_from_new(raw)
    assert mapped["name"] == "Quiet Studio"
    assert mapped["website"] == "https://quietstudio.com.sg"
    assert mapped["phone"] == "6123 4567"
    assert mapped["review_count"] == 14
    assert mapped["place_id"] == "ChIJabc"


def test_place_from_new_survives_missing_fields():
    mapped = sources.place_from_new({"id": "X"})
    assert mapped["name"] == ""
    assert mapped["website"] == ""
    # None means unknown. It must not become zero.
    assert mapped["review_count"] is None


def test_field_mask_asks_only_for_what_is_used():
    for field in ("places.websiteUri", "places.nationalPhoneNumber",
                  "places.userRatingCount", "nextPageToken"):
        assert field in sources.FIELD_MASK
    # Photos and reviews are expensive and unused. They must not be requested.
    assert "places.photos" not in sources.FIELD_MASK
    assert "places.reviews" not in sources.FIELD_MASK


# ---------------------------------------------------------------------------
# CSV input
# ---------------------------------------------------------------------------

def test_from_csv_reads_mixed_case_headings(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text("Name,Website\nAlpha,https://alpha.sg\n,skipped\n",
                    encoding="utf-8")
    rows = sources.from_csv(str(path))
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"


def test_sweep_without_a_key_gives_a_clear_message():
    try:
        sources.sweep(["x"], "", cap=10)
    except ValueError as error:
        assert "GOOGLE_PLACES_API_KEY" in str(error)
    else:
        raise AssertionError("a missing key must raise ValueError")


def test_an_ip_address_host_is_not_an_identity():
    """Two firms behind one IP address are still two firms."""
    assert sources.root_domain("http://127.0.0.1:8099/a") == ""
    assert sources.root_domain("http://192.168.1.10/") == ""
    assert sources.root_domain("http://localhost:3000/") == ""
    firms = [
        firm("Alpha", phone="+65 6111 1111", website="http://127.0.0.1:8099/a"),
        firm("Beta", phone="+65 6222 2222", website="http://127.0.0.1:8099/b"),
    ]
    kept, removed = sources.dedupe(firms)
    assert removed == 0
