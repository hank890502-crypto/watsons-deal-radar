#!/bin/bash
# macOS 雙擊：安裝 launchd 排程（每天 09:00 / 15:00 / 21:00 掃描並推送）
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.watsons-deal-radar.scan.plist"
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__REPO__|$REPO|g" scripts/com.watsons-deal-radar.scan.plist > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST" && echo "已安裝排程：$PLIST" && echo "立即測試：launchctl start com.watsons-deal-radar.scan（log 在 /tmp/watsons-deal-radar.log）"
read -r -p "按 Enter 關閉"
