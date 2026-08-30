"""
cache.py
--------
A small disk cache so a run can stop and start again without loss.

WHY THIS EXISTS
A full sweep makes hundreds of Google Places requests and renders hundreds of
websites. Each Places request costs money and each render costs about five
seconds. Before this cache, one network fault in the middle of a sweep threw
all of that away. Now every finished item is written to disk, and the next run
reads it back instead of paying for it a second time.

The cache is plain JSON files, one per item, named after a hash of the key. It
is safe to delete the whole folder at any time.
"""

import hashlib
import json
import math
import os
import time

import compatibility
import config


class Cache:
    def __init__(self, directory=None, ttl_hours=None, enabled=True,
                 respect_robots=None, log=None):
        self.directory = directory or config.CACHE_DIR
        self.log = log or print
        self._warned = set()
        self._ignored_paths = set()
        # A ttl of 0 means "always stale". `or` would treat that as "unset",
        # so the check has to be explicit.
        if ttl_hours is None:
            ttl_hours = config.CACHE_TTL_HOURS
        self.ttl_seconds = ttl_hours * 3600
        self.enabled = enabled
        self.fingerprint = compatibility.cache_fingerprint(respect_robots)
        self.hits = 0
        self.misses = 0
        if self.enabled:
            try:
                os.makedirs(self.directory, exist_ok=True)
            except OSError as error:
                self._warn("write", self.directory, error)
                self.enabled = False

    def _path(self, namespace, key):
        identity = f"{namespace}::{self.fingerprint}::{key}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.directory, f"{namespace}-{digest}.json")

    def get(self, namespace, key):
        """Give back the stored value, or None when it is absent or too old."""
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except FileNotFoundError:
            return None
        except OSError as error:
            self._warn("read", path, error)
            return None
        except (UnicodeError, ValueError):
            self._ignored_paths.add(path)
            return None
        if (not isinstance(record, dict)
                or record.get("_pipeline_version") != config.PIPELINE_SCHEMA_VERSION):
            self._ignored_paths.add(path)
            return None
        if record.get("_cache_fingerprint") != self.fingerprint:
            self._ignored_paths.add(path)
            return None
        now = time.time()
        stored_at = record.get("stored_at")
        if not _valid_timestamp(stored_at, now):
            self._ignored_paths.add(path)
            return None
        if self.ttl_seconds <= 0 or now - stored_at > self.ttl_seconds:
            return None
        self.hits += 1
        return record.get("value")

    def put(self, namespace, key, value):
        if not self.enabled:
            return value
        self.misses += 1
        path = self._path(namespace, key)
        if path in self._ignored_paths:
            return value
        record = {
            "_pipeline_version": config.PIPELINE_SCHEMA_VERSION,
            "_cache_fingerprint": self.fingerprint,
            "stored_at": time.time(), "key": key, "value": value,
        }
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            os.replace(temporary, path)
        except OSError as error:
            try:
                os.remove(temporary)
            except OSError:
                pass
            self._warn("write", path, error)
        return value

    def _warn(self, operation, path, error):
        if operation in self._warned:
            return
        self._warned.add(operation)
        self.log(f"Warning: cache {operation} failed for {path}: {error}; "
                 "continuing without persisted cache data.")

    def summary(self):
        return f"cache: {self.hits} reused, {self.misses} new"


def _valid_timestamp(value, now):
    """Cache timestamps must be finite real numbers no later than now."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= now
    except (OverflowError, TypeError, ValueError):
        return False
