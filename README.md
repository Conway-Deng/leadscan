# LeadScan

Finds businesses that **invest in being seen but cannot capture the leads** —
the profile that says yes to a cold call from a small marketing agency. It
gives you a ranked call sheet: who to call, their phone number, and the first
sentence to say.

## Quick start (no API key)

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium        # one time, about 150 MB

python leadscan.py --input sample_businesses.csv
```

Open `warm_leads.html` in a browser. Hottest lead first, with a click-to-call
phone number and a tick box for each call.

## Find real businesses (needs a Google Places key)

1. In the Google Cloud console, enable **Places API (New)** for your project.
2. Copy `.env.example` to `.env` and paste your `GOOGLE_PLACES_API_KEY`.
3. Run a sweep:

```bash
python leadscan.py --sweep sg-interior --want 40
python leadscan.py --list-sweeps            # show all sweeps and their searches
```

## What comes out

Three files, side by side:

| File | Use it for |
|---|---|
| `warm_leads.html` | The call sheet. Open it and start calling. |
| `warm_leads.xlsx` | The same list as a spreadsheet, with filters. |
| `warm_leads.csv` | The machine-readable record. |

## How a lead is scored

A lead is good when all three of these are true:

1. **Quiet** — few Google reviews, so the firm has little organic reach.
2. **Invests in being seen** — an advertising-related tag, or content on
   Instagram or TikTok.
3. **Broken funnel** — no way to capture a lead, a slow site, no HTTPS, or no
   website at all.

A firm with a large social following is skipped. It already owns an audience.

### Tiers

| Tier | Meaning |
|---|---|
| `hot` | An advertising-related tag was found AND the funnel leaks. The tag proves installed infrastructure, not a currently active campaign. |
| `warm` | The firm builds an audience on Instagram or TikTok AND the funnel leaks. |
| `cool` | A quiet firm with a real defect, but no proof that it markets itself. Hidden unless you pass `--include-cool`. |

### Points

| Signal | Meaning | Points |
|---|---|---|
| No form, booking link, WhatsApp link or click-to-call | Cannot capture a lead | +40 |
| Quiet (30 reviews or fewer) | Small firm, likely needs help | +20 |
| Advertisement tag found | Advertising infrastructure installed | +15 |
| No mobile viewport | Loses phone traffic | +15 |
| Slow (over 5 s) | Loses clicks | +15 |
| Instagram or TikTok, no advertisement tag | Organic effort | +10 |
| No HTTPS | The browser warns visitors | +10 |
| Over 100 reviews | Already has traction | −10 |

Change any threshold in `config.py`, or with an environment variable, without
touching the engine:

```bash
LEADSCAN_QUIET_REVIEWS=20 python leadscan.py --sweep sg-car
```

### What counts as an advertisement tag

These tags show that advertising-related infrastructure is installed. They do
not prove that a campaign is currently active:

* Meta Pixel (`fbq('init', ...)`, `fbevents.js`, the `/tr?id=` image)
* Google Ads (`AW-...`, `google_conversion_id`, `googleadservices.com`)
* TikTok, LinkedIn, Microsoft, Pinterest, Snap and X pixels

Google Analytics and Google Tag Manager do **not** count. Nearly every site has
them, and they show only that somebody measures traffic, not that somebody buys
it. They are still recorded in the `reasons` column, because "you measure the
traffic but you cannot capture it" is a useful thing to know on the call.

## Command reference

| Option | What it does |
|---|---|
| `--input FILE.csv` | Read a hand-made list. Columns: `name`, `website`, and optionally `phone`, `review_count`. |
| `--sweep NAME` | Run a named search sweep from `config.py`. |
| `--list-sweeps` | Show every sweep and its search terms. |
| `--want N` | Keep the best N leads (default 40). |
| `--cap N` | Scan at most N firms in a sweep (default 200). |
| `--out PATH` | Where to write. The `.html` and `.xlsx` go beside it. |
| `--social-only` | Target firms with NO website, only Instagram or TikTok. |
| `--include-cool` | Also keep quiet firms that show no marketing effort. |
| `--workers N` | Render N sites at the same time (default 3). Each worker uses about 200 MB. |
| `--shallow` | Read the home page only. See "The contact page" below. |
| `--exclude PATH` | A CSV of firms already contacted. They are dropped before any site is rendered. |
| `--journal PATH` | Append every finished firm to a JSON Lines file. See "If a run stops". |
| `--audit URL` | Review ONE website and write the prospect report. For an inbound enquiry or a named target. |
| `--reports [DIR]` | Write one prospect-facing website review per lead (default `reports/`). |
| `--crm [PATH]` | Also write a CSV shaped for Instantly, Lemlist, Smartlead or HubSpot. |
| `--ignore-robots` | Read sites even when `robots.txt` says not to. The default is to obey it. |
| `--no-cache` | Bypass cache reads and journal replay; audit everything again. |
| `--clear-cache` | Delete the cache folder first. |
| `--log PATH` | Append the run log to a file. |

## The report you send the prospect

This is the thing that turns a cold call into a warm one, and it is what every
paid tool in this market charges $29 to $299 a month for.

```bash
python leadscan.py --sweep sg-interior --reports          # one per lead
python leadscan.py --audit https://somefirm.com.sg        # one, on demand
```

Each report is a single self-contained HTML file. Open it in a browser and
print to PDF, or send the file. It says what is wrong, what it costs the owner
in plain language, and what to do about each item — plus what is already
working, because a report that is all bad news reads as a sales pitch.

Put your own name on it with these in `.env`:

```
LEADSCAN_BRAND_NAME=Nixon Media
LEADSCAN_BRAND_TAGLINE=Interior design marketing, Singapore
LEADSCAN_BRAND_CONTACT=nixon@example.sg · +65 9123 4567
LEADSCAN_BRAND_COLOUR=#0f766e
LEADSCAN_BRAND_CTA=Happy to walk through any of this on a short call.
```

**Every report carries a "what this review did not check" section.** That is
deliberate, and it is the opposite of what the generic audit tools do. The
owner will forward the report to whoever built their site. A report that
overclaims gets pulled apart in one reply and costs the meeting; a report that
states its own limits survives.

A firm with no website, or one whose site did not load, gets no report. There
is nothing to review, and "you have no website" is a worse opening than a call.

## Hosted public audit widget

LeadScan's public website review is live at
<https://enchanting-alpaca-de0ed3.netlify.app>. The production path is:

```text
Netlify static frontend
    -> Render FastAPI worker
    -> LeadScan audit engine and Chromium
    -> durable Postgres lead capture
    -> customer report
```

The visitor enters a website address, an optional contact name, and a required
work email. The frontend sends exactly `url`, `contact_name`, and `email` to
`POST /api/audit`. The customer-facing result shows the score, reviewed website,
and full website review inside a sandboxed iframe.

### Frontend components

* `site/index.html`, `site/app.js`, `site/styles.css`: Static frontend assets without build tools or external script frameworks.
* `netlify.toml`: Netlify deployment headers, Content Security Policy, and caching directives.
* `site/index.html` configures the production Render worker origin in `meta[name="leadscan-api-origin"]`. The client validates that exact HTTPS origin and always appends the fixed `/api/audit` path; arbitrary API paths cannot be configured from the frontend.

### Worker API & private lead storage

* `public_api.py`: FastAPI application serving strictly `POST /api/audit` (automatic OpenAPI/docs endpoints disabled).
* Requires the exact three-field schema (`url`, `contact_name`, `email`). Requests without contact details are rejected before auditing.
* `DATABASE_URL` selects the shared `PostgresLeadStore` and is the production configuration. It creates `public_leads` safely, uses parameterized inserts, commits each successful write, and returns the inserted row ID.
* `LEADSCAN_LEAD_DB_PATH` selects the private SQLite fallback for local development or a single-instance deployment backed by a persistent volume. SQLite uses a busy timeout and private file mode enforcement.
* Postgres takes deterministic precedence when both persistence settings are present.
* Lead capture is fail-closed: if persistence fails, the API returns `lead_capture_failed` and does not return a successful report. There is no public lead-read endpoint.
* Worker CORS middleware validates and allows strictly one exact HTTPS origin via `LEADSCAN_ALLOWED_ORIGIN`. Wildcard origins (`*`) and credentials are not permitted. CORS is a browser transport control only and is not treated as authentication.
* Local Chromium is supported directly. The source also supports an optional Browserless CDP connection when both `BROWSERLESS_ENDPOINT` and `BROWSERLESS_TOKEN` are configured.

### Verified production deployment

* **Public frontend:** <https://enchanting-alpaca-de0ed3.netlify.app> (Netlify, publicly accessible without Netlify authentication).
* **Public worker:** <https://leadscan-9fsy.onrender.com> (Render).
* **Frontend production commit:** `57deef5277a6a4bda64f7c11545f7555aef6206f`.
* The production cross-origin flow has completed a real Chromium audit of `example.com` and returned the customer report.
* Hosted lead persistence uses `DATABASE_URL` -> `PostgresLeadStore` -> Neon/Postgres.
* On 2026-09-02, an operator confirmed the same disposable lead row in `public_leads` before and after a normal Render restart. Production persistence is therefore **verified durable**.

No production connection string, token, or disposable test identity is stored in this repository.

### Alternative Fly/SQLite deployment

* `fly.worker.toml` configures persistent storage via volume `leadscan_data` mounted at `/data`, setting `LEADSCAN_LEAD_DB_PATH=/data/leadscan-public-leads.sqlite3`.
* `deploy/fly/start-worker.sh` enforces that `/data` is an active mount point before starting the worker, failing closed if missing, and runs Uvicorn as unprivileged `pwuser`.
* SQLite deployment is designed for **one worker Machine**; Fly volumes are per-Machine and not a shared/replicated multi-Machine database.
* This remains a technically supported alternative architecture, not the current hosted production deployment. Its volume attachment and runtime permissions must be verified by any operator choosing that deployment.

## The first message

Every lead in the CSV comes with a WhatsApp opener, an email subject and an
email body, already written from what the scan found. The call sheet carries a
link that opens WhatsApp with the message typed.

The drafts follow the same rule as everything else: they name only what was
seen, they never promise a result, and they offer the review free. Writing the
same message forty times by hand is the step that actually stops people from
working a list.

## Sending the list to an outreach tool

```bash
python leadscan.py --sweep sg-car --crm
```

`crm_import.csv` uses the column names Instantly, Lemlist, Smartlead and HubSpot
expect, so it imports without renaming anything. Leads with no usable email
address are left out: an un-mailable contact inflates the bounce rate that every
later send depends on.

Addresses are graded before they get there — `personal`, `shared` (an `info@`
inbox), `freemail`, or dropped as a template placeholder, a disposable domain or
a typing mistake. No mail server is ever contacted to test a mailbox; that check
is what paid verification services sell, and doing it at volume from your own
address gets you listed as a spam probe.

## The contact page

Most small firms keep the enquiry form on `/contact` and use the home page for
pictures. A scan of the home page alone therefore reports "cannot capture a
lead" for a firm that captures leads perfectly well, and the caller opens with a
statement the prospect knows is wrong. That is worse than a missed lead.

So when the home page shows no way to capture a lead, the scan follows the
first contact, enquiry, booking or quote link on the same site and looks there
too. A second page can only ADD evidence, never remove it: the speed, the HTTPS
state and the mobile viewport stay as measured on the home page, because that is
the page the advertisement sends people to.

Pass `--shallow` to switch this off. On the fixture site it changes one firm
from "hot, cannot capture a lead" at rank 3 to rank 7 with its real enquiry form
and email address found.

## If a run stops

Every finished firm is appended to `warm_leads.journal.jsonl` as soon as it is
scored. If a run is interrupted — a sleeping laptop, a dropped network, Ctrl-C —
start the same command again and every firm already in the journal is skipped.
Nothing is scanned twice and no API call is paid for twice.

The journal is also the honest record of what a run really saw. The CSV holds
only the leads that were kept.

## Speed

Sites are rendered in parallel, three at a time by default. One firm takes about
five seconds, so a 200-firm sweep goes from over half an hour to roughly ten
minutes. Raise `--workers` if you have the memory; each worker runs its own
browser and uses about 200 MB.

The polite delay is per host, not global. Waiting a second between two different
companies' servers protects nobody; waiting a second before asking the same
server for a second page does.

## The cache

Every Places search, every rendered page and every follower lookup is stored in
`.leadscan-cache/` for seven days. If a run stops halfway, start it again and it
picks up where it left off without paying for the same API calls twice. Delete
the folder at any time, or pass `--clear-cache`. `--no-cache` fetches and audits
everything again, bypassing both cache reads and existing journal replay. Newly
completed rows are still appended to the journal for crash recovery.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

The test suite covers CLI scanning, scoring rules, report generation, public API rate limiting, concurrency gates, SQLite lead capture, frontend static validation, and real Chromium browser execution:

* Unit and static security tests execute locally via `pytest`. Platform-specific permission checks skip automatically where unsupported (e.g. Windows file mode checks).
* GitHub Actions runs tests across Python 3.10 and 3.12 on Ubuntu.
* A separate CI job runs real Chromium browser integration tests using Playwright.
* A worker-container CI job builds `Dockerfile.worker` and verifies container entrypoint, file structure, and non-root Chromium execution.
* CI requires no external API keys or network services.

To try the whole pipeline end to end with no key and no internet:

```bash
python tests/fixtures/serve.py 8099 &
python leadscan.py --input tests/fixtures/fixture_businesses.csv --include-cool
```

The fixture site holds six cases with known answers: a site with advertising
infrastructure installed and no funnel (hot), an Instagram-only firm (warm), a
firm with analytics and a working form (not a lead), a page full of footer
noise that must not produce a fake social profile, a firm whose form is on
`/contact` (which must not be
reported as broken), and a parked domain.

## Files

| File | What it holds |
|---|---|
| `leadscan.py` | The command. Argument parsing and the run loop. |
| `config.py` | Every threshold and every sweep. |
| `sources.py` | Google Places (New), CSV input, de-duplication. |
| `browser.py` | Playwright. Rendering, retry, polite delays. |
| `detect.py` | Reads one page. Pure, so it is fully tested. |
| `scoring.py` | Judges one business against the ICP. Pure. |
| `checks.py` | Joins the three above into one audit. |
| `report.py` | Writes the CSV, HTML and XLSX call sheets. |
| `runner.py` | Parallel workers and the crash-safe journal. |
| `cache.py` | The resumable disk cache. |
| `robots.py` | Reads `robots.txt` and obeys it. |
| `audit_report.py` | The branded one-page review you send the prospect. |
| `public_api.py` | FastAPI transport layer and rate-limited endpoint for `/api/audit`. |
| `public_audit.py` | Single-site audit runner, URL safety checks, and report generator. |
| `lead_capture.py` | Private Postgres and SQLite lead storage with fail-closed persistence. |
| `site/` | Static public website review frontend (HTML, CSS, JS). |
| `netlify.toml` | Netlify static headers, security headers, and CSP connect-src policy. |
| `Dockerfile.worker` | Production container image for unprivileged worker and Chromium. |
| `fly.worker.toml` | Fly worker VM configuration, persistent mount, and resource limits. |
| `deploy/fly/` | Worker startup script, egress firewall, and network policies. |
| `outreach.py` | The first message, and the CRM-shaped export. |
| `verify.py` | Grades an email address before you use it. |
| `adlibrary.py` | Optional Meta Ad Library check. **Read the note at the top.** |
| `make_readable.py` | Rebuild the call sheet from an edited CSV. |

## Privacy and courtesy

* The API key lives only in `.env`, which is git-ignored.
* The output files and the cache hold business phone numbers taken from public
  listings. They are git-ignored. Keep them private and delete them when the
  campaign ends.
* `robots.txt` is read and obeyed. A site that says no keeps the status
  `blocked by robots.txt` in the output, so the caller can open it by hand, and
  it is never scored on evidence that was not collected. `--ignore-robots`
  switches this off; think before you use it.
* At most two pages are read per firm, with a one-second gap between two hits on
  the same server. Raise `LEADSCAN_POLITE_DELAY` before you run at high volume.
* Each business has a 90-second total audit budget. Set
  `LEADSCAN_BUSINESS_TIMEOUT_SECONDS` to tune that cooperative deadline.
* `--exclude` stops you calling the same firm twice across sweeps. Point it at
  the last call sheet you worked through.

See `HANDOFF.md` for the state of the work and what to do next.
