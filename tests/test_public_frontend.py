from html.parser import HTMLParser
from pathlib import Path
import re
import pytest


SITE_DIR = Path("site")
INDEX_HTML = SITE_DIR / "index.html"
STYLES_CSS = SITE_DIR / "styles.css"
APP_JS = SITE_DIR / "app.js"
NETLIFY_CONFIG = Path("netlify.toml")


class AttributeExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parse_index_tags():
    content = INDEX_HTML.read_text(encoding="utf-8")
    parser = AttributeExtractor()
    parser.feed(content)
    return parser.tags


def test_site_files_exist():
    assert INDEX_HTML.is_file()
    assert STYLES_CSS.is_file()
    assert APP_JS.is_file()
    assert NETLIFY_CONFIG.is_file()


def test_frontend_declares_manual_worker_origin_configuration():
    tags = parse_index_tags()
    content = INDEX_HTML.read_text(encoding="utf-8")

    origin_metas = [
        attrs for tag, attrs in tags
        if tag == "meta" and attrs.get("name") == "leadscan-api-origin"
    ]
    assert len(origin_metas) == 1
    assert origin_metas[0].get("content") == ""

    assert "http://" not in content
    assert "https://" not in content

    for tag, attrs in tags:
        if tag == "link" and attrs.get("rel") == "stylesheet":
            assert attrs.get("href") == "styles.css"
        elif tag == "script":
            assert attrs.get("src") == "app.js"


def test_index_references_only_local_assets():
    tags = parse_index_tags()
    content = INDEX_HTML.read_text(encoding="utf-8")

    # No external scripts, stylesheets, or images
    for tag, attrs in tags:
        if tag == "link" and attrs.get("rel") == "stylesheet":
            assert attrs.get("href") == "styles.css"
        elif tag == "script":
            assert attrs.get("src") == "app.js"
        elif tag == "img":
            src = attrs.get("src", "")
            assert not src.startswith("http://") and not src.startswith("https://")

    assert "http://" not in content
    assert "https://" not in content


def test_form_and_input_elements():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    # Form
    assert "audit-form" in tags_by_id
    form_tag, form_attrs = tags_by_id["audit-form"]
    assert form_tag == "form"

    # Website Input
    assert "website-url" in tags_by_id
    input_tag, input_attrs = tags_by_id["website-url"]
    assert input_tag == "input"
    assert input_attrs.get("type") == "text"
    assert input_attrs.get("inputmode") == "url"
    assert input_attrs.get("maxlength") == "2048"
    assert "required" in input_attrs

    # Contact Name Input
    assert "contact-name" in tags_by_id
    name_tag, name_attrs = tags_by_id["contact-name"]
    assert name_tag == "input"
    assert name_attrs.get("type") == "text"
    assert name_attrs.get("autocomplete") == "name"
    assert name_attrs.get("maxlength") == "120"
    assert "required" not in name_attrs

    # Contact Email Input
    assert "contact-email" in tags_by_id
    email_tag, email_attrs = tags_by_id["contact-email"]
    assert email_tag == "input"
    assert email_attrs.get("type") == "email"
    assert email_attrs.get("inputmode") == "email"
    assert email_attrs.get("autocomplete") == "email"
    assert email_attrs.get("maxlength") == "254"
    assert "required" in email_attrs
    assert email_attrs.get("aria-describedby") == "contact-privacy"

    # Submit button
    assert "audit-submit" in tags_by_id
    btn_tag, btn_attrs = tags_by_id["audit-submit"]
    assert btn_tag == "button"
    assert btn_attrs.get("type") == "submit"


def test_contact_capture_fields_have_clear_privacy_copy():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    assert "contact-privacy" in tags_by_id
    html_content = INDEX_HTML.read_text(encoding="utf-8")

    lower_content = html_content.lower()
    assert "name" in lower_content and "email" in lower_content
    assert "follow up" in lower_content or "follow-up" in lower_content
    assert "sensitive" in lower_content

    for forbidden_id in ("phone", "telephone", "job-title", "company-size"):
        assert forbidden_id not in tags_by_id


def test_live_status_region():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    assert "audit-status" in tags_by_id
    status_tag, status_attrs = tags_by_id["audit-status"]
    assert status_attrs.get("role") == "status"
    assert status_attrs.get("aria-live") == "polite"


def test_result_section_and_required_ids():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    assert "audit-result" in tags_by_id
    res_tag, res_attrs = tags_by_id["audit-result"]
    assert res_tag == "section"
    assert "hidden" in res_attrs

    # Result fields
    assert "result-score" in tags_by_id
    assert "result-tier" in tags_by_id
    assert "result-hook" in tags_by_id


def test_report_iframe_sandbox_and_security_attributes():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    assert "report-preview" in tags_by_id
    frame_tag, frame_attrs = tags_by_id["report-preview"]
    assert frame_tag == "iframe"
    assert "sandbox" in frame_attrs
    sandbox_val = frame_attrs.get("sandbox") or ""
    for forbidden_perm in ("allow-scripts", "allow-same-origin", "allow-forms", "allow-top-navigation"):
        assert forbidden_perm not in sandbox_val

    assert frame_attrs.get("referrerpolicy") == "no-referrer"
    assert bool(frame_attrs.get("title"))


def test_reset_button():
    tags = parse_index_tags()
    tags_by_id = {attrs["id"]: (tag, attrs) for tag, attrs in tags if "id" in attrs}

    assert "audit-reset" in tags_by_id
    btn_tag, btn_attrs = tags_by_id["audit-reset"]
    assert btn_tag == "button"
    assert btn_attrs.get("type") == "button"


def test_js_relative_api_endpoint_and_method():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert "leadscan-api-origin" in js_content
    assert "resolveAuditEndpoint" in js_content
    assert '"/api/audit"' in js_content or "'/api/audit'" in js_content
    assert '"POST"' in js_content or "'POST'" in js_content
    assert "application/json" in js_content
    assert 'fetch("/api/audit"' not in js_content
    assert "fetch('/api/audit'" not in js_content


def test_js_no_absolute_api_origins():
    js_content = APP_JS.read_text(encoding="utf-8")
    assert "http://" not in js_content
    assert "https://" not in js_content


def test_js_validates_configured_worker_origin_fail_closed():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert "new URL(" in js_content
    assert '"https:"' in js_content or "'https:'" in js_content
    assert "pathname" in js_content
    assert "username" in js_content
    assert "password" in js_content
    assert "search" in js_content
    assert "hash" in js_content
    assert "*" in js_content
    assert "," in js_content
    assert "/api/audit" in js_content
    assert "configuration" in js_content

    for forbidden_source in ("location.search", "location.hash", "localStorage", "sessionStorage"):
        assert forbidden_source not in js_content


def test_netlify_csp_requires_exact_manual_worker_origin():
    toml_content = NETLIFY_CONFIG.read_text(encoding="utf-8")

    # Match Content-Security-Policy header
    csp_match = re.search(r'Content-Security-Policy\s*=\s*"([^"]+)"', toml_content)
    assert csp_match is not None, "Content-Security-Policy header not found in netlify.toml"
    csp_value = csp_match.group(1)

    # Parse connect-src directive
    connect_match = re.search(r'connect-src\s+([^;]+)', csp_value)
    assert connect_match is not None, "connect-src directive not found in CSP"
    connect_sources = connect_match.group(1).split()

    assert "'self'" in connect_sources
    assert "https://leadscan-worker.example.invalid" in connect_sources
    assert len(connect_sources) == 2

    assert "*" not in connect_sources
    assert "https:" not in connect_sources
    assert "*.fly.dev" not in connect_sources
    assert "*.netlify.app" not in connect_sources

    # Deployment notes review
    lower_content = toml_content.lower()
    assert "leadscan-api-origin" in lower_content
    assert "leadscan_allowed_origin" in lower_content
    assert "leadscan-worker.example.invalid" in lower_content
    assert "production" in lower_content or "deploy" in lower_content


def test_js_uses_srcdoc_and_no_innerhtml():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert ".srcdoc" in js_content
    assert ".innerHTML" not in js_content
    assert "insertAdjacentHTML" not in js_content


def test_js_contains_all_error_code_mappings():
    js_content = APP_JS.read_text(encoding="utf-8")

    for code in (
        "invalid_request",
        "invalid_url",
        "rate_limited",
        "busy",
        "audit_timeout",
        "audit_failed",
        "lead_capture_failed",
        "configuration",
        "generic",
        "network",
    ):
        assert code in js_content


def test_js_sends_contact_capture_contract_without_browser_persistence():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert "contact_name" in js_content
    assert "email" in js_content
    assert "contact-name" in js_content
    assert "contact-email" in js_content

    assert '"/api/audit"' in js_content or "'/api/audit'" in js_content
    assert '"POST"' in js_content or "'POST'" in js_content
    assert "application/json" in js_content

    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "console.log"):
        assert forbidden not in js_content


def test_js_error_handling_does_not_leak_raw_internals():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert "error.message" not in js_content
    assert "error.stack" not in js_content
    assert "err.message" not in js_content


def test_js_loading_and_reset_semantics():
    js_content = APP_JS.read_text(encoding="utf-8")

    assert ".disabled = " in js_content
    assert "contactNameInput.disabled" in js_content
    assert "contactEmailInput.disabled" in js_content
    assert 'contactNameInput.value = ""' in js_content or "contactNameInput.value = ''" in js_content
    assert 'contactEmailInput.value = ""' in js_content or "contactEmailInput.value = ''" in js_content
    assert "Reviewing…" in js_content or "Reviewing..." in js_content
    assert 'reportPreview.srcdoc = ""' in js_content or "reportPreview.srcdoc = ''" in js_content


def test_css_responsive_and_accessibility():
    css_content = STYLES_CSS.read_text(encoding="utf-8")

    assert ".contact-grid" in css_content
    assert ".form-field" in css_content
    assert "@media" in css_content
    assert "max-width" in css_content
    assert "prefers-reduced-motion" in css_content
    assert "[hidden]" in css_content


def test_no_build_tool_or_package_files_created():
    for forbidden in ("package.json", "package-lock.json", "vite.config.js", "vite.config.ts", "node_modules"):
        assert not Path(forbidden).exists()
