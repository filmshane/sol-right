#!/usr/bin/env bash
# Add secondary LAN IP for SOL-RIGHT site (idempotent)
set -euo pipefail
IFACE="${IFACE:-enp0s31f6}"
IP="${IP:-192.168.1.210}"
MASK="${MASK:-24}"
if ip -4 addr show dev "$IFACE" | grep -q "inet ${IP}/"; then
  echo "IP ${IP} already on ${IFACE}"
else
  sudo ip addr add "${IP}/${MASK}" dev "$IFACE"
  echo "Added ${IP}/${MASK} on ${IFACE}"
fi
ip -4 addr show dev "$IFACE" | sed -n '1,20p'
