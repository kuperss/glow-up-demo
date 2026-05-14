FROM python:3.11-slim

WORKDIR /app

# 系統套件 (notebooklm-py 純 Python，不需 chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 程式碼 + 後端知識庫 / 私有產品資料
COPY app.py dancelight_service.py product_rag.py kb_rag.py start.sh ./
COPY data ./data

RUN chmod +x start.sh

ENV PORT=8000
EXPOSE 8000

CMD ["./start.sh"]
