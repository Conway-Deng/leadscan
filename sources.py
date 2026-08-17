"""
sources.py
----------
Where the list of businesses comes from. Two modes:

  1. CSV            -- read a hand-made list. No API key needed.
  2. Google Places  -- find businesses by search. Needs GOOGLE_PLACES_API_KEY.

PLACES API VERSION
This module uses the Places API (New) endpoint
`https://places.googleapis.com/v1/places:searchText`. The old
`maps.googleapis.com/maps/api/place/textsearch/json` pair is in Legacy status
and Google directs all new work to the new endpoint.

The change also cuts the request count by more than half. The legacy flow was:
one Text Search per query, then ONE EXTRA Place Details request for every
unique business, only to read the website, the phone number and the review
count. The new endpoint returns those same three fields inside the search
response when they are named in the field mask, so the per-business Details
request disappears. A 200-firm sweep drops from about 230 requests to about 30.

Set LEADSCAN_PLACES_LEGACY=1 to force the old endpoints if an existing key is
restricted to them.

DE-DUPLICATION
A single company appears many times in a sweep: once per branch, once per
search term, and once per spelling. The earlier version removed duplicates by
place id only, so "Ft2 (Ubi)" and "Ft2 (Woodlands)" both survived with the same
phone number and the same website. Nixon then called the same company twice.
Duplicates are now removed by place id, then by phone number, then by website
root domain.
"""

import csv
import os
import re
import time
import urllib.parse

import requests

import config

# Places API (New)
SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

# Only these fields are billed. Ask for nothing more.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.userRatingCount",
    "places.rating",
    "places.businessStatus",
    "places.regularOpeningHours",
    "nextPageToken",
])

# Legacy endpoints, kept only as a fallback.
LEGACY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
LEGACY_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
LEGACY_DETAIL_FIELDS = "website,formatted_phone_number,user_ratings_total,rating"

# Public suffixes with two labels, so "example.com.sg" gives "example.com.sg"
# and not "com.sg".
_TWO_LABEL_SUFFIXES = {
    "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg", "com.my", "com.au",
    "co.uk", "org.uk", "co.id", "co.th", "com.hk", "com.tw", "co.jp", "co.kr",
    "com.ph", "com.vn", "co.nz", "com.cn", "com.br",
}


# ---------------------------------------------------------------------------
# CSV input
# ---------------------------------------------------------------------------

def from_csv(path):
    """Read a hand-made list of businesses. Columns: name, website."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for record in csv.DictReader(handle):
            lower = {(k or "").strip().lower(): (v or "").strip()
                     for k, v in record.items()}
            name = lower.get("name", "")
            if not name:
                continue
            rows.append({
                "name": name,
                "website": lower.get("website", ""),
                "phone": lower.get("phone", ""),
                "address": lower.get("address", ""),
                "review_count": _as_int(lower.get("review_count")),
                "rating": None,
                "place_id": "",
            })
    return rows


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Normalising, for de-duplication
# ---------------------------------------------------------------------------

def normalise_phone(phone):
    """Keep the digits only, and keep the last 8 of them.

    A Singapore number is written as '+65 6123 4567', '6123 4567' and
    '65 6123 4567' in different records. The last 8 digits are the same in all
    three, so they compare correctly.
    """
    digits = re.sub(r"\D", "", phone or "")
    return digits[-8:] if len(digits) >= 8 else digits


def root_domain(url):
    """Give back the registrable domain of a URL, in lower case."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    # A bare IP address or "localhost" identifies a server, not a company. Two
    # different firms can sit behind one address, so it must never be used to
    # join two records together.
    if host == "localhost" or re.fullmatch(r"[\d.]+", host) or ":" in host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Social platforms and site builders are never a company's own domain, so two
# firms that both link to Instagram must not collapse into one.
_SHARED_DOMAINS = {
    "instagram.com", "facebook.com", "tiktok.com", "linktr.ee", "wa.me",
    "linkedin.com", "carousell.sg", "shopee.sg", "lazada.sg", "sites.google.com",
    "wixsite.com", "blogspot.com", "business.site", "godaddysites.com",
    "weebly.com", "webnode.page", "myshopify.com",
}


def dedupe(businesses):
    """
    Remove duplicate companies. Keep the record with the most reviews, because
    that record is usually the main branch.

    Give back (kept list, number removed).
    """
    alias = {}      # identity key -> the chosen business record
    order = []      # chosen records, in first-seen order

    for business in businesses:
        keys = _identity_keys(business)
        match = next((alias[k] for k in keys if k in alias), None)
        if match is None:
            order.append(business)
            for key in keys:
                alias[key] = business
            continue
        if _reviews_of(business) > _reviews_of(match):
            # The new record looks like the main branch. Swap it in everywhere.
            order[order.index(match)] = business
            for key, value in list(alias.items()):
                if value is match:
                    alias[key] = business
            match = business
        for key in keys:
            alias.setdefault(key, match)

    return order, len(businesses) - len(order)


def _identity_keys(business):
    """All the ways this record can be recognised as the same company."""
    keys = []
    place_id = (business.get("place_id") or "").strip()
    if place_id:
        keys.append("pid:" + place_id)

    phone = normalise_phone(business.get("phone"))
    if len(phone) >= 8:
        keys.append("tel:" + phone)

    domain = root_domain(business.get("website"))
    if domain and domain not in _SHARED_DOMAINS:
        keys.append("web:" + domain)

    if not keys:
        keys.append("name:" + re.sub(r"[^a-z0-9]", "", (business.get("name") or "").lower()))
    return keys


def _reviews_of(business):
    value = business.get("review_count")
    return value if isinstance(value, int) else -1


# ---------------------------------------------------------------------------
# Places API (New)
# ---------------------------------------------------------------------------

def _search_text_new(query, api_key, max_pages=3):
    """One search on the new endpoint, following pagination up to ~60 results."""
    results = []
    page_token = None
    for _ in range(max_pages):
        body = {
            "textQuery": query,
            "pageSize": 20,
            "languageCode": config.LANGUAGE_CODE,
            "regionCode": config.REGION_CODE,
        }
        if page_token:
            body["pageToken"] = page_token
        response = requests.post(
            SEARCH_TEXT_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Places API (New) error {response.status_code}: "
                f"{response.text[:300]}"
            )
        payload = response.json()
        results.extend(payload.get("places", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
        time.sleep(config.PLACES_DELAY_SECONDS)
    return results


def place_from_new(place):
    """Turn one Places API (New) record into the flat business dict."""
    name = (place.get("displayName") or {}).get("text", "")
    phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or ""
    return {
        "name": name,
        "website": place.get("websiteUri") or "",
        "phone": phone,
        "address": place.get("formattedAddress") or "",
        "review_count": place.get("userRatingCount"),
        "rating": place.get("rating"),
        "place_id": place.get("id") or "",
        "business_status": place.get("businessStatus") or "",
        # The weekday text is what a person reads, so it is what the call sheet
        # shows. It costs no extra request: it rides in the same field mask.
        "opening_hours": ((place.get("regularOpeningHours") or {})
                          .get("weekdayDescriptions") or []),
    }


# ---------------------------------------------------------------------------
# Legacy fallback
# ---------------------------------------------------------------------------

def _search_text_legacy(query, api_key, max_pages=3):
    results, token = [], None
    for _ in range(max_pages):
        if token is None:
            params = {"query": query, "key": api_key, "region": config.REGION_CODE.lower()}
        else:
            time.sleep(2)
            params = {"pagetoken": token, "key": api_key}
        response = requests.get(LEGACY_SEARCH_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == "INVALID_REQUEST" and token:
            time.sleep(2)
            continue
        if status not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(
                f"Places API (legacy) error: {status} {payload.get('error_message', '')}"
            )
        results.extend(payload.get("results", []))
        token = payload.get("next_page_token")
        if not token:
            break
    return results


def _details_legacy(place_id, api_key):
    """Give back the details dict, or None when the lookup itself failed."""
    if not place_id:
        return {}
    try:
        response = requests.get(
            LEGACY_DETAILS_URL,
            params={"place_id": place_id, "fields": LEGACY_DETAIL_FIELDS, "key": api_key},
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("result", {}) or {}
    except requests.exceptions.RequestException:
        return None


def place_from_legacy(place, api_key):
    """Legacy search gives no website or phone, so a Details call is needed."""
    place_id = place.get("place_id", "")
    details = _details_legacy(place_id, api_key)
    lookup_failed = details is None
    details = details or {}
    return {
        "name": place.get("name", ""),
        "website": details.get("website", "") or "",
        "phone": details.get("formatted_phone_number", "") or "",
        "address": place.get("formatted_address", "") or "",
        # None means unknown. It does not mean zero reviews.
        "review_count": details.get("user_ratings_total"),
        "rating": details.get("rating"),
        "place_id": place_id,
        "business_status": place.get("business_status", ""),
        "details_failed": lookup_failed,
    }


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def sweep(queries, api_key, cap=200, cache=None, log=print, use_legacy=None):
    """
    Run many searches, remove duplicates, and give back a list of businesses.

    WHY A SWEEP: one search gives 20 firms, and mostly the well-known ones.
    Different terms and different districts surface the quiet long-tail firms,
    which are the ones that need help.
    """
    if not api_key:
        raise ValueError(
            "GOOGLE_PLACES_API_KEY is not set. Put it in .env, or use "
            "--input with a CSV instead."
        )

    if use_legacy is None:
        use_legacy = os.getenv("LEADSCAN_PLACES_LEGACY", "").strip().lower() in (
            "1", "true", "yes"
        )

    raw = []
    for query in queries:
        if len(raw) >= cap:
            log(f"  cap of {cap} reached, stopping the sweep early")
            break

        cached = cache.get("places", query) if cache else None
        if cached is not None:
            log(f"  searching: {query}  (reused from cache)")
            raw.extend(cached)
            continue

        log(f"  searching: {query}")
        try:
            if use_legacy:
                found = [place_from_legacy(p, api_key)
                         for p in _search_text_legacy(query, api_key)]
            else:
                found = [place_from_new(p) for p in _search_text_new(query, api_key)]
        except RuntimeError as error:
            if not use_legacy and ("403" in str(error) or "SERVICE_DISABLED" in str(error)):
                log("  Places API (New) refused the key. Enable 'Places API (New)' "
                    "in the Google Cloud console. Trying the legacy endpoints...")
                use_legacy = True
                found = [place_from_legacy(p, api_key)
                         for p in _search_text_legacy(query, api_key)]
            else:
                raise

        if cache:
            cache.put("places", query, found)
        raw.extend(found)
        time.sleep(config.PLACES_DELAY_SECONDS)

    # Drop places that Google marks as closed. A call to them is wasted.
    open_firms = [b for b in raw
                  if b.get("business_status") not in
                  ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")]
    closed = len(raw) - len(open_firms)

    unique, removed = dedupe(open_firms)
    log(f"  {len(raw)} results -> {len(unique)} unique firms "
        f"({removed} duplicates removed, {closed} closed businesses dropped)")
    return unique[:cap]


# Kept so that an older script that calls the previous name still works.
sweep_google_places = sweep
