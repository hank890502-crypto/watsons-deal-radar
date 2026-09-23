#!/bin/bash
# macOS 雙擊：跑一次完整掃描（含通知），並嘗試推送到 GitHub（需要 .env 裡的 GITHUB_TOKEN，或本機 git 已有憑證）
cd "$(dirname "$0")/.." || exit 1
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python -m radar scan --publish "$@"
echo; echo "完成，按 Enter 關閉"; read -r
