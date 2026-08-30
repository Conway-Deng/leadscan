"""
report.py
---------
Turns the scored rows into the three things a caller actually uses:

  * warm_leads.csv   -- the machine-readable record
  * warm_leads.html  -- the call sheet. Open it in a browser and start calling.
  * warm_leads.xlsx  -- the same sheet for anyone who prefers a spreadsheet.

SPREADSHEET SAFETY
A business name can start with "=", "+", "-" or "@". Excel and Google Sheets
read such a value as a formula, which is a known way to attack a person who
opens a downloaded file. Every text value written to CSV or XLSX is therefore
given a leading apostrophe when it starts with one of those characters.
"""

import csv
import html as html_module
import os

import scoring

CSV_FIELDS = [
    "tier", "score", "name", "phone", "email", "review_count", "rating",
    "instagram_followers", "email_grade", "ad_tags", "capture_methods",
    "pages_checked", "hook",
    "website", "instagram", "facebook", "tiktok", "address", "opening_hours",
    "reasons", "status", "whatsapp_message", "email_subject", "email_body",
]

# Column heading, source key, and whether the value is a link.
SHEET_COLUMNS = [
    ("Tier", "tier", False),
    ("Score", "score", False),
    ("Business", "name", False),
    ("Phone", "phone", False),
    ("What to say", "hook", False),
    ("Advertising tags installed", "ad_tags", False),
    ("Reviews", "review_count", False),
    ("IG followers", "instagram_followers", False),
    ("Email", "email", False),
    ("Email quality", "email_grade", False),
    ("Opening hours", "opening_hours", False),
    ("Website", "website", True),
    ("Instagram", "instagram", True),
    ("Facebook", "facebook", True),
    ("TikTok", "tiktok", True),
    ("Status", "status", False),
]

_DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Stop a spreadsheet from reading a text value as a formula."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if value[:1] in _DANGEROUS_PREFIX:
        return "'" + value
    return value


def select_leads(results, want, include_cool=False):
    """
    Keep the leads worth calling, best first.

    'hot'  = advertising-related infrastructure was found and the funnel leaks.
    'warm' = the firm builds an audience on social and the funnel leaks.
    'cool' = a quiet firm with a real defect, but no proof of marketing effort.
    """
    keep = [r for r in results if r["warm"] and not r["disqualified"]]
    if not include_cool:
        keep = [r for r in keep if r.get("tier") != scoring.TIER_COOL] or keep
    keep.sort(key=scoring.sort_key)
    return keep[:want]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: safe_cell(row.get(key, "")) for key in CSV_FIELDS})
    return path


# ---------------------------------------------------------------------------
# The HTML call sheet
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LeadScan call sheet</title>
<style>
  :root {{ color-scheme: light dark;
    --bg:#f6f7f9; --card:#fff; --ink:#16181d; --muted:#5c6472; --line:#e3e6ec;
    --hot:#c2410c; --hotbg:#fff2e8; --warm:#a16207; --warmbg:#fef6e0;
    --cool:#3f6212; --coolbg:#f0f6e4; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#0f1115; --card:#171a20; --ink:#e8eaee; --muted:#9aa3b2; --line:#272c35;
    --hotbg:#3a1e10; --warmbg:#3a2f10; --coolbg:#1f2d14;
    --hot:#fb923c; --warm:#fbbf24; --cool:#a3e635; }} }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  header {{ max-width:1180px; margin:0 auto 20px; }}
  h1 {{ font-size:22px; margin:0 0 4px; letter-spacing:-.01em }}
  .sub {{ color:var(--muted); font-size:13px }}
  .counts {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px }}
  .pill {{ padding:4px 10px; border-radius:999px; font-size:12px; font-weight:600;
    border:1px solid var(--line); background:var(--card) }}
  .pill.hot {{ color:var(--hot); background:var(--hotbg) }}
  .pill.warm {{ color:var(--warm); background:var(--warmbg) }}
  .pill.cool {{ color:var(--cool); background:var(--coolbg) }}
  main {{ max-width:1180px; margin:0 auto; display:grid; gap:12px }}
  .lead {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:14px 16px; display:grid; grid-template-columns:auto 1fr auto; gap:14px;
    align-items:start }}
  .lead.done {{ opacity:.45 }}
  .tick {{ width:22px; height:22px; margin-top:3px; cursor:pointer; accent-color:#2563eb }}
  .rank {{ font-size:12px; color:var(--muted); font-weight:600 }}
  .name {{ font-size:16px; font-weight:650; margin:2px 0 4px }}
  .hook {{ margin:8px 0 0; padding:9px 11px; border-left:3px solid var(--line);
    background:var(--bg); border-radius:0 8px 8px 0; font-size:14px }}
  .meta {{ display:flex; gap:14px; flex-wrap:wrap; font-size:12.5px;
    color:var(--muted); margin-top:8px }}
  .meta a {{ color:inherit }}
  .side {{ text-align:right; display:grid; gap:6px; justify-items:end }}
  .tel {{ font-size:16px; font-weight:650; text-decoration:none; color:inherit;
    white-space:nowrap }}
  .tel:hover {{ text-decoration:underline }}
  .score {{ font-size:12px; color:var(--muted) }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:999px;
    font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em }}
  .tag.hot {{ color:var(--hot); background:var(--hotbg) }}
  .tag.warm {{ color:var(--warm); background:var(--warmbg) }}
  .tag.cool {{ color:var(--cool); background:var(--coolbg) }}
  footer {{ max-width:1180px; margin:24px auto 0; color:var(--muted); font-size:12px }}
  @media (max-width:720px) {{
    body {{ padding:14px }}
    .lead {{ grid-template-columns:auto 1fr; }}
    .side {{ grid-column:2; justify-items:start; text-align:left }}
  }}
  @media print {{
    body {{ background:#fff; padding:0 }} .tick {{ display:none }}
    .lead {{ break-inside:avoid; border-color:#ccc }}
  }}
</style></head>
<body>
<header>
  <h1>LeadScan call sheet</h1>
  <div class="sub">{count} leads, best first. Generated {stamp}. Tick a row when the call is done.</div>
  <div class="counts">{pills}</div>
</header>
<main>
{cards}
</main>
<footer>
  These records hold business phone numbers taken from public listings. Keep the
  file private and delete it when the campaign ends.
</footer>
<script>
document.querySelectorAll('.tick').forEach(function (box) {{
  box.addEventListener('change', function () {{
    box.closest('.lead').classList.toggle('done', box.checked);
  }});
}});
</script>
</body></html>
"""

_CARD = """  <article class="lead">
    <input class="tick" type="checkbox" aria-label="Mark {name} as called">
    <div>
      <div class="rank">#{rank}</div>
      <div class="name">{name}</div>
      <span class="tag {tier}">{tier}</span>{adnote}
      <p class="hook">{hook}</p>
      <div class="meta">{meta}</div>
    </div>
    <div class="side">{call}<div class="score">score {score}</div></div>
  </article>"""


def write_html(rows, path, stamp=""):
    cards = []
    for index, row in enumerate(rows, 1):
        tier = row.get("tier") or "cool"
        phone = (row.get("phone") or "").strip()
        if phone:
            call = (f'<a class="tel" href="tel:{html_module.escape(_tel(phone))}">'
                    f'{html_module.escape(phone)}</a>')
        else:
            call = '<span class="tel">no phone listed</span>'

        meta = []
        if row.get("review_count") is not None:
            meta.append(f"{row['review_count']} reviews")
        if row.get("instagram_followers"):
            meta.append(f"{row['instagram_followers']:,} IG followers")
        if row.get("email"):
            label = html_module.escape(row["email"])
            note = (row.get("email_grade") or "").strip()
            if note and note != "personal":
                label += f" ({html_module.escape(note)})"
            meta.append(f'<a href="mailto:{html_module.escape(row["email"])}">'
                        f'{label}</a>')
        for label, key in (("website", "website"), ("Instagram", "instagram"),
                           ("Facebook", "facebook"), ("TikTok", "tiktok")):
            link = (row.get(key) or "").strip()
            if link:
                meta.append(f'<a href="{html_module.escape(link)}" target="_blank" '
                            f'rel="noopener noreferrer">{label}</a>')
        hours = (row.get("opening_hours") or "").strip()
        if hours:
            meta.append(f'<span title="{html_module.escape(hours)}">opening hours &#9432;</span>')
        if row.get("status") and row["status"] != "ok":
            meta.append(html_module.escape(str(row["status"])))

        chat = (row.get("whatsapp_link") or "").strip()
        if chat:
            meta.append(f'<a href="{html_module.escape(chat)}" target="_blank" '
                        f'rel="noopener noreferrer">open WhatsApp with the '
                        f'message ready</a>')
        adtags = (row.get("ad_tags") or "").strip()
        adnote = (f' <span class="score">{html_module.escape(adtags)}</span>'
                  if adtags else "")

        cards.append(_CARD.format(
            rank=index,
            name=html_module.escape(row.get("name", "")),
            tier=html_module.escape(tier),
            adnote=adnote,
            hook=html_module.escape(row.get("hook", "")),
            meta=" &middot; ".join(meta),
            call=call,
            score=row.get("score", 0),
        ))

    counts = {}
    for row in rows:
        counts[row.get("tier") or "cool"] = counts.get(row.get("tier") or "cool", 0) + 1
    pills = "".join(
        f'<span class="pill {tier}">{counts[tier]} {tier}</span>'
        for tier in ("hot", "warm", "cool") if counts.get(tier)
    )

    page = _PAGE.format(
        count=len(rows),
        stamp=html_module.escape(stamp),
        pills=pills or '<span class="pill">no leads</span>',
        cards="\n".join(cards) or "<p>No leads matched. Widen the sweep or lower the bar.</p>",
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)
    return path


def _tel(phone):
    """Make a dialable tel: value. Keep a leading plus, drop everything else."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return ("+" if phone.strip().startswith("+") else "") + digits


# ---------------------------------------------------------------------------
# The spreadsheet
# ---------------------------------------------------------------------------

def write_xlsx(rows, path):
    """Write the call sheet as a real spreadsheet. Needs openpyxl."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None

    tier_fill = {
        "hot": PatternFill("solid", fgColor="FFE7D6"),
        "warm": PatternFill("solid", fgColor="FDF1D0"),
        "cool": PatternFill("solid", fgColor="EAF2DC"),
    }
    widths = {"Business": 30, "What to say": 78, "Phone": 18, "Website": 34,
              "Email": 28, "Advertising tags installed": 24, "Opening hours": 40,
              "Instagram": 30, "Facebook": 30,
              "TikTok": 30, "Status": 16}

    book = Workbook()
    sheet = book.active
    sheet.title = "Call sheet"

    headings = [column[0] for column in SHEET_COLUMNS]
    sheet.append(headings)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F3B52")
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"

    for row in rows:
        values = []
        for _, key, _is_link in SHEET_COLUMNS:
            values.append(safe_cell(row.get(key, "")))
        sheet.append(values)
        fill = tier_fill.get(row.get("tier"))
        if fill:
            sheet.cell(row=sheet.max_row, column=1).fill = fill

    # Make the links clickable.
    for index, (_heading, key, is_link) in enumerate(SHEET_COLUMNS, start=1):
        if not is_link:
            continue
        for number, row in enumerate(rows, start=2):
            link = (row.get(key) or "").strip()
            if link.startswith(("http://", "https://")):
                cell = sheet.cell(row=number, column=index)
                cell.hyperlink = link
                cell.style = "Hyperlink"

    for index, heading in enumerate(headings, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(heading, 14)
    for number in range(2, sheet.max_row + 1):
        sheet.cell(row=number, column=5).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[number].height = 42

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headings))}{sheet.max_row}"
    book.save(path)
    return path


def write_all(rows, base_path, stamp=""):
    """Write every format next to `base_path`. Give back the paths written."""
    stem, _ = os.path.splitext(base_path)
    folder = os.path.dirname(os.path.abspath(stem))
    if folder:
        os.makedirs(folder, exist_ok=True)
    written = {"csv": write_csv(rows, stem + ".csv"),
               "html": write_html(rows, stem + ".html", stamp)}
    xlsx = write_xlsx(rows, stem + ".xlsx")
    if xlsx:
        written["xlsx"] = xlsx
    return written
