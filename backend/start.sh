#!/bin/sh
# fly.io 容器啟動：從 fly secrets 還原 cookie 檔
mkdir -p /app/credentials

# 優先用 base64 編碼版本（避免 shell escape 問題）
if [ -n "$NOTEBOOKLM_STORAGE_JSON_B64" ]; then
    echo "$NOTEBOOKLM_STORAGE_JSON_B64" | base64 -d > /app/credentials/notebooklm_storage.json
    echo "[start.sh] cookie 從 NOTEBOOKLM_STORAGE_JSON_B64 (base64) 還原成功"
elif [ -n "$NOTEBOOKLM_STORAGE_JSON" ]; then
    printf '%s' "$NOTEBOOKLM_STORAGE_JSON" > /app/credentials/notebooklm_storage.json
    echo "[start.sh] cookie 從 NOTEBOOKLM_STORAGE_JSON (raw) 還原成功"
else
    echo "[start.sh] 警告：NOTEBOOKLM_STORAGE_JSON_B64 / NOTEBOOKLM_STORAGE_JSON 都未設定"
fi

# 驗證 cookie 可讀
if [ -f /app/credentials/notebooklm_storage.json ]; then
    SIZE=$(wc -c < /app/credentials/notebooklm_storage.json)
    echo "[start.sh] cookie 檔大小: $SIZE bytes"
fi

export NOTEBOOKLM_STORAGE="/app/credentials/notebooklm_storage.json"

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
