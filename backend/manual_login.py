"""繞過 notebooklm-py CLI login 的 bug，手動產生 storage_state.json.

問題：notebooklm CLI 在按 Enter 後會 force navigate 到 accounts.google.com，
但 Google 看到已登入的 session 會重導回 notebooklm.google.com，
playwright 的 goto 因此中斷，login 失敗。

這腳本跳過那個強制跳轉，直接把 cookies 存出來。

用法：
  pip install "notebooklm-py[browser]"
  playwright install chromium
  python manual_login.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

NOTEBOOKLM_URL = "https://notebooklm.google.com/"

# notebooklm-py 預設路徑
HOME = Path.home() / ".notebooklm"
BROWSER_PROFILE = HOME / "browser_profile"
STORAGE_PATH = HOME / "storage_state.json"

# 也複製一份到 credentials/（部署時用）
PROJECT_CREDS = Path(__file__).resolve().parent / "credentials" / "notebooklm_storage.json"


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    PROJECT_CREDS.parent.mkdir(parents=True, exist_ok=True)

    print(f"瀏覽器 profile: {BROWSER_PROFILE}")
    print(f"輸出 storage : {STORAGE_PATH}")
    print(f"複製到專案   : {PROJECT_CREDS}")
    print()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--password-store=basic",
            ],
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(NOTEBOOKLM_URL)

        print("=" * 50)
        print("步驟：")
        print("  1. 在跳出的瀏覽器完成 Google 登入")
        print("  2. 等到看見 NotebookLM 首頁（左側可看到 Notebook 列表）")
        print("  3. 回到這個視窗按 ENTER")
        print("=" * 50)

        try:
            input("\n[完成後按 ENTER] ")
        except (EOFError, KeyboardInterrupt):
            print("\n取消")
            context.close()
            return 1

        current_url = page.url
        if "notebooklm.google.com" not in current_url:
            print(f"\n警告：目前不在 NotebookLM (現在在 {current_url})")
            ans = input("還是要存嗎？[y/N] ").strip().lower()
            if ans != "y":
                context.close()
                return 1

        # 直接存，不做會壞的 force-redirect
        context.storage_state(path=str(STORAGE_PATH))

        # 同時複製到專案
        import shutil
        shutil.copy2(STORAGE_PATH, PROJECT_CREDS)

        context.close()

    print(f"\n[OK] storage 已存到：{STORAGE_PATH}")
    print(f"[OK] 已複製到專案   ：{PROJECT_CREDS}")
    print("\n下一步：")
    print("  notebooklm auth check --test")
    print("  → 五項全 ✓ 表示成功")
    print("\n部署到 Fly：")
    print("  $json = Get-Content credentials\\notebooklm_storage.json -Raw")
    print("  fly secrets set NOTEBOOKLM_STORAGE_JSON=\"$json\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
