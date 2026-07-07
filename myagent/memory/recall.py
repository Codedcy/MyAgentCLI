"""Memory recall — semantic + keyword matching for L4 context loading.

Uses sentence-transformers for semantic embedding when available,
with pure keyword-matching fallback.

Fixes audit #31.
"""

from __future__ import annotations

import logging
import re

from myagent.memory.store import MemoryFile, MemoryStore

logger = logging.getLogger("myagent.memory")


_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_][a-z0-9_-]*", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "you",
    "can",
    "that",
    "this",
    "with",
    "have",
    "from",
    "are",
    "not",
    "but",
    "all",
    "was",
    "has",
    "had",
    "its",
    "his",
    "her",
    "our",
    "will",
    "would",
    "could",
    "should",
    "been",
    "being",
}


# ── embedding helpers (lazy-loaded) ──────────────────────────────

_embedding_model = None
_EMBEDDING_UNAVAILABLE = False


def _get_embedding_model():
    """Lazy-load sentence-transformers model. Returns None if unavailable."""
    global _embedding_model, _EMBEDDING_UNAVAILABLE

    if _embedding_model is not None:
        return _embedding_model
    if _EMBEDDING_UNAVAILABLE:
        return None

    try:
        from sentence_transformers import SentenceTransformer

        # Use a small, fast multilingual model
        _embedding_model = SentenceTransformer(
            "paraphrase-multilingual-MiniLM-L12-v2"
        )
        logger.info("Loaded sentence-transformers for semantic memory recall",
                    extra={"category": "system"})
        return _embedding_model
    except ImportError:
        _EMBEDDING_UNAVAILABLE = True
        logger.exception(
            "sentence-transformers not available; using keyword recall",
            extra={
                "category": "error",
                "component": "memory",
                "context": "import sentence-transformers for recall",
            },
        )
        logger.debug("sentence-transformers not available — using keyword recall",
                     extra={"category": "system"})
        return None
    except Exception:
        _EMBEDDING_UNAVAILABLE = True
        logger.exception(
            "Failed to load sentence-transformers; using keyword recall",
            extra={
                "category": "error",
                "component": "memory",
                "context": "memory_embedding_model_load",
            },
        )
        return None


def _cosine_similarity(a, b) -> float:
    """Compute cosine similarity between two vectors."""
    import numpy as np

    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a_norm = np.linalg.norm(a)
    b_norm = np.linalg.norm(b)
    if a_norm == 0 or b_norm == 0:
        return 0.0
    return float(np.dot(a, b) / (a_norm * b_norm))


def _keyword_tokens(text: str) -> set[str]:
    """Extract search tokens for keyword fallback, including CJK n-grams."""
    tokens: set[str] = set()
    normalized = text.lower()

    for token in _ASCII_TOKEN_RE.findall(normalized):
        if len(token) >= 2 and token not in _STOP_WORDS:
            tokens.add(token)

    for run in _CJK_RUN_RE.findall(text):
        if len(run) < 2:
            continue
        max_n = min(4, len(run))
        for n in range(2, max_n + 1):
            for i in range(0, len(run) - n + 1):
                tokens.add(run[i:i + n])
        if len(run) <= 8:
            tokens.add(run)

    return tokens


# ── recall ───────────────────────────────────────────────────────


async def recall(
    query: str,
    store: MemoryStore,
    limit: int = 10,
) -> list[MemoryFile]:
    """Recall relevant memories by semantic embedding + keyword matching.

    If sentence-transformers is available, uses cosine similarity on
    embeddings for ranking. Falls back to TF-like keyword scoring
    otherwise.

    After initial recall, follows [[wiki-style links]] in each recalled
    memory to include linked (cross-referenced) memories in the result set.
    Linked memories are appended after direct matches with lower priority.

    Args:
        query: The search query (usually current user input).
        store: MemoryStore to search.
        limit: Max number of memories to return.

    Returns:
        Ranked list of MemoryFile objects, most relevant first.
    """
    query_tokens = _keyword_tokens(query)
    if not query_tokens:
        return []

    # Gather all memories from both scopes
    all_entries = []
    all_entries.extend(await store.list_all("project"))
    all_entries.extend(await store.list_all("user"))

    if not all_entries:
        return []

    # Try semantic embedding recall
    model = _get_embedding_model()
    if model is not None:
        try:
            results = await _semantic_recall(model, query, store, all_entries, limit)
        except Exception:
            logger.exception(
                "Semantic recall failed; falling back to keyword",
                extra={
                    "category": "error",
                    "component": "memory",
                    "context": "memory_semantic_recall",
                },
            )
            results = await _keyword_recall(query_tokens, store, all_entries, limit)
    else:
        # Fallback: keyword-based recall
        results = await _keyword_recall(query_tokens, store, all_entries, limit)

    # Follow wiki links: for each recalled memory, resolve [[links]]
    # and include linked memories in the result set (appended after direct matches).
    results = await _resolve_cross_references(results, store, limit)

    return results


async def _semantic_recall(
    model,
    query: str,
    store: MemoryStore,
    entries: list,
    limit: int,
) -> list[MemoryFile]:
    """Semantic embedding-based recall using cosine similarity."""

    # Build corpus: read all memory content
    memory_files: list[MemoryFile] = []
    texts: list[str] = []
    for entry in entries:
        mf = await store.read(entry.name)
        if mf is None:
            continue
        memory_files.append(mf)
        # Combine name + description + content for embedding
        texts.append(f"{mf.name}: {mf.description}\n{mf.content}")

    if not memory_files:
        return []

    # Compute embeddings
    query_embedding = model.encode(query, convert_to_numpy=True)
    doc_embeddings = model.encode(texts, convert_to_numpy=True)

    # Score by cosine similarity
    scored = []
    for i, mf in enumerate(memory_files):
        similarity = _cosine_similarity(query_embedding, doc_embeddings[i])
        if similarity > 0.1:  # minimum relevance threshold
            scored.append((similarity, mf))

    # Sort by descending similarity
    scored.sort(key=lambda x: -x[0])
    return [mf for _, mf in scored[:limit]]


async def _keyword_recall(
    query_tokens: set,
    store: MemoryStore,
    entries: list,
    limit: int,
) -> list[MemoryFile]:
    """Keyword-based recall with TF-like scoring (pure Python, no deps)."""
    scored = []
    for entry in entries:
        mf = await store.read(entry.name)
        if mf is None:
            continue
        text = f"{mf.name} {mf.description} {mf.content}".lower()
        score = sum(1 for token in query_tokens if token in text)
        if score > 0:
            scored.append((score, mf))

    # Sort by descending score, then by name for stability
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [mf for _, mf in scored[:limit]]


async def _resolve_cross_references(
    results: list[MemoryFile],
    store: MemoryStore,
    limit: int,
) -> list[MemoryFile]:
    """Follow [[wiki-style links]] in recalled memories to include linked memories.

    For each directly recalled memory that has metadata["links"], look up
    the linked memory names and include them in the result set. Linked
    memories are appended after direct matches (lower priority).

    Deduplication: if a linked memory is already in the direct results, it
    is not added again.
    """
    direct_names = {mf.name for mf in results}
    linked_names: set[str] = set()
    linked_memories: list[MemoryFile] = []

    # Collect all unique link targets from direct results
    for mf in results:
        links = mf.metadata.get("links", [])
        if isinstance(links, list):
            for link_name in links:
                link_name = link_name.strip()
                if link_name and link_name not in direct_names and link_name not in linked_names:
                    linked_names.add(link_name)

    # Resolve linked memories
    slots_remaining = limit - len(results)
    for link_name in linked_names:
        if slots_remaining <= 0:
            break
        linked_mf = await store.read(link_name)
        if linked_mf is not None:
            linked_memories.append(linked_mf)
            slots_remaining -= 1

    return results + linked_memories
