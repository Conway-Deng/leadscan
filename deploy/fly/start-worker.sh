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
# 2. Privilege Drop (pwuser):
#    Permanently drops root privileges via gosu and replaces PID 1 with Uvicorn.
# 3. Non-Root Chromium Execution:
#    The browser runtime executes exclusively under the unprivileged pwuser account.
# ==============================================================================

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: start-worker.sh must start as root to configure firewall." >&2
  exit 1
fi

# 1. Apply kernel-level packet filter before launching application
/app/deploy/fly/apply-egress-firewall.sh

# 2. Prepare user environment for Playwright
export HOME=/home/pwuser

# 3. Replace PID 1 with Uvicorn running as pwuser
exec gosu pwuser:pwuser uvicorn public_api:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 1 \
  --no-proxy-headers \
  --no-server-header \
  --timeout-graceful-shutdown 120
