"""把掃描結果提交並推送到 GitHub（給本機／家用主機排程用）。

GitHub Actions 的機器被屈臣氏（Akamai）擋 403，所以掃描必須在台灣的機器上跑；
跑完用這個模組把 web/data/*.json 推回 repo，GitHub Pages 會自動重新部署（.github/workflows/deploy.yml）。

認證：環境變數 GITHUB_TOKEN（fine-grained PAT，只給這個 repo 的 Contents: Read and write）。
token 只透過 credential helper 從環境變數讀，不寫進任何設定檔、也不出現在命令列參數。
沒有 token 時退回一般 `git push`（若本機 git 已有 GitHub 憑證也能用）。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from .config import ROOT, load_env

log = logging.getLogger(__name__)

DATA_PATHS = [
    "web/data/latest.json",
    "web/data/alerts.json",
    "web/data/history.json",
    "web/data/status.json",
    "web/data/probe.json",
    "data/alert_state.json",
    "data/cache/shopee",
]
# 用環境變數餵 token：不落地、不進 argv
_HELPER = "!f() { echo username=x-access-token; echo \"password=$GITHUB_TOKEN\"; }; f"


def _git(args: list[str], cwd: Path = ROOT, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check, env=env)


def remote_url(remote: str = "origin") -> str | None:
    try:
        return _git(["remote", "get-url", remote]).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def dashboard_url(remote: str = "origin") -> str | None:
    """https://github.com/owner/repo(.git) → https://owner.github.io/repo/"""
    url = remote_url(remote) or ""
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?/?$", url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    return f"https://{owner.lower()}.github.io/{repo}/"


def publish(message: str | None = None, remote: str = "origin", branch: str = "main", paths: list[str] | None = None) -> dict:
    load_env()
    token = os.environ.get("GITHUB_TOKEN", "")
    paths = [p for p in (paths or DATA_PATHS) if (ROOT / p).exists()]
    if not remote_url(remote):
        return {"ok": False, "reason": f"找不到 git remote '{remote}'"}
    if not paths:
        return {"ok": True, "pushed": False, "reason": "沒有可推送的資料檔"}
    _git(["add", "-f", "--", *paths])
    if _git(["diff", "--cached", "--quiet", "--", *paths], check=False).returncode == 0:
        return {"ok": True, "pushed": False, "reason": "資料沒有變化"}
    from .alerts import now_iso

    msg = message or f"data: scan {now_iso()[:16]}"
    # 只 commit 資料檔（其他已 stage 的變更保持原狀）
    _git(["-c", "user.name=watsons-deal-radar", "-c", "user.email=radar@local", "commit", "-q", "-m", msg, "--", *paths])
    env = dict(os.environ)
    auth = ["-c", f"credential.helper={_HELPER}"] if token else []
    # 先把遠端的新 commit（例如 Actions 的部署紀錄）拉下來，避免 non-fast-forward
    pull = _git([*auth, "pull", "--rebase", "--autostash", "-q", remote, branch], check=False, env=env)
    if pull.returncode != 0:
        log.warning("git pull --rebase failed: %s", (pull.stderr or pull.stdout)[-400:])
    push = _git([*auth, "push", "-q", remote, f"HEAD:{branch}"], check=False, env=env)
    if push.returncode != 0:
        err = (push.stderr or push.stdout)[-600:]
        hint = "" if token else "（未設定 GITHUB_TOKEN：請在 .env 加入 fine-grained PAT，或用 GitHub Desktop 手動 Push）"
        return {"ok": False, "pushed": False, "reason": f"git push 失敗：{err}{hint}"}
    return {"ok": True, "pushed": True, "message": msg, "dashboard": dashboard_url(remote)}
