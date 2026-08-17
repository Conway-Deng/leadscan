"""
verify.py
---------
Grades an email address before you use it.

WHY THIS IS HERE
Two of the paid tools in this market (Outscraper, Scrap.io) sell "email
enrichment" as a headline feature. LeadScan already pulls addresses off the
site and the contact page, but a scraped address is not the same as a usable
one. Three things go wrong:

  * `info@` and `enquiries@` reach a shared inbox that nobody owns. They are
    fine for a form-fill, and close to useless for a personal first email.
  * A typed domain (`gmial.com`) or a builder placeholder (`you@example.com`)
    bounces, and a bounce hurts the sending domain that every later email
    depends on.
  * A disposable domain is somebody's throwaway, not the business.

So each address gets a grade, and the call sheet shows it. No address is
deleted: a shared inbox is still better than nothing, and the caller decides.

WHAT THIS DOES NOT DO
It never connects to a mail server to ask whether a mailbox exists. That check
("SMTP handshake") is what paid verification services sell. It is slow, it is
often blocked, and doing it at volume from your own address gets you listed as
a spam probe. If you need mailbox-level certainty, pay a verification service
for the final list. This module removes the addresses that are obviously not
worth sending to, which is most of the value for a fraction of the trouble.

The MX record check runs only when `dnspython` is installed. Without it, every
other check still runs and the domain grade is reported as unknown.
"""

import re

# A local part that reaches a shared inbox, not a person.
ROLE_ADDRESSES = {
    "info", "enquiry", "enquiries", "inquiry", "inquiries", "contact",
    "hello", "hi", "admin", "office", "sales", "support", "help", "team",
    "mail", "email", "general", "ask", "customerservice", "cs", "service",
    "marketing", "billing", "accounts", "finance", "hr", "careers", "jobs",
    "webmaster", "postmaster", "abuse", "noreply", "no-reply", "donotreply",
}

# Domains that belong to a person's throwaway address, not a business.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "temp-mail.org", "dispostable.com", "maildrop.cc",
}

# Placeholder addresses left behind by a website template.
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "yoursite.com", "email.com", "sentry.io", "wixpress.com", "test.com",
    "mysite.com", "website.com", "company.com",
}

# Common typing mistakes in a free-mail domain. A bounce costs more than the
# two seconds it takes to check.
TYPO_DOMAINS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.co": "gmail.com",
    "gmail.con": "gmail.com", "gnail.com": "gmail.com", "gmil.com": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com",
    "yahooo.com": "yahoo.com", "yaho.com": "yahoo.com",
    "outlok.com": "outlook.com", "outloo.com": "outlook.com",
}

# A free-mail address is a real person, but it says the firm has no business
# mail. That is itself a useful fact on a call.
FREE_MAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "live.com",
    "icloud.com", "qq.com", "163.com", "singnet.com.sg", "yahoo.com.sg",
}

_SYNTAX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}$")

# Grades, best first.
GRADE_PERSONAL = "personal"     # a named person at the company domain
GRADE_SHARED = "shared"         # info@, enquiries@ -- a shared inbox
GRADE_FREEMAIL = "freemail"     # a real person, but on gmail and similar
GRADE_BAD = "unusable"          # placeholder, disposable, typo, bad syntax


def grade(address, check_mx=None):
    """
    Grade one address.

    Give back a dict: address, grade, reason, deliverable.
    `deliverable` is True unless something says the address will bounce.
    """
    address = (address or "").strip().lower()
    if not address or not _SYNTAX.match(address):
        return _out(address, GRADE_BAD, "not a valid email address", False)

    local, _, domain = address.partition("@")

    if domain in PLACEHOLDER_DOMAINS:
        return _out(address, GRADE_BAD, "a website template placeholder", False)
    if domain in DISPOSABLE_DOMAINS:
        return _out(address, GRADE_BAD, "a disposable address", False)
    if domain in TYPO_DOMAINS:
        return _out(address, GRADE_BAD,
                    f"looks like a typing mistake for {TYPO_DOMAINS[domain]}", False)

    if check_mx is None:
        check_mx = has_mx(domain)
    if check_mx is False:
        return _out(address, GRADE_BAD, "the domain accepts no mail", False)

    base = local.split("+")[0].replace(".", "").replace("-", "").replace("_", "")
    if base in ROLE_ADDRESSES:
        return _out(address, GRADE_SHARED,
                    "reaches a shared inbox, not one person", True)
    if domain in FREE_MAIL_DOMAINS:
        return _out(address, GRADE_FREEMAIL,
                    "a personal free-mail address, not a company one", True)
    return _out(address, GRADE_PERSONAL, "a named person at the company domain", True)


def best(addresses, check_mx=None):
    """Pick the most useful address from a list. None when there is none."""
    graded = [grade(a, check_mx=check_mx) for a in addresses or []]
    usable = [g for g in graded if g["deliverable"]]
    if not usable:
        return None
    order = {GRADE_PERSONAL: 0, GRADE_SHARED: 1, GRADE_FREEMAIL: 2}
    usable.sort(key=lambda g: order.get(g["grade"], 3))
    return usable[0]


def has_mx(domain):
    """
    True when the domain publishes a mail record.

    Give back None when the check cannot run, which is not the same as False.
    None means unknown, and unknown never condemns an address.
    """
    try:
        import dns.resolver
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        # A missing record, a timeout and a blocked resolver all land here.
        # Only a clear "no such record" should condemn the address, and this
        # library does not separate them reliably, so treat it all as unknown.
        return None


def _out(address, grade_name, reason, deliverable):
    return {"address": address, "grade": grade_name,
            "reason": reason, "deliverable": deliverable}
