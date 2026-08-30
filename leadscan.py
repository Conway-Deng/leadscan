"""
leadscan.py -- the command you run.

Pipeline:
    sources  -> sweep many searches, remove duplicates, pull phone + reviews
    checks   -> render each site, detect the funnel and the ad tags, score it
    report   -> a ranked call sheet as CSV, HTML and XLSX

USAGE
    # Test with a CSV. No key needed.
    python leadscan.py --input sample_businesses.csv

    # Full Singapore sweep. Needs GOOGLE_PLACES_API_KEY in .env.
    python leadscan.py --sweep sg-interior --want 40

    # Firms with no website at all.
    python leadscan.py --sweep sg-car --social-only

    # List the sweeps that are defined.
    python leadscan.py --list-sweeps
"""

import argparse
import datetime
import os
import sys

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import audit_report
import cache as cache_module
import checks
import config
import deadlines
import outreach
import report
import runner
import scoring
import sources

load_dotenv()

SWEEPS = config.SWEEPS      # kept at this name for older scripts


class Logger:
    """Print to the screen and, when asked, append to a log file."""

    def __init__(self, path=None):
        self.path = path
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def __call__(self, message):
        print(message, flush=True)
        if self.path:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} {message}\n")
            except OSError:
                pass


def audit_all(businesses, social_only=False, cache=None, log=print,
              workers=1, deep=True, journal_path=None, respect_robots=True,
              resume_journal=True):
    """Render and score every business. Give back all the result rows."""
    return runner.run_audits(businesses, social_only=social_only, cache=cache,
                             log=log, workers=workers, deep=deep,
                             journal_path=journal_path,
                             respect_robots=respect_robots,
                             resume_journal=resume_journal)


def _apply_exclusions(businesses, path):
    """
    Remove firms that appear in a previously written call sheet.

    The match uses the same identity rules as de-duplication, so a firm is
    recognised again even when Google returns a different branch name or the
    phone number is written in another shape.
    """
    previous = sources.from_csv(path)
    banned = set()
    for record in previous:
        banned.update(sources._identity_keys(record))
    kept = [b for b in businesses
            if not banned.intersection(sources._identity_keys(b))]
    return kept, len(businesses) - len(kept)


def _audit_one(args, log):
    """
    Review one website and write the prospect report for it.

    This is the on-demand version of the sweep: somebody enquires, or you have
    one firm in mind, and you want the review in front of them today. It is
    also how you check what the report looks like before you send any.
    """
    from browser import Browser

    url = args.audit.strip()
    business = {"name": args.audit, "website": url, "phone": "",
                "review_count": None, "place_id": ""}
    store = cache_module.Cache(enabled=not args.no_cache,
                               respect_robots=not args.ignore_robots, log=log)

    log(f"Reviewing {url} ...")
    try:
        with Browser(log=log, respect_robots=not args.ignore_robots) as browser:
            business_deadline = deadlines.Deadline(
                config.BUSINESS_TIMEOUT_SECONDS)
            row = checks.audit_business(
                browser, business, cache=store, deep=not args.shallow,
                deadline=business_deadline)
    except deadlines.AuditDeadlineExceeded:
        log("Could not review the site: audit deadline exceeded")
        return 1

    if row["status"] != "ok":
        log(f"Could not review the site: {row['status']}")
        return 1

    findings = row.get("_findings") or {}
    # Name the report after the domain, because a URL is not a business name.
    stem = audit_report.safe_filename(sources.root_domain(url) or "site")
    folder = args.reports or "reports"
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, stem + ".html")
    stamp = datetime.date.today().isoformat()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(audit_report.build(row, findings, stamp=stamp))

    log("")
    log(f"Report written to {path}")
    log(f"Score {row['score']}  tier {row.get('tier') or 'none'}")
    log(f'Opening line: "{row["hook"]}"')
    if not audit_report.brand()["name"]:
        log("")
        log("Tip: set LEADSCAN_BRAND_NAME and LEADSCAN_BRAND_CONTACT in .env "
            "to put your own name on the report.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Find quiet businesses that invest in being seen but cannot "
                    "capture the leads.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="CSV of businesses (columns: name, website)")
    source.add_argument("--sweep", choices=sorted(config.SWEEPS),
                        help="Named search sweep")
    source.add_argument("--audit", metavar="URL",
                        help="Review ONE website and write the prospect report. "
                             "Use this for an inbound enquiry or a named target")
    parser.add_argument("--list-sweeps", action="store_true",
                        help="Show every sweep and its search terms, then stop")
    parser.add_argument("--want", type=int, default=40,
                        help="How many leads to keep (default 40)")
    parser.add_argument("--cap", type=int, default=200,
                        help="Most firms to scan in a sweep (default 200)")
    parser.add_argument("--out", default="warm_leads.csv",
                        help="Output path. The .html and .xlsx files sit beside it")
    parser.add_argument("--social-only", action="store_true",
                        help="Target businesses with NO website, only Instagram or "
                             "TikTok. Slower, because each firm needs a web search")
    parser.add_argument("--include-cool", action="store_true",
                        help="Also keep quiet firms with a defect but no proof "
                             "that they market themselves")
    parser.add_argument("--workers", type=int, default=3, metavar="N",
                        help="How many sites to render at the same time "
                             "(default 3). Each worker uses about 200 MB")
    parser.add_argument("--shallow", action="store_true",
                        help="Read the home page only. By default the scan also "
                             "follows the first contact link, because most firms "
                             "keep the enquiry form on /contact")
    parser.add_argument("--exclude", metavar="PATH",
                        help="CSV of firms already contacted. Matching firms are "
                             "dropped before any site is rendered")
    parser.add_argument("--journal", metavar="PATH",
                        help="Append every finished firm to this JSON Lines file. "
                             "Re-running the command skips whatever is in it")
    parser.add_argument("--reports", metavar="DIR", nargs="?", const="reports",
                        help="Write one prospect-facing website review per lead "
                             "into DIR (default: reports/). This is the thing "
                             "you send the business owner")
    parser.add_argument("--crm", metavar="PATH", nargs="?", const="crm_import.csv",
                        help="Also write a CSV shaped for Instantly, Lemlist, "
                             "Smartlead or HubSpot. Only leads with a usable "
                             "email address are included")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Read sites even when robots.txt says not to. "
                             "The default is to obey it")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore cache and journal replay; fetch and audit "
                             "everything again. New journal rows are still "
                             "appended for crash recovery")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete the cache folder before the run")
    parser.add_argument("--log", metavar="PATH", help="Append the run log to a file")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    log = Logger(args.log)

    if args.list_sweeps:
        for name in sorted(config.SWEEPS):
            log(f"\n{name}  ({len(config.SWEEPS[name])} searches)")
            for query in config.SWEEPS[name]:
                log(f"    {query}")
        return 0

    if args.audit:
        return _audit_one(args, log)

    if not args.input and not args.sweep:
        log("Choose a source: --input FILE.csv, --sweep NAME, or --audit URL "
            "(--list-sweeps shows the sweep names).")
        return 2

    if args.clear_cache:
        import shutil
        shutil.rmtree(config.CACHE_DIR, ignore_errors=True)
        log(f"Cache cleared: {config.CACHE_DIR}")

    store = cache_module.Cache(enabled=not args.no_cache,
                               respect_robots=not args.ignore_robots, log=log)

    # --- Source the businesses ---
    if args.input:
        if not os.path.exists(args.input):
            log(f"File not found: {args.input}")
            return 2
        businesses = sources.from_csv(args.input)
        businesses, removed = sources.dedupe(businesses)
        if removed:
            log(f"{removed} duplicate rows removed from the input file.")
    else:
        key = os.getenv("GOOGLE_PLACES_API_KEY")
        log("Sourcing businesses from Google Places...")
        try:
            businesses = sources.sweep(config.SWEEPS[args.sweep], key,
                                       cap=args.cap, cache=store, log=log)
        except (ValueError, RuntimeError) as error:
            log(str(error))
            return 1

    if not businesses:
        log("No businesses found.")
        return 1

    # --- Drop firms that were already contacted. Calling somebody twice costs
    #     more goodwill than a missed lead costs money. ---
    if args.exclude:
        if not os.path.exists(args.exclude):
            log(f"Exclusion file not found: {args.exclude}")
            return 2
        businesses, dropped = _apply_exclusions(businesses, args.exclude)
        log(f"{dropped} firms dropped as already contacted.")
        if not businesses:
            log("Every firm in this sweep was already contacted.")
            return 1

    # --- --social-only keeps only the firms with no website ---
    if args.social_only:
        total = len(businesses)
        businesses = [b for b in businesses if not (b.get("website") or "").strip()]
        log(f"Social-only mode: {len(businesses)} of {total} firms have no website.")
        if not businesses:
            log("No firms without a website in this sweep.")
            return 1
        log(f"\nSearching and scoring {len(businesses)} firms "
            f"(a few seconds each)...\n")
    else:
        log(f"\nRendering and scoring {len(businesses)} sites "
            f"(a few seconds each)...\n")

    journal_path = args.journal
    if journal_path is None:
        stem, _ = os.path.splitext(args.out)
        journal_path = stem + ".journal.jsonl"

    try:
        results = audit_all(businesses, social_only=args.social_only, cache=store,
                            log=log, workers=args.workers, deep=not args.shallow,
                            journal_path=journal_path,
                            respect_robots=not args.ignore_robots,
                            resume_journal=not args.no_cache)
    except runner.AuditRunError as error:
        log(f"Audit failed: {error}")
        return 1

    # --- Write the call sheet ---
    leads = report.select_leads(results, args.want, include_cool=args.include_cool)
    # Write the first message for every lead, so nobody has to write forty.
    marque = audit_report.brand()
    outreach.add_drafts(leads, marque["name"], marque["contact"])
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    written = report.write_all(leads, args.out, stamp=stamp)

    counts = {}
    for row in leads:
        tier = row.get("tier") or "cool"
        counts[tier] = counts.get(tier, 0) + 1
    skipped = sum(1 for r in results if r["disqualified"])

    log("")
    log(f"Done. {len(leads)} leads kept from {len(results)} firms scanned.")
    log("  " + ", ".join(
        f"{counts.get(tier, 0)} {tier}" for tier in ("hot", "warm", "cool")
    ))
    log(f"  {skipped} firms skipped (too large a following).")
    log(f"  {store.summary()}")
    for kind, path in written.items():
        log(f"  {kind:<5} {path}")

    if args.reports:
        audit_report.write_reports(leads, args.reports, stamp=stamp, log=log)

    if args.crm:
        path, kept = outreach.write_crm_csv(leads, args.crm)
        log(f"  crm   {path}  ({kept} of {len(leads)} leads have a usable "
            f"email address)")

    if leads:
        top = leads[0]
        log("")
        log(f"Top lead: {top['name']}  (score {top['score']}, "
            f"{top.get('tier') or 'cool'})  {top['phone']}")
        log(f'  Opening line: "{top["hook"]}"')
        log("")
        log("Open the .html file in a browser to start calling.")
    else:
        log("\nNo leads matched. Try --include-cool, or a different sweep.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
