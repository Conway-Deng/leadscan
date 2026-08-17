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
import os
import time

import config


class Cache:
    def __init__(self, directory=None, ttl_hours=None, enabled=True):
        self.directory = directory or config.CACHE_DIR
        # A ttl of 0 means "always stale". `or` would treat that as "unset",
        # so the check has to be explicit.
        if ttl_hours is None:
            ttl_hours = config.CACHE_TTL_HOURS
        self.ttl_seconds = ttl_hours * 3600
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if self.enabled:
            os.makedirs(self.directory, exist_ok=True)

    def _path(self, namespace, key):
        digest = hashlib.sha256(f"{namespace}::{key}".encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.directory, f"{namespace}-{digest}.json")

    def get(self, namespace, key):
        """Give back the stored value, or None when it is absent or too old."""
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        if time.time() - record.get("stored_at", 0) > self.ttl_seconds:
            return None
        self.hits += 1
        return record.get("value")

    def put(self, namespace, key, value):
        if not self.enabled:
            return value
        self.misses += 1
        path = self._path(namespace, key)
        record = {"stored_at": time.time(), "key": key, "value": value}
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            os.replace(temporary, path)
        except OSError:
            pass
        return value

    def summary(self):
        return f"cache: {self.hits} reused, {self.misses} new"
