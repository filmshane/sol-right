"""Local free vector DB (Chroma) for FAQ / knowledge retrieval.

Replaces the brief's n8n in-memory vector store with an on-disk Chroma
collection under /opt/sol-right/data/chroma. No cloud vector SaaS required.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KB_DIR = ROOT / "app" / "kb"
DEFAULT_CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION = "sol_right_kb"

_client: chromadb.PersistentClient | None = None


def _client_instance(persist_dir: str | Path = DEFAULT_CHROMA_DIR) -> chromadb.PersistentClient:
    global _client
    path = str(persist_dir)
    if _client is None:
        Path(path).mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def _chunk_markdown(text: str, source: str) -> list[dict[str, str]]:
    """Split markdown into Q/A or section chunks."""
    chunks: list[dict[str, str]] = []
    # Q/A pairs
    parts = re.split(r"\n(?=Q:\s)", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("Q:"):
            chunks.append({"text": part, "source": source, "kind": "faq"})
        elif part.startswith("#"):
            # section blocks without Q:
            body = part.strip()
            if len(body) > 40:
                chunks.append({"text": body[:2000], "source": source, "kind": "section"})
    if not chunks and text.strip():
        # fallback sliding windows
        words = text.split()
        for i in range(0, len(words), 180):
            window = " ".join(words[i : i + 220])
            if window.strip():
                chunks.append({"text": window, "source": source, "kind": "window"})
    return chunks


def load_kb_files(kb_dir: str | Path = DEFAULT_KB_DIR) -> list[dict[str, str]]:
    kb = Path(kb_dir)
    docs: list[dict[str, str]] = []
    for path in sorted(kb.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        docs.extend(_chunk_markdown(text, source=path.name))
    return docs


def ensure_index(
    kb_dir: str | Path = DEFAULT_KB_DIR,
    persist_dir: str | Path = DEFAULT_CHROMA_DIR,
    force: bool = False,
) -> dict[str, Any]:
    """Build or refresh the Chroma collection from KB markdown files."""
    client = _client_instance(persist_dir)
    docs = load_kb_files(kb_dir)
    if not docs:
        return {"ok": False, "error": "no kb documents", "count": 0}

    fingerprint = hashlib.sha256(
        "\n".join(d["text"] for d in docs).encode("utf-8")
    ).hexdigest()[:16]

    existing = {c.name for c in client.list_collections()}
    if COLLECTION in existing and not force:
        col = client.get_collection(COLLECTION)
        # if count matches and metadata fingerprint matches, skip rebuild
        meta = col.metadata or {}
        if meta.get("fingerprint") == fingerprint and col.count() == len(docs):
            return {
                "ok": True,
                "rebuilt": False,
                "count": col.count(),
                "fingerprint": fingerprint,
                "persist_dir": str(persist_dir),
            }
        client.delete_collection(COLLECTION)

    col = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"fingerprint": fingerprint, "hnsw:space": "cosine"},
    )
    ids = [f"doc-{i}-{hashlib.md5(d['text'].encode()).hexdigest()[:10]}" for i, d in enumerate(docs)]
    col.upsert(
        ids=ids,
        documents=[d["text"] for d in docs],
        metadatas=[{"source": d["source"], "kind": d["kind"]} for d in docs],
    )
    return {
        "ok": True,
        "rebuilt": True,
        "count": col.count(),
        "fingerprint": fingerprint,
        "persist_dir": str(persist_dir),
    }


def retrieve(query: str, n_results: int = 4) -> dict[str, Any]:
    """Semantic retrieve top FAQ/knowledge chunks for the agent."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query", "matches": []}
    ensure_index()
    client = _client_instance()
    col = client.get_or_create_collection(COLLECTION)
    if col.count() == 0:
        ensure_index(force=True)
        col = client.get_collection(COLLECTION)
    res = col.query(query_texts=[q], n_results=min(n_results, max(1, col.count())))
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    matches = []
    for i, doc in enumerate(docs):
        matches.append(
            {
                "text": doc,
                "source": (metas[i] or {}).get("source") if i < len(metas) else None,
                "kind": (metas[i] or {}).get("kind") if i < len(metas) else None,
                "distance": dists[i] if i < len(dists) else None,
            }
        )
    return {
        "ok": True,
        "query": q,
        "count": len(matches),
        "matches": matches,
        "engine": "chromadb",
        "collection": COLLECTION,
    }


def status() -> dict[str, Any]:
    try:
        info = ensure_index()
        client = _client_instance()
        col = client.get_or_create_collection(COLLECTION)
        return {
            "ok": True,
            "engine": "chromadb",
            "collection": COLLECTION,
            "count": col.count(),
            "persist_dir": str(DEFAULT_CHROMA_DIR),
            "index": info,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "engine": "chromadb"}
