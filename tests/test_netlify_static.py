from pathlib import Path
import pytest


NETLIFY_TOML = Path("netlify.toml")
GITIGNORE = Path(".gitignore")
PRODUCTION_WORKER_ORIGIN = "https://leadscan-9fsy.onrender.com"
WORKER_ORIGIN_PLACEHOLDER = "https://leadscan-worker.example.invalid"


def test_netlify_toml_exists():
    assert NETLIFY_TOML.is_file()


def test_build_publish_directory_is_site():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert '[build]' in content
    assert 'publish = "site"' in content or "publish = 'site'" in content


def test_no_build_command_in_config():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert "command =" not in content
    assert "command=" not in content


def test_no_functions_configuration():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert "functions =" not in content
    assert "functions=" not in content
    assert "[functions" not in content
    assert "netlify/functions" not in content


def test_no_redirect_or_proxy_configuration():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert "[[redirects]]" not in content
    assert "/api/audit" not in content
    assert "http://" not in content

    assert PRODUCTION_WORKER_ORIGIN in content
    assert WORKER_ORIGIN_PLACEHOLDER not in content
    assert content.count("https://") == 1



def test_headers_rule_and_security_headers():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert '[[headers]]' in content
    assert 'for = "/*"' in content or "for = '/*'" in content
    assert 'X-Content-Type-Options = "nosniff"' in content
    assert 'Referrer-Policy = "no-referrer"' in content
    assert 'X-Frame-Options = "DENY"' in content
    assert "Permissions-Policy =" in content


def test_content_security_policy_directives():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert "Content-Security-Policy =" in content

    required_directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    for directive in required_directives:
        assert directive in content


def test_csp_rejects_dangerous_broadening():
    content = NETLIFY_TOML.read_text(encoding="utf-8")
    assert "unsafe-eval" not in content
    assert "script-src 'unsafe-inline'" not in content
    assert "connect-src *" not in content
    assert "default-src *" not in content


def test_gitignore_entries_for_netlify():
    content = GITIGNORE.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    assert ".netlify/" in lines
    assert "site/" not in lines
    assert "netlify.toml" not in lines


def test_no_legacy_or_redundant_files_exist():
    assert not Path("_redirects").exists()
    assert not Path("_headers").exists()
    assert not Path("site/_redirects").exists()
    assert not Path("site/_headers").exists()
    assert not Path("netlify/functions").exists()
    assert not Path("netlify/edge-functions").exists()


def test_no_node_or_build_files_exist():
    for forbidden in ("package.json", "package-lock.json", "vite.config.js", "vite.config.ts", "node_modules"):
        assert not Path(forbidden).exists()
