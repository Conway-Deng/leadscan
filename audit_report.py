"""
audit_report.py
---------------
Builds the one-page website review that you SEND TO THE PROSPECT.

WHY THIS EXISTS
Every paid tool an agency uses in this market sells one thing above all others:
a branded report the agency can put in front of the business owner. Insites,
Woorank, SEOptimer, SE Ranking, BrightLocal and marketgoo all lead with it, at
between $29 and $299 a month. LeadScan produced a call sheet for the caller and
nothing at all for the prospect, so the call was still a cold call.

This closes that gap. It also fits the partnership plan in HANDOFF.md, where
step one is "give first". A named report about their own website is a much
better opening than a phone call about nothing.

THE HONESTY RULE, APPLIED HARDER HERE
A call sheet is read by one person who knows how the tool works. A report is
read by the business owner, who does not, and who will forward it to whoever
built their site. So this report:

  * states only what the scan actually saw;
  * names the pages it looked at;
  * has a "what this review did not check" section, in plain sight, listing
    the things a real audit would cover and this one did not.

That last section is the opposite of what the generic audit tools do, and it is
the reason this report survives contact with the prospect's web developer. A
report that overclaims gets torn apart in one reply and costs the meeting.
"""

import datetime
import html
import os

import verify

# ---------------------------------------------------------------------------
# Branding. Set these in .env to put your own name on the report.
# ---------------------------------------------------------------------------

def brand():
    return {
        "name": os.getenv("LEADSCAN_BRAND_NAME", "").strip(),
        "tagline": os.getenv("LEADSCAN_BRAND_TAGLINE", "").strip(),
        "contact": os.getenv("LEADSCAN_BRAND_CONTACT", "").strip(),
        "colour": os.getenv("LEADSCAN_BRAND_COLOUR", "#2563eb").strip(),
        "cta": os.getenv(
            "LEADSCAN_BRAND_CTA",
            "Happy to walk through any of this on a short call.",
        ).strip(),
    }


# ---------------------------------------------------------------------------
# Turning findings into plain-English items
# ---------------------------------------------------------------------------
# Each item is (title, what it means, what to do). No jargon, because the reader
# is a business owner and not a developer.

def _problems(row, findings):
    items = []
    capture = findings.get("capture_methods") or []
    if not capture:
        items.append((
            "There is no way to contact you from the page",
            "I could not find a form, a booking link, a WhatsApp link, or a "
            "phone number you can tap. A visitor cannot contact you directly "
            "from this page using those capture methods.",
            "Add one clear contact action, placed prominently at the top and "
            "bottom of the page, such as a direct WhatsApp button or simple form.",
        ))
    if findings.get("is_parked"):
        items.append((
            "Your web address does not open a working website",
            "The address currently displays a holding or parked domain page "
            "rather than an active website for your business.",
            "Point the domain at a working website with your services, "
            "location, and contact details.",
        ))
    if not findings.get("has_mobile_viewport", True):
        items.append((
            "The site is not set up for a phone screen",
            "The page is built at desktop width without a mobile viewport "
            "configuration, so on mobile devices text can appear small and "
            "buttons can be harder to tap.",
            "Configure a responsive mobile viewport in your website template, "
            "platform settings, or with your web developer.",
        ))
    if findings.get("is_slow"):
        items.append((
            f"The page took {findings.get('load_seconds')} seconds to appear",
            "That measured load time is slow, which can make the site harder "
            "to use, especially on a mobile data connection.",
            "Investigate common causes of slow loading, such as uncompressed "
            "images, heavy scripts, or server response times.",
        ))
    if not findings.get("is_https", True):
        items.append((
            "There is no padlock in the address bar",
            "Browsers show a 'Not secure' warning on pages without one, which "
            "visitors see when accessing the site.",
            "Enable an SSL/TLS certificate (HTTPS) through your hosting "
            "provider, domain registrar, or web developer.",
        ))
    return items


def _working(row, findings):
    items = []
    if findings.get("is_https", False):
        items.append("The padlock is on, so browsers treat your site as secure.")
    if findings.get("has_mobile_viewport", False):
        items.append("The page is set up for phone screens.")
    load = findings.get("load_seconds")
    if load is not None and not findings.get("is_slow"):
        items.append(f"The page appeared in {load} seconds, which is good.")
    capture = findings.get("capture_methods") or []
    if capture:
        items.append("People can reach you from the page: "
                     + ", ".join(capture) + ".")
    tags = findings.get("ad_tags") or []
    if tags:
        items.append("Advertising-related site infrastructure is installed: "
                     + " and ".join(tags) + ".")
    if findings.get("instagram") or findings.get("tiktok"):
        where = " and ".join(
            name for name, link in (("Instagram", findings.get("instagram")),
                                    ("TikTok", findings.get("tiktok"))) if link)
        items.append(f"Your website links to your {where} profile.")
    if row.get("review_count"):
        items.append(f"You have {row['review_count']} Google reviews, showing an "
                     f"established public track record.")
    return items


def _not_checked(findings):
    """
    What this review did NOT look at. Stated plainly, on the report.

    This is the section the generic audit tools leave out, and it is the reason
    this report survives being forwarded to the prospect's web developer.
    """
    pages = findings.get("pages_checked") or []
    if not pages:
        opened = "Only the home page was opened. The rest of the site was not read."
    elif len(pages) == 1:
        opened = f"Only the home page and {pages[0]} were opened. The rest of the site was not read."
    elif len(pages) == 2:
        opened = f"Only the home page, {pages[0]} and {pages[1]} were opened. The rest of the site was not read."
    else:
        opened = "Only the home page, " + ", ".join(pages[:-1]) + f" and {pages[-1]} were opened. The rest of the site was not read."

    lines = [
        opened,
        "Search rankings, keywords and backlinks were not measured.",
        "Nothing was checked inside your advertising accounts. Whether an "
        "advert is running right now, and what it costs you, is not visible "
        "from outside.",
        "The wording, the photographs and the prices were not judged. That is "
        "a conversation, not a scan.",
    ]
    return lines


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website review — {name}</title>
<style>
  :root {{ --ink:#15181e; --muted:#5b6472; --line:#e4e7ee; --bg:#fff;
           --brand:{colour}; --bad:#b91c1c; --badbg:#fef2f2;
           --good:#15803d; --goodbg:#f0fdf4; --note:#78350f; --notebg:#fffbeb; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:#f4f5f7; color:var(--ink);
    font:15.5px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .sheet {{ max-width:820px; margin:28px auto; background:var(--bg);
    border:1px solid var(--line); border-radius:14px; padding:38px 44px; }}
  .top {{ display:flex; justify-content:space-between; align-items:flex-start;
    gap:20px; border-bottom:3px solid var(--brand); padding-bottom:16px; }}
  h1 {{ font-size:25px; margin:0 0 4px; letter-spacing:-.015em }}
  .sub {{ color:var(--muted); font-size:13.5px }}
  .by {{ text-align:right; font-size:13px; color:var(--muted); white-space:nowrap }}
  .by strong {{ display:block; color:var(--brand); font-size:15px }}
  h2 {{ font-size:17px; margin:32px 0 12px; letter-spacing:-.01em }}
  .lede {{ margin:22px 0 0; padding:16px 18px; background:var(--notebg);
    border-left:4px solid var(--brand); border-radius:0 10px 10px 0; }}
  .item {{ border:1px solid var(--line); border-radius:11px; padding:15px 17px;
    margin-bottom:11px; }}
  .item h3 {{ margin:0 0 7px; font-size:15.5px; color:var(--bad);
    display:flex; gap:9px; align-items:baseline }}
  .n {{ display:inline-flex; align-items:center; justify-content:center;
    min-width:22px; height:22px; border-radius:50%; background:var(--badbg);
    color:var(--bad); font-size:12px; font-weight:700; flex:none }}
  .item p {{ margin:0 0 8px }}
  .fix {{ margin:0; padding:9px 12px; background:#f7f8fa; border-radius:8px;
    font-size:14.5px }}
  .fix b {{ color:var(--good) }}
  ul.good {{ list-style:none; padding:0; margin:0 }}
  ul.good li {{ padding:7px 0 7px 26px; position:relative; border-bottom:1px solid var(--line) }}
  ul.good li:last-child {{ border-bottom:0 }}
  ul.good li::before {{ content:"✓"; position:absolute; left:2px; color:var(--good);
    font-weight:700 }}
  .scope {{ background:#f7f8fa; border:1px solid var(--line); border-radius:11px;
    padding:15px 17px; font-size:14px; color:var(--muted) }}
  .scope ul {{ margin:8px 0 0; padding-left:20px }}
  .scope li {{ margin:4px 0 }}
  .foot {{ margin-top:30px; padding-top:18px; border-top:1px solid var(--line);
    font-size:14px }}
  .foot .cta {{ font-size:15.5px; margin:0 0 8px }}
  .stamp {{ color:var(--muted); font-size:12.5px; margin-top:14px }}
  @media print {{
    @page {{ size:A4; margin:10mm; }}

    body {{
      background:#fff;
      font-size:11.5px;
      line-height:1.32;
    }}

    .sheet {{
      margin:0;
      border:0;
      border-radius:0;
      padding:0;
      max-width:none;
    }}

    h1 {{ font-size:19px; }}

    h2 {{
      font-size:13.5px;
      margin:10px 0 5px;
      break-after:avoid;
    }}

    .sub {{ font-size:11px; }}

    .by {{ font-size:10.5px; }}

    .by strong {{ font-size:12.5px; }}

    .top {{
      padding-bottom:7px;
      break-inside:avoid;
    }}

    .lede {{
      margin-top:8px;
      padding:7px 9px;
      break-inside:avoid;
    }}

    .item {{
      padding:7px 9px;
      margin-bottom:4px;
      break-inside:avoid;
    }}

    .item h3 {{
      margin-bottom:3px;
      font-size:13px;
    }}

    .n {{
      min-width:18px;
      height:18px;
      font-size:10px;
    }}

    .item p {{ margin-bottom:3px; }}

    .fix {{
      padding:4px 6px;
      font-size:11.5px;
    }}

    ul.good {{ break-inside:avoid; }}

    ul.good li {{
      padding-top:2px;
      padding-bottom:2px;
    }}

    .scope {{
      padding:7px 9px;
      font-size:11px;
      break-inside:avoid;
    }}

    .scope li {{ margin:1px 0; }}

    .foot {{
      margin-top:8px;
      padding-top:6px;
      break-inside:avoid;
    }}

    .foot .cta {{
      font-size:12px;
      margin-bottom:3px;
    }}

    .stamp {{
      margin-top:5px;
      font-size:10.5px;
    }}
  }}
</style></head><body>
<main class="sheet">
  <div class="top">
    <div>
      <h1>Website review</h1>
      <div class="sub">{name}{site}</div>
    </div>
    <div class="by">{by}</div>
  </div>

  <p class="lede">{lede}</p>

{problems}
{working}

  <h2>What this review did not check</h2>
  <div class="scope">
    <p style="margin:0">So that nothing here is overstated:</p>
    <ul>{scope}</ul>
  </div>

  <div class="foot">
    <p class="cta">{cta}</p>
    {contact}
    <p class="stamp">Checked on {stamp}. {pages}</p>
  </div>
</main>
</body></html>
"""


def _lede(problem_count, findings):
    if problem_count == 0:
        return ("I had a look at your website from a customer's point of view "
                "and did not find anything obviously getting in the way. The "
                "notes below are what I checked.")
    tags = findings.get("ad_tags") or []
    thing = "these" if problem_count > 1 else "this"
    if tags:
        return (f"I had a look at your website from a customer's point of view. "
                f"I found {' and '.join(tags)} installed on the page. That does "
                f"not show whether a campaign is live; {thing} "
                f"{'are' if problem_count > 1 else 'is'} the first "
                f"{'problems' if problem_count > 1 else 'problem'} I would fix.")
    if findings.get("instagram") or findings.get("tiktok"):
        where = " and ".join(
            name for name, link in (("Instagram", findings.get("instagram")),
                                    ("TikTok", findings.get("tiktok"))) if link)
        return ("I had a look at your website from a customer's point of view. "
                f"I found a link to your {where} profile, so the notes below "
                "focus on the website experience when visitors arrive.")
    return ("I had a look at your website from a customer's point of view. "
            f"Here {'are' if problem_count > 1 else 'is'} {problem_count} "
            f"{'things' if problem_count > 1 else 'thing'} I would change "
            f"first.")


def build(row, findings=None, brand_info=None, stamp=None):
    """Give back the report as one self-contained HTML string."""
    findings = findings or {}
    marque = brand_info or brand()
    escape = html.escape

    problems = _problems(row, findings)
    working = _working(row, findings)

    problem_html = ""
    if problems:
        problem_html = "  <h2>What may be making enquiries harder</h2>\n"
        for number, (title, why, fix) in enumerate(problems, 1):
            problem_html += (
                f'  <div class="item"><h3><span class="n">{number}</span>'
                f"{escape(title)}</h3>\n"
                f"    <p>{escape(why)}</p>\n"
                f'    <p class="fix"><b>What I would do:</b> {escape(fix)}</p>'
                f"</div>\n"
            )

    working_html = ""
    if working:
        working_html = ("  <h2>What is already working</h2>\n"
                        '  <ul class="good">'
                        + "".join(f"<li>{escape(line)}</li>" for line in working)
                        + "</ul>\n")

    by = ""
    if marque["name"]:
        by = f'<strong>{escape(marque["name"])}</strong>'
        if marque["tagline"]:
            by += escape(marque["tagline"])
    else:
        by = "Prepared for you"

    contact = ""
    if marque["contact"]:
        contact = f'<p style="margin:0">{escape(marque["contact"])}</p>'

    pages = findings.get("pages_checked") or []
    pages_note = ""
    if pages:
        if len(pages) == 1:
            opened_pages = "the home page and " + escape(pages[0])
        elif len(pages) == 2:
            opened_pages = "the home page, " + escape(pages[0]) + " and " + escape(pages[1])
        else:
            opened_pages = "the home page, " + ", ".join(escape(p) for p in pages[:-1]) + " and " + escape(pages[-1])
        pages_note = f"Pages opened: {opened_pages}."
    elif row.get("website"):
        pages_note = "Page opened: the home page."

    site = ""
    if row.get("website"):
        site = " &middot; " + escape(row["website"])

    return _PAGE.format(
        name=escape(row.get("name", "your business")),
        site=site,
        colour=escape(marque["colour"]),
        by=by,
        lede=escape(_lede(len(problems), findings)),
        problems=problem_html,
        working=working_html,
        scope="".join(f"<li>{escape(line)}</li>" for line in _not_checked(findings)),
        cta=escape(marque["cta"]),
        contact=contact,
        stamp=escape(stamp or datetime.date.today().isoformat()),
        pages=pages_note,
    )


def safe_filename(name, fallback="report"):
    """A file name from a business name. Safe on every operating system."""
    cleaned = "".join(
        character if character.isalnum() or character in " -_" else "-"
        for character in (name or "")
    ).strip().strip("-").strip()
    cleaned = "-".join(cleaned.split())[:60]
    return (cleaned or fallback).lower()


def write_reports(rows, folder, findings_by_name=None, stamp=None, log=print):
    """
    Write one report per lead into `folder`. Give back the paths written.

    A lead with no website gets no report: there is nothing to review, and a
    report that says "you have no website" is a worse opening than a phone call.
    """
    os.makedirs(folder, exist_ok=True)
    findings_by_name = findings_by_name or {}
    written = []
    used = set()
    for row in rows:
        if not (row.get("website") or "").strip():
            continue
        if row.get("status") not in ("ok", None, ""):
            # A site that did not load cannot be reviewed honestly.
            continue
        stem = safe_filename(row.get("name"))
        candidate, number = stem, 2
        while candidate in used:
            candidate, number = f"{stem}-{number}", number + 1
        used.add(candidate)

        path = os.path.join(folder, candidate + ".html")
        # The full findings ride on the row, so no website is reopened here.
        findings = row.get("_findings") or findings_by_name.get(row.get("name"))
        page = build(row, findings or row_findings(row), stamp=stamp)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(page)
        written.append(path)
    log(f"  {len(written)} prospect reports written to {folder}/")
    return written


def row_findings(row):
    """
    Rebuild the findings a report needs from a flat output row.

    The call sheet flattens lists into strings, so this turns them back. It
    means a report can be regenerated from a saved CSV or journal without
    opening a single website again.
    """
    def as_list(value, sep=","):
        return [part.strip() for part in (value or "").split(sep) if part.strip()]

    return {
        "capture_methods": as_list(row.get("capture_methods")),
        "ad_tags": as_list(row.get("ad_tags")),
        "instagram": row.get("instagram", ""),
        "tiktok": row.get("tiktok", ""),
        "load_seconds": row.get("load_seconds"),
        "is_slow": bool(row.get("load_seconds")) and float(row["load_seconds"]) > 5,
        "is_https": (row.get("website") or "").lower().startswith("https://"),
        "has_mobile_viewport": "not built for a phone" not in (row.get("reasons") or ""),
        "is_parked": "parked" in (row.get("reasons") or ""),
        "pages_checked": as_list(row.get("pages_checked"), sep="|"),
    }
