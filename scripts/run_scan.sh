#!/usr/bin/env bash
# 給 cron / launchd 用：每天固定時間掃描並通知
# 例（每天 09:00、21:00）：
#   0 9,21 * * * /path/to/watsons-deal-radar/scripts/run_scan.sh >> /tmp/radar.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python -m radar scan --dashboard-url "${DASHBOARD_URL:-}"
