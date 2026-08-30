"""
Tests for the prospect audit report and the email grader.

These two features exist because of what the competing tools sell. Every paid
agency audit tool leads with a branded report for the prospect; two of the
Google Maps lead tools lead with email enrichment. The tests below guard the
part that matters most: the report must never say more than the scan saw.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_report  # noqa: E402
import verify  # noqa: E402


BRAND = {"name": "Nixon Media", "tagline": "SG", "contact": "nixon@example.sg",
         "colour": "#0f766e", "cta": "Happy to talk."}


def row(**extra):
    base = {"name": "Quiet Interiors", "website": "https://quiet.sg",
            "review_count": 14, "status": "ok", "score": 100, "tier": "hot"}
    base.update(extra)
    return base


def findings(**extra):
    base = {"capture_methods": [], "ad_tags": [], "analytics_tags": [],
            "is_https": True, "has_mobile_viewport": True, "is_slow": False,
            "load_seconds": 1.5, "is_parked": False, "instagram": "",
            "tiktok": "", "emails": [], "pages_checked": []}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# The report must not overclaim
# ---------------------------------------------------------------------------

def test_no_ad_tag_means_no_mention_of_ad_spend():
    """A prospect who buys no ads must not be told they do."""
    page = audit_report.build(row(), findings(), BRAND)
    assert "paying to bring people" not in page
    assert "Google Ads" not in page


def test_an_ad_tag_is_named_and_only_then():
    page = audit_report.build(row(), findings(ad_tags=["Meta Pixel"]), BRAND)
    assert "Meta Pixel" in page
    assert "installed" in page
    assert "paying to bring people" not in page


def test_the_scope_section_is_always_present():
    """The 'what this did not check' block is what keeps the report honest."""
    page = audit_report.build(row(), findings(), BRAND)
    assert "What this review did not check" in page
    assert "Search rankings" in page
    assert "advertising accounts" in page


def test_a_working_site_is_told_so_rather_than_scared():
    page = audit_report.build(
        row(),
        findings(capture_methods=["contact form", "WhatsApp link"]),
        BRAND,
    )
    assert "did not find anything obviously getting in the way" in page
    assert "What is costing you enquiries" not in page
    assert "What may be making enquiries harder" not in page


def test_no_unsupported_visitor_behavior_claims_on_missing_capture():
    page = audit_report.build(row(), findings(capture_methods=[]), BRAND)
    assert "Most do not" not in page
    assert "cannot contact you directly from this page" in page


def test_slow_page_does_not_claim_measured_abandonment_or_guaranteed_halving():
    page = audit_report.build(
        row(),
        findings(is_slow=True, load_seconds=8.2),
        BRAND,
    )
    assert "leave before they see anything" not in page
    assert "large share of visitors" not in page
    assert "halves the load time" not in page
    assert "photographs are the whole problem" not in page
    assert "That measured load time is slow" in page


def test_https_recommendation_does_not_claim_every_host_free_or_instant():
    page = audit_report.build(row(), findings(is_https=False), BRAND)
    assert "Every host offers one free" not in page
    assert "usually takes minutes" not in page
    assert "Enable an SSL/TLS certificate" in page


def test_problem_heading_is_not_causal_costing_enquiries():
    page = audit_report.build(row(), findings(capture_methods=[]), BRAND)
    assert "What is costing you enquiries" not in page
    assert "What may be making enquiries harder" in page


def test_social_links_do_not_claim_active_effort_or_audience_growth():
    page = audit_report.build(
        row(),
        findings(instagram="https://instagram.com/firm", tiktok="https://tiktok.com/@firm"),
        BRAND,
    )
    assert "putting real work into your social accounts" not in page
    assert "building an audience on" not in page
    assert "Your website links to your Instagram and TikTok profile." in page


def test_mobile_viewport_does_not_claim_all_templates_or_just_a_setting():
    page = audit_report.build(row(), findings(has_mobile_viewport=False), BRAND)
    assert "Any modern template does this" not in page
    assert "usually a setting, not a rebuild" not in page
    assert "Configure a responsive mobile viewport" in page


def test_every_real_defect_gets_a_fix_not_just_a_complaint():
    page = audit_report.build(
        row(),
        findings(has_mobile_viewport=False, is_https=False, is_slow=True,
                 load_seconds=9.4),
        BRAND,
    )
    assert page.count("What I would do:") == 4       # capture, mobile, slow, https
    assert "9.4 seconds" in page


def test_the_good_news_is_included():
    page = audit_report.build(
        row(),
        findings(capture_methods=["contact form"], instagram="https://ig/x"),
        BRAND,
    )
    assert "What is already working" in page
    assert "Instagram" in page
    assert "14 Google reviews" in page


def test_pages_checked_empty_reports_only_home_page():
    page = audit_report.build(row(), findings(pages_checked=[]), BRAND)
    assert "Only the home page was opened." in page
    assert "Page opened: the home page." in page
    assert "the contact page" not in page


def test_pages_checked_single_page_uses_actual_url_without_contact_page_assumption():
    page = audit_report.build(
        row(),
        findings(pages_checked=["https://quiet.sg/get-a-quote"]),
        BRAND,
    )
    assert "https://quiet.sg/get-a-quote" in page
    assert "Only the home page and https://quiet.sg/get-a-quote were opened." in page
    assert "Pages opened: the home page and https://quiet.sg/get-a-quote." in page
    assert "the contact page" not in page


def test_pages_checked_two_pages_lists_both_actual_urls():
    page = audit_report.build(
        row(),
        findings(pages_checked=["https://quiet.sg/get-a-quote", "https://quiet.sg/book"]),
        BRAND,
    )
    assert "https://quiet.sg/get-a-quote" in page
    assert "https://quiet.sg/book" in page
    assert "Only the home page, https://quiet.sg/get-a-quote and https://quiet.sg/book were opened." in page
    assert "Pages opened: the home page, https://quiet.sg/get-a-quote and https://quiet.sg/book." in page
    assert "the contact page" not in page


def test_pages_checked_hostile_url_is_escaped():
    hostile = "https://quiet.sg/<script>alert(1)</script>"
    page = audit_report.build(row(), findings(pages_checked=[hostile]), BRAND)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# ---------------------------------------------------------------------------
# Branding and safety
# ---------------------------------------------------------------------------

def test_the_brand_appears_on_the_report():
    page = audit_report.build(row(), findings(), BRAND)
    assert "Nixon Media" in page
    assert "nixon@example.sg" in page
    assert "#0f766e" in page


def test_a_hostile_business_name_is_escaped():
    page = audit_report.build(row(name="<script>alert(1)</script>"),
                              findings(), BRAND)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_the_report_is_one_self_contained_file():
    """It gets emailed, so it must not fetch anything from outside."""
    page = audit_report.build(row(), findings(), BRAND)
    for outside in ("<img", "src=\"http", "@import", "<link"):
        assert outside not in page


def test_the_file_name_is_safe_on_every_system():
    assert audit_report.safe_filename("Ft2 (Ubi) / Woodlands") == "ft2--ubi----woodlands"
    assert audit_report.safe_filename("") == "report"
    assert "/" not in audit_report.safe_filename("a/b\\c")
    assert len(audit_report.safe_filename("x" * 200)) <= 60


def test_report_print_css_rules_and_page_break_protections():
    page = audit_report.build(row(), findings(), BRAND)
    assert "@media print" in page
    assert "size: A4" in page or "size:A4" in page
    assert "margin: 10mm" in page or "margin:10mm" in page
    assert "break-inside:avoid" in page or "break-inside: avoid" in page
    assert "break-after:avoid" in page or "break-after: avoid" in page


def test_report_content_integrity_preserved_with_print_styles():
    page = audit_report.build(
        row(),
        findings(capture_methods=[], is_slow=True, load_seconds=8.0),
        BRAND,
    )
    assert "What this review did not check" in page
    assert "What may be making enquiries harder" in page
    assert "What I would do:" in page
    assert "8.0 seconds" in page


# ---------------------------------------------------------------------------
# Writing a folder of reports
# ---------------------------------------------------------------------------

def test_a_firm_with_no_website_gets_no_report(tmp_path):
    """'You have no website' is a worse opening than a phone call."""
    written = audit_report.write_reports(
        [row(website="", status="no website")], str(tmp_path), log=lambda m: None)
    assert written == []


def test_a_site_that_did_not_load_gets_no_report(tmp_path):
    """A site that was never read cannot be reviewed honestly."""
    written = audit_report.write_reports(
        [row(status="timeout after 20s")], str(tmp_path), log=lambda m: None)
    assert written == []


def test_two_firms_with_the_same_name_do_not_overwrite(tmp_path):
    rows = [row(_findings=findings()), row(_findings=findings())]
    written = audit_report.write_reports(rows, str(tmp_path), log=lambda m: None)
    assert len(written) == 2
    assert len(set(written)) == 2


def test_the_findings_ride_on_the_row_so_no_site_is_reopened(tmp_path):
    rows = [row(_findings=findings(ad_tags=["TikTok Pixel"]))]
    written = audit_report.write_reports(rows, str(tmp_path), log=lambda m: None)
    assert "TikTok Pixel" in open(written[0], encoding="utf-8").read()


def test_row_findings_restores_empty_or_missing_pages_checked():
    # Missing field
    assert audit_report.row_findings({})["pages_checked"] == []
    # Empty field
    assert audit_report.row_findings({"pages_checked": ""})["pages_checked"] == []


def test_row_findings_restores_single_persisted_page():
    result = audit_report.row_findings({"pages_checked": "https://studio.sg/contact"})
    assert result["pages_checked"] == ["https://studio.sg/contact"]


def test_row_findings_restores_two_persisted_pages_in_order():
    result = audit_report.row_findings(
        {"pages_checked": "https://studio.sg/quote | https://studio.sg/contact"}
    )
    assert result["pages_checked"] == ["https://studio.sg/quote", "https://studio.sg/contact"]


def test_row_findings_trims_whitespace_and_drops_empty_components():
    result = audit_report.row_findings(
        {"pages_checked": " https://studio.sg/quote  |  | https://studio.sg/contact "}
    )
    assert result["pages_checked"] == ["https://studio.sg/quote", "https://studio.sg/contact"]


def test_end_to_end_report_reconstruction_includes_all_restored_pages():
    r = row(pages_checked="https://studio.sg/quote | https://studio.sg/contact")
    restored = audit_report.row_findings(r)
    page = audit_report.build(r, restored, BRAND)
    assert "https://studio.sg/quote" in page
    assert "https://studio.sg/contact" in page
    assert "Only the home page, https://studio.sg/quote and https://studio.sg/contact were opened." in page
    assert "Pages opened: the home page, https://studio.sg/quote and https://studio.sg/contact." in page


# ---------------------------------------------------------------------------
# The email grader
# ---------------------------------------------------------------------------

def test_a_named_person_at_the_company_domain_grades_best():
    result = verify.grade("jia.hui@quietinteriors.com.sg", check_mx=True)
    assert result["grade"] == verify.GRADE_PERSONAL
    assert result["deliverable"] is True


def test_a_shared_inbox_is_flagged_but_kept():
    result = verify.grade("enquiries@quietinteriors.com.sg", check_mx=True)
    assert result["grade"] == verify.GRADE_SHARED
    assert result["deliverable"] is True


def test_free_mail_is_flagged_as_such():
    result = verify.grade("quietinteriors@gmail.com", check_mx=True)
    assert result["grade"] == verify.GRADE_FREEMAIL


def test_a_template_placeholder_is_unusable():
    """A bounce hurts the sending domain every later email depends on."""
    assert verify.grade("you@example.com", check_mx=True)["deliverable"] is False


def test_a_typing_mistake_is_caught():
    result = verify.grade("hello@gmial.com", check_mx=True)
    assert result["deliverable"] is False
    assert "gmail.com" in result["reason"]


def test_a_disposable_address_is_unusable():
    assert verify.grade("x@mailinator.com", check_mx=True)["deliverable"] is False


def test_junk_input_does_not_crash():
    for junk in ("", None, "not an email", "@", "a@b", "a@@b.com"):
        assert verify.grade(junk, check_mx=True)["deliverable"] is False


def test_an_unknown_mx_result_never_condemns_an_address():
    """None means 'could not check'. It must not mean 'no'."""
    result = verify.grade("hello@quietinteriors.com.sg", check_mx=None)
    # check_mx=None triggers a real lookup, which in a sandbox gives unknown.
    assert result["deliverable"] is True


def test_the_best_address_is_chosen_by_usefulness():
    chosen = verify.best(
        ["info@studio.sg", "you@example.com", "wei.ling@studio.sg"],
        check_mx=True,
    )
    assert chosen["address"] == "wei.ling@studio.sg"


def test_best_falls_back_to_a_shared_inbox():
    chosen = verify.best(["info@studio.sg"], check_mx=True)
    assert chosen["grade"] == verify.GRADE_SHARED


def test_best_gives_nothing_when_every_address_is_unusable():
    assert verify.best(["you@example.com", "x@mailinator.com"], check_mx=True) is None


def test_best_on_an_empty_list_is_safe():
    assert verify.best([], check_mx=True) is None
    assert verify.best(None, check_mx=True) is None


# ---------------------------------------------------------------------------
# Outreach drafts
# ---------------------------------------------------------------------------

import outreach  # noqa: E402


def lead_row(**extra):
    base = {"name": "Quiet Interiors", "phone": "+65 6111 1111",
            "website": "https://quiet.sg", "email": "wei@quiet.sg",
            "ad_tags": "", "capture_methods": "", "reasons": "",
            "tier": "warm", "score": 80, "review_count": 14, "address": ""}
    base.update(extra)
    return base


def test_the_draft_never_claims_ad_spend_that_was_not_seen():
    message = outreach.whatsapp_message(lead_row())
    assert "pixel" not in message.lower()
    assert "paying" not in message.lower()
    assert "ad" not in message.lower().replace("ready", "").replace("read", "")


def test_the_draft_names_the_tag_when_one_was_seen():
    message = outreach.whatsapp_message(lead_row(ad_tags="Meta Pixel"))
    assert "Meta Pixel" in message


def test_the_draft_offers_the_review_free():
    message = outreach.whatsapp_message(lead_row())
    assert "No charge" in message


def test_the_draft_never_promises_a_result():
    for row_data in (lead_row(), lead_row(ad_tags="Meta Pixel"),
                     lead_row(reasons="parked or unpublished site")):
        message = outreach.whatsapp_message(row_data).lower()
        for promise in ("guarantee", "double your", "10x", "we will get you"):
            assert promise not in message


def test_the_whatsapp_link_carries_the_message_and_the_country_code():
    link = outreach.whatsapp_link(lead_row(phone="6111 1111"))
    assert link.startswith("https://wa.me/6561111111?text=")


def test_a_missing_phone_gives_no_link():
    assert outreach.whatsapp_link(lead_row(phone="")) == ""


def test_the_email_has_a_subject_and_a_signature():
    subject = outreach.email_subject(lead_row())
    body = outreach.email_body(lead_row(), "Nixon Media", "nixon@example.sg")
    assert "Quiet Interiors" in subject
    assert body.startswith("Hi Quiet Interiors,")
    assert "Nixon Media" in body
    assert "yours whether" in body      # the give-first promise


def test_a_parked_site_gets_its_own_message():
    message = outreach.whatsapp_message(lead_row(reasons="parked or unpublished site"))
    assert "holding page" in message


def test_add_drafts_puts_every_field_on_the_row():
    rows = outreach.add_drafts([lead_row()], "Nixon Media", "nixon@example.sg")
    for key in ("whatsapp_message", "whatsapp_link", "email_subject", "email_body"):
        assert rows[0][key]


# ---------------------------------------------------------------------------
# The CRM export
# ---------------------------------------------------------------------------

def test_the_crm_export_drops_leads_with_no_usable_email(tmp_path):
    """An un-mailable contact inflates the bounce rate of every later send."""
    path = str(tmp_path / "crm.csv")
    _written, kept = outreach.write_crm_csv([
        lead_row(email="wei@quiet.sg"),
        lead_row(email=""),
        lead_row(email="you@example.com"),      # a template placeholder
    ], path)
    assert kept == 1


def test_the_crm_export_uses_the_column_names_outreach_tools_expect(tmp_path):
    import csv as csv_module
    path = str(tmp_path / "crm.csv")
    outreach.write_crm_csv([lead_row(ad_tags="Meta Pixel")], path)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        record = next(csv_module.DictReader(handle))
    assert record["email"] == "wei@quiet.sg"
    assert record["company_name"] == "Quiet Interiors"
    assert record["custom_ad_tags"] == "Meta Pixel"
    assert record["custom_observation"]


def test_the_crm_export_escapes_a_formula_name(tmp_path):
    import csv as csv_module
    path = str(tmp_path / "crm.csv")
    outreach.write_crm_csv([lead_row(name="=cmd|'/c calc'!A1")], path)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        record = next(csv_module.DictReader(handle))
    assert record["company_name"].startswith("'=")
