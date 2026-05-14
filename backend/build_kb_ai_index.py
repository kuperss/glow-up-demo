"""Build the backend knowledge-base embedding index.

This converts kb/*.md plus selected website pages into small retrievable
chunks. Run whenever website training content or KB markdown changes.

PowerShell:
  $env:GEMINI_API_KEY="..."
  python backend/build_kb_ai_index.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from build_product_ai_index import RateLimiter, api_key, embed_with_retry
from kb_rag import KB_CHUNKS_PATH, KB_INDEX_PATH
from product_rag import BASE_DIR, EMBEDDING_DIM, EMBEDDING_MODEL

PROJECT_ROOT = BASE_DIR.parent
KB_DIR = PROJECT_ROOT / "kb"
SITE_PAGES = (
    "index.html",
    "brand.html",
    "products.html",
    "academy.html",
    "career.html",
    "onboarding.html",
    "challenge.html",
    "digital.html",
    "day3.html",
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WS_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = WS_RE.sub(" ", text)
    return text.strip()


def normalize_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def chunk_text(text: str, max_chars: int = 1100, overlap: int = 120) -> list[str]:
    text = text.strip()
    if not text:
        return []

    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(para) > max_chars:
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
        else:
            sentences = [para]

        for piece in sentences:
            piece = piece.strip()
            if not piece:
                continue
            if buf and len(buf) + len(piece) + 2 > max_chars:
                chunks.append(buf.strip())
                tail = buf[-overlap:].strip() if overlap > 0 else ""
                buf = (tail + "\n" + piece).strip() if tail else piece
            else:
                buf = (buf + "\n\n" + piece).strip() if buf else piece

    if buf:
        chunks.append(buf.strip())
    return chunks


def split_markdown_sections(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    sections: list[dict[str, str]] = []
    heading_stack: list[str] = [path.stem]
    current_lines: list[str] = []
    current_title = path.stem

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append({"title": current_title, "text": body})

    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush()
            current_lines = []
            level = len(m.group(1))
            heading = clean_text(m.group(2).strip("# ").strip())
            heading_stack = heading_stack[:level]
            heading_stack.append(heading)
            current_title = " > ".join(heading_stack[1:])
            continue
        current_lines.append(line)
    flush()
    return sections


class VisibleTextParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "svg", "noscript", "template", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag.lower() in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = clean_text(data)
        if text:
            self.parts.append(text)

    def visible_text(self) -> str:
        lines = []
        seen = set()
        for raw in "\n".join(self.parts).splitlines():
            line = clean_text(raw)
            if len(line) < 2:
                continue
            # Drop base64 fragments and long minified-looking strings.
            if len(line) > 220 and " " not in line and "，" not in line and "。" not in line:
                continue
            key = normalize_for_dedupe(line)
            if key in seen:
                continue
            seen.add(key)
            lines.append(line)
        return "\n\n".join(lines)


def extract_html_text(path: Path) -> str:
    parser = VisibleTextParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.visible_text()


def build_chunks(kb_dir: Path, site_root: Path, include_site: bool = True) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    dedupe: set[str] = set()

    def add_chunk(source: str, title: str, text: str, kind: str) -> None:
        text = text.strip()
        if len(text) < 60:
            return
        key = hashlib.sha1(normalize_for_dedupe(text).encode("utf-8")).hexdigest()
        if key in dedupe:
            return
        dedupe.add(key)
        chunks.append({
            "id": f"kb-{len(chunks) + 1:04d}",
            "kind": kind,
            "source": source,
            "title": title.strip() or source,
            "text": text,
        })

    for path in sorted(kb_dir.glob("*.md")):
        if path.name.startswith("00_"):
            continue
        for section in split_markdown_sections(path):
            body = f"{section['title']}\n\n{section['text']}"
            for piece in chunk_text(body):
                add_chunk(f"kb/{path.name}", section["title"], piece, "markdown")

    if include_site:
        for page in SITE_PAGES:
            path = site_root / page
            if not path.exists():
                continue
            text = extract_html_text(path)
            title = f"網站頁面 {page}"
            for piece in chunk_text(text, max_chars=1000, overlap=80):
                add_chunk(page, title, piece, "site")

    return chunks


def save_chunks(path: Path, chunks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")


def save_index(
    output_path: Path,
    vecs: np.ndarray,
    chunk_ids: list[str],
    chunks_path: Path,
    done_count: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output_path,
        vecs=vecs,
        chunk_ids=np.array(chunk_ids, dtype=object),
        meta=np.array({
            "model_name": EMBEDDING_MODEL,
            "output_dim": EMBEDDING_DIM,
            "n_rows": len(chunk_ids),
            "done_rows": done_count,
            "source": str(chunks_path),
            "document_schema": ["id", "kind", "source", "title", "text"],
            "purpose": "dancelight-training-ai-knowledge-rag",
        }, dtype=object),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build knowledge-base embedding index.")
    parser.add_argument("--kb-dir", default=str(KB_DIR), help="markdown KB directory")
    parser.add_argument("--site-root", default=str(PROJECT_ROOT), help="website root for HTML pages")
    parser.add_argument("--chunks", default=str(KB_CHUNKS_PATH), help="output chunks JSON path")
    parser.add_argument("--output", default=str(KB_INDEX_PATH), help="output .npz path")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--requests-per-minute", type=int, default=90)
    parser.add_argument("--no-site", action="store_true")
    parser.add_argument("--resume", action="store_true", default=True)
    args = parser.parse_args()

    kb_dir = Path(args.kb_dir)
    site_root = Path(args.site_root)
    chunks_path = Path(args.chunks)
    output_path = Path(args.output)

    chunks = build_chunks(kb_dir, site_root, include_site=not args.no_site)
    if not chunks:
        raise RuntimeError("no knowledge chunks generated")
    save_chunks(chunks_path, chunks)

    key = api_key()
    rows = [
        (
            str(chunk["id"]),
            f"title: {chunk['title']} | source: {chunk['source']} | text: {chunk['text']}",
        )
        for chunk in chunks
    ]
    chunk_ids = [chunk_id for chunk_id, _ in rows]
    vecs = np.zeros((len(rows), EMBEDDING_DIM), dtype=np.float32)
    done_idx: set[int] = set()

    if args.resume and output_path.exists():
        try:
            prev = np.load(output_path, allow_pickle=True)
            prev_ids = [str(x) for x in prev["chunk_ids"].tolist()]
            prev_vecs = np.asarray(prev["vecs"], dtype=np.float32)
            if prev_ids == chunk_ids and prev_vecs.shape == vecs.shape:
                vecs = prev_vecs.copy()
                done_idx = {i for i in range(len(rows)) if np.any(vecs[i] != 0)}
                print(f"[resume] skip {len(done_idx)}, build {len(rows) - len(done_idx)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[resume] ignored previous index: {exc}")

    start = time.time()
    limiter = RateLimiter(args.requests_per_minute)
    total = len(rows) - len(done_idx)
    print(f"chunks: {len(rows)}")
    print(f"chunks file: {chunks_path}")
    print(f"model: {EMBEDDING_MODEL}, dim={EMBEDDING_DIM}")
    print(f"workers: {args.max_workers}, rpm={args.requests_per_minute}")

    done = 0
    failed = 0
    with httpx.Client(timeout=30) as client:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futures = {
                ex.submit(embed_with_retry, client, key, text, limiter): i
                for i, (_chunk_id, text) in enumerate(rows)
                if i not in done_idx
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    vecs[idx] = future.result()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"! failed row {idx} {chunk_ids[idx]}: {str(exc)[:160]}")
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = max(time.time() - start, 0.001)
                    print(f"... {done}/{total} ({done / elapsed:.1f}/s)")
                if done % 200 == 0:
                    save_index(output_path, vecs, chunk_ids, chunks_path, len(done_idx) + done - failed)

    save_index(output_path, vecs, chunk_ids, chunks_path, len(rows) - failed)
    print(f"OK chunks: {chunks_path} ({chunks_path.stat().st_size / 1024:.1f} KB)")
    print(f"OK index: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    if failed:
        print(f"failed: {failed}; re-run with same command to resume")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    main()
