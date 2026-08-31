"""
public_limits.py
----------------
Framework-independent, thread-safe admission controls for public audit services.

SECURITY & ARCHITECTURE NOTES:
1. Process-local boundary:
   These controls (SlidingWindowRateLimiter, ConcurrencyGate) operate entirely
   in-memory within a single worker process.
2. Defense-in-depth:
   They are NOT a replacement for edge/CDN/reverse-proxy rate limiting or API gateways.
   Multi-instance/horizontal deployments require shared storage (e.g. Redis) or
   edge-level enforcement.
3. Client Identity:
   Client keys must later be derived only from trusted, sanitized reverse-proxy headers
   (e.g., authenticated client IP or token) at the HTTP framework boundary.
4. Resource Exhaustion Protection:
   The purpose of these primitives is to bound accidental or hostile resource usage
   (CPU, memory, concurrent headless browser sessions) inside a worker process.
"""

import collections
import math
import threading
import time


def _client_key(value):
    """
    Normalize client identifier into a safe string key.

    Empty strings, None, and whitespace-only values collapse into '<unknown>'.
    """
    if value is None:
        return "<unknown>"
    cleaned = str(value).strip()
    return cleaned if cleaned else "<unknown>"


class SlidingWindowRateLimiter:
    """
    Bounded, thread-safe sliding-window rate limiter per client key.
    """

    def __init__(
        self,
        limit,
        window_seconds,
        max_clients=4096,
        clock=None,
    ):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError(f"limit must be an integer > 0, got {limit!r}")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or window_seconds <= 0:
            raise ValueError(f"window_seconds must be a number > 0, got {window_seconds!r}")
        if not isinstance(max_clients, int) or isinstance(max_clients, bool) or max_clients <= 0:
            raise ValueError(f"max_clients must be an integer > 0, got {max_clients!r}")

        self.limit = limit
        self.window_seconds = float(window_seconds)
        self.max_clients = max_clients
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._clients = collections.OrderedDict()

    def allow(self, client_key):
        """
        Evaluate whether client_key is allowed to proceed under the sliding window.

        Returns:
            tuple: (allowed: bool, retry_after_seconds: int)
        """
        with self._lock:
            key = _client_key(client_key)
            now = self._clock()
            cutoff = now - self.window_seconds

            if key in self._clients:
                self._clients.move_to_end(key)
                timestamps = self._clients[key]
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()

                if len(timestamps) < self.limit:
                    timestamps.append(now)
                    return True, 0
                else:
                    oldest = timestamps[0]
                    rem = oldest + self.window_seconds - now
                    retry_after = max(1, math.ceil(rem))
                    return False, retry_after

            # Unseen client: prune expired entries from oldest side first
            stale_keys = []
            for k, timestamps in list(self._clients.items()):
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if not timestamps:
                    stale_keys.append(k)

            for k in stale_keys:
                self._clients.pop(k, None)

            if len(self._clients) >= self.max_clients:
                return False, max(1, math.ceil(self.window_seconds))

            self._clients[key] = collections.deque([now])
            return True, 0

    @property
    def client_count(self):
        """Return the current number of tracked client entries."""
        with self._lock:
            return len(self._clients)


class ConcurrencyGate:
    """
    Atomic in-flight request counter for bounding concurrent resource-heavy tasks.
    """

    def __init__(self, limit):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError(f"limit must be an integer > 0, got {limit!r}")
        self.limit = limit
        self._in_flight = 0
        self._lock = threading.Lock()

    def try_acquire(self):
        """
        Atomically acquire a slot if under concurrency limit.

        Returns:
            bool: True if slot acquired, False if at or exceeding limit.
        """
        with self._lock:
            if self._in_flight >= self.limit:
                return False
            self._in_flight += 1
            return True

    def release(self):
        """
        Atomically release one acquired slot.

        Raises:
            RuntimeError: If called when no slots are in flight.
        """
        with self._lock:
            if self._in_flight <= 0:
                raise RuntimeError("release() called with zero in-flight slots")
            self._in_flight -= 1

    @property
    def in_flight(self):
        """Return the current number of in-flight tasks."""
        with self._lock:
            return self._in_flight
