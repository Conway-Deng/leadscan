"""Deterministic compatibility fingerprints for cached and journaled work."""

import hashlib
import json
import os

import config


def _digest(payload):
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def places_legacy_enabled():
    """Return the effective Places API mode using the source pipeline's rules."""
    return os.getenv("LEADSCAN_PLACES_LEGACY", "").strip().lower() in (
        "1", "true", "yes")


def evidence_settings(respect_robots=None):
    """Central record of settings that affect sourced or rendered evidence."""
    if respect_robots is None:
        respect_robots = config.RESPECT_ROBOTS
    return {
        "pipeline_schema_version": config.PIPELINE_SCHEMA_VERSION,
        "respect_robots": bool(respect_robots),
        "slow_seconds": config.SLOW_SECONDS,
        "nav_timeout_ms": config.NAV_TIMEOUT_MS,
        "retry_timeout_ms": config.RETRY_TIMEOUT_MS,
        "render_retries": config.RENDER_RETRIES,
        "settle_seconds": config.SETTLE_SECONDS,
        "user_agent": config.USER_AGENT,
        "region_code": config.REGION_CODE,
        "language_code": config.LANGUAGE_CODE,
        "social_search_region": config.SOCIAL_SEARCH_REGION,
        "places_legacy": places_legacy_enabled(),
    }


def cache_fingerprint(respect_robots=None):
    """Fingerprint every setting that affects collected or sourced evidence."""
    return _digest(evidence_settings(respect_robots))


def run_fingerprint(social_only=False, deep=True, respect_robots=None):
    """Modes and settings that affect a scored journal result."""
    return _digest({
        "evidence_fingerprint": cache_fingerprint(respect_robots),
        "runtime": {
            "social_only": bool(social_only),
            "deep": bool(deep),
        },
        "scoring": {
            "quiet_reviews": config.QUIET_REVIEWS,
            "established_reviews": config.ESTABLISHED_REVIEWS,
            "mid_reviews": config.MID_REVIEWS,
            "influencer_followers": config.INFLUENCER_FOLLOWERS,
            "social_only_max_followers": config.SOCIAL_ONLY_MAX_FOLLOWERS,
        },
    })


def business_fingerprint(business):
    """Hash the normalized full source row so changed input is re-audited."""
    return _digest(_normalise(business or {}))


def _normalise(value):
    if isinstance(value, dict):
        return {str(key): _normalise(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
