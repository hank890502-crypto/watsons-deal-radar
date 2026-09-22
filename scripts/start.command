#!/bin/bash
# macOS 一鍵啟動（雙擊）：建立虛擬環境 → 安裝套件 → 啟動本機網頁 App
cd "$(dirname "$0")/.." || exit 1
if [ ! -d .venv ]; then
  echo "建立虛擬環境…"
  python3 -m venv .venv || { echo "需要 Python 3.10+（brew install python）"; read -r; exit 1; }
fi
source .venv/bin/activate
pip install -q -r requirements.txt
PORT=${PORT:-8765}
( sleep 2; open "http://127.0.0.1:${PORT}" ) &
python -m radar serve --port "${PORT}"
