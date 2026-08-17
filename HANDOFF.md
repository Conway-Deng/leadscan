# HANDOFF — LeadScan

Last updated: 2026-08-17 (v4)

## The idea in one line

Find quiet firms in Singapore that invest in being seen — paid advertisements
or organic Instagram and TikTok — but cannot capture the leads, and hand the
caller a ranked list with phone numbers and a ready opening line.

## The ICP — this is the whole strategy

A firm is a good lead when all three are true:

1. **Quiet** — few Google reviews, so they lack organic reach.
2. **Invests in being seen** — an advertisement tag, or content on Instagram or
   TikTok.
3. **Broken funnel** — no lead capture, a slow or dead site, no HTTPS, or no
   site at all.

Disqualify a firm with a large following. They own an audience already.

---

## v3 — what changed, and why it matters

### 1. The "hot" tier was mostly wrong

`spends_on_ads` was `has_meta_pixel or has_google_tag`, and `has_google_tag`
matched `gtag(`, `googletagmanager.com` and `google-analytics.com`. Almost every
website has one of those. The result: most leads were marked "hot confirmed
ad-spender" on the strength of a Google Analytics tag, and the caller opened
with "you are running paid ads" to a firm that has never bought one.

Now `detect.py` separates the two:

* **Advertisement proof** — Meta Pixel `fbq('init')`, `fbevents.js`, the
  `/tr?id=` image, Google Ads `AW-`, `google_conversion_id`,
  `googleadservices.com`, TikTok, LinkedIn, Microsoft, Pinterest, Snap, X.
* **Measurement only** — GA4 `G-`, Universal `UA-`, GTM `gtm.js`, Hotjar,
  Clarity. Recorded in `reasons`, never counted as spend.

The clearest single signal is the Google tag prefix. `AW-` is Google Ads.
`G-` is Analytics. They look nearly identical in the page source and mean
completely different things.

### 2. The biggest score item almost never fired

`can_capture_lead` was true if the page held `tel:`, `mailto:`, a `<form>` of
any kind, or any of the words "consultation", "book now", "get a quote",
"inquire", "let's talk". Nearly every website passes that test, so the +40 core
pain — the entire premise of the product — rarely applied.

Now a capture method must be a real one: a `<form>` with a contact field (and
not a search, newsletter or login form), a hosted booking tool (Calendly,
Acuity, Fresha, Vagaro, HubSpot, Typeform, Jotform, a chat widget), a WhatsApp
click-to-chat link, a `tel:` link or a `mailto:` link. The words alone no longer
count.

### 3. The known defects from v2, all closed

| v2 defect | State |
|---|---|
| `Ft2 (Ubi)` and `Ft2 (Woodlands)` both called — same company | Fixed. De-duplication by place id, then by the last 8 digits of the phone number, then by website root domain. Shared domains (Instagram, Linktree, site builders, IP addresses) are never used to join two firms. |
| `facebook.com/2008/fbml` reported as a Facebook page | Fixed, with a test. The rejection list now covers the XML namespace, the `/tr` pixel endpoint, share and dialog URLs, and every platform subdomain. |
| "You may be spending on ads without tracking" on firms with no tag | Fixed. Three honest openings only: a tag was seen, social content was seen, or neither — in which case the line leads with the defect and says nothing about advertising. |
| A few legitimate sites hit the 15 s timeout and were logged as broken | Fixed. 20 s first try, one retry at 30 s. Only a timeout is retried; a DNS failure or a 404 will not change. |
| Thresholds hard-coded across two files | Fixed. Everything is in `config.py` and every value has an environment-variable override. |

### 4. Faults found during this pass that were not in the v2 list

* **`_FB_NOISE` held `/tr`**, so any Facebook page whose name starts with "tr"
  (`facebook.com/trendyrenovations`) was thrown away as noise.
* **The Instagram-as-website patch ran after the social test**, so a firm whose
  only web presence is an Instagram page was never counted as marketing on
  social — exactly the firm the ICP is looking for.
* **`Browser.__exit__` closed three resources on one line.** If the first close
  raised, the browser and the Playwright process leaked for the rest of the run.
* **The follower-count regular expression scanned the whole page.** Any text
  reading "… 4,000 Followers" anywhere on a page could be read as the follower
  count and disqualify a firm. It now reads the `og:description` meta tag only.
* **A failed Place Details lookup returned `{}`**, which is indistinguishable
  from "this firm has no reviews". `None` now means unknown throughout.
* **CSV formula injection.** A business name that starts with `=`, `+`, `-` or
  `@` is executed by Excel and Google Sheets when the file is opened. Every text
  cell is now escaped in both the CSV and the XLSX.

### 5. Google Places migrated to the v1 (New) API

The legacy `maps.googleapis.com/maps/api/place/*` endpoints are in Legacy
status. `sources.py` now posts to `places.googleapis.com/v1/places:searchText`
with a field mask.

This also cuts cost. The legacy flow needed one Text Search per query **plus one
Place Details request for every unique business**, only to read the website, the
phone number and the review count. The new endpoint returns those three fields
inside the search response. A 200-firm sweep drops from about 230 requests to
about 30. Set `LEADSCAN_PLACES_LEGACY=1` to force the old path; the code also
falls back to it automatically on a 403.

### 6. Output the caller can actually use

`warm_leads.html` is now a real call sheet: hottest lead first, a large
click-to-call phone number, the opening line in a quoted block, a tick box per
row, light and dark themes, and a print layout. `warm_leads.xlsx` is the same
list with filters and clickable links. The CSV stays as the record.

### 7. Engineering

* **Resumable cache** (`.leadscan-cache/`, 7 days). A sweep that stops halfway
  costs nothing to restart. `--no-cache` and `--clear-cache` control it.
* **Polite delays.** One second between sites, configurable.
* **82 tests**, none needing a browser, a key or a network connection, plus a
  local fixture site (`tests/fixtures/serve.py`) that exercises the whole
  pipeline end to end offline.
* **One failing site no longer stops the run.** The error is logged and the
  sweep continues.
* `--log PATH` writes a timestamped run log.

---

## v3.1 — the second pass

### 8. The contact page: the last big source of false positives

Most small firms keep the enquiry form on `/contact` and use the home page for
pictures. A home-page-only scan therefore reported "cannot capture a lead" for
a firm that captures leads perfectly well. That is worse than a missed lead:
the caller opens with a statement the prospect knows is wrong, and the call is
over in one sentence.

When the home page shows no capture method, the scan now follows the first
contact, enquiry, booking or quote link on the same site. A second page can only
ADD evidence, never remove it — speed, HTTPS and the mobile viewport stay as
measured on the home page, because that is the page an advertisement sends
people to. Privacy pages, blog posts and external links are not followed.
`--shallow` switches it off.

Measured on the fixture site: one firm moves from "HOT, cannot capture a lead"
at rank 3 to rank 7, with its real form, its WhatsApp link and its email address
found. Expect the same correction on real data, and expect the hot list to
shrink again.

### 9. Parked and unpublished domains

A domain that is for sale, suspended or still showing "coming soon" is not a
broken funnel. The firm believes it has a website and it does not, so the
opening line has to be different. Detected from the visible text only — never
from a script or a comment.

One heuristic was tried and **deliberately removed**: "this page has very little
text, so it is probably a placeholder". It fired on two perfectly good fixture
sites. A false "your website is parked" is exactly the kind of claim that ends a
cold call, so if the scan cannot prove it, it does not say it.

### 10. Parallel rendering, and a run that survives a crash

* `--workers N` (default 3) renders N sites at once, each worker with its own
  browser, because Playwright's synchronous API belongs to the thread that made
  it. A 200-firm sweep goes from over half an hour to roughly ten minutes.
  Rows come back in input order, so two runs of the same input give an identical
  file — verified.
* **The polite delay is now per host, not global.** The old global delay cost a
  200-firm sweep 200 seconds of waiting that protected nobody: every firm is on
  a different server. What the delay is actually for is not asking one server
  for two pages in quick succession, which is what the contact-page check does.
* **A journal** (`warm_leads.journal.jsonl`) records every firm the moment it is
  scored. Start the same command again after an interruption and everything in
  the journal is skipped. Verified by truncating a journal, wiping the cache and
  re-running: only the missing four firms were re-scanned.
* Ctrl-C now stops cleanly and says how to carry on.

### 11. `--exclude`: stop calling the same firm twice

Point it at the last call sheet you worked through. Matching firms are dropped
before any site is rendered, so they cost nothing. The match uses the same
identity rules as de-duplication, so a firm is recognised again even when Google
returns a different branch name or a differently formatted phone number.

### 12. Continuous integration

`.github/workflows/tests.yml` runs the 107 tests on Python 3.10 and 3.12 for
every push, and fails the build if a Google API key pattern or a tracked `.env`
ever reaches the repository.

### 13. robots.txt is now read and obeyed

LeadScan opens hundreds of sites in a loop. That is a crawler, whatever the user
agent says. Most small-business sites either have no `robots.txt` or allow
everything, so in practice this changes almost nothing — but the cases where it
does change something are the cases where it matters.

A blocked firm is NOT dropped. It stays in the output with the status
`blocked by robots.txt` so the caller can open the site by hand. It is simply
never scored on evidence that was not collected, because guessing a verdict from
an unread page would break the honesty rule the whole model rests on.
`--ignore-robots` switches the check off.

### 14. Opening hours on the call sheet

Places API (New) returns `regularOpeningHours` inside the same field mask, at no
extra request, so the call sheet now carries them. A caller who dials a closed
shop wastes the lead as surely as a wrong number.

---

## v4 — what the competitors sell, and what was missing

A scan of the tools an agency in this market actually pays for
(Insites $299/mo, Woorank $80-200/mo, SEOptimer from €29/mo, SE Ranking from
$44/mo, BrightLocal, marketgoo, BuzzBoard) and of the Google Maps lead tools
(Outscraper, Scrap.io, Apify, PhantomBuster, Get Map Leads) produced three
findings.

### Finding 1 — the ad-tag test is a real differentiator, and it was undersold

In the Google Maps lead-tool category, **ad pixel detection is offered by
nobody**, and website technology detection by nobody either. Those tools
compete on volume of rows and on email enrichment. LeadScan's whole premise —
"this firm is provably paying for traffic AND cannot capture it" — is not
something any of them can answer. That is the thing to lead with, and it is now
stated plainly in the README rather than buried in the scoring table.

### Finding 2 — every audit tool sells a report; LeadScan had none (FIXED)

Insites, Woorank, SEOptimer, SE Ranking, BrightLocal and marketgoo all lead
with a white-label report the agency puts in front of the business owner. Get
Map Leads sells "branded PDF reports" as a headline. LeadScan produced a call
sheet for the caller and nothing at all for the prospect, so the call was still
a cold call.

`audit_report.py` now writes a one-page, self-contained, printable review per
lead (`--reports`), branded from `.env`, plus a single-site `--audit URL` mode
for an inbound enquiry. This is also step one of the partnership ladder in this
document: "give first". A named report about their own website is a much better
opening than a phone call about nothing.

**Every report carries a "what this review did not check" section**, listing the
things a real audit would cover and this one did not. That is the opposite of
what the generic audit tools do, and it is deliberate: the owner forwards the
report to whoever built the site, and a report that overclaims is destroyed in
one reply. A report that states its own limits survives and gets the meeting.

A firm with no website, or one whose site did not load, gets no report.

### Finding 3 — email enrichment is a headline feature elsewhere (FIXED)

Outscraper and Scrap.io both sell "built-in email enrichment". LeadScan already
pulled addresses off the site and the contact page, but a scraped address is not
a usable one. `verify.py` grades each address: `personal` (a named person at the
company domain), `shared` (`info@`, `enquiries@` — kept, but flagged), `freemail`,
or unusable (a template placeholder, a disposable domain, a typing mistake such
as `gmial.com`, bad syntax, or a domain that accepts no mail).

It deliberately does NOT open an SMTP connection to test a mailbox. That check
is what paid verification services sell; it is slow, often blocked, and doing it
at volume from your own address gets you listed as a spam probe. If you need
mailbox-level certainty, pay a service for the final list.

### Also new in v4

* **The first message is written for you.** Every lead carries a WhatsApp
  opener, an email subject and an email body, built from what the scan found.
  The call sheet has a link that opens WhatsApp with the message already typed.
  Writing the same message forty times by hand is the step that actually stops
  people from working a list. The drafts obey the same honesty rule: they name
  only what was seen, never promise a result, and offer the review free.
* **`--crm`** writes a CSV with the column names Instantly, Lemlist, Smartlead
  and HubSpot expect, so it imports without renaming anything. Leads with no
  usable address are left out, because an un-mailable contact inflates the
  bounce rate every later send depends on.
* The findings now ride on the output row, so a report can be rebuilt from a
  saved run without opening a single website again.

### What the competitors have that LeadScan still does not

* **An embeddable audit widget.** Insites, Woorank, SEOptimer and SE Ranking all
  sell one: a form on the agency's own site where a visitor types their URL and
  gets a report, which captures the visitor as a lead. `--audit URL` is the
  local half of this. The missing half is hosting, and that is a real product
  decision, not an afternoon's work.
* **Rank tracking and citation management** (BrightLocal, SE Ranking). Out of
  scope, and `Localrank-MVP` in this same portfolio already covers the rank-grid
  idea.
* **A contact person's name.** Every outreach tool merges `{{first_name}}`.
  LeadScan has only the business name, and guessing a person from an email
  address was considered and dropped: "Hi Info," and a wrong name are both worse
  than no name.

---

## Read this before you build the Meta Ad Library gate

The v2 plan was: get a Meta app and identity check (about one day) and use the
Ad Library API as the real proof of a LIVE advertisement.

**That day will not produce the result you want in Singapore.** The
`ads_archive` endpoint returns COMMERCIAL advertisements only when
`ad_reached_countries` names an EU member state or the United Kingdom, because
the EU Digital Services Act forces Meta to archive them. Everywhere else,
Singapore included, the endpoint returns political and social-issue
advertisements only. A Singapore aesthetic clinic advertising to a Singapore
audience does not appear in the API at all.

The public Ad Library web page does show those advertisements, but reading it
with a script breaks the Meta terms of service and the risk falls on your
account. `adlibrary.py` therefore implements the API path (useful if you ever
sweep the EU or the UK), refuses to guess for an uncovered country, and
documents all of this at the top of the file.

The practical substitute is the stricter advertisement-tag test described above.
A site with a Meta Pixel and a Google Ads tag and no booking form is a strong
lead, and you can say exactly what you saw.

---

## Remaining work

### A. Run it against real data (the only thing that matters now)

- [ ] Run `sg-interior` again with the new scoring and compare against the v2
      list of 39 leads (14 "hot", 25 warm). The expectation: far fewer "hot"
      leads, and the ones that remain are real. **The v2 hot list should be
      treated as unverified** — most of it was probably Google Analytics.
- [ ] Tune `QUIET_REVIEWS`, `ESTABLISHED_REVIEWS`, `INFLUENCER_FOLLOWERS` and
      `SOCIAL_ONLY_MAX_FOLLOWERS` against that real distribution. They are
      guesses until real data touches them.
- [ ] Run the four untouched sweeps: `sg-car`, `sg-aesthetics`, `sg-aircon`,
      `sg-motorbike`. Two new ones are also defined: `sg-reno-trades` and
      `sg-wellness`. These niches probably have more broken funnels than
      interior design.

### B. Validate `--social-only` end to end

- [ ] It is built and it is now cached and polite, but it has still never been
      run on a real sweep. Instagram and TikTok block a logged-out visitor
      often, so expect many blank follower counts. That is handled, not a fault.

### C. Worth building next

- [x] ~~Second-page check~~ — done in v3.1.
- [x] ~~Email enrichment beyond the home page~~ — done, via the same fetch.
- [ ] **Rank by opportunity, not only by defect.** A firm with 8 reviews and a
      Meta Pixel is worth more than a firm with 8 reviews and no pixel, and the
      score already says so. What it does not know is deal size. A per-sweep
      weight in `config.py` (aesthetics is worth more per client than aircon)
      would sharpen the order of a combined call list. Small change, real
      effect once more than one niche has been swept.
- [x] ~~Call when they are open~~ — the hours are on the sheet as of v3.2. What
      is still missing is an "open right now" badge, which needs the local time
      and a parse of the weekday text. Half an hour of work.
- [ ] **A cost estimate before a sweep.** `--dry-run` printing "this sweep will
      make about 30 Places requests" would make the API bill predictable.

### D. Ideas that were considered and rejected

* **Meta Ad Library gate** — see the section above. Not viable for Singapore.
* **Scraping Instagram or TikTok while logged in** — breaks their terms of
  service and puts the account at risk. The best-effort logged-out read stays
  best-effort, and the Google review count carries the "quiet" signal.

---

## Privacy and security notes

* The API key lives only in `.env` (git-ignored). It appears in no other file.
* `.leadscan-cache/`, `warm_leads.*` and `out/` are git-ignored. They hold
  scraped business phone numbers. Keep them private, and delete them when the
  campaign ends.
* **Check the repository is still private.** It holds the ICP, the sweep terms
  and the whole method, and the handoff note says it should be private.
* At most two pages per firm, with a one-second gap between two hits on the
  same server. Raise `LEADSCAN_POLITE_DELAY` before you run at high volume.
* `*.journal.jsonl` is git-ignored. It holds every firm a run saw, including
  phone numbers, and it is more complete than the call sheet.
* Business names from Google are escaped before they reach the HTML call sheet
  and before they reach the CSV or the XLSX.
