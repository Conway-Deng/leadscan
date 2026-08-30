"""Cooperative monotonic deadlines for one business audit."""

import time


class AuditDeadlineExceeded(RuntimeError):
    """The total time budget for one business has been exhausted."""


class Deadline:
    def __init__(self, seconds, clock=None):
        self._clock = clock or time.monotonic
        self.ends_at = self._clock() + max(0.0, float(seconds))

    def remaining(self):
        return max(0.0, self.ends_at - self._clock())

    def check(self):
        remaining = self.remaining()
        if remaining <= 0:
            raise AuditDeadlineExceeded("audit deadline exceeded")
        return remaining

    def cap_seconds(self, requested):
        return min(max(0.0, float(requested)), self.check())

    def cap_milliseconds(self, requested):
        requested = max(0, int(requested))
        if requested == 0:
            self.check()
            return 0
        remaining_ms = int(self.check() * 1000)
        if remaining_ms < 1:
            raise AuditDeadlineExceeded("audit deadline exceeded")
        return min(requested, remaining_ms)
