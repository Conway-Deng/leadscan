# HANDOFF — LeadScan

Last updated: 2026-08-17 (v3)

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

- [ ] **Second-page check.** Many firms put the booking form on `/contact`, not
      on the home page. Scanning the home page alone marks them as broken. Fetch
      the first internal link whose text matches contact, enquiry, book or
      quote, and treat a capture method found there as a capture method. This is
      the largest remaining source of false positives.
- [ ] **Email enrichment beyond the home page.** `detect.find_emails` works, but
      most firms hide the address on `/contact`. The same second-page fetch
      solves both.
- [ ] **Rank by opportunity, not only by defect.** A firm with 8 reviews and a
      Meta Pixel is worth more than a firm with 8 reviews and no pixel, and the
      score already says so. What it does not know is deal size. A tag for the
      niche (aesthetics is worth more per client than aircon) would sharpen the
      order of the call list.

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
* One hit per site, one second apart. Raise `LEADSCAN_POLITE_DELAY` before you
  run at high volume.
* Business names from Google are escaped before they reach the HTML call sheet
  and before they reach the CSV or the XLSX.
