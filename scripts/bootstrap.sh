#!/usr/bin/env bash
set -euo pipefail
cd /opt/sol-right
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip -q install --upgrade pip
pip -q install -r requirements.txt
echo "venv ready: /opt/sol-right/.venv"
