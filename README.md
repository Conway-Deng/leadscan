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
2. **Invests in being seen** — a live advertisement tag, or content on
   Instagram or TikTok.
3. **Broken funnel** — no way to capture a lead, a slow site, no HTTPS, or no
   website at all.

A firm with a large social following is skipped. It already owns an audience.

### Tiers

| Tier | Meaning |
|---|---|
| `hot` | A real advertisement tag was found AND the funnel leaks. Money goes out every day this stays broken. |
| `warm` | The firm builds an audience on Instagram or TikTok AND the funnel leaks. |
| `cool` | A quiet firm with a real defect, but no proof that it markets itself. Hidden unless you pass `--include-cool`. |

### Points

| Signal | Meaning | Points |
|---|---|---|
| No form, booking link, WhatsApp link or click-to-call | Cannot capture a lead | +40 |
| Quiet (30 reviews or fewer) | Small firm, likely needs help | +20 |
| Advertisement tag found | Proven spend | +15 |
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

Only a tag that shows money is being spent:

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
| `--no-cache` | Fetch everything again. |
| `--clear-cache` | Delete the cache folder first. |
| `--log PATH` | Append the run log to a file. |

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
the folder at any time, or pass `--clear-cache`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

107 tests. None of them needs a browser, a key or a network connection.
They run in under a second and gate every push through GitHub Actions.

To try the whole pipeline end to end with no key and no internet:

```bash
python tests/fixtures/serve.py 8099 &
python leadscan.py --input tests/fixtures/fixture_businesses.csv --include-cool
```

The fixture site holds six cases with known answers: a confirmed advertiser
with no funnel (hot), an Instagram-only firm (warm), a firm with analytics and
a working form (not a lead), a page full of footer noise that must not produce a
fake social profile, a firm whose form is on `/contact` (which must not be
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
| `adlibrary.py` | Optional Meta Ad Library check. **Read the note at the top.** |
| `make_readable.py` | Rebuild the call sheet from an edited CSV. |

## Privacy and courtesy

* The API key lives only in `.env`, which is git-ignored.
* The output files and the cache hold business phone numbers taken from public
  listings. They are git-ignored. Keep them private and delete them when the
  campaign ends.
* At most two pages are read per firm, with a one-second gap between two hits on
  the same server. Raise `LEADSCAN_POLITE_DELAY` before you run at high volume.
* `--exclude` stops you calling the same firm twice across sweeps. Point it at
  the last call sheet you worked through.

See `HANDOFF.md` for the state of the work and what to do next.
