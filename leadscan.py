"""
leadscan.py  --  the command you run.

Pipeline:
    sources  -> sweep many searches, dedupe, pull phone + reviews
    checks   -> render each site, detect funnel/ads/socials, score vs the ICP
    output   -> a ranked sheet of WARM leads only (influencers filtered out),
                each with phone, reviews, socials, the problem, and the hook.

USAGE:
    # Test with a CSV (no key, no Places sweep):
    python leadscan.py --input sample_businesses.csv

    # Full Singapore sweep (needs GOOGLE_PLACES_API_KEY in .env):
    python leadscan.py --sweep sg-interior --want 40
"""

import argparse
import csv
import os
import sys
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sources
import checks

load_dotenv()

# Named search sweeps. Mixing terms + regions surfaces the quiet long-tail firms,
# not just the famous ones. Add your own here anytime.
SWEEPS = {
    "sg-interior": [
        "interior design firm Singapore",
        "HDB renovation Singapore",
        "condo renovation interior design Singapore",
        "home renovation contractor Singapore",
        "interior designer Jurong",
        "interior designer Tampines",
        "interior designer Woodlands",
        "interior designer Bedok",
        "interior designer Ang Mo Kio",
        "interior designer Punggol",
    ],
    # High-ticket auto care: heavy Meta/IG advertisers, often run off IG/Linktree
    # with no real funnel. Strongest non-interior niche for Nixon.
    "sg-car": [
        "car detailing Singapore",
        "ceramic coating car Singapore",
        "paint protection film Singapore",
        "car wrapping Singapore",
        "car grooming Singapore",
        "car workshop Singapore",
        "car servicing Singapore",
        "car window tinting Singapore",
        "car detailing studio Ubi",
        "car detailing Sin Ming",
    ],
    # Smaller SG market (COE caps volume) -- run this only after the stronger
    # niches; superbike dealers are high-ticket but few.
    "sg-motorbike": [
        "motorcycle workshop Singapore",
        "motorbike servicing Singapore",
        "motorcycle dealer Singapore",
        "big bike dealer Singapore",
        "superbike shop Singapore",
        "motorcycle accessories shop Singapore",
        "motorbike tyre shop Singapore",
        "motorcycle repair Singapore",
    ],
    # Biggest Meta advertisers in SG, broken funnels everywhere, high LTV.
    "sg-aesthetics": [
        "aesthetic clinic Singapore",
        "medical aesthetics Singapore",
        "med spa Singapore",
        "beauty salon Singapore",
        "facial spa Singapore",
        "slimming clinic Singapore",
        "hair removal clinic Singapore",
        "aesthetic clinic Orchard",
        "aesthetic clinic Tampines",
        "aesthetic clinic Jurong",
    ],
    # Huge SG market, many small players advertising with thin websites.
    "sg-aircon": [
        "aircon servicing Singapore",
        "aircon installation Singapore",
        "aircon chemical wash Singapore",
        "aircon repair Singapore",
        "aircon servicing Jurong",
        "aircon servicing Tampines",
        "aircon servicing Woodlands",
        "aircon servicing Bedok",
        "aircon servicing Sengkang",
        "aircon servicing Yishun",
    ],
}


def audit_all(businesses, social_only=False):
    """Render + score every business. Returns all result rows.

    social_only=True switches to the no-website tier: instead of rendering a
    site, we web-search each firm's IG/TikTok and score on 'they have no funnel'.
    """
    results = []
    with checks.Browser() as browser:
        for i, biz in enumerate(businesses, 1):
            if social_only:
                row = checks.audit_social_only(browser, biz)
            else:
                row = checks.audit_business(browser, biz)
            results.append(row)
            tag = "SKIP " if row["disqualified"] else ("WARM " if row["warm"] else "     ")
            print(f"  [{i:>3}/{len(businesses)}] {row['score']:>3} {tag} {row['name']}")
    return results


def write_output(results, out_path, want):
    # Keep only warm, non-disqualified leads. Sort HOT (confirmed ad-spenders)
    # before WARM, and by score within each tier -- so Nixon calls the surest bets
    # first and never wastes a call on an unconfirmed lead while a hot one waits.
    warm = [r for r in results if r["warm"] and not r["disqualified"]]
    tier_rank = {"hot": 0, "warm": 1}
    warm.sort(key=lambda r: (tier_rank.get(r.get("tier", "warm"), 1), -r["score"]))
    warm = warm[:want]

    fields = ["tier", "score", "name", "phone", "review_count", "instagram_followers",
              "instagram", "facebook", "tiktok", "hook", "website", "status"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(warm)
    return warm


def main():
    p = argparse.ArgumentParser(description="Find quiet businesses that invest in getting seen -- paid ads or organic IG/TikTok -- but can't capture the leads.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="CSV of businesses (columns: name, website)")
    src.add_argument("--sweep", choices=list(SWEEPS), help="Named search sweep")
    p.add_argument("--want", type=int, default=40, help="How many warm leads to keep (default 40)")
    p.add_argument("--cap", type=int, default=200, help="Max firms to scan in a sweep (default 200)")
    p.add_argument("--out", default="warm_leads.csv", help="Output CSV path")
    p.add_argument("--social-only", action="store_true",
                   help="Target businesses with NO website, only IG/TikTok. Web-searches "
                        "each firm's socials and skips accounts over 3,000 followers. Slower.")
    args = p.parse_args()

    if args.input:
        if not os.path.exists(args.input):
            sys.exit(f"File not found: {args.input}")
        businesses = sources.from_csv(args.input)
    else:
        key = os.getenv("GOOGLE_PLACES_API_KEY")
        try:
            print("Sourcing businesses from Google Places...")
            businesses = sources.sweep_google_places(SWEEPS[args.sweep], key, cap=args.cap)
        except (ValueError, RuntimeError) as e:
            sys.exit(str(e))

    if not businesses:
        sys.exit("No businesses found.")

    # --social-only: keep ONLY firms with no website -- everything they do runs
    # off IG/TikTok, and having no funnel at all is the whole reason to call them.
    if args.social_only:
        total = len(businesses)
        businesses = [b for b in businesses if not b.get("website")]
        print(f"Social-only mode: {len(businesses)} of {total} firms have no website.")
        if not businesses:
            sys.exit("No no-website businesses found in this sweep.")

    if args.social_only:
        print(f"\nWeb-searching + scoring {len(businesses)} no-website firms "
              f"(a few seconds each, slower than normal)...\n")
    else:
        print(f"\nRendering + scoring {len(businesses)} sites (a few seconds each)...\n")
    results = audit_all(businesses, social_only=args.social_only)
    warm = write_output(results, args.out, args.want)

    skipped = sum(1 for r in results if r["disqualified"])
    hot_n = sum(1 for r in warm if r.get("tier") == "hot")
    print(f"\nDone. {len(warm)} warm leads written to {args.out} "
          f"({hot_n} HOT confirmed ad-spenders, {len(warm) - hot_n} warm)")
    if args.social_only:
        print(f"({skipped} firms over 3,000 followers filtered out.)")
    else:
        print(f"({skipped} influencer-run firms filtered out.)")
    if warm:
        top = warm[0]
        print(f"\nTop lead: {top['name']}  (score {top['score']})  {top['phone']}")
        print(f"  Opening line: \"{top['hook']}\"")


if __name__ == "__main__":
    main()
