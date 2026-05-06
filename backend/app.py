"""舞光戰將 AI 助教 — FastAPI backend。

提供唯一一個 endpoint：
  POST /api/dancelight/ask
    Header: Authorization: Bearer <DANCELIGHT_SHARED_SECRET>
    Body:   { question: str, system?: str, messages?: list }
    Resp:   { answer: str }

內部呼叫 NotebookLM 的固定筆記本（DANCELIGHT_NOTEBOOK_ID 環境變數）。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dancelight_service import DancelightService, verify_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup：什麼都不做，client 會在第一次 ask 時惰性建立（避免冷啟動阻塞）
    yield
    # shutdown：關掉持久化的 NotebookLMClient
    log.info("shutting down — closing NotebookLM client")
    await dancelight.close()


app = FastAPI(title="舞光戰將 AI 助教", lifespan=lifespan)

# CORS：訓練網站（GitHub Pages）+ 本機開發
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://kuperss.github.io",
        "http://localhost:8080",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

dancelight = DancelightService()


class AskRequest(BaseModel):
    question: str
    system: str = ""
    messages: list[dict] = []  # 前端帶來的歷史訊息（目前不用，留作擴充）


class AskResponse(BaseModel):
    answer: str


@app.get("/")
def root():
    return {"service": "dancelight-ai", "status": "running"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/cache/stats")
def cache_stats(authorization: str | None = Header(None)):
    """看快取命中率（只給帶 shared secret 的呼叫看）."""
    if not verify_secret(authorization):
        raise HTTPException(401, "unauthorized")
    return dancelight.cache_stats()


@app.post("/cache/clear")
def cache_clear(authorization: str | None = Header(None)):
    """清空快取（KB 更新後可呼叫；只給帶 shared secret 的呼叫）."""
    if not verify_secret(authorization):
        raise HTTPException(401, "unauthorized")
    dancelight.cache_clear()
    return {"ok": True, "stats": dancelight.cache_stats()}


@app.post("/api/dancelight/ask", response_model=AskResponse)
async def dancelight_ask(body: AskRequest, authorization: str | None = Header(None)):
    """舞光戰將訓練 AI 助教 — RAG 走 NotebookLM 固定筆記本."""
    if not verify_secret(authorization):
        log.warning("unauthorized request to /api/dancelight/ask")
        raise HTTPException(401, "unauthorized")

    if not body.question.strip():
        raise HTTPException(400, "question is empty")

    log.info("ask: %s", body.question[:80])
    try:
        answer = await dancelight.ask(body.question, body.system)
        log.info("ok: returned %d chars", len(answer))
        return AskResponse(answer=answer)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "auth" in msg.lower():
            log.error("NotebookLM cookie expired: %s", msg)
            raise HTTPException(503, f"NotebookLM cookie expired, run manual_login.py: {msg}")
        log.exception("NotebookLM error")
        raise HTTPException(500, f"NotebookLM error: {msg}")


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
