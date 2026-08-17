"""
config.py
---------
Every tunable number and every search sweep lives here. Change behaviour here,
not in the engine files.

Each value can also be set with an environment variable. This lets you tune a
run without an edit to the code:

    LEADSCAN_QUIET_REVIEWS=20 python leadscan.py --sweep sg-interior
"""

import os


def _int(name, default):
    """Read an integer from the environment. Use the default if it is absent."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _float(name, default):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# ICP thresholds
# ---------------------------------------------------------------------------

# At or above this follower count the firm owns an audience. Skip it.
INFLUENCER_FOLLOWERS = _int("LEADSCAN_INFLUENCER_FOLLOWERS", 20_000)

# A no-website firm above this follower count can sell for itself. Skip it.
SOCIAL_ONLY_MAX_FOLLOWERS = _int("LEADSCAN_SOCIAL_MAX_FOLLOWERS", 3_000)

# At or below this review count the firm is quiet. This is the main ICP signal.
QUIET_REVIEWS = _int("LEADSCAN_QUIET_REVIEWS", 30)

# Above this review count the firm has traction. Score it down.
ESTABLISHED_REVIEWS = _int("LEADSCAN_ESTABLISHED_REVIEWS", 60)

# Between QUIET_REVIEWS and this value the firm gets a small bonus.
MID_REVIEWS = _int("LEADSCAN_MID_REVIEWS", 100)

# A page slower than this many seconds loses clicks.
SLOW_SECONDS = _float("LEADSCAN_SLOW_SECONDS", 5.0)


# ---------------------------------------------------------------------------
# Network behaviour
# ---------------------------------------------------------------------------

NAV_TIMEOUT_MS = _int("LEADSCAN_NAV_TIMEOUT_MS", 20_000)   # first try
RETRY_TIMEOUT_MS = _int("LEADSCAN_RETRY_TIMEOUT_MS", 30_000)  # second try
RENDER_RETRIES = _int("LEADSCAN_RENDER_RETRIES", 1)        # extra tries after the first
SETTLE_SECONDS = _float("LEADSCAN_SETTLE_SECONDS", 1.2)    # wait for late scripts

# Wait this long between hits on different sites. Be polite.
POLITE_DELAY_SECONDS = _float("LEADSCAN_POLITE_DELAY", 1.0)

# Wait this long between Google Places requests.
PLACES_DELAY_SECONDS = _float("LEADSCAN_PLACES_DELAY", 0.2)

USER_AGENT = os.getenv(
    "LEADSCAN_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_DIR = os.getenv("LEADSCAN_CACHE_DIR", ".leadscan-cache")
CACHE_TTL_HOURS = _int("LEADSCAN_CACHE_TTL_HOURS", 168)   # 7 days


# ---------------------------------------------------------------------------
# Search sweeps
# ---------------------------------------------------------------------------
# Mix terms and regions. This finds the quiet long-tail firms, not only the
# well-known ones.

SWEEPS = {
    "sg-interior": [
        "interior design firm Singapore",
        "HDB renovation Singapore",
        "condo renovation interior design Singapore",
        "home renovation contractor Singapore",
        "interior designer Jurong",
        "interior designer Tampines",
        "interior designer Woodlands",
        "interior designer Bedok",
        "interior designer Ang Mo Kio",
        "interior designer Punggol",
    ],
    # High-ticket car care. Heavy Meta and Instagram advertisers. Many run off
    # Instagram or Linktree with no real funnel.
    "sg-car": [
        "car detailing Singapore",
        "ceramic coating car Singapore",
        "paint protection film Singapore",
        "car wrapping Singapore",
        "car grooming Singapore",
        "car workshop Singapore",
        "car servicing Singapore",
        "car window tinting Singapore",
        "car detailing studio Ubi",
        "car detailing Sin Ming",
    ],
    # Smaller market. The COE limits volume. Run this after the stronger niches.
    "sg-motorbike": [
        "motorcycle workshop Singapore",
        "motorbike servicing Singapore",
        "motorcycle dealer Singapore",
        "big bike dealer Singapore",
        "superbike shop Singapore",
        "motorcycle accessories shop Singapore",
        "motorbike tyre shop Singapore",
        "motorcycle repair Singapore",
    ],
    # The largest Meta advertisers in Singapore. Broken funnels are common and
    # the customer value is high.
    "sg-aesthetics": [
        "aesthetic clinic Singapore",
        "medical aesthetics Singapore",
        "med spa Singapore",
        "beauty salon Singapore",
        "facial spa Singapore",
        "slimming clinic Singapore",
        "hair removal clinic Singapore",
        "aesthetic clinic Orchard",
        "aesthetic clinic Tampines",
        "aesthetic clinic Jurong",
    ],
    # A large market with many small companies. Most have thin websites.
    "sg-aircon": [
        "aircon servicing Singapore",
        "aircon installation Singapore",
        "aircon chemical wash Singapore",
        "aircon repair Singapore",
        "aircon servicing Jurong",
        "aircon servicing Tampines",
        "aircon servicing Woodlands",
        "aircon servicing Bedok",
        "aircon servicing Sengkang",
        "aircon servicing Yishun",
    ],
    # Trades with high job value and usually no booking form.
    "sg-reno-trades": [
        "plumber Singapore",
        "electrician Singapore",
        "handyman services Singapore",
        "carpentry contractor Singapore",
        "painting services Singapore",
        "flooring contractor Singapore",
        "waterproofing contractor Singapore",
        "false ceiling contractor Singapore",
    ],
    # Personal services with repeat customers and heavy Instagram use.
    "sg-wellness": [
        "pilates studio Singapore",
        "yoga studio Singapore",
        "personal trainer Singapore",
        "physiotherapy clinic Singapore",
        "massage spa Singapore",
        "chiropractor Singapore",
        "nail salon Singapore",
        "hair salon Singapore",
    ],
}

# Default region and language for a Places search.
REGION_CODE = os.getenv("LEADSCAN_REGION_CODE", "SG")
LANGUAGE_CODE = os.getenv("LEADSCAN_LANGUAGE_CODE", "en")

# The word used in a social search, for example "<name> Singapore instagram".
SOCIAL_SEARCH_REGION = os.getenv("LEADSCAN_SOCIAL_REGION", "Singapore")
