"""舞光戰將訓練系統的 backend AI service.

接收前端問題 → 查後端知識庫與私有產品 RAG → 交給 OpenAI（建議）或 NotebookLM 回答。
"""
from __future__ import annotations

import os
import re
import time
import hashlib
import logging
from collections import OrderedDict
import httpx
from notebooklm import NotebookLMClient
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from kb_rag import KnowledgeRAG
from product_rag import ProductRAG

log = logging.getLogger(__name__)

NOTEBOOK_ID = os.environ.get("DANCELIGHT_NOTEBOOK_ID", "1af7e026-a5e0-443e-81e7-87c09ba07a6d")
SHARED_SECRET = os.environ.get("DANCELIGHT_SHARED_SECRET", "")
STORAGE_PATH = os.environ.get(
    "NOTEBOOKLM_STORAGE",
    "/app/credentials/notebooklm_storage.json",
)
# 多 Google 帳號修正：notebooklm-py 預設只認第 1 個帳號（authuser=0）。
# DANCELIGHT_AUTHUSER 可以填：
#   - 留空 或 "0" → 不掛 hook，用 notebooklm-py 預設（單一帳號 / 第 1 個帳號就用這）
#   - 帳號 email（"yourname@gmail.com"）— 推薦，順位變動不會壞
#   - 數字順位（"4"）— 同一個 Chrome session 順位會變、容易出錯
AUTHUSER = os.environ.get("DANCELIGHT_AUTHUSER", "").strip()
# 只有非空且非 "0" 時才需要強制覆蓋。"0" = notebooklm-py 預設行為，掛 hook 反而可能跟單一帳號 cookie 衝突
NEED_AUTHUSER_HOOK = bool(AUTHUSER) and AUTHUSER != "0"

# 熱門問題快取設定
CACHE_TTL_SECONDS = int(os.environ.get("DANCELIGHT_CACHE_TTL_SECONDS", "21600"))  # 6 小時
CACHE_MAX_ENTRIES = int(os.environ.get("DANCELIGHT_CACHE_MAX_ENTRIES", "500"))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("DANCELIGHT_OPENAI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o-mini")).strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_TIMEOUT_SECONDS = float(os.environ.get("DANCELIGHT_OPENAI_TIMEOUT_SECONDS", "60"))
DEFAULT_LLM_PROVIDER = os.environ.get(
    "DANCELIGHT_LLM_PROVIDER",
    "openai" if OPENAI_API_KEY else "notebooklm",
).strip().lower()


class _TTLCache:
    """簡易 TTL + LRU cache（不引入額外依賴）。

    LRU：超過 maxsize 時丟最久沒用的；TTL：到期自動失效。
    """

    def __init__(self, maxsize: int, ttl: int):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if not item:
            self.misses += 1
            return None
        value, expire_at = item
        if time.time() > expire_at:
            del self._store[key]
            self.misses += 1
            return None
        self._store.move_to_end(key)  # LRU 計次：最近用過放最尾
        self.hits += 1
        return value

    def set(self, key: str, value: str) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time() + self.ttl)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)  # 丟最舊

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total else 0.0
        return {
            "size": len(self._store),
            "maxsize": self.maxsize,
            "ttl_seconds": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 3),
        }


def _normalize_question(q: str) -> str:
    """把問題標準化以提高快取命中率：
    - 去前後空白
    - 多個空白合併成 1 個
    - 移除尾端標點（？！。.,，）
    - 全部轉小寫（中文無大小寫，主要影響英數）
    """
    q = re.sub(r"\s+", " ", q).strip().lower()
    q = re.sub(r"[？！。\.,，?!]+$", "", q)
    return q


def _make_cache_key(question: str, system_prompt: str) -> str:
    """快取 key：normalized question + system prompt 的 SHA-256。

    包含 system prompt：admin 換人格時所有舊答自動失效。
    """
    payload = f"{_normalize_question(question)}|||{system_prompt or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _force_authuser_hook(request: httpx.Request) -> None:
    """httpx 事件 hook — 對 notebooklm.google.com 強制覆蓋 authuser 參數與 header。

    notebooklm-py 內部部分 URL 寫死 authuser=0（_sources.py），會選錯帳號；
    這個 hook 在 request 即將送出前覆蓋成正確的 AUTHUSER 值。
    Google 服務同時接受 authuser=N（數字）與 authuser=email（推薦）。
    """
    if request.url.host == "notebooklm.google.com":
        # 用 copy_set_param 覆蓋既有 authuser；既有值不對也直接被替換
        # （httpx 會自動處理 email 中 @ 的 URL encoding）
        request.url = request.url.copy_set_param("authuser", AUTHUSER)
        # 同步覆蓋 x-goog-authuser header
        request.headers["x-goog-authuser"] = AUTHUSER


class DancelightService:
    def __init__(
        self,
        storage_path: str = STORAGE_PATH,
        notebook_id: str = NOTEBOOK_ID,
    ):
        self.storage_path = storage_path
        self.notebook_id = notebook_id
        # 持久化 client：連線、TLS 握手、cookie 解析只在第一次跑，省每次 1-3 秒
        # 但設 30 分鐘 TTL — 每 30 分鐘重建一次，當磁碟上的 cookie 檔被
        # auto_refresh_cookie.py 更新時，最多 30 分鐘內後端就會吃到新值
        self._client: NotebookLMClient | None = None
        self._client_opened_at: float = 0.0
        self._client_max_age = int(os.environ.get("DANCELIGHT_CLIENT_MAX_AGE_SECONDS", "1800"))
        import asyncio as _asyncio
        self._client_lock = _asyncio.Lock()
        # 熱門問題快取：同一問題在 TTL 內只查 NotebookLM 一次，之後秒回
        self._cache = _TTLCache(maxsize=CACHE_MAX_ENTRIES, ttl=CACHE_TTL_SECONDS)
        # 後端私有產品 RAG：產品 JSON / 向量索引只在 server 端讀取，不給瀏覽器整包下載
        self.product_rag = ProductRAG()
        # 後端一般知識庫 RAG：公司、品牌、規章、福利、業務技巧與網站內容
        self.kb_rag = KnowledgeRAG()
        log.info(
            "DancelightService init: notebook=%s storage=%s cache=ttl=%ds max=%d",
            notebook_id, storage_path, CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES,
        )

    def cache_stats(self) -> dict:
        return self._cache.stats()

    def cache_clear(self) -> None:
        self._cache.clear()
        log.info("cache cleared")

    async def _ensure_client(self) -> NotebookLMClient:
        """惰性建立並 cache NotebookLMClient。

        - 第一次 ask 時建立
        - 超過 max_age 時自動重建（吃磁碟上更新後的 cookie）
        - 遇到 auth 失敗會被 ask() reset
        """
        async with self._client_lock:
            # 超齡的 client 強制重建，這樣當磁碟 cookie 被外部腳本更新後能吃到新值
            if self._client is not None and self._client_max_age > 0:
                age = time.time() - self._client_opened_at
                if age > self._client_max_age:
                    log.info("client aged out (%.0fs > %ds), recreating", age, self._client_max_age)
                    try:
                        await self._client.__aexit__(None, None, None)
                    except Exception:
                        pass
                    self._client = None

            if self._client is None:
                log.info("opening new NotebookLMClient (storage=%s)", self.storage_path)
                client = await NotebookLMClient.from_storage(self.storage_path)
                await client.__aenter__()
                # 註冊 authuser hook（多帳號才需要；單一帳號掛了會反而選錯）
                if NEED_AUTHUSER_HOOK:
                    try:
                        http = client._core._http_client
                        if http is not None:
                            hooks = http.event_hooks.setdefault("request", [])
                            if _force_authuser_hook not in hooks:
                                hooks.append(_force_authuser_hook)
                                log.info("authuser hook registered: authuser=%s", AUTHUSER)
                    except Exception as e:
                        log.warning("authuser hook registration failed: %s", e)
                else:
                    log.info("authuser hook skipped (DANCELIGHT_AUTHUSER empty or 0)")
                self._client = client
                self._client_opened_at = time.time()
            return self._client

    async def _reset_client(self) -> None:
        """強制丟掉現有 client（auth 失敗或已被 Google 踢登後呼叫）."""
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.__aexit__(None, None, None)
                except Exception:
                    pass
                self._client = None

    async def close(self) -> None:
        """app shutdown 時呼叫，釋放 httpx 連線."""
        await self._reset_client()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=20),
        retry=retry_if_exception_type((ConnectionError, OSError, TimeoutError)),
        reraise=True,
    )
    async def ask(
        self,
        question: str,
        system_prompt: str = "",
        messages: list[dict] | None = None,
        model: str = "",
        provider: str = "",
    ) -> str:
        """呼叫設定的 LLM 取回答.

        question: 使用者的問題
        system_prompt: 角色設定（會 prepend 到問題前面）

        回傳: 純文字答案
        """
        if not question:
            raise ValueError("question is empty")

        # 1. 先查後端知識庫與私有產品庫，只注入少量相關片段，不把整包資料給前端
        knowledge_context = await self.kb_rag.build_context(question)
        product_context = await self.product_rag.build_context(question)
        llm_provider = (provider or DEFAULT_LLM_PROVIDER or "openai").strip().lower()
        selected_model = (model or OPENAI_MODEL or "").strip()
        cache_salt = (
            f"provider={llm_provider}|model={selected_model}\n"
            + system_prompt
            + ("\n\n" + knowledge_context if knowledge_context else "")
            + ("\n\n" + product_context if product_context else "")
        )

        # 2. 先查快取（同問題 + 同 system prompt + 同產品上下文 在 TTL 內直接秒回）
        cache_key = _make_cache_key(question, cache_salt)
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info("cache HIT: %s (ans=%d chars)", question[:50], len(cached))
            return cached

        log.info("cache MISS: %s", question[:50])

        if llm_provider in {"backend", "backend_openai", "openai"}:
            answer = await self._ask_openai(
                question=question,
                system_prompt=system_prompt,
                knowledge_context=knowledge_context,
                product_context=product_context,
                messages=messages or [],
                model=model,
            )
        elif llm_provider == "notebooklm":
            answer = await self._ask_notebooklm(question, system_prompt, knowledge_context, product_context)
        else:
            raise ValueError(f"unknown LLM provider: {llm_provider}")

        # 3. 寫進快取（只有有實際內容才存，避免快取空答覆）
        if answer.strip():
            self._cache.set(cache_key, answer)
        return answer

    async def _ask_notebooklm(
        self,
        question: str,
        system_prompt: str,
        knowledge_context: str,
        product_context: str,
    ) -> str:
        # NotebookLM chat 沒有獨立 system role，把 system / 知識庫 / 私有產品查詢結果 / question 合併成一則 user message
        prompt_parts = []
        if system_prompt:
            prompt_parts.append(f"【角色設定】\n{system_prompt}")
        if knowledge_context:
            prompt_parts.append(knowledge_context)
        if product_context:
            prompt_parts.append(product_context)
        prompt_parts.append(f"【使用者問題】\n{question}")
        prompt = "\n\n".join(prompt_parts)

        client = await self._ensure_client()
        try:
            response = await client.chat.ask(self.notebook_id, prompt)
            return getattr(response, "answer", "") or ""
        except Exception as e:
            # 若是 auth 相關錯誤，丟掉舊 client，下次 ask 會重新建（拿新 cookie）
            msg = str(e).lower()
            if "auth" in msg or "401" in msg or "expired" in msg or "signin" in msg:
                log.warning("auth error, resetting client: %s", e)
                await self._reset_client()
            raise

    async def _ask_openai(
        self,
        question: str,
        system_prompt: str,
        knowledge_context: str,
        product_context: str,
        messages: list[dict],
        model: str = "",
    ) -> str:
        """Use OpenAI from the backend so API key and product context stay private."""
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set on backend")

        system_parts = []
        if system_prompt:
            system_parts.append(system_prompt)
        if knowledge_context:
            system_parts.append(knowledge_context)
        if product_context:
            system_parts.append(product_context)
        system_parts.append(
            "回答時只能根據後端提供的內部知識庫與產品查詢結果；"
            "若資料不足，請明確說不確定，不要自行編造公司制度、福利、型號、價格、庫存或規格。"
        )

        chat_messages: list[dict[str, str]] = [
            {"role": "system", "content": "\n\n".join(system_parts)}
        ]

        for item in (messages or [])[-10:]:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                chat_messages.append({"role": role, "content": content})

        if not chat_messages or chat_messages[-1].get("role") != "user" or chat_messages[-1].get("content") != question:
            chat_messages.append({"role": "user", "content": question})

        payload = {
            "model": (model or OPENAI_MODEL or "gpt-4o-mini").strip(),
            "messages": chat_messages,
        }
        temperature = os.environ.get("DANCELIGHT_OPENAI_TEMPERATURE", "").strip()
        if temperature:
            payload["temperature"] = float(temperature)

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=OPENAI_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{OPENAI_BASE_URL}/chat/completions", headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        answer = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not answer:
            raise RuntimeError("OpenAI response is empty")
        return answer


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
