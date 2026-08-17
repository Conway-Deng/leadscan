"""
make_readable.py
----------------
Rebuild the call sheet from a CSV that already exists.

`leadscan.py` writes the HTML and the XLSX for you, so you rarely need this. It
is useful after you edit the CSV by hand, for example to delete a firm you have
already called.

    python make_readable.py                     # reads warm_leads.csv
    python make_readable.py my_leads.csv
"""

import csv
import datetime
import sys

import report
import scoring


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        row["score"] = _int(row.get("score"), 0)
        row["review_count"] = _int(row.get("review_count"), None)
        row["instagram_followers"] = _int(row.get("instagram_followers"), None)
        # A value that was protected against a spreadsheet formula gets its
        # leading apostrophe removed again for display.
        for key in ("name", "hook", "phone"):
            value = row.get(key) or ""
            if value[:1] == "'":
                row[key] = value[1:]
    rows.sort(key=scoring.sort_key)
    return rows


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    source = argv[0] if argv else "warm_leads.csv"
    try:
        rows = load(source)
    except FileNotFoundError:
        print(f"File not found: {source}")
        return 2

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    written = report.write_all(rows, source, stamp=stamp)
    for kind, path in written.items():
        print(f"{kind:<5} {path}")
    print(f"\n{len(rows)} leads. Open the .html file in a browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
