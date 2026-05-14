"""Private product RAG for the training AI assistant.

The browser should not download the full product catalog or vector index.
This module keeps both files on the backend, retrieves a small set of relevant
products, and formats them as grounded context for the assistant.
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

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DANCELIGHT_PRODUCT_DATA_DIR", BASE_DIR / "data"))
PRODUCT_CATALOG_PATH = Path(
    os.environ.get("DANCELIGHT_PRODUCT_CATALOG_PATH", DATA_DIR / "products_private.json")
)
PRODUCT_INDEX_PATH = Path(
    os.environ.get("DANCELIGHT_PRODUCT_INDEX_PATH", DATA_DIR / "product_ai_embeddings.npz")
)

EMBEDDING_MODEL = os.environ.get("DANCELIGHT_EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIM = int(os.environ.get("DANCELIGHT_EMBEDDING_DIM", "768"))
PRODUCT_CONTEXT_TOP_K = int(os.environ.get("DANCELIGHT_PRODUCT_CONTEXT_TOP_K", "5"))
PRODUCT_SEARCH_LIMIT = int(os.environ.get("DANCELIGHT_PRODUCT_SEARCH_LIMIT", "8"))

SKU_RE = re.compile(r"\b[A-Z]{1,5}-[A-Z0-9][A-Z0-9-]{2,}\b", re.I)
SPEC_RE = re.compile(r"(\d+\s*W|\d{4}\s*K|IP\d{2}|R(?:a|9)\s*[≥>=]?\s*\d+|\d+\s*lm)", re.I)

PRODUCT_TERMS = (
    "產品", "型號", "規格", "瓦數", "色溫", "光通量", "流明", "演色", "光束角",
    "崁燈", "吸頂燈", "軌道燈", "磁吸", "投射燈", "泛光燈", "平板燈", "高天井",
    "軟條", "燈管", "燈泡", "壁燈", "檯燈", "吊燈", "日光燈", "格柵燈",
    "防潮燈", "緊急照明", "滅蚊燈", "殺菌燈", "黑板燈", "護眼", "智慧崁燈",
    "索爾", "奧丁", "馬爾", "拉斐爾", "達文西", "阿波羅", "宙斯", "舞色", "雲朵", "星鑽",
    "客廳", "臥室", "玄關", "廚房", "浴室", "陽台", "書房", "服飾店", "餐廳",
    "咖啡廳", "展示櫃", "精品店", "辦公室", "會議室", "教室", "學校", "診所",
    "工廠", "廠房", "倉庫", "停車場", "戶外", "招牌", "路燈", "庭園", "景觀",
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(_as_text(v) for v in value if _as_text(v))
    return str(value).strip()


def _normalize_vec(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm < 1e-12:
        return vec
    return vec / norm


def build_product_document(product: dict[str, Any]) -> str:
    """Compact text used when building/searching the product AI index."""
    specs = []
    for label, key in (
        ("瓦數", "消耗電力"),
        ("色溫", "色溫"),
        ("光通量", "光通量"),
        ("演色性", "演色性"),
        ("光束角", "光束角"),
        ("發光角度", "發光角度"),
        ("IP", "IP等級"),
        ("尺寸", "尺寸"),
        ("壽命", "平均壽命"),
    ):
        value = _as_text(product.get(key))
        if value:
            specs.append(f"{label}:{value}")

    parts = [
        f"型號:{_as_text(product.get('產品型號'))}",
        f"商品名稱:{_as_text(product.get('商品名稱'))}",
        f"類型:{_as_text(product.get('類型'))}",
    ]
    if specs:
        parts.append("規格:" + " / ".join(specs))
    for label, key in (
        ("適用場景", "適用場景"),
        ("使用用途", "使用用途"),
        ("銷售切入點", "銷售切入點"),
        ("建議客群", "建議客群"),
    ):
        value = _as_text(product.get(key))
        if value:
            parts.append(f"{label}:{value}")
    return "\n".join(p for p in parts if p and not p.endswith(":"))


class ProductRAG:
    def __init__(
        self,
        catalog_path: Path = PRODUCT_CATALOG_PATH,
        index_path: Path = PRODUCT_INDEX_PATH,
    ) -> None:
        self.catalog_path = catalog_path
        self.index_path = index_path
        self._lock = threading.Lock()
        self._loaded = False
        self._products: list[dict[str, Any]] = []
        self._product_by_sku: dict[str, dict[str, Any]] = {}
        self._search_docs: list[str] = []
        self._vecs: np.ndarray | None = None
        self._model_ids: list[str] = []
        self._index_meta: dict[str, Any] = {}
        self._query_vec_cache: dict[str, np.ndarray] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return

            if not self.catalog_path.exists():
                log.warning("product catalog not found: %s", self.catalog_path)
                self._loaded = True
                return

            with self.catalog_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            self._products = raw if isinstance(raw, list) else []
            self._product_by_sku = {}
            self._search_docs = []
            for product in self._products:
                sku = _as_text(product.get("產品型號")).upper()
                if sku:
                    self._product_by_sku[sku] = product
                self._search_docs.append(build_product_document(product).lower())

            if self.index_path.exists():
                try:
                    data = np.load(self.index_path, allow_pickle=True)
                    self._vecs = np.asarray(data["vecs"], dtype=np.float32)
                    self._model_ids = [str(v).upper() for v in data["model_ids"].tolist()]
                    self._index_meta = data["meta"].item() if "meta" in data else {}
                    log.info(
                        "product RAG loaded: products=%d index=%s meta=%s",
                        len(self._products), self._vecs.shape, self._index_meta,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("product index load failed: %s", exc)
                    self._vecs = None
                    self._model_ids = []
            else:
                log.warning("product embedding index not found: %s", self.index_path)

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
    def is_product_related(text: str) -> bool:
        if not text:
            return False
        if SKU_RE.search(text) or SPEC_RE.search(text):
            return True
        return any(term.lower() in text.lower() for term in PRODUCT_TERMS)

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
            log.warning("product query embedding failed: %s", exc)
            return None

        values = (data.get("embedding") or {}).get("values")
        if not values and data.get("embeddings"):
            values = data["embeddings"][0].get("values")
        if not values:
            log.warning("product query embedding empty response")
            return None

        vec = _normalize_vec(np.asarray(values, dtype=np.float32))
        if len(self._query_vec_cache) > 256:
            self._query_vec_cache.clear()
        self._query_vec_cache[cache_key] = vec
        return vec

    def _keyword_scores(self, query: str) -> dict[str, float]:
        q = query.strip().lower()
        if not q:
            return {}

        terms = [t.lower() for t in PRODUCT_TERMS if t.lower() in q]
        terms += [m.group(0).lower() for m in SPEC_RE.finditer(query)]
        raw_tokens = re.findall(r"[A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", query)
        terms += [t.lower() for t in raw_tokens if len(t.strip()) >= 2]
        terms = list(dict.fromkeys(terms))

        scores: dict[str, float] = {}
        if not terms:
            return scores

        for idx, doc in enumerate(self._search_docs):
            score = 0.0
            for term in terms:
                if term and term in doc:
                    score += 4.0 if len(term) >= 4 else 2.0
            if score > 0:
                sku = _as_text(self._products[idx].get("產品型號")).upper()
                if sku:
                    scores[sku] = score
        return scores

    def _exact_sku_scores(self, query: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for match in SKU_RE.findall(query.upper()):
            sku = match.strip().upper()
            if sku in self._product_by_sku:
                scores[sku] = 1000.0
        return scores

    async def retrieve(
        self,
        query: str,
        limit: int = PRODUCT_CONTEXT_TOP_K,
        force: bool = False,
        query_vec: np.ndarray | None = None,
        allow_embedding: bool = True,
    ) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if not self._products:
            return []
        if not force and not self.is_product_related(query):
            return []

        limit = max(1, min(int(limit or PRODUCT_CONTEXT_TOP_K), 20))
        combined: dict[str, dict[str, Any]] = {}

        def add_score(sku: str, score: float, source: str, semantic_score: float | None = None) -> None:
            product = self._product_by_sku.get(sku.upper())
            if not product:
                return
            item = combined.setdefault(sku.upper(), {"product": product, "score": 0.0, "sources": set()})
            item["score"] += score
            item["sources"].add(source)
            if semantic_score is not None:
                item["semantic_score"] = max(float(semantic_score), float(item.get("semantic_score", -1)))

        for sku, score in self._exact_sku_scores(query).items():
            add_score(sku, score, "sku")
        for sku, score in self._keyword_scores(query).items():
            add_score(sku, score, "keyword")

        if self._vecs is not None and self._model_ids:
            q_vec = query_vec
            if q_vec is None and allow_embedding:
                q_vec = await self._embed_query(query)
            if q_vec is not None:
                sims = self._vecs @ q_vec.reshape(-1)
                top_n = np.argsort(-sims)[: max(limit * 4, 20)]
                for rank, idx in enumerate(top_n):
                    if idx >= len(self._model_ids):
                        continue
                    sku = self._model_ids[int(idx)]
                    sim = float(sims[int(idx)])
                    # Cosine dominates only for a small top set; keyword/SKU still keep precision.
                    add_score(sku, (sim + 1.0) * 45.0 + max(0, 20 - rank), "semantic", sim)

        results = list(combined.values())
        results.sort(key=lambda item: item["score"], reverse=True)
        out: list[dict[str, Any]] = []
        for item in results[:limit]:
            product = dict(item["product"])
            product["_match"] = {
                "score": round(float(item["score"]), 2),
                "sources": sorted(item["sources"]),
            }
            if "semantic_score" in item:
                product["_match"]["semantic_score"] = round(float(item["semantic_score"]), 4)
            out.append(product)
        return out

    def get_product(self, sku: str) -> dict[str, Any] | None:
        self._ensure_loaded()
        if not sku:
            return None
        product = self._product_by_sku.get(sku.strip().upper())
        return dict(product) if product else None

    async def search(self, query: str, limit: int = PRODUCT_SEARCH_LIMIT) -> list[dict[str, Any]]:
        products = await self.retrieve(query, limit=limit, force=True)
        return [self._compact_product(p) for p in products]

    @staticmethod
    def _compact_product(product: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "產品型號", "商品名稱", "類型", "消耗電力", "色溫", "光通量",
            "演色性", "光束角", "發光角度", "IP等級", "尺寸", "平均壽命",
            "適用場景", "使用用途", "銷售切入點", "建議客群",
        )
        return {k: product.get(k) for k in keys if product.get(k) not in (None, "", [])}

    def format_context(self, products: list[dict[str, Any]]) -> str:
        if not products:
            return ""
        blocks = []
        for idx, product in enumerate(products, 1):
            lines = [
                f"{idx}. [{_as_text(product.get('產品型號'))}] {_as_text(product.get('商品名稱'))}",
            ]
            specs = []
            for label, key in (
                ("類型", "類型"),
                ("瓦數", "消耗電力"),
                ("色溫", "色溫"),
                ("光通量", "光通量"),
                ("演色性", "演色性"),
                ("光束角", "光束角"),
                ("IP", "IP等級"),
                ("尺寸", "尺寸"),
                ("壽命", "平均壽命"),
            ):
                value = _as_text(product.get(key))
                if value:
                    specs.append(f"{label}:{value}")
            if specs:
                lines.append("規格: " + " / ".join(specs))
            for label, key in (
                ("場景", "適用場景"),
                ("用途", "使用用途"),
                ("銷售切入點", "銷售切入點"),
                ("建議客群", "建議客群"),
            ):
                value = _as_text(product.get(key))
                if value:
                    lines.append(f"{label}: {value}")
            blocks.append("\n".join(lines))

        return (
            "【內部產品資料庫查詢結果】\n"
            "以下是後端從私有產品資料庫挑出的候選產品。回答產品規格、適用場景、"
            "推薦話術時，以本段為準；若本段沒有足夠資訊，請明確說不確定。\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    async def build_context(
        self,
        query: str,
        limit: int = PRODUCT_CONTEXT_TOP_K,
        query_vec: np.ndarray | None = None,
        allow_embedding: bool = True,
    ) -> str:
        products = await self.retrieve(
            query,
            limit=limit,
            query_vec=query_vec,
            allow_embedding=allow_embedding,
        )
        return self.format_context(products)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        return {
            "catalog_path": str(self.catalog_path),
            "index_path": str(self.index_path),
            "products": len(self._products),
            "index_shape": list(self._vecs.shape) if self._vecs is not None else None,
            "index_meta": self._index_meta,
            "embedding_key_configured": bool(self._api_key()),
        }
