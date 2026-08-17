"""
outreach.py
-----------
Writes the first message, and exports the list in the shape an outreach tool
wants.

WHY
Every competing tool stops at a list. The caller then sits with a spreadsheet
and writes the same message forty times, which is the step that actually stops
people from working the list. The scan already knows the one true thing about
each firm, so it can write the opening.

THE RULE IS THE SAME AS EVERYWHERE ELSE
The message says only what the scan saw. It never claims an advertising budget
that was not detected, and it never promises a result. It offers the review,
because the review is the thing of value and it costs nothing to give.

Two channels, because Singapore small businesses answer WhatsApp and ignore
email:
  * a WhatsApp or SMS opener, short, no links, easy to read on a phone;
  * an email with a subject line and a body, for the firms with a real address.
"""

import csv
import urllib.parse

import verify

# The most useful single fact about the firm, in the firm's own words.
def _observation(row):
    tags = [t.strip() for t in (row.get("ad_tags") or "").split(",") if t.strip()]
    capture = (row.get("capture_methods") or "").strip()
    reasons = row.get("reasons") or ""

    if "parked" in reasons:
        return ("your web address opens a holding page rather than your site",
                "anyone who looks you up finds nothing")
    if not capture:
        if tags:
            return (f"you have a {tags[0]} running but no form, booking link or "
                    f"tap-to-call on the page",
                    "the traffic you pay for has nowhere to go")
        return ("there is no form, booking link or tap-to-call on the page",
                "people who are ready to buy have no easy next step")
    if "not built for a phone" in reasons or "phone screen" in reasons:
        return ("the site is not set up for a phone screen",
                "most people who search for a local firm are on a phone")
    if "HTTPS" in reasons or "padlock" in reasons:
        return ("there is no padlock on the address",
                "the browser shows a warning before anyone reads a word")
    if "s to load" in reasons:
        return ("the page is slow to appear",
                "a lot of people leave before they see anything")
    return ("there are one or two things getting in the way",
            "you are probably losing enquiries you never hear about")


def first_name(row):
    """
    There is no contact name in the data, so use the business name.

    Guessing a person's name from an email address was considered and dropped.
    "Hi Info," and a wrong name are both worse than no name at all.
    """
    return (row.get("name") or "there").strip()


def whatsapp_message(row, brand_name=""):
    """A short opener for WhatsApp or SMS. No links: they look like spam."""
    what, why = _observation(row)
    who = f" I run {brand_name}." if brand_name else ""
    return (
        f"Hi, is this {first_name(row)}?{who} I was looking at local firms "
        f"today and noticed {what}, so {why}. I wrote up a short review of the "
        f"site with what I would change first. Would it help if I sent it over? "
        f"No charge either way."
    )


def email_subject(row):
    what, _why = _observation(row)
    if "holding page" in what:
        return f"{first_name(row)} - your web address is not opening your site"
    if "no form" in what or "tap-to-call" in what:
        return f"{first_name(row)} - a quick note on your website's enquiry form"
    return f"{first_name(row)} - a short review of your website"


def email_body(row, brand_name="", brand_contact=""):
    what, why = _observation(row)
    signature = f"\n\n{brand_name}" if brand_name else ""
    if brand_contact:
        signature += f"\n{brand_contact}"
    return (
        f"Hi {first_name(row)},\n\n"
        f"I was looking through local firms this week and had a look at your "
        f"site. One thing stood out: {what}, so {why}.\n\n"
        f"I put together a one-page review of it. It lists what is working, "
        f"the two or three things I would change first, and exactly what I "
        f"checked and what I did not. It is attached, and it is yours whether "
        f"or not we ever speak.\n\n"
        f"If any of it is useful, I am happy to go through it on a short call."
        f"{signature}"
    )


def whatsapp_link(row, brand_name=""):
    """A wa.me link that opens the chat with the message already typed."""
    digits = "".join(character for character in (row.get("phone") or "")
                     if character.isdigit())
    if not digits:
        return ""
    if len(digits) == 8:            # a local Singapore number
        digits = "65" + digits
    text = urllib.parse.quote(whatsapp_message(row, brand_name))
    return f"https://wa.me/{digits}?text={text}"


def add_drafts(rows, brand_name="", brand_contact=""):
    """Put the drafts on every row, so they reach the CSV and the sheet."""
    for row in rows:
        row["whatsapp_message"] = whatsapp_message(row, brand_name)
        row["whatsapp_link"] = whatsapp_link(row, brand_name)
        row["email_subject"] = email_subject(row)
        row["email_body"] = email_body(row, brand_name, brand_contact)
    return rows


# ---------------------------------------------------------------------------
# The CRM / outreach-tool export
# ---------------------------------------------------------------------------
# Instantly, Lemlist, Smartlead and HubSpot all import a flat CSV and map the
# extra columns to merge fields. The column names below are the ones those
# tools expect, so the file imports without renaming anything.

CRM_FIELDS = [
    "email", "first_name", "company_name", "phone", "website",
    "custom_observation", "custom_consequence", "custom_tier",
    "custom_score", "custom_ad_tags", "custom_reviews", "custom_city",
]


def write_crm_csv(rows, path, only_with_email=True):
    """
    Write the list in the shape an outreach tool imports.

    Rows with no usable email address are dropped by default: importing them
    creates contacts that can never be mailed and inflates the bounce rate that
    every later send depends on.
    """
    keep = []
    for row in rows:
        address = (row.get("email") or "").strip()
        if only_with_email:
            if not address:
                continue
            if not verify.grade(address, check_mx=True)["deliverable"]:
                continue
        what, why = _observation(row)
        keep.append({
            "email": address,
            "first_name": first_name(row),
            "company_name": row.get("name", ""),
            "phone": row.get("phone", ""),
            "website": row.get("website", ""),
            "custom_observation": what,
            "custom_consequence": why,
            "custom_tier": row.get("tier", ""),
            "custom_score": row.get("score", ""),
            "custom_ad_tags": row.get("ad_tags", ""),
            "custom_reviews": row.get("review_count", ""),
            "custom_city": (row.get("address") or "").split(",")[-1].strip(),
        })

    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CRM_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in keep:
            writer.writerow({key: _safe(value) for key, value in record.items()})
    return path, len(keep)


def _safe(value):
    """Stop a spreadsheet reading a value as a formula. Same rule as report.py."""
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
