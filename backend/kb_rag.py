"""Backend knowledge-base RAG for the training AI assistant.

This index covers the public training KB and selected website page text:
company/brand, rules, benefits, career path, and sales skills. Product SKU
lookup stays in product_rag.py because it needs the private product catalog.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from product_rag import DATA_DIR, EMBEDDING_DIM, EMBEDDING_MODEL, _normalize_vec

log = logging.getLogger(__name__)

KB_CHUNKS_PATH = Path(os.environ.get("DANCELIGHT_KB_CHUNKS_PATH", DATA_DIR / "kb_chunks.json"))
KB_INDEX_PATH = Path(os.environ.get("DANCELIGHT_KB_INDEX_PATH", DATA_DIR / "kb_ai_embeddings.npz"))
KB_CONTEXT_TOP_K = int(os.environ.get("DANCELIGHT_KB_CONTEXT_TOP_K", "6"))

DOMAIN_TERMS = (
    "舞光", "展晟", "led", "產品", "照明", "燈", "業務", "新人", "制度", "規章",
    "福利", "請假", "加班", "出勤", "打卡", "公務車", "事故", "員購", "尾牙",
    "五光獎", "三安", "職涯", "升等", "客戶", "拜訪", "報價", "議價", "異議",
    "經銷商", "燈飾店", "電料行", "設計師", "工程商", "體驗館", "品牌", "王金蓮",
    "黃心怡", "黃真瑋", "黃祺軒", "王進生", "新世代學院", "光安心", "光永續",
    "光氛圍", "光智慧", "光感動", "報表", "mor", "bi", "erp", "業管", "採購", "財會",
)


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", text or "")
    out: list[str] = []
    for token in raw:
        t = token.lower().strip()
        if len(t) >= 2:
            out.append(t)
    return list(dict.fromkeys(out))


class KnowledgeRAG:
    def __init__(
        self,
        chunks_path: Path = KB_CHUNKS_PATH,
        index_path: Path = KB_INDEX_PATH,
    ) -> None:
        self.chunks_path = chunks_path
        self.index_path = index_path
        self._lock = threading.Lock()
        self._loaded = False
        self._chunks: list[dict[str, Any]] = []
        self._chunk_by_id: dict[str, dict[str, Any]] = {}
        self._search_docs: list[str] = []
        self._vecs: np.ndarray | None = None
        self._chunk_ids: list[str] = []
        self._index_meta: dict[str, Any] = {}
        self._query_vec_cache: dict[str, np.ndarray] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return

            if not self.chunks_path.exists():
                log.warning("knowledge chunks not found: %s", self.chunks_path)
                self._loaded = True
                return

            with self.chunks_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            self._chunks = raw if isinstance(raw, list) else []
            self._chunk_by_id = {str(c.get("id")): c for c in self._chunks if c.get("id")}
            self._search_docs = [
                "\n".join([
                    str(c.get("title") or ""),
                    str(c.get("source") or ""),
                    str(c.get("text") or ""),
                ]).lower()
                for c in self._chunks
            ]

            if self.index_path.exists():
                try:
                    data = np.load(self.index_path, allow_pickle=True)
                    self._vecs = np.asarray(data["vecs"], dtype=np.float32)
                    self._chunk_ids = [str(v) for v in data["chunk_ids"].tolist()]
                    self._index_meta = data["meta"].item() if "meta" in data else {}
                    log.info(
                        "knowledge RAG loaded: chunks=%d index=%s meta=%s",
                        len(self._chunks), self._vecs.shape, self._index_meta,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("knowledge index load failed: %s", exc)
                    self._vecs = None
                    self._chunk_ids = []
            else:
                log.warning("knowledge embedding index not found: %s", self.index_path)

            self._loaded = True

    @staticmethod
    def _api_key() -> str:
        return (
            os.environ.get("DANCELIGHT_EMBEDDING_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        ).strip()

    @staticmethod
    def is_domain_related(text: str) -> bool:
        q = (text or "").lower()
        return any(term.lower() in q for term in DOMAIN_TERMS)

    async def _embed_query(self, text: str) -> np.ndarray | None:
        key = self._api_key()
        if not key:
            return None
        cache_key = text.strip().lower()
        if cache_key in self._query_vec_cache:
            return self._query_vec_cache[cache_key]

        payload = {
            "content": {
                "parts": [{"text": f"task: search result | query: {text}"}],
            },
            "outputDimensionality": EMBEDDING_DIM,
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers={"x-goog-api-key": key}, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("knowledge query embedding failed: %s", exc)
            return None

        values = (data.get("embedding") or {}).get("values")
        if not values and data.get("embeddings"):
            values = data["embeddings"][0].get("values")
        if not values:
            log.warning("knowledge query embedding empty response")
            return None

        vec = _normalize_vec(np.asarray(values, dtype=np.float32))
        if len(self._query_vec_cache) > 256:
            self._query_vec_cache.clear()
        self._query_vec_cache[cache_key] = vec
        return vec

    async def embed_query(self, text: str) -> np.ndarray | None:
        return await self._embed_query(text)

    def _keyword_scores(self, query: str) -> dict[str, float]:
        terms = _tokenize(query)
        terms += [term.lower() for term in DOMAIN_TERMS if term.lower() in (query or "").lower()]
        terms = list(dict.fromkeys(terms))
        if not terms:
            return {}

        scores: dict[str, float] = {}
        for idx, doc in enumerate(self._search_docs):
            score = 0.0
            for term in terms:
                if term and term in doc:
                    score += 20.0 if term in [t.lower() for t in DOMAIN_TERMS] else 10.0
            if score > 0 and idx < len(self._chunks):
                chunk_id = str(self._chunks[idx].get("id"))
                scores[chunk_id] = score
        return scores

    async def retrieve(
        self,
        query: str,
        limit: int = KB_CONTEXT_TOP_K,
        query_vec: np.ndarray | None = None,
        allow_embedding: bool = True,
    ) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if not self._chunks:
            return []

        limit = max(1, min(int(limit or KB_CONTEXT_TOP_K), 12))
        combined: dict[str, dict[str, Any]] = {}

        def add_score(chunk_id: str, score: float, source: str, semantic_score: float | None = None) -> None:
            chunk = self._chunk_by_id.get(chunk_id)
            if not chunk:
                return
            item = combined.setdefault(chunk_id, {"chunk": chunk, "score": 0.0, "sources": set()})
            item["score"] += score
            item["sources"].add(source)
            if semantic_score is not None:
                item["semantic_score"] = max(float(semantic_score), float(item.get("semantic_score", -1)))

        for chunk_id, score in self._keyword_scores(query).items():
            add_score(chunk_id, score, "keyword")

        if self._vecs is not None and self._chunk_ids:
            q_vec = query_vec
            if q_vec is None and allow_embedding:
                q_vec = await self._embed_query(query)
            if q_vec is not None:
                sims = self._vecs @ q_vec.reshape(-1)
                top_n = np.argsort(-sims)[: max(limit * 4, 20)]
                for rank, idx in enumerate(top_n):
                    if idx >= len(self._chunk_ids):
                        continue
                    sim = float(sims[int(idx)])
                    # Skip very weak matches unless keyword search already caught them.
                    if sim < 0.35 and not self.is_domain_related(query):
                        continue
                    add_score(
                        self._chunk_ids[int(idx)],
                        max(0.0, sim) * 50.0 + max(0, 12 - rank),
                        "semantic",
                        sim,
                    )

        results = list(combined.values())
        results.sort(
            key=lambda item: (1 if "keyword" in item["sources"] else 0, item["score"]),
            reverse=True,
        )
        out: list[dict[str, Any]] = []
        for item in results[:limit]:
            chunk = dict(item["chunk"])
            chunk["_match"] = {
                "score": round(float(item["score"]), 2),
                "sources": sorted(item["sources"]),
            }
            if "semantic_score" in item:
                chunk["_match"]["semantic_score"] = round(float(item["semantic_score"]), 4)
            out.append(chunk)
        return out

    def format_context(self, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            return ""
        blocks = []
        for idx, chunk in enumerate(chunks, 1):
            title = str(chunk.get("title") or "").strip()
            source = str(chunk.get("source") or "").strip()
            text = str(chunk.get("text") or "").strip()
            blocks.append(f"{idx}. {title}\n來源: {source}\n{text}")
        return (
            "【內部知識庫查詢結果】\n"
            "以下是後端從公司知識庫與網站內容挑出的相關片段。回答公司制度、福利、"
            "品牌故事、業務技巧與網站內容時，以本段為準；若本段沒有足夠資訊，請明確說不確定。\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    async def build_context(
        self,
        query: str,
        limit: int = KB_CONTEXT_TOP_K,
        query_vec: np.ndarray | None = None,
        allow_embedding: bool = True,
    ) -> str:
        chunks = await self.retrieve(
            query,
            limit=limit,
            query_vec=query_vec,
            allow_embedding=allow_embedding,
        )
        return self.format_context(chunks)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        return {
            "chunks_path": str(self.chunks_path),
            "index_path": str(self.index_path),
            "chunks": len(self._chunks),
            "index_shape": list(self._vecs.shape) if self._vecs is not None else None,
            "index_meta": self._index_meta,
            "embedding_key_configured": bool(self._api_key()),
        }
