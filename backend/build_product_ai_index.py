"""Build the backend-only product AI embedding index.

This intentionally uses a richer, assistant-oriented document than the older
catalog index that embedded only 商品名稱. Run whenever products_private.json
changes.

PowerShell:
  $env:GEMINI_API_KEY="..."
  python backend/build_product_ai_index.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from product_rag import (
    BASE_DIR,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    PRODUCT_CATALOG_PATH,
    PRODUCT_INDEX_PATH,
    build_product_document,
)


class RateLimiter:
    def __init__(self, requests_per_minute: int):
        self.interval = 60.0 / max(1, requests_per_minute)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self._last = time.monotonic()


RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)")


def api_key() -> str:
    key = (
        os.environ.get("DANCELIGHT_EMBEDDING_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError("請先設定 GEMINI_API_KEY 或 DANCELIGHT_EMBEDDING_API_KEY")
    return key


def normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm < 1e-12:
        return vec
    return vec / norm


def embed_one(client: httpx.Client, key: str, text: str) -> np.ndarray:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent"
    payload = {
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": EMBEDDING_DIM,
    }
    resp = client.post(url, headers={"x-goog-api-key": key}, json=payload)
    resp.raise_for_status()
    data = resp.json()
    values = (data.get("embedding") or {}).get("values")
    if not values and data.get("embeddings"):
        values = data["embeddings"][0].get("values")
    if not values:
        raise RuntimeError("empty embedding response")
    return normalize(np.asarray(values, dtype=np.float32))


def embed_with_retry(
    client: httpx.Client,
    key: str,
    text: str,
    limiter: RateLimiter,
    max_retries: int = 3,
) -> np.ndarray:
    for attempt in range(max_retries + 1):
        limiter.wait()
        try:
            return embed_one(client, key, text)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if ("429" in msg or "RESOURCE_EXHAUSTED" in msg or "503" in msg) and attempt < max_retries:
                m = RETRY_DELAY_RE.search(msg)
                delay = float(m.group(1)) if m else min(2 ** attempt, 30)
                time.sleep(delay + 1)
                continue
            raise
    raise RuntimeError("unreachable")


def load_products(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("product catalog must be a JSON array")
    return data


def save_index(
    output_path: Path,
    vecs: np.ndarray,
    model_ids: list[str],
    source_path: Path,
    done_count: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output_path,
        vecs=vecs,
        model_ids=np.array(model_ids, dtype=object),
        meta=np.array({
            "model_name": EMBEDDING_MODEL,
            "output_dim": EMBEDDING_DIM,
            "n_rows": len(model_ids),
            "done_rows": done_count,
            "source": str(source_path),
            "document_schema": [
                "產品型號", "商品名稱", "類型", "核心規格",
                "適用場景", "使用用途", "銷售切入點", "建議客群",
            ],
            "purpose": "dancelight-training-ai-product-rag",
        }, dtype=object),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build product AI embedding index.")
    parser.add_argument("--catalog", default=str(PRODUCT_CATALOG_PATH), help="private products JSON path")
    parser.add_argument("--output", default=str(PRODUCT_INDEX_PATH), help="output .npz path")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--requests-per-minute", type=int, default=90)
    parser.add_argument("--resume", action="store_true", default=True)
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    output_path = Path(args.output)
    products = load_products(catalog_path)
    key = api_key()

    rows: list[tuple[str, str]] = []
    for product in products:
        sku = str(product.get("產品型號") or "").strip()
        if not sku:
            continue
        doc = build_product_document(product)
        rows.append((sku, f"title: {sku} | text: {doc}"))

    if not rows:
        raise RuntimeError("no products with 產品型號")

    model_ids = [sku for sku, _ in rows]
    vecs = np.zeros((len(rows), EMBEDDING_DIM), dtype=np.float32)
    done_idx: set[int] = set()

    if args.resume and output_path.exists():
        try:
            prev = np.load(output_path, allow_pickle=True)
            prev_ids = [str(x) for x in prev["model_ids"].tolist()]
            prev_vecs = np.asarray(prev["vecs"], dtype=np.float32)
            if prev_ids == model_ids and prev_vecs.shape == vecs.shape:
                vecs = prev_vecs.copy()
                done_idx = {i for i in range(len(rows)) if np.any(vecs[i] != 0)}
                print(f"[resume] skip {len(done_idx)}, build {len(rows) - len(done_idx)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[resume] ignored previous index: {exc}")

    start = time.time()
    limiter = RateLimiter(args.requests_per_minute)
    total = len(rows) - len(done_idx)
    print(f"catalog: {catalog_path}")
    print(f"products: {len(rows)}")
    print(f"model: {EMBEDDING_MODEL}, dim={EMBEDDING_DIM}")
    print(f"workers: {args.max_workers}, rpm={args.requests_per_minute}")

    done = 0
    failed = 0
    with httpx.Client(timeout=30) as client, ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {
            ex.submit(embed_with_retry, client, key, text, limiter): i
            for i, (_sku, text) in enumerate(rows)
            if i not in done_idx
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                vecs[idx] = future.result()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"! failed row {idx} {model_ids[idx]}: {str(exc)[:160]}")
            done += 1
            if done % 100 == 0 or done == total:
                elapsed = max(time.time() - start, 0.001)
                print(f"... {done}/{total} ({done / elapsed:.1f}/s)")
            if done % 300 == 0:
                save_index(output_path, vecs, model_ids, catalog_path, len(done_idx) + done - failed)

    save_index(output_path, vecs, model_ids, catalog_path, len(rows) - failed)
    print(f"OK: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    if failed:
        print(f"failed: {failed}; re-run with same command to resume")


if __name__ == "__main__":
    os.chdir(BASE_DIR.parent)
    main()

