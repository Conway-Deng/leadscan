"""Turn warm_leads.csv into a clean, readable call sheet.

Outputs warm_leads.html -- opens in any web browser, no software needed.
"""
import csv
import html

SRC = "warm_leads.csv"
OUT = "warm_leads.html"

# Columns to show while calling, in a sensible order.
COLS = [
    ("score", "Score"),
    ("name", "Business"),
    ("phone", "Phone"),
    ("hook", "Hook (what to say)"),
    ("review_count", "Reviews"),
    ("instagram_followers", "IG followers"),
    ("website", "Website"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("status", "Status"),
]
LINK_COLS = {"website", "instagram", "facebook"}

with open(SRC, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

rows.sort(key=lambda r: int(r["score"]) if r.get("score", "").strip().isdigit() else 0,
          reverse=True)


def score_class(score):
    if score >= 70:
        return "hot"
    if score >= 50:
        return "warm"
    return "cool"


def cell_html(key, val):
    val = (val or "").strip()
    if not val:
        return '<span class="empty">&mdash;</span>'
    if key in LINK_COLS and val.startswith("http"):
        label = val.replace("https://", "").replace("http://", "").rstrip("/")
        if len(label) > 40:
            label = label[:38] + "…"
        return f'<a href="{html.escape(val)}" target="_blank">{html.escape(label)}</a>'
    if key == "phone":
        tel = val.replace(" ", "")
        return f'<a href="tel:{html.escape(tel)}" class="phone">{html.escape(val)}</a>'
    return html.escape(val)


tbody = []
for row in rows:
    score = int(row["score"]) if row.get("score", "").strip().isdigit() else 0
    tds = []
    for key, _ in COLS:
        cls = ""
        if key == "score":
            cls = f' class="score {score_class(score)}"'
        elif key == "hook":
            cls = ' class="hook"'
        elif key == "name":
            cls = ' class="name"'
        elif key in ("review_count", "instagram_followers"):
            cls = ' class="num"'
        tds.append(f"<td{cls}>{cell_html(key, row.get(key))}</td>")
    tbody.append("      <tr>\n        " + "\n        ".join(tds) + "\n      </tr>")

headers = "".join(f"<th>{html.escape(label)}</th>" for _, label in COLS)

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Warm Leads &mdash; Interior Design</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    margin: 0; padding: 24px; background: #f4f6fa; color: #1a2230;
  }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .sub {{ color: #6a7688; margin: 0 0 16px; font-size: 14px; }}
  .toolbar {{ margin-bottom: 14px; }}
  #search {{
    width: 340px; max-width: 100%; padding: 10px 12px; font-size: 15px;
    border: 1px solid #c7d0dd; border-radius: 8px; outline: none;
  }}
  #search:focus {{ border-color: #1f4e78; box-shadow: 0 0 0 3px rgba(31,78,120,.15); }}
  #count {{ margin-left: 12px; color: #6a7688; font-size: 14px; }}
  .wrap {{ overflow-x: auto; background: #fff; border-radius: 12px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
  th, td {{ padding: 10px 12px; text-align: left; vertical-align: top;
            border-bottom: 1px solid #eef1f6; }}
  thead th {{
    position: sticky; top: 0; background: #1f4e78; color: #fff;
    font-weight: 600; font-size: 13px; cursor: default; z-index: 2;
  }}
  tbody tr:hover {{ background: #f0f5fc; }}
  td.name {{ font-weight: 600; min-width: 200px; }}
  td.hook {{ min-width: 360px; max-width: 460px; color: #333c4d; line-height: 1.45; }}
  td.num {{ text-align: center; color: #55617a; }}
  a {{ color: #1f4e78; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  a.phone {{ font-weight: 600; white-space: nowrap; }}
  .empty {{ color: #c2cad6; }}
  .score {{ text-align: center; font-weight: 700; border-radius: 6px; }}
  .score.hot  {{ background: #c6efce; color: #14682a; }}
  .score.warm {{ background: #ffeb9c; color: #7a5b00; }}
  .score.cool {{ background: #fce4d6; color: #9c4310; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #10141c; color: #e6ebf3; }}
    .wrap {{ background: #1a2130; box-shadow: none; }}
    th, td {{ border-bottom-color: #262f40; }}
    td.hook {{ color: #b9c3d4; }}
    tbody tr:hover {{ background: #202a3b; }}
    #search {{ background: #1a2130; color: #e6ebf3; border-color: #33405a; }}
  }}
</style>
</head>
<body>
  <h1>Warm Leads &mdash; Interior Design</h1>
  <p class="sub">{len(rows)} businesses, sorted hottest first. Green = 70+, yellow = 50&ndash;69, orange = under 50. Click a phone to dial, or a link to open.</p>
  <div class="toolbar">
    <input id="search" type="search" placeholder="Filter by name, hook, status&hellip;" autofocus>
    <span id="count"></span>
  </div>
  <div class="wrap">
    <table id="leads">
      <thead><tr>{headers}</tr></thead>
      <tbody>
{chr(10).join(tbody)}
      </tbody>
    </table>
  </div>
<script>
  const rowsEl = Array.from(document.querySelectorAll('#leads tbody tr'));
  const search = document.getElementById('search');
  const count = document.getElementById('count');
  function apply() {{
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    for (const tr of rowsEl) {{
      const hit = !q || tr.textContent.toLowerCase().includes(q);
      tr.style.display = hit ? '' : 'none';
      if (hit) shown++;
    }}
    count.textContent = shown + ' shown';
  }}
  search.addEventListener('input', apply);
  apply();
</script>
</body>
</html>
"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(page)

print(f"Wrote {OUT} with {len(rows)} leads. Double-click it to open in your browser.")
