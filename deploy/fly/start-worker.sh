#!/bin/sh
set -eu

# ==============================================================================
# start-worker.sh
# ---------------
# Container entrypoint for the LeadScan worker.
#
# LIFECYCLE & PRIVILEGE MODEL:
# 1. Bootstrap (Root):
#    Executes apply-egress-firewall.sh to install fail-closed kernel egress rules.
# 2. Private Storage Mount Validation (Root):
#    Validates LEADSCAN_LEAD_DB_PATH and verifies /data is a real mounted volume.
#    Sets directory permissions (0700) for pwuser without recursive ownership changes.
# 3. Privilege Drop (pwuser):
#    Permanently drops root privileges via gosu and replaces PID 1 with Uvicorn.
# 4. Non-Root Chromium & App Execution:
#    The browser runtime and API execute exclusively under the unprivileged pwuser account.
# ==============================================================================

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: start-worker.sh must start as root to configure firewall." >&2
  exit 1
fi

# 1. Apply kernel-level packet filter before launching application
/app/deploy/fly/apply-egress-firewall.sh

# 2. Validate exact persistent lead database path and mounted volume
LEAD_DATA_DIR="/data"
EXPECTED_LEAD_DB_PATH="/data/leadscan-public-leads.sqlite3"

if [ "${LEADSCAN_LEAD_DB_PATH:-}" != "$EXPECTED_LEAD_DB_PATH" ]; then
  echo "Error: LEADSCAN_LEAD_DB_PATH must be configured to '$EXPECTED_LEAD_DB_PATH'." >&2
  exit 1
fi

if ! awk -v target="$LEAD_DATA_DIR" '
    $2 == target { found = 1 }
    END { exit found ? 0 : 1 }
' /proc/mounts; then
  echo "Error: required persistent lead-data mount '$LEAD_DATA_DIR' is unavailable." >&2
  exit 1
fi

# 3. Reject unsafe existing database path types (symlink or non-regular file)
if [ -L "$EXPECTED_LEAD_DB_PATH" ]; then
  echo "Error: lead database path is a symbolic link." >&2
  exit 1
fi

if [ -e "$EXPECTED_LEAD_DB_PATH" ] && [ ! -f "$EXPECTED_LEAD_DB_PATH" ]; then
  echo "Error: lead database path is not a regular file." >&2
  exit 1
fi

# 4. Prepare directory ownership and permissions for pwuser (non-recursive)
chown pwuser:pwuser "$LEAD_DATA_DIR"
chmod 0700 "$LEAD_DATA_DIR"

# 5. Verify unprivileged application user can write to the mount directory
if ! gosu pwuser:pwuser test -w "$LEAD_DATA_DIR"; then
  echo "Error: persistent lead-data mount is not writable by pwuser." >&2
  exit 1
fi

# 6. Prepare user environment for Playwright
export HOME=/home/pwuser

# 7. Replace PID 1 with Uvicorn running as pwuser
exec gosu pwuser:pwuser uvicorn public_api:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 1 \
  --no-proxy-headers \
  --no-server-header \
  --timeout-graceful-shutdown 120
