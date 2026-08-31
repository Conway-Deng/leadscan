from pathlib import Path
import pytest


DOCKERIGNORE = Path(".dockerignore")
DOCKERFILE = Path("Dockerfile.worker")
FIREWALL_SCRIPT = Path("deploy/fly/apply-egress-firewall.sh")
START_SCRIPT = Path("deploy/fly/start-worker.sh")


def test_deployment_files_exist():
    assert DOCKERIGNORE.is_file()
    assert DOCKERFILE.is_file()
    assert FIREWALL_SCRIPT.is_file()
    assert START_SCRIPT.is_file()


def test_dockerfile_base_image_and_package_pinning():
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM mcr.microsoft.com/playwright/python:v1.62.0-noble" in content
    assert '"playwright==1.62.0"' in content or "'playwright==1.62.0'" in content or "playwright==1.62.0" in content
    assert "playwright install" not in content


def test_dockerfile_tools_and_privilege_checks():
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "id pwuser" in content
    assert "gosu" in content
    assert "iptables" in content
    assert 'ENTRYPOINT ["/app/deploy/fly/start-worker.sh"]' in content


def test_dockerignore_rules():
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    # Essential excluded items
    for excluded in (".git", ".env", ".venv", "tests", "site", ".netlify", "reports", "Procfile", "netlify.toml"):
        assert excluded in lines

    # Essential included items (must NOT be excluded)
    assert "requirements.txt" not in lines
    assert "deploy" not in lines
    assert "deploy/fly" not in lines
    assert "Dockerfile.worker" not in lines


def test_firewall_script_fail_closed_and_custom_chains():
    content = FIREWALL_SCRIPT.read_text(encoding="utf-8")

    assert "set -eu" in content
    assert 'id -u' in content
    assert "iptables" in content
    assert "ip6tables" in content
    assert "LEADSCAN_EGRESS" in content

    # Should not flush global OUTPUT or set global policies
    assert "iptables -F OUTPUT" not in content
    assert "iptables -P OUTPUT DROP" not in content
    assert "iptables -P OUTPUT ACCEPT" not in content


def test_firewall_ipv4_and_ipv6_deny_rules():
    content = FIREWALL_SCRIPT.read_text(encoding="utf-8")

    ipv4_must_block = [
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "0.0.0.0/8",
        "100.64.0.0/10",
        "240.0.0.0/4",
    ]
    for r in ipv4_must_block:
        assert r in content

    ipv6_must_block = [
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    ]
    for r in ipv6_must_block:
        assert r in content


def test_firewall_order_established_and_dns_exceptions():
    content = FIREWALL_SCRIPT.read_text(encoding="utf-8")

    assert "ESTABLISHED,RELATED" in content
    assert "fdaa::3/128" in content
    assert "dport 53" in content

    # In IPv6 section, DNS exception MUST appear BEFORE fc00::/7 ULA deny
    ipv6_section = content[content.find("# 2. IPv6 Egress Rules"):]
    dns_pos = ipv6_section.find("fdaa::3/128")
    ula_pos = ipv6_section.find("fc00::/7")
    assert dns_pos != -1 and ula_pos != -1
    assert dns_pos < ula_pos

    # In IPv4 section, ESTABLISHED rule MUST appear BEFORE block lists
    ipv4_section = content[content.find("# 1. IPv4 Egress Rules"):]
    est_pos = ipv4_section.find("ESTABLISHED,RELATED")
    block_pos = ipv4_section.find("10.0.0.0/8")
    assert est_pos != -1 and block_pos != -1
    assert est_pos < block_pos



def test_start_worker_script_order_and_privilege_drop():
    content = START_SCRIPT.read_text(encoding="utf-8")

    assert "set -eu" in content
    assert "id -u" in content
    assert "apply-egress-firewall.sh" in content
    assert "exec gosu pwuser:pwuser" in content

    # Firewall must run before gosu
    fw_pos = content.find("apply-egress-firewall.sh")
    gosu_pos = content.find("exec gosu pwuser:pwuser")
    assert fw_pos != -1 and gosu_pos != -1
    assert fw_pos < gosu_pos

    # Required uvicorn flags
    assert "--workers 1" in content
    assert "--no-proxy-headers" in content
    assert "--no-server-header" in content
    assert "--timeout-graceful-shutdown 120" in content

    assert "--reload" not in content
