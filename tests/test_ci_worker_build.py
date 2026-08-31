from pathlib import Path
import re


WORKFLOW = Path(".github/workflows/tests.yml")


def _workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def _worker_job_text():
    text = _workflow_text()
    marker = "  worker-container-build:\n"
    idx = text.find(marker)
    if idx == -1:
        raise AssertionError(f"Marker {marker!r} not found in workflow")
    return text[idx:]


def test_worker_container_build_job_exists():
    assert WORKFLOW.exists()
    text = _workflow_text()
    assert "worker-container-build:" in text
    assert "runs-on: ubuntu-latest" in text
    assert "timeout-minutes: 25" in text
    assert "contents: read" in text


def test_worker_job_has_no_unindented_child_lines():
    lines = _worker_job_text().splitlines()
    assert lines
    assert lines[0] == "  worker-container-build:"

    for line in lines[1:]:
        if line.strip():
            assert line.startswith("    "), repr(line)


def test_worker_run_block_contents_remain_indented():
    lines = _worker_job_text().splitlines()
    in_run_block = False

    for line in lines:
        if line.strip() == "run: |":
            assert line.startswith("        run: |")
            in_run_block = True
            continue

        if in_run_block:
            # Next step begins with 6 spaces + "- "
            if line.startswith("      - "):
                in_run_block = False
                continue
            if line.strip():
                leading = len(line) - len(line.lstrip(" "))
                assert leading >= 10, f"Run block line under-indented: {line!r}"


def test_worker_container_job_builds_real_worker_dockerfile():
    job_text = _worker_job_text()
    assert "docker build" in job_text
    assert "--pull" in job_text
    assert "--file Dockerfile.worker" in job_text
    assert "leadscan-worker-ci:" in job_text
    assert "${GITHUB_SHA}" in job_text
    assert "Dockerfile.test" not in job_text
    assert "Dockerfile.ci" not in job_text


def test_worker_container_job_checks_runtime_contents():
    job_text = _worker_job_text()
    assert "id pwuser" in job_text
    assert "command -v gosu" in job_text
    assert "command -v iptables" in job_text
    assert "command -v ip6tables" in job_text
    assert "/app/deploy/fly/apply-egress-firewall.sh" in job_text
    assert "/app/deploy/fly/start-worker.sh" in job_text
    assert "--entrypoint python" in job_text
    assert 'import importlib.metadata as m' in job_text
    assert 'm.version("playwright")' in job_text
    assert '"1.62.0"' in job_text
    assert "test ! -e /app/tests" in job_text
    assert "test ! -e /app/site" in job_text
    assert "test ! -e /app/.git" in job_text
    assert "test ! -e /app/.env" in job_text


def test_worker_container_job_launches_chromium_as_pwuser():
    job_text = _worker_job_text()
    assert "--user pwuser:pwuser" in job_text
    assert "HOME=/home/pwuser" in job_text
    assert "--ipc=host" in job_text
    assert "--entrypoint python" in job_text
    assert "sync_playwright" in job_text
    assert "chromium.launch" in job_text
    assert "browser.new_page" in job_text
    assert "page.set_content" in job_text
    assert "LeadScan Worker CI" in job_text
    assert "browser.close" in job_text
    assert "p.stop()" in job_text
    assert "non_root_chromium=PASS" in job_text


def test_worker_container_job_does_not_execute_firewall_entrypoint():
    job_text = _worker_job_text()

    # Verify intended entrypoint overrides are present
    assert "--entrypoint /bin/sh" in job_text
    assert "--entrypoint python" in job_text

    # Assert entrypoint is not overridden to either production script
    assert "--entrypoint /app/deploy/fly/start-worker.sh" not in job_text
    assert "--entrypoint /app/deploy/fly/apply-egress-firewall.sh" not in job_text

    # Assert forbidden runtime privileges
    assert "--privileged" not in job_text
    assert "NET_ADMIN" not in job_text
    assert "--network host" not in job_text

    # Require exact existence check strings
    assert "test -x /app/deploy/fly/apply-egress-firewall.sh" in job_text
    assert "test -x /app/deploy/fly/start-worker.sh" in job_text

    # Every non-comment line containing production script paths must be an allowed check
    allowed_patterns = [
        re.compile(r"^\s*test -x /app/deploy/fly/(?:apply-egress-firewall|start-worker)\.sh\s*$"),
        re.compile(r"^\s*test \"\$ENTRYPOINT\" = '\[\"/app/deploy/fly/start-worker\.sh\"\]'\s*$"),
    ]

    for line in job_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "/app/deploy/fly/start-worker.sh" in stripped or "/app/deploy/fly/apply-egress-firewall.sh" in stripped:
            assert any(p.match(line) for p in allowed_patterns), (
                f"Disallowed script reference line: {line!r}"
            )


def test_worker_container_job_is_ephemeral_and_registry_free():
    job_text = _worker_job_text()
    for forbidden in [
        "docker login",
        "docker push",
        "ghcr.io",
        "actions/upload-artifact",
        "docker/build-push-action",
        "FLY_API_TOKEN",
        "${{ secrets.",
    ]:
        assert forbidden not in job_text, f"Found forbidden term in worker job: {forbidden}"


def test_existing_test_jobs_are_still_present():
    text = _workflow_text()
    assert "pytest:" in text
    assert '"3.10"' in text
    assert '"3.12"' in text
    assert "browser-integration:" in text
    assert "Install Playwright Chromium" in text
    assert "Check no secret was committed" in text
