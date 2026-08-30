"""Deadlines and persistence failures: production recovery must be honest."""

import builtins
import json
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser as browser_module  # noqa: E402
import cache as cache_module  # noqa: E402
import checks  # noqa: E402
import compatibility  # noqa: E402
import config  # noqa: E402
import deadlines  # noqa: E402
import leadscan  # noqa: E402
import runner  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _install_browser(monkeypatch, browser_class):
    module = types.ModuleType("browser")
    module.Browser = browser_class
    monkeypatch.setitem(sys.modules, "browser", module)


def _result(business):
    return {"name": business["name"], "score": 70, "tier": "hot",
            "warm": True, "disqualified": False}


def test_timed_out_business_is_journaled_and_next_business_completes(
        tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(deadlines.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(config, "BUSINESS_TIMEOUT_SECONDS", 1)

    class FakeBrowser:
        def __init__(self, **_kwargs):
            self.deadline = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_deadline(self, deadline):
            self.deadline = deadline

        def render(self, url):
            if "alpha" in url:
                clock.advance(2)
            html = ("<html><body><form action='/enquiry'>"
                    "<input type='email' name='email'></form></body></html>")
            return html, url, 0.1, None

    _install_browser(monkeypatch, FakeBrowser)
    path = str(tmp_path / "run.jsonl")
    businesses = [
        {"place_id": "A", "name": "Alpha", "website": "https://alpha.test"},
        {"place_id": "B", "name": "Beta", "website": "https://beta.test"},
    ]

    rows = runner.run_audits(businesses, workers=1, deep=False,
                             journal_path=path, log=lambda _message: None)

    assert rows[0]["status"] == "audit deadline exceeded"
    assert rows[0]["score"] == 0
    assert rows[0]["warm"] is False
    assert rows[0]["tier"] == ""
    assert rows[0]["hook"] == ""
    assert rows[1]["status"] == "ok"
    records = runner.Journal(
        path, compatibility.run_fingerprint(deep=False)).done_keys()
    assert len(records) == 2
    assert {record["status"] for record in records.values()} == {
        "audit deadline exceeded", "ok"}


def test_deadline_stops_contact_page_and_follower_requests(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(deadlines.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(config, "BUSINESS_TIMEOUT_SECONDS", 1)
    calls = {"render": [], "followers": []}

    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def render(self, url):
            calls["render"].append(url)
            clock.advance(1)
            html = ("<a href='/contact'>Contact</a>"
                    "<a href='https://www.instagram.com/alpha.test'>Instagram</a>")
            return html, url, 0.1, None

        def followers(self, url):
            calls["followers"].append(url)
            return 10

    _install_browser(monkeypatch, FakeBrowser)
    rows = runner.run_audits(
        [{"place_id": "A", "name": "Alpha", "website": "https://alpha.test"}],
        workers=1, deep=True, log=lambda _message: None)

    assert rows[0]["status"] == "audit deadline exceeded"
    assert calls == {"render": ["https://alpha.test"], "followers": []}


def test_social_only_business_deadline_prevents_follower_lookup(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(deadlines.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(config, "BUSINESS_TIMEOUT_SECONDS", 1)
    follower_calls = []

    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def find_social(self, _name, _region):
            clock.advance(1)
            return {"instagram": "https://instagram.test/alpha"}

        def followers(self, url):
            follower_calls.append(url)
            return 10

    _install_browser(monkeypatch, FakeBrowser)
    rows = runner.run_audits(
        [{"place_id": "A", "name": "Alpha", "website": ""}],
        workers=1, social_only=True, log=lambda _message: None)

    assert rows[0]["status"] == "audit deadline exceeded"
    assert follower_calls == []


def test_single_site_audit_deadline_returns_nonzero_without_report(
        tmp_path, monkeypatch, capsys):
    clock = FakeClock()
    monkeypatch.setattr(deadlines.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(config, "BUSINESS_TIMEOUT_SECONDS", 4)
    received = []

    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    _install_browser(monkeypatch, FakeBrowser)

    def expire(_browser, _business, cache=None, deep=True, deadline=None):
        received.append(deadline.remaining())
        clock.advance(4)
        deadline.check()

    def unexpected_report(*_args, **_kwargs):
        raise AssertionError("expired audit must not build a report")

    monkeypatch.setattr(leadscan.checks, "audit_business", expire)
    monkeypatch.setattr(leadscan.audit_report, "build", unexpected_report)
    report_dir = tmp_path / "reports"

    result = leadscan.main([
        "--audit", "https://alpha.test", "--no-cache",
        "--reports", str(report_dir)])

    output = capsys.readouterr().out
    assert result == 1
    assert received == [4]
    assert "Could not review the site: audit deadline exceeded" in output
    assert "Traceback" not in output
    assert not report_dir.exists()


def test_navigation_retry_and_settle_are_capped_by_remaining_budget(monkeypatch):
    clock = FakeClock()
    budget = deadlines.Deadline(5, clock=clock.monotonic)
    navigation_timeouts = []
    settle_waits = []

    class Response:
        status = 200

    class Page:
        def __init__(self, first):
            self.first = first
            self.url = "https://alpha.test"

        def goto(self, _url, timeout, wait_until):
            navigation_timeouts.append(timeout)
            if self.first:
                clock.advance(2)
                raise RuntimeError("Timeout while loading")
            return Response()

        def wait_for_timeout(self, milliseconds):
            settle_waits.append(milliseconds)

        def content(self):
            return "<html><body>ok</body></html>"

        def close(self):
            pass

    pages = iter([Page(True), Page(False)])
    browser = browser_module.Browser(polite_delay=0, respect_robots=False)
    browser.context = types.SimpleNamespace(new_page=lambda: next(pages))
    monkeypatch.setattr(config, "NAV_TIMEOUT_MS", 20_000)
    monkeypatch.setattr(config, "RETRY_TIMEOUT_MS", 30_000)
    monkeypatch.setattr(config, "RENDER_RETRIES", 1)
    monkeypatch.setattr(config, "SETTLE_SECONDS", 1.2)

    html, _url, _load, error = browser.render(
        "https://alpha.test", deadline=budget)

    assert error is None
    assert html
    assert navigation_timeouts == [5_000, 3_000]
    assert settle_waits == [1_200]


def test_polite_wait_is_capped_by_remaining_budget(monkeypatch):
    clock = FakeClock()
    waits = []
    budget = deadlines.Deadline(3, clock=clock.monotonic)
    browser = browser_module.Browser(polite_delay=10, respect_robots=False)
    browser._last_hit_by_host["alpha.test"] = 0
    monkeypatch.setattr(browser_module.time, "time", clock.monotonic)

    def advance(seconds):
        waits.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(browser_module.time, "sleep", advance)
    with pytest.raises(deadlines.AuditDeadlineExceeded):
        browser._wait_politely("https://alpha.test", deadline=budget)
    assert waits == [3]


def test_timeout_setting_invalidates_cache_and_run_fingerprints(monkeypatch):
    original = (compatibility.cache_fingerprint(), compatibility.run_fingerprint())
    monkeypatch.setattr(config, "BUSINESS_TIMEOUT_SECONDS",
                        config.BUSINESS_TIMEOUT_SECONDS + 1)
    changed = (compatibility.cache_fingerprint(), compatibility.run_fingerprint())
    assert changed[0] != original[0]
    assert changed[1] != original[1]


def test_journal_failure_on_final_business_is_fatal(monkeypatch):
    _install_successful_worker(monkeypatch)

    def fail_append(*_args, **_kwargs):
        raise runner.JournalPersistenceError("disk full")

    monkeypatch.setattr(runner.Journal, "append", fail_append)
    with pytest.raises(runner.AuditRunError, match="Journal persistence failed.*disk full"):
        runner.run_audits(
            [{"place_id": "A", "name": "Alpha"}], workers=1,
            journal_path="run.jsonl", log=lambda _message: None)


def test_journal_flush_error_keeps_original_details(monkeypatch):
    class BrokenJournalFile:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def write(self, _value):
            pass

        def flush(self):
            raise OSError("device is read-only")

    monkeypatch.setattr(
        builtins, "open", lambda *_args, **_kwargs: BrokenJournalFile())
    with pytest.raises(
            runner.JournalPersistenceError,
            match="could not append run.jsonl.*device is read-only"):
        runner.Journal("run.jsonl").append(
            "pid:A", _result({"name": "Alpha"}), business={"name": "Alpha"})


def test_journal_rows_before_failure_remain_readable(tmp_path, monkeypatch):
    _install_successful_worker(monkeypatch)
    path = str(tmp_path / "run.jsonl")
    original_append = runner.Journal.append
    calls = {"count": 0}

    def fail_second(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise runner.JournalPersistenceError("permission denied")
        return original_append(self, *args, **kwargs)

    monkeypatch.setattr(runner.Journal, "append", fail_second)
    businesses = [{"place_id": "A", "name": "Alpha"},
                  {"place_id": "B", "name": "Beta"}]

    with pytest.raises(runner.AuditRunError, match="Journal persistence failed"):
        runner.run_audits(businesses, workers=1, journal_path=path,
                          log=lambda _message: None)

    records = runner.Journal(path).done_keys()
    assert len(records) == 1
    assert next(iter(records.values()))["name"] == "Alpha"
    assert len((tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_journal_failure_makes_cli_nonzero_without_output_writers(
        tmp_path, monkeypatch, capsys):
    source = tmp_path / "businesses.csv"
    source.write_text("place_id,name,website\nA,Alpha,https://alpha.test\n",
                      encoding="utf-8")
    output = tmp_path / "leads.csv"

    def fatal_audit(*_args, **_kwargs):
        raise runner.AuditRunError("Journal persistence failed: disk full")

    def unexpected_writer(*_args, **_kwargs):
        raise AssertionError("output writer must not be called")

    monkeypatch.setattr(leadscan, "audit_all", fatal_audit)
    monkeypatch.setattr(leadscan.report, "write_all", unexpected_writer)
    result = leadscan.main([
        "--input", str(source), "--no-cache", "--out", str(output)])

    assert result != 0
    assert "Audit failed: Journal persistence failed" in capsys.readouterr().out
    assert not output.exists()


def test_cache_write_failure_warns_once_without_failing_valid_audit(
        tmp_path, monkeypatch):
    messages = []
    cache = cache_module.Cache(directory=str(tmp_path), log=messages.append)

    def fail_replace(_source, _target):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(cache_module.os, "replace", fail_replace)

    class FakeBrowser:
        def render(self, url):
            html = ("<html><body><form action='/enquiry'>"
                    "<input type='email' name='email'></form></body></html>")
            return html, url, 0.1, None

    row = checks.audit_business(
        FakeBrowser(), {"name": "Alpha", "website": "https://alpha.test"},
        cache=cache, deep=False)
    cache.put("render", "https://beta.test", {"value": 2})

    assert row["status"] == "ok"
    assert len(messages) == 1
    assert "cache write failed" in messages[0]
    assert "read-only cache" in messages[0]
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_cache_file_is_a_quiet_miss(tmp_path):
    messages = []
    cache = cache_module.Cache(directory=str(tmp_path), log=messages.append)
    assert cache.get("render", "absent") is None
    assert messages == []


def test_non_missing_cache_read_error_is_reported(tmp_path, monkeypatch):
    messages = []
    cache = cache_module.Cache(directory=str(tmp_path), log=messages.append)
    path = cache._path("render", "denied")
    original_open = builtins.open

    def denied_open(file, *args, **kwargs):
        if os.fspath(file) == path:
            raise PermissionError("access denied")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied_open)
    assert cache.get("render", "denied") is None
    assert len(messages) == 1
    assert "cache read failed" in messages[0]
    assert "access denied" in messages[0]


def test_invalid_utf8_cache_is_preserved_as_a_miss(tmp_path):
    cache = cache_module.Cache(directory=str(tmp_path))
    path = cache._path("render", "https://invalid-utf8.test")
    original = b"\xff\xfe{not-json"
    with open(path, "wb") as handle:
        handle.write(original)

    assert cache.get("render", "https://invalid-utf8.test") is None
    cache.put("render", "https://invalid-utf8.test", {"fresh": True})
    assert open(path, "rb").read() == original


def test_invalid_cache_timestamps_are_preserved_as_misses(tmp_path):
    cache = cache_module.Cache(directory=str(tmp_path))
    invalid_timestamps = [
        "yesterday", None, float("nan"), float("inf"), float("-inf"),
        cache_module.time.time() + 3600,
    ]

    for index, stored_at in enumerate(invalid_timestamps):
        key = f"https://invalid-{index}.test"
        path = cache._path("render", key)
        record = {
            "_pipeline_version": config.PIPELINE_SCHEMA_VERSION,
            "_cache_fingerprint": cache.fingerprint,
            "stored_at": stored_at,
            "key": key,
            "value": {"findings": {"has_ad_tags": False}},
        }
        original = json.dumps(record).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(original)

        assert cache.get("render", key) is None
        cache.put("render", key, {"fresh": True})
        assert open(path, "rb").read() == original


def test_malformed_cache_timestamp_does_not_fail_business_audit(tmp_path):
    cache = cache_module.Cache(directory=str(tmp_path))
    website = "https://malformed-cache.test"
    path = cache._path("render", website)
    record = {
        "_pipeline_version": config.PIPELINE_SCHEMA_VERSION,
        "_cache_fingerprint": cache.fingerprint,
        "stored_at": "not-a-timestamp",
        "key": website,
        "value": {},
    }
    original = json.dumps(record).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(original)

    class FakeBrowser:
        def render(self, url):
            html = ("<html><body><form action='/enquiry'>"
                    "<input type='email' name='email'></form></body></html>")
            return html, url, 0.1, None

    row = checks.audit_business(
        FakeBrowser(), {"name": "Alpha", "website": website},
        cache=cache, deep=False)

    assert row["status"] == "ok"
    assert open(path, "rb").read() == original


def test_journal_failure_stops_dequeues_before_browser_teardown_finishes(
        tmp_path, monkeypatch):
    b_started = threading.Event()
    allow_b_to_finish = threading.Event()
    b_appended = threading.Event()
    teardown_started = threading.Event()
    release_teardown = threading.Event()
    failure_thread = {}
    started = []
    started_lock = threading.Lock()

    class BlockingBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if threading.get_ident() == failure_thread.get("ident"):
                teardown_started.set()
                assert release_teardown.wait(5)

    _install_browser(monkeypatch, BlockingBrowser)

    def controlled_audit(_browser, business, cache=None, deep=True, deadline=None):
        with started_lock:
            started.append(business["place_id"])
        if business["place_id"] == "A":
            assert b_started.wait(5)
        elif business["place_id"] == "B":
            b_started.set()
            assert allow_b_to_finish.wait(5)
        return _result(business)

    monkeypatch.setattr(runner.checks, "audit_business", controlled_audit)
    original_append = runner.Journal.append

    def fail_alpha(self, key, row, **kwargs):
        if key == "pid:A":
            failure_thread["ident"] = threading.get_ident()
            raise runner.JournalPersistenceError("disk full during flush")
        result = original_append(self, key, row, **kwargs)
        if key == "pid:B":
            b_appended.set()
        return result

    monkeypatch.setattr(runner.Journal, "append", fail_alpha)
    errors = []

    def run():
        try:
            runner.run_audits(
                [{"place_id": value, "name": value} for value in "ABC"],
                workers=2, journal_path=str(tmp_path / "run.jsonl"),
                log=lambda _message: None)
        except Exception as error:
            errors.append(error)

    run_thread = threading.Thread(target=run)
    run_thread.start()
    try:
        assert teardown_started.wait(5)
        allow_b_to_finish.set()
        assert b_appended.wait(5)
        assert set(started) == {"A", "B"}
    finally:
        release_teardown.set()
        allow_b_to_finish.set()
        run_thread.join(5)

    assert not run_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], runner.AuditRunError)
    assert "disk full during flush" in str(errors[0])
    records = runner.Journal(str(tmp_path / "run.jsonl")).done_keys()
    assert len(records) == 1
    assert next(iter(records.values()))["name"] == "B"


def _install_successful_worker(monkeypatch):
    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    _install_browser(monkeypatch, FakeBrowser)

    def successful_audit(_browser, business, cache=None, deep=True, deadline=None):
        return _result(business)

    monkeypatch.setattr(runner.checks, "audit_business", successful_audit)
