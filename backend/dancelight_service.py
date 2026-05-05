"""舞光戰將訓練系統的 NotebookLM RAG service.

接收前端問題 → 用固定 NotebookLM 筆記本（DANCELIGHT_NOTEBOOK_ID）回答。
不建立／不刪除任何 notebook，只 chat.ask。
"""
from __future__ import annotations

import os
import logging
from notebooklm import NotebookLMClient
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = logging.getLogger(__name__)

NOTEBOOK_ID = os.environ.get("DANCELIGHT_NOTEBOOK_ID", "1af7e026-a5e0-443e-81e7-87c09ba07a6d")
SHARED_SECRET = os.environ.get("DANCELIGHT_SHARED_SECRET", "")
STORAGE_PATH = os.environ.get(
    "NOTEBOOKLM_STORAGE",
    "/app/credentials/notebooklm_storage.json",
)


class DancelightService:
    def __init__(
        self,
        storage_path: str = STORAGE_PATH,
        notebook_id: str = NOTEBOOK_ID,
    ):
        self.storage_path = storage_path
        self.notebook_id = notebook_id
        log.info("DancelightService init: notebook=%s storage=%s", notebook_id, storage_path)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=20),
        retry=retry_if_exception_type((ConnectionError, OSError, TimeoutError)),
        reraise=True,
    )
    async def ask(self, question: str, system_prompt: str = "") -> str:
        """呼叫 NotebookLM chat 取回答.

        question: 使用者的問題
        system_prompt: 角色設定（會 prepend 到問題前面）

        回傳: 純文字答案
        """
        if not question:
            raise ValueError("question is empty")

        # NotebookLM chat 沒有獨立 system role，把 system 跟 question 合併成一則 user message
        prompt = (
            f"【角色設定】\n{system_prompt}\n\n【使用者問題】\n{question}"
            if system_prompt
            else question
        )

        async with await NotebookLMClient.from_storage(self.storage_path) as client:
            answer = await client.chat.ask(self.notebook_id, prompt)
            return getattr(answer, "answer", "") or ""


def verify_secret(authorization_header) -> bool:
    """驗證前端帶來的 Bearer token.

    沒設 SHARED_SECRET 時視為開發模式（全放行 + 警告）。
    Production 一定要設 DANCELIGHT_SHARED_SECRET。
    """
    if not SHARED_SECRET:
        log.warning("DANCELIGHT_SHARED_SECRET not set — allowing all (DEV ONLY)")
        return True
    if not authorization_header:
        return False
    if not authorization_header.startswith("Bearer "):
        return False
    return authorization_header[len("Bearer "):].strip() == SHARED_SECRET
