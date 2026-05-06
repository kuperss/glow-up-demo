"""決定性測試：在台灣本機 IP 跑同一份 cookie，看是否會比 fly.io 東京 IP 撐得久。

跑法：
  cd backend
  .\.venv\Scripts\Activate.ps1   # 啟用 venv
  python test_cookie_lifetime.py

機制：
  - 直接用 NotebookLMClient（同 production backend）
  - 每 5 分鐘 ping 一次（剛開始）
  - 連續成功 12 次 (1 小時) 後，間隔拉長到 30 分鐘
  - 紀錄每次 ping 的成功/失敗 + timestamp 到 cookie_lifetime_log.csv
  - 一直跑到第一次失敗 → 印出總撐了多久 → 退出

預期結果：
  - 撐 < 2 小時 → 跟 fly.io 一樣短 → 不是 IP 問題（可能是 cookie 處理 bug、帳號異常等）
  - 撐 6-24 小時以上 → 確認是 IP 風控 → 搬到台灣 IP 確實能解決
  - 撐到 24+ 小時 → 強烈確認；可以放心搬

CSV 欄位：
  timestamp, elapsed_minutes, status (OK/FAIL), error_msg
"""
from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
# 本機跑 → 本機 cookie 路徑（DancelightService 預設指向 /app/...，那是 fly 容器內路徑）
import os as _os
_os.environ.setdefault("NOTEBOOKLM_STORAGE", str(_HERE / "credentials" / "notebooklm_storage.json"))

from dancelight_service import DancelightService

LOG_PATH = _HERE / "cookie_lifetime_log.csv"
TEST_QUESTION = "舞光的核心理念是什麼？"  # 任何 KB 裡找得到的問題

# 排程：開始 5 分鐘間隔，連續 12 次成功後改 30 分鐘
INITIAL_INTERVAL = 5 * 60
LATER_INTERVAL = 30 * 60
SWITCH_AFTER_OK_COUNT = 12


async def main():
    print(f"[start] {datetime.now()}")
    print(f"[log]   {LOG_PATH}")
    service = DancelightService()
    start_ts = datetime.now()

    # CSV header
    new_log = not LOG_PATH.exists()
    f = LOG_PATH.open("a", encoding="utf-8", newline="")
    writer = csv.writer(f)
    if new_log:
        writer.writerow(["timestamp", "elapsed_minutes", "status", "error_msg"])
        f.flush()

    ok_count = 0
    iteration = 0

    while True:
        iteration += 1
        ts = datetime.now()
        elapsed_min = round((ts - start_ts).total_seconds() / 60, 1)

        try:
            answer = await service.ask(TEST_QUESTION)
            ok_count += 1
            status_msg = f"#{iteration} [OK] elapsed={elapsed_min}min ans={len(answer)}chars"
            print(f"{ts.strftime('%H:%M:%S')} {status_msg}")
            writer.writerow([ts.isoformat(timespec="seconds"), elapsed_min, "OK", ""])
            f.flush()
        except Exception as e:
            err = str(e)[:200]
            print(f"{ts.strftime('%H:%M:%S')} #{iteration} [FAIL] elapsed={elapsed_min}min")
            print(f"  err: {err}")
            writer.writerow([ts.isoformat(timespec="seconds"), elapsed_min, "FAIL", err])
            f.flush()
            print()
            print(f"=== 結論 ===")
            print(f"撐了 {elapsed_min} 分鐘 ({elapsed_min/60:.1f} 小時) 才失效")
            if elapsed_min < 120:
                print("→ 撐 < 2 小時：跟 fly.io 一樣 = 應該不是 IP 問題")
                print("  可能其他因素：帳號異常 / cookie 同帳號多用 / lib bug")
            elif elapsed_min < 360:
                print("→ 撐 2-6 小時：稍有延長但不顯著")
                print("  IP 影響存在但不是主因")
            elif elapsed_min < 1440:
                print("→ 撐 6-24 小時：顯著延長")
                print("  IP 風控確認是主因 — 搬台灣 IP 會大幅改善")
            else:
                print("→ 撐 > 24 小時：完全是 IP 風控")
                print("  搬台灣 IP 後可預期接近官方說的「幾天到幾週」")
            await service.close()
            f.close()
            return

        # 決定下一次間隔
        interval = LATER_INTERVAL if ok_count >= SWITCH_AFTER_OK_COUNT else INITIAL_INTERVAL
        await asyncio.sleep(interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[abort] 使用者中斷")
