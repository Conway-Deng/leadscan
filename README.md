# LeadScan

Finds businesses that **spend money on ads but can't capture leads** — the exact
profile that says "yes" to a cold call from an SMMA. Built to feed Nixon warm,
pre-qualified prospects with a ready-to-say opening line.

## Quick start (no API key needed)

```powershell
python -m pip install -r requirements.txt
python leadscan.py --input sample_businesses.csv
```

Open `leads.csv` — hottest leads first, each with a `hook` (the call opener).

## Auto-find real businesses (needs a Google Places key)

1. Copy `.env.example` to `.env`, paste your `GOOGLE_PLACES_API_KEY`.
2. Run:
   ```powershell
   python leadscan.py --search "med spa in Austin" --limit 20
   ```

## How a lead is scored

| Signal | Meaning | Effect |
|---|---|---|
| No form / booking / click-to-call | Can't capture leads | +45 (the core pain) |
| No mobile viewport | Loses phone traffic | +20 |
| No HTTPS | Scares buyers | +15 |
| Slow (>5s) | Loses clicks | +15 |
| Meta Pixel / Google tag present | **Proves they buy ads** | +15 and marked `qualified` |

`qualified` leads (proven ad-spenders) always sort above the rest.

See `HANDOFF.md` for the roadmap and the partnership plan with Nixon.
