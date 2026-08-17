"""
runner.py
---------
Runs the audit over many businesses, in parallel, and never loses work.

WHY PARALLEL
One site takes about five seconds to render, and the contact-page check can
double that. A 200-firm sweep in one thread takes over half an hour, and the
whole time the machine is doing nothing but waiting for other people's servers.
Different firms are on different servers, so there is no reason to wait for one
before starting the next.

Playwright's synchronous API belongs to the thread that created it, so each
worker thread starts its own browser. A browser costs roughly 200 MB, which is
why the default is three workers and not twenty.

WHY A JOURNAL
A sweep is long, and a long job gets interrupted: a laptop sleeps, a network
drops, somebody presses Ctrl-C. Every finished row is appended to a JSON Lines
journal as soon as it exists. Start the same command again and every firm
already in the journal is skipped, so no work and no API spend is repeated.
The journal is also the record of what a run really saw, which the CSV is not,
because the CSV holds only the leads that were kept.
"""

import json
import os
import queue
import threading

import checks


def business_key(business):
    """A stable name for one business, used by the journal to skip repeats."""
    place_id = (business.get("place_id") or "").strip()
    if place_id:
        return "pid:" + place_id
    website = (business.get("website") or "").strip().lower()
    return f"nw:{(business.get('name') or '').strip().lower()}|{website}"


class Journal:
    """Append-only record of every audited business, one JSON object per line."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        if path:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)

    def done_keys(self):
        """Keys already recorded, so they can be skipped."""
        if not self.path or not os.path.exists(self.path):
            return {}
        found = {}
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue        # a half-written last line, ignore it
                key = record.get("_key")
                if key:
                    found[key] = record
        return found

    def append(self, key, row):
        if not self.path:
            return
        record = dict(row)
        record["_key"] = key
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, default=str) + "\n")
                    handle.flush()
            except OSError:
                pass


def run_audits(businesses, social_only=False, cache=None, log=print,
               workers=3, deep=True, journal_path=None, respect_robots=True):
    """
    Audit every business and give back the result rows.

    Rows come back in the order the businesses were given, not the order they
    finished, so two runs of the same input produce the same file.
    """
    journal = Journal(journal_path)
    already = journal.done_keys()
    if already:
        log(f"Journal holds {len(already)} finished firms. They will be skipped.")

    todo = []
    results = [None] * len(businesses)
    for index, business in enumerate(businesses):
        key = business_key(business)
        if key in already:
            row = dict(already[key])
            row.pop("_key", None)
            results[index] = row
        else:
            todo.append((index, key, business))

    if not todo:
        log("Everything was already in the journal.")
        return [row for row in results if row]

    workers = max(1, min(workers, len(todo)))
    work = queue.Queue()
    for item in todo:
        work.put(item)

    counter = {"done": 0}
    counter_lock = threading.Lock()
    total = len(todo)

    def worker(worker_number):
        from browser import Browser
        try:
            with Browser(log=lambda m: None,
                         respect_robots=respect_robots) as browser:
                while True:
                    try:
                        index, key, business = work.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        if social_only:
                            row = checks.audit_social_only(browser, business, cache=cache)
                        else:
                            row = checks.audit_business(browser, business,
                                                        cache=cache, deep=deep)
                    except Exception as error:
                        # One bad site must never stop the run.
                        row = _error_row(business, error)
                    results[index] = row
                    journal.append(key, row)
                    with counter_lock:
                        counter["done"] += 1
                        _report(log, counter["done"], total, row)
                    work.task_done()
        except Exception as error:
            log(f"  worker {worker_number} stopped: {str(error)[:120]}")

    threads = [threading.Thread(target=worker, args=(n + 1,), daemon=True)
               for n in range(workers)]
    log(f"Using {workers} worker(s).\n")
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        log("\nStopped. Everything finished so far is in the journal. "
            "Run the same command again to carry on.")
    return [row for row in results if row]


def _report(log, done, total, row):
    if row["disqualified"]:
        mark = "SKIP"
    elif row["warm"]:
        mark = (row.get("tier") or "warm").upper()[:4]
    else:
        mark = "    "
    log(f"  [{done:>3}/{total}] {row['score']:>3} {mark:<4} {row['name']}")


def _error_row(business, error):
    return {
        "score": 0, "tier": "", "warm": False, "disqualified": False,
        "name": business.get("name", ""), "phone": business.get("phone", ""),
        "address": business.get("address", ""),
        "review_count": business.get("review_count"),
        "rating": business.get("rating"),
        "opening_hours": " | ".join(business.get("opening_hours") or []),
        "instagram_followers": None,
        "instagram": "", "facebook": "", "tiktok": "", "email": "",
        "ad_tags": "", "capture_methods": "", "load_seconds": None,
        "hook": "", "reasons": f"scan failed: {str(error)[:120]}",
        "website": business.get("website", ""), "final_url": "",
        "status": "scan failed",
    }
