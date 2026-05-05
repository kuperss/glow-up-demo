#!/bin/sh
# fly.io 容器啟動：從 fly secrets 還原 cookie 檔
mkdir -p /app/credentials

if [ -n "$NOTEBOOKLM_STORAGE_JSON" ]; then
    printf '%s' "$NOTEBOOKLM_STORAGE_JSON" > /app/credentials/notebooklm_storage.json
    echo "[start.sh] cookie 從 NOTEBOOKLM_STORAGE_JSON 還原成功"
else
    echo "[start.sh] 警告：NOTEBOOKLM_STORAGE_JSON 未設定，AI 會 401"
fi

export NOTEBOOKLM_STORAGE="/app/credentials/notebooklm_storage.json"

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
