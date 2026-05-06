"""Test B：透過 fly.io 後端（東京 IP 出 Google）測 cookie 壽命。

跟 test_cookie_lifetime.py 對稱：同樣的問題、同樣的間隔（前 1 小時 5 分鐘
一次、之後 30 分鐘一次），只差在 outbound IP — 本機跑 = 台灣 IP，
跑 fly = 東京 IP。

跑法：
  cd backend
  .\.venv\Scripts\Activate.ps1
  python test_cookie_lifetime_fly.py

前置：
  1. fly 已經 scale up（flyctl scale count 1）
  2. fly 上的 cookie 是最新的（剛 deploy 過）
  3. 本機要設環境變數：
       $env:DANCELIGHT_ENDPOINT = "https://dancelight-ai.fly.dev"
       $env:DANCELIGHT_SECRET   = "你的 shared secret"
     不設的話從預設值取（可能對也可能不對）

紀錄：cookie_lifetime_log_fly.csv
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

LOG_PATH = Path(__file__).parent / "cookie_lifetime_log_fly.csv"
TEST_QUESTION = "舞光的核心理念是什麼？"  # 跟 test_cookie_lifetime.py 同題

ENDPOINT = os.environ.get("DANCELIGHT_ENDPOINT", "https://dancelight-ai.fly.dev").rstrip("/")
SECRET = os.environ.get("DANCELIGHT_SECRET", "")

# 排程：跟 test_cookie_lifetime.py 完全一致以便比較
INITIAL_INTERVAL = 5 * 60
LATER_INTERVAL = 30 * 60
SWITCH_AFTER_OK_COUNT = 12

ASK_TIMEOUT = httpx.Timeout(connect=10, read=120, write=30, pool=30)


async def ask_fly(client: httpx.AsyncClient, question: str) -> str:
    headers = {"Content-Type": "application/json"}
    if SECRET:
        headers["Authorization"] = f"Bearer {SECRET}"
    resp = await client.post(
        f"{ENDPOINT}/api/dancelight/ask",
        headers=headers,
        json={"question": question, "system": "", "messages": []},
        timeout=ASK_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("answer", "")


async def main():
    print(f"[start]    {datetime.now()}")
    print(f"[endpoint] {ENDPOINT}")
    print(f"[secret]   {'(set)' if SECRET else '(EMPTY — will fail if backend requires bearer)'}")
    print(f"[log]      {LOG_PATH}")
    start_ts = datetime.now()

    new_log = not LOG_PATH.exists()
    f = LOG_PATH.open("a", encoding="utf-8", newline="")
    writer = csv.writer(f)
    if new_log:
        writer.writerow(["timestamp", "elapsed_minutes", "status", "error_msg"])
        f.flush()

    ok_count = 0
    iteration = 0

    async with httpx.AsyncClient() as client:
        while True:
            iteration += 1
            ts = datetime.now()
            elapsed_min = round((ts - start_ts).total_seconds() / 60, 1)

            try:
                answer = await ask_fly(client, TEST_QUESTION)
                ok_count += 1
                print(f"{ts.strftime('%H:%M:%S')} #{iteration} [OK] elapsed={elapsed_min}min ans={len(answer)}chars")
                writer.writerow([ts.isoformat(timespec="seconds"), elapsed_min, "OK", ""])
                f.flush()
            except Exception as e:
                err = str(e)[:300]
                print(f"{ts.strftime('%H:%M:%S')} #{iteration} [FAIL] elapsed={elapsed_min}min")
                print(f"  err: {err}")
                writer.writerow([ts.isoformat(timespec="seconds"), elapsed_min, "FAIL", err])
                f.flush()
                print()
                print(f"=== Test B (Tokyo IP) 結論 ===")
                print(f"撐了 {elapsed_min} 分鐘 ({elapsed_min/60:.1f} 小時) 才失效")
                print()
                print("拿這個結果跟 cookie_lifetime_log.csv (Test A, Taiwan IP) 比較：")
                print("  Test A 撐 >> Test B → IP 風控確認，搬遷有效")
                print("  Test A 撐 ≈ Test B → IP 不是主因，搬遷無用")
                f.close()
                return

            interval = LATER_INTERVAL if ok_count >= SWITCH_AFTER_OK_COUNT else INITIAL_INTERVAL
            await asyncio.sleep(interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[abort] 使用者中斷")
