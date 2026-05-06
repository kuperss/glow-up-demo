"""自動更新 NotebookLM cookie 並部署到 fly.io。

跑法（手動或排程）：
  python auto_refresh_cookie.py

前置：
  必須已經跑過 manual_login.py 一次（建立 browser_profile 並登入過目標帳號）。

機制：
  1. 用既有的 ~/.notebooklm/browser_profile（headless）開啟 NotebookLM
  2. 若還在登入狀態 → 存新 cookie → base64 + fly secrets set
  3. 若已登出 → 印出提示要求手動跑 manual_login.py

排程建議：
  Windows 工作排程器，每 2-3 天跑一次。Google 約 2 週會強制踢登一次，
  所以每 1-2 週還是會需要手動跑 manual_login.py 一次。

Exit code:
  0 = 成功更新並部署
  1 = session 已過期，需要 manual_login.py
  2 = 其他錯誤（環境/網路）
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HOME = Path.home() / ".notebooklm"
BROWSER_PROFILE = HOME / "browser_profile"
STORAGE_PATH = HOME / "storage_state.json"
PROJECT_CREDS = Path(__file__).resolve().parent / "credentials" / "notebooklm_storage.json"

NOTEBOOKLM_URL = "https://notebooklm.google.com/"
FLY_APP = os.environ.get("FLY_APP", "dancelight-ai")

# 找 flyctl — 優先用 PATH，fallback 到 winget 預設路徑
FLYCTL_DEFAULT = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft" / "WinGet" / "Packages"
    / "Fly-io.flyctl_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "flyctl.exe"
)


def find_flyctl() -> str | None:
    flyctl = shutil.which("flyctl") or shutil.which("fly")
    if flyctl:
        return flyctl
    if FLYCTL_DEFAULT.exists():
        return str(FLYCTL_DEFAULT)
    return None


def main() -> int:
    if not BROWSER_PROFILE.exists():
        print("[ERROR] browser_profile 不存在 — 請先跑 manual_login.py 一次")
        return 1

    print(f"[info] 用 profile: {BROWSER_PROFILE}")
    print(f"[info] 訪問: {NOTEBOOKLM_URL}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--password-store=basic",
            ],
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(NOTEBOOKLM_URL, timeout=30000)
        except Exception as e:
            print(f"[ERROR] 無法連到 NotebookLM: {e}")
            context.close()
            return 2

        # 給頁面 5 秒做完所有 cookie 設定 / redirect
        page.wait_for_timeout(5000)

        current_url = page.url
        print(f"[info] 目前 URL: {current_url}")

        if "accounts.google.com" in current_url or "/login" in current_url or "/signin" in current_url:
            print("[ERROR] session 已過期 — 必須跑 manual_login.py 手動重新登入")
            context.close()
            return 1

        # 還在登入狀態 → 存新 cookie
        context.storage_state(path=str(STORAGE_PATH))
        PROJECT_CREDS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STORAGE_PATH, PROJECT_CREDS)
        context.close()

    print(f"[OK] 新 cookie 已存到: {PROJECT_CREDS}")

    # base64 + flyctl secrets set
    flyctl = find_flyctl()
    if not flyctl:
        print("[WARN] 找不到 flyctl，略過部署。請手動上傳 cookie：")
        print("  $bytes = [System.IO.File]::ReadAllBytes(...)")
        return 0

    print(f"[info] 部署到 fly: {FLY_APP}")
    b64 = base64.b64encode(PROJECT_CREDS.read_bytes()).decode("ascii")
    try:
        subprocess.run(
            [flyctl, "secrets", "set", f"NOTEBOOKLM_STORAGE_JSON_B64={b64}", "-a", FLY_APP],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] flyctl secrets set 失敗: {e}")
        return 2

    print("[OK] 部署完成。後端會在 1-2 分鐘內 rolling update 完。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
