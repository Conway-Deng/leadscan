import math
import threading
import pytest

from public_limits import SlidingWindowRateLimiter, ConcurrencyGate, _client_key


class FakeClock:
    def __init__(self, start=0.0):
        self.current = float(start)

    def __call__(self):
        return self.current

    def advance(self, delta):
        self.current += float(delta)


# ---------------------------------------------------------------------------
# Rate Limiter Tests
# ---------------------------------------------------------------------------

def test_client_key_normalization():
    assert _client_key(None) == "<unknown>"
    assert _client_key("") == "<unknown>"
    assert _client_key("   ") == "<unknown>"
    assert _client_key("  127.0.0.1  ") == "127.0.0.1"
    assert _client_key(12345) == "12345"


def test_rate_limiter_invalid_config():
    clock = FakeClock()
    for bad_limit in (0, -1, "2", False, True):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(limit=bad_limit, window_seconds=60, clock=clock)

    for bad_window in (0, -10, 0.0, "60", False, True):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(limit=5, window_seconds=bad_window, clock=clock)

    for bad_max_clients in (0, -5, "100", False, True):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(limit=5, window_seconds=60, max_clients=bad_max_clients, clock=clock)


def test_rate_limiter_admits_up_to_limit():
    clock = FakeClock(100.0)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)

    allowed1, retry1 = limiter.allow("client-A")
    assert allowed1 is True
    assert retry1 == 0

    allowed2, retry2 = limiter.allow("client-A")
    assert allowed2 is True
    assert retry2 == 0


def test_rate_limiter_rejects_above_limit():
    clock = FakeClock(100.0)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.allow("client-A") == (True, 0)
    assert limiter.allow("client-A") == (True, 0)

    allowed, retry = limiter.allow("client-A")
    assert allowed is False
    assert retry == 60


def test_rate_limiter_retry_after_calculation():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=30, clock=clock)

    assert limiter.allow("client-A") == (True, 0)  # admitted at t=0
    clock.advance(10.0)
    assert limiter.allow("client-A") == (True, 0)  # admitted at t=10

    clock.advance(5.0)  # t=15
    allowed, retry = limiter.allow("client-A")
    assert allowed is False
    # Oldest timestamp is t=0, window is 30, so expires at t=30. Current t=15 -> retry 15.
    assert retry == 15

    clock.advance(15.0)  # t=30 (oldest at t=0 now expired)
    allowed, retry = limiter.allow("client-A")
    assert allowed is True
    assert retry == 0


def test_rate_limiter_rejected_attempts_do_not_extend_window():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=10, clock=clock)

    assert limiter.allow("client-A") == (True, 0)  # admitted at t=0

    # Rejected attempt at t=5
    clock.advance(5.0)
    assert limiter.allow("client-A") == (False, 5)

    # Rejected attempt at t=8
    clock.advance(3.0)
    assert limiter.allow("client-A") == (False, 2)

    # Advance to t=10.0 (t=0 is now expired)
    clock.advance(2.0)
    assert limiter.allow("client-A") == (True, 0)


def test_rate_limiter_exact_expiry_boundary():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("client-A") == (True, 0)  # t=0

    clock.advance(59.9)
    assert limiter.allow("client-A") == (False, 1)

    clock.advance(0.1)  # t=60.0 exactly
    assert limiter.allow("client-A") == (True, 0)


def test_rate_limiter_clients_are_independent():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("client-1") == (True, 0)
    assert limiter.allow("client-1") == (False, 60)

    # client-2 is unaffected
    assert limiter.allow("client-2") == (True, 0)
    assert limiter.allow("client-2") == (False, 60)


def test_rate_limiter_unknown_normalization_shares_bucket():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.allow(None) == (True, 0)
    assert limiter.allow("") == (True, 0)
    assert limiter.allow("   ") == (False, 60)
    assert limiter.client_count == 1


def test_rate_limiter_max_clients_bounds_growth():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, max_clients=3, clock=clock)

    assert limiter.allow("c1") == (True, 0)
    assert limiter.allow("c2") == (True, 0)
    assert limiter.allow("c3") == (True, 0)
    assert limiter.client_count == 3

    # 4th client rejected due to table exhaustion
    allowed, retry = limiter.allow("c4")
    assert allowed is False
    assert retry == 60
    assert limiter.client_count == 3


def test_rate_limiter_stale_clients_evicted_for_new_clients():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, max_clients=2, clock=clock)

    assert limiter.allow("c1") == (True, 0)  # t=0
    clock.advance(10.0)
    assert limiter.allow("c2") == (True, 0)  # t=10
    assert limiter.client_count == 2

    # Advance beyond c1's expiry (t=60)
    clock.advance(55.0)  # t=65 (c1 expired, c2 expires at t=70)

    # c3 arrives; c1 should be pruned, admitting c3
    assert limiter.allow("c3") == (True, 0)
    assert limiter.client_count == 2


def test_rate_limiter_existing_client_works_when_table_full():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, max_clients=2, clock=clock)

    assert limiter.allow("c1") == (True, 0)
    assert limiter.allow("c2") == (True, 0)
    assert limiter.client_count == 2

    # Existing clients can still use their remaining limit
    assert limiter.allow("c1") == (True, 0)
    assert limiter.allow("c2") == (True, 0)

    # Unseen client c3 is rejected
    assert limiter.allow("c3") == (False, 60)


def test_rate_limiter_thread_safety():
    clock = FakeClock(0.0)
    limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60, clock=clock)

    results = []
    threads = []
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        allowed, _ = limiter.allow("shared-client")
        results.append(allowed)

    for _ in range(20):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert results.count(True) == 5
    assert results.count(False) == 15


# ---------------------------------------------------------------------------
# Concurrency Gate Tests
# ---------------------------------------------------------------------------

def test_concurrency_gate_invalid_config():
    for bad in (0, -1, "2", False, True, None):
        with pytest.raises(ValueError):
            ConcurrencyGate(limit=bad)


def test_concurrency_gate_admit_and_exhaust():
    gate = ConcurrencyGate(limit=2)
    assert gate.in_flight == 0

    assert gate.try_acquire() is True
    assert gate.in_flight == 1

    assert gate.try_acquire() is True
    assert gate.in_flight == 2

    assert gate.try_acquire() is False
    assert gate.in_flight == 2


def test_concurrency_gate_release():
    gate = ConcurrencyGate(limit=1)

    assert gate.try_acquire() is True
    assert gate.try_acquire() is False

    gate.release()
    assert gate.in_flight == 0

    assert gate.try_acquire() is True
    assert gate.in_flight == 1


def test_concurrency_gate_release_underflow_raises():
    gate = ConcurrencyGate(limit=2)
    with pytest.raises(RuntimeError, match="zero in-flight"):
        gate.release()


def test_concurrency_gate_thread_safety():
    gate = ConcurrencyGate(limit=3)
    results = []
    threads = []
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        acquired = gate.try_acquire()
        results.append(acquired)

    for _ in range(20):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert results.count(True) == 3
    assert results.count(False) == 17
    assert gate.in_flight == 3
