#!/usr/bin/env bash
# 排程用：完整掃描 → 通知 → 推送資料到 GitHub（Pages 自動更新）
# macOS：用 scripts/com.watsons-deal-radar.scan.plist（launchd）
# Linux（家用主機 / 樹莓派 / ARM mini PC）：crontab -e 加
#   0 9,15,21 * * * /path/to/watsons-deal-radar/scripts/run_scan.sh >> /tmp/watsons-deal-radar.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python -m radar scan --publish
