"""
ChromaDB-backed interaction store for HireOS.

Logs every LLM interaction (chat + agent triggers) with embeddings for semantic search.
Rolling 90-day retention. Embedded, no external server needed.
"""
from __future__ import annotations

import os
import uuid
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection

    import chromadb
    from chromadb.utils import embedding_functions

    chroma_path = os.getenv("CHROMA_PATH", "./output/chroma")
    os.makedirs(chroma_path, exist_ok=True)

    _client = chromadb.PersistentClient(path=chroma_path)

    ef = embedding_functions.DefaultEmbeddingFunction()
    _collection = _client.get_or_create_collection(
        name="chat_interactions",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    logger.info(f"[ChatStore] ChromaDB ready at {chroma_path} — {_collection.count()} docs")
    return _collection


def log_interaction(
    user_input: str,
    agent_output: str,
    action_type: str,
    job_id: Optional[int] = None,
    llm_used: str = "",
    company: str = "",
    title: str = "",
) -> str:
    """Store one interaction. Returns the chroma doc id."""
    try:
        col = _get_collection()
        doc_id = str(uuid.uuid4())
        now = datetime.utcnow()

        document = (
            f"{action_type}: {user_input[:400]}\n\nResult: {agent_output[:400]}"
        )
        metadata = {
            "timestamp_unix": int(now.timestamp()),
            "date_str": now.strftime("%Y-%m-%d"),
            "action_type": action_type,
            "job_id": job_id if job_id is not None else -1,
            "llm_used": llm_used or "",
            "company": company or "",
            "title": title or "",
        }

        col.add(documents=[document], metadatas=[metadata], ids=[doc_id])
        return doc_id
    except Exception as e:
        logger.warning(f"[ChatStore] log_interaction failed (non-fatal): {e}")
        return ""


def get_recent(days: int = 90) -> List[Dict[str, Any]]:
    """Return all interactions within the last `days` days, newest first."""
    try:
        col = _get_collection()
        cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp())
        results = col.get(
            where={"timestamp_unix": {"$gte": cutoff}},
            include=["documents", "metadatas"],
        )
        items = []
        for doc, meta, doc_id in zip(
            results["documents"], results["metadatas"], results["ids"]
        ):
            items.append({"id": doc_id, "document": doc, **meta})
        items.sort(key=lambda x: x.get("timestamp_unix", 0), reverse=True)
        return items
    except Exception as e:
        logger.warning(f"[ChatStore] get_recent failed: {e}")
        return []


def get_stats(days: int = 90) -> Dict[str, Any]:
    """Aggregate stats over the last `days` days."""
    from collections import Counter

    items = get_recent(days)
    if not items:
        return {
            "total_interactions": 0,
            "by_action": {},
            "by_day": [],
            "top_companies": [],
            "most_used_llm": None,
            "active_days": 0,
        }

    by_action: Counter = Counter()
    by_day: Counter = Counter()
    companies: Counter = Counter()
    llms: Counter = Counter()

    for item in items:
        by_action[item.get("action_type", "unknown")] += 1
        by_day[item.get("date_str", "")] += 1
        if item.get("company"):
            companies[item["company"]] += 1
        if item.get("llm_used"):
            llms[item["llm_used"]] += 1

    by_day_sorted = [
        {"date": d, "count": c}
        for d, c in sorted(by_day.items())
    ]

    return {
        "total_interactions": len(items),
        "by_action": dict(by_action),
        "by_day": by_day_sorted,
        "top_companies": [c for c, _ in companies.most_common(10)],
        "most_used_llm": llms.most_common(1)[0][0] if llms else None,
        "active_days": len(by_day),
    }


def search_similar(query: str, n: int = 10, days: int = 90) -> List[Dict[str, Any]]:
    """Semantic search over recent interactions."""
    try:
        col = _get_collection()
        cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp())
        results = col.query(
            query_texts=[query],
            n_results=min(n, max(col.count(), 1)),
            where={"timestamp_unix": {"$gte": cutoff}},
            include=["documents", "metadatas", "distances"],
        )
        items = []
        for doc, meta, dist, doc_id in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            results["ids"][0],
        ):
            items.append({"id": doc_id, "document": doc, "similarity": round(1 - dist, 3), **meta})
        return items
    except Exception as e:
        logger.warning(f"[ChatStore] search_similar failed: {e}")
        return []


def purge_old(days: int = 90) -> int:
    """Delete interactions older than `days` days. Returns count deleted."""
    try:
        col = _get_collection()
        cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp())
        old = col.get(
            where={"timestamp_unix": {"$lt": cutoff}},
            include=[],
        )
        ids = old["ids"]
        if ids:
            col.delete(ids=ids)
            logger.info(f"[ChatStore] Purged {len(ids)} interactions older than {days}d")
        return len(ids)
    except Exception as e:
        logger.warning(f"[ChatStore] purge_old failed: {e}")
        return 0


def get_recent_samples(days: int = 90, limit: int = 20) -> List[str]:
    """Return raw document strings for recent interactions (for LLM context)."""
    items = get_recent(days)[:limit]
    return [item["document"] for item in items]
