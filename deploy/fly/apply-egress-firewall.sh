#!/bin/sh
set -eu

# ==============================================================================
# apply-egress-firewall.sh
# ------------------------
# Fail-closed OS-level egress filtering for the LeadScan worker container.
#
# SECURITY BOUNDARY NOTES:
# 1. Defense-in-Depth:
#    This packet filter blocks special, private, link-local, multicast, and metadata
#    address ranges at the Linux kernel level. Application-level URL safety, CDP
#    Fetch interception, and robots validation (Task 8A) remain mandatory.
# 2. Platform DNS:
#    Fly Machines resolve DNS via fdaa::3 on port 53. An explicit exception is
#    permitted for fdaa::3:53 before blocking the fc00::/7 IPv6 ULA space.
# 3. Fail-Closed Guarantee:
#    This script must run as root before worker startup. Any error during setup
#    aborts startup and prevents untrusted scanning traffic.
# ==============================================================================

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: apply-egress-firewall.sh must be executed as root." >&2
  exit 1
fi

command -v iptables >/dev/null 2>&1 || {
  echo "Error: iptables is not installed." >&2
  exit 1
}

command -v ip6tables >/dev/null 2>&1 || {
  echo "Error: ip6tables is not installed." >&2
  exit 1
}

CHAIN_NAME="LEADSCAN_EGRESS"

# ------------------------------------------------------------------------------
# 1. IPv4 Egress Rules
# ------------------------------------------------------------------------------

# Initialize custom chain idempotently without replacing global OUTPUT policy
iptables -N "$CHAIN_NAME" 2>/dev/null || iptables -F "$CHAIN_NAME"
iptables -C OUTPUT -j "$CHAIN_NAME" 2>/dev/null || iptables -A OUTPUT -j "$CHAIN_NAME"

# Allow established and related inbound response connections
iptables -A "$CHAIN_NAME" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Reject private, metadata, link-local, and reserved IPv4 destinations
IPV4_BLOCKED="
0.0.0.0/8
10.0.0.0/8
100.64.0.0/10
127.0.0.0/8
169.254.0.0/16
172.16.0.0/12
192.0.0.0/24
192.0.2.0/24
192.168.0.0/16
198.18.0.0/15
198.51.100.0/24
203.0.113.0/24
224.0.0.0/4
240.0.0.0/4
"

for range in $IPV4_BLOCKED; do
  iptables -A "$CHAIN_NAME" -d "$range" -j REJECT
done

# ------------------------------------------------------------------------------
# 2. IPv6 Egress Rules
# ------------------------------------------------------------------------------

# Initialize custom chain idempotently without replacing global OUTPUT policy
ip6tables -N "$CHAIN_NAME" 2>/dev/null || ip6tables -F "$CHAIN_NAME"
ip6tables -C OUTPUT -j "$CHAIN_NAME" 2>/dev/null || ip6tables -A OUTPUT -j "$CHAIN_NAME"

# Allow established and related inbound response connections
ip6tables -A "$CHAIN_NAME" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Explicitly allow Fly platform DNS resolver ONLY on port 53 before ULA block
ip6tables -A "$CHAIN_NAME" -d fdaa::3/128 -p udp --dport 53 -j ACCEPT
ip6tables -A "$CHAIN_NAME" -d fdaa::3/128 -p tcp --dport 53 -j ACCEPT

# Reject private, link-local, multicast, and special IPv6 destinations
IPV6_BLOCKED="
::/128
::1/128
::ffff:0:0/96
64:ff9b::/96
64:ff9b:1::/48
100::/64
2001:2::/48
2001:db8::/32
2002::/16
fc00::/7
fe80::/10
fec0::/10
ff00::/8
"

for range in $IPV6_BLOCKED; do
  ip6tables -A "$CHAIN_NAME" -d "$range" -j REJECT
done
