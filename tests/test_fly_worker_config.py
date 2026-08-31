import json
from pathlib import Path
import pytest


FLY_CONFIG = Path("fly.worker.toml")
EGRESS_POLICY = Path("deploy/fly/network-policy.egress.json")


def test_fly_worker_config_exists():
    assert FLY_CONFIG.is_file()
    assert EGRESS_POLICY.is_file()


def test_fly_worker_config_no_app_name():
    content = FLY_CONFIG.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    for line in lines:
        assert not line.startswith("app ="), "fly.worker.toml must not hardcode an app name"
        assert not line.startswith("app="), "fly.worker.toml must not hardcode an app name"


def test_fly_worker_config_region_and_draining():
    content = FLY_CONFIG.read_text(encoding="utf-8")

    assert 'primary_region = "sin"' in content or "primary_region = 'sin'" in content
    assert 'kill_signal = "SIGTERM"' in content or "kill_signal = 'SIGTERM'" in content
    assert "kill_timeout = 120" in content


def test_fly_worker_config_build_and_service():
    content = FLY_CONFIG.read_text(encoding="utf-8")

    assert 'dockerfile = "Dockerfile.worker"' in content or "dockerfile = 'Dockerfile.worker'" in content
    assert "internal_port = 8080" in content
    assert "force_https = true" in content
    assert 'auto_stop_machines = "stop"' in content or "auto_stop_machines = 'stop'" in content
    assert "auto_start_machines = true" in content
    assert "min_machines_running = 0" in content

    # Concurrency
    assert 'type = "requests"' in content or "type = 'requests'" in content
    assert "soft_limit = 2" in content
    assert "hard_limit = 8" in content

    # Idle timeout
    assert "idle_timeout = 600" in content


def test_fly_worker_config_vm_sizing():
    content = FLY_CONFIG.read_text(encoding="utf-8")

    assert 'cpu_kind = "shared"' in content or "cpu_kind = 'shared'" in content
    assert "cpus = 1" in content
    assert 'memory = "1gb"' in content or "memory = '1gb'" in content
    assert 'persist_rootfs = "never"' in content or "persist_rootfs = 'never'" in content


def test_fly_worker_config_forbidden_features():
    content = FLY_CONFIG.read_text(encoding="utf-8")

    forbidden_patterns = [
        "[[volumes]]",
        "release_command",
        "entrypoint",
        "cmd",
        "[[services]]",
        "/health",
        "/healthz",
        "http_checks",
        "LEADSCAN_TRUSTED_CLIENT_IP_HEADER",
        "netlify",
        "Access-Control-Allow-Origin",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in content


def test_fly_worker_config_private_persistent_lead_volume():
    content = FLY_CONFIG.read_text(encoding="utf-8")

    assert 'LEADSCAN_LEAD_DB_PATH = "/data/leadscan-public-leads.sqlite3"' in content or "LEADSCAN_LEAD_DB_PATH = '/data/leadscan-public-leads.sqlite3'" in content
    assert 'source = "leadscan_data"' in content or "source = 'leadscan_data'" in content
    assert 'destination = "/data"' in content or "destination = '/data'" in content
    assert 'persist_rootfs = "never"' in content or "persist_rootfs = 'never'" in content
    assert "initial_size" not in content

    # Only one mount destination /data
    assert content.count('destination = "/data"') + content.count("destination = '/data'") == 1

    # Warning comments regarding single-machine and non-replicated SQLite
    lower_content = content.lower()
    assert "one machine" in lower_content
    assert "not replicated" in lower_content or "replicated" in lower_content


def test_network_policy_egress_json_structure():
    data = json.loads(EGRESS_POLICY.read_text(encoding="utf-8"))

    assert data.get("name") == "leadscan-public-egress"
    assert data.get("selector") == {"all": True}

    rules = data.get("rules", [])
    assert len(rules) == 1

    rule = rules[0]
    assert rule.get("action") == "allow"
    assert rule.get("direction") == "egress"

    ports = rule.get("ports", [])
    port_set = {(p.get("protocol"), p.get("port")) for p in ports}
    expected_set = {
        ("tcp", 80),
        ("tcp", 443),
        ("udp", 53),
        ("tcp", 53),
    }
    assert port_set == expected_set
    assert len(ports) == 4

    # Confirm no ingress rule or secrets
    assert "ingress" not in json.dumps(data)
    assert "token" not in json.dumps(data).lower()
    assert "secret" not in json.dumps(data).lower()
    assert "app" not in data


def test_task_9b1a_files_remain_present():
    assert Path(".dockerignore").is_file()
    assert Path("Dockerfile.worker").is_file()
    assert Path("deploy/fly/apply-egress-firewall.sh").is_file()
    assert Path("deploy/fly/start-worker.sh").is_file()
    assert Path("tests/test_worker_container.py").is_file()
