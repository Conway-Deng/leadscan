# HANDOFF — LeadScan (lead-gen tool for the Nixon partnership)

Last updated: 2026-07-05

## The idea in one line
Find QUIET interior-design firms in Singapore that invest in getting seen (paid ads
or organic IG/TikTok) but can't capture the leads, and hand Nixon a ranked call list
with phone numbers + a ready opening line — so he stops cold-calling people who don't
have the problem.

## The ICP (Ideal Customer Profile) — this is the whole strategy
A firm is a good lead when ALL three are true:
1. **Quiet** — few Google reviews / small following (they lack organic reach → need help).
2. **Already invests in getting seen** — has an ad pixel/tag, OR posts on IG/TikTok.
3. **Broken funnel** — no booking form, slow/dead site, no HTTPS, or no website at all.
DISQUALIFY: influencer-run firms (big following) — they own an audience, don't need Nixon.

## What's built and WORKING now (v2)
- `leadscan.py` — the command. Sweeps many searches, dedupes, renders, scores, outputs.
- `sources.py` — Google Places sweep with pagination (up to ~60/search) + phone + review count.
- `checks.py` — renders each site in a REAL headless browser (Playwright) so JS-loaded
  pixels/forms are seen; detects lead-capture, ad pixel/tag, socials, speed, https;
  scores against the ICP; writes the plain-English hook.
- Output = `warm_leads.csv`, split into **🔥 hot** (confirmed ad-spender + broken funnel)
  and **warm** (quiet + weak funnel / no ad proof). Columns: tier, score, name, phone,
  reviews, IG followers, IG/FB/TikTok, hook, website.
- **Last run: 39 leads (14 hot, 25 warm)** from the `sg-interior` sweep. Solid enough to
  take the hot 14 to Nixon.

## How to run
```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium              # one-time, ~150MB browser

python leadscan.py --input sample_businesses.csv                 # test, no key
python leadscan.py --sweep sg-interior --want 50 --cap 200       # full run (needs .env key)
python leadscan.py --sweep sg-interior --social-only             # no-website firms only
```
Key lives in `.env` (git-ignored). Sweeps are defined at the top of `leadscan.py`
(sg-interior, sg-car, sg-aesthetics, sg-aircon, sg-motorbike).

## REMAINING WORK — pick up here tomorrow

### A. Quick tuning / small bugs (fast wins)
- [ ] **Phone-based dedupe.** Ft2 (Ubi) and Ft2 (Woodlands) both appear — same company,
      different branches, same phone. Dedupe by phone or root domain, not just place_id.
      (in `sources.py`, the `seen` dict.)
- [ ] **One junk Facebook link leaked** (`facebook.com/2008/fbml` — an XML namespace, not
      a page) on Elm & Line. Add `/2008/` and `xmlns` to `_FB_NOISE` in `checks.py`.
- [ ] **Reword the "You may be spending on ads without tracking" hook.** For firms with NO
      detected pixel, we shouldn't imply they run ads. Lead with the funnel problem instead.
- [ ] **Tune the thresholds with real data:** INFLUENCER_FOLLOWERS (20k),
      SOCIAL_ONLY_MAX_FOLLOWERS (3k), ESTABLISHED_REVIEWS (60), quiet cutoff (30 reviews).

### B. Validate the --social-only mode
- [ ] It's BUILT but not yet run end-to-end on a real sweep. Test `find_social`,
      `tiktok_followers`, `instagram_followers` actually return data (IG/TikTok block
      logged-out scraping a lot — expect blanks and handle gracefully).

### C. Bigger features
- [ ] **Meta Ad Library gate** — the real "confirmed LIVE ad" proof (v2 relied on the
      pixel, which only shows they *can* run ads, not that one is live now). Needs a Meta
      app + ID verification (~1 day). Fixes the last accuracy gap.
- [ ] **Instagram follower counts are mostly blank** (IG blocks logged-out). Either accept
      it (review-count carries the "quiet" signal) or add a paid enrichment API later.
- [ ] **Raise render timeout / add one retry** — a few legit sites hit the 15s timeout and
      got logged as "broken." Retry once before calling a site dead.
- [ ] **Email enrichment** for done-for-you outreach (currently phone-only).
- [ ] **Export a clean Nixon-ready sheet** (nice formatting, hot leads first) — the CSV is
      functional but not pretty.

### D. Run the other niches
- [ ] `sg-car`, `sg-aesthetics`, `sg-aircon`, `sg-motorbike` sweeps are defined but not run.
      These may have MORE broken funnels than interior design (thin sites, run off IG).

## Partnership plan with Nixon (don't skip the ladder)
Nixon = small SMMA, ~2.4k IG followers, closes some clients, niche = interior design.
1. **Give first.** Hand him the hot 14 free: "here's who to call and what to say."
2. **Rev-share on a slice** if it makes him close more — time-boxed, one offer.
3. **Only then** talk equity/co-founder, roles in writing, a way out.
Open question: is Nixon's real business the *agency* or the *content*? If it's the content
(audience of aspiring cold-callers), the bigger play is selling LeadScan to ALL of them.

## Security notes
- API key lives ONLY in `.env` (git-ignored via `.env` + `*.env`, with `!.env.example`).
  Never hardcode, never commit. Verified: the key appears in no other file.
- Generated lead CSVs (`warm_leads.csv`, etc.) are git-ignored — they hold scraped phone
  numbers, keep them private.
- The GitHub repo is **private** for the same reason.
- Be polite crawling: one hit per site with a timeout. Add rate-limiting before high volume.
