"""
sources.py
----------
Where the list of businesses comes from. Two modes:

  1. CSV  -> read a hand-made list. Needs ZERO API keys.
  2. Google Places -> auto-find businesses by search. Needs GOOGLE_PLACES_API_KEY.

For Places we now pull richer data per business:
  name, website, phone, review_count, rating, place_id.
review_count is our reliable "how quiet is this business" signal -- a small firm
with few reviews is more likely to actually need Nixon's help.

We also SWEEP many searches (different terms + regions) and de-duplicate, so we
build a pool of a few hundred firms instead of just 20 from one search.
"""

import csv
import time
import requests

PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# Detail fields we ask Google for. Each is cheap; phone + reviews are the gold.
DETAIL_FIELDS = "website,formatted_phone_number,user_ratings_total,rating"


def from_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = (r.get("name") or r.get("Name") or "").strip()
            website = (r.get("website") or r.get("Website") or "").strip()
            if name:
                rows.append({"name": name, "website": website, "phone": "",
                             "review_count": None, "rating": None, "place_id": ""})
    return rows


def _text_search_all_pages(query, api_key, max_pages=3):
    """
    One search, following Google's pagination to get up to ~60 results
    (20 per page, 3 pages).

    Gotcha: a next_page_token is NOT valid the instant you get it -- Google needs
    a moment to activate it, and hitting it too soon returns INVALID_REQUEST. So
    for token pages we retry with backoff, and if it still won't activate we just
    keep the results we already have instead of crashing the whole sweep.
    """
    results = []
    token = None
    for page_num in range(max_pages):
        if token is None:
            data = _places_get({"query": query, "key": api_key})
            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                raise RuntimeError(f"Places API error: {status} {data.get('error_message','')}")
        else:
            data = _fetch_token_page(token, api_key)
            if data is None:      # token never activated -- stop, keep what we have
                break
        results.extend(data.get("results", []))
        token = data.get("next_page_token")
        if not token:
            break
    return results


def _places_get(params):
    resp = requests.get(PLACES_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _fetch_token_page(token, api_key, tries=4):
    """Retry a page-token request with growing delays; give up gracefully."""
    delay = 2
    for _ in range(tries):
        time.sleep(delay)
        data = _places_get({"pagetoken": token, "key": api_key})
        if data.get("status") == "OK":
            return data
        if data.get("status") == "INVALID_REQUEST":
            delay += 1          # token not ready yet -- wait a bit longer, retry
            continue
        return None             # any other status: stop paginating this query
    return None


def _get_details(place_id, api_key):
    if not place_id:
        return {}
    try:
        resp = requests.get(
            DETAILS_URL,
            params={"place_id": place_id, "fields": DETAIL_FIELDS, "key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}) or {}
    except requests.exceptions.RequestException:
        return {}


def sweep_google_places(queries, api_key, cap=200):
    """
    Run MANY searches, de-duplicate by place_id, and enrich each unique business
    with phone + review count. Returns a list of business dicts.

    WHY a sweep: one search = 20 firms, mostly the well-known (already-good) ones.
    Different terms ("HDB renovation", "condo reno") and regions surface the quiet
    long-tail firms -- the ones who actually need help.
    """
    if not api_key:
        raise ValueError("No GOOGLE_PLACES_API_KEY set -- use --input CSV mode instead.")

    seen = {}  # place_id -> raw place, so duplicates across searches collapse
    for q in queries:
        print(f"  searching: {q}")
        for place in _text_search_all_pages(q, api_key):
            pid = place.get("place_id")
            if pid and pid not in seen:
                seen[pid] = place
        if len(seen) >= cap:
            break

    print(f"  {len(seen)} unique firms found. Pulling phone + reviews...")
    businesses = []
    for pid, place in list(seen.items())[:cap]:
        d = _get_details(pid, api_key)
        businesses.append({
            "name": place.get("name", ""),
            "website": d.get("website", "") or "",
            "phone": d.get("formatted_phone_number", "") or "",
            "review_count": d.get("user_ratings_total"),
            "rating": d.get("rating"),
            "place_id": pid,
        })
    return businesses
