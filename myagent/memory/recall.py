"""Memory recall — keyword-based matching for L4 context loading."""

from __future__ import annotations

from myagent.memory.store import MemoryFile, MemoryStore


async def recall(
    query: str,
    store: MemoryStore,
    limit: int = 10,
) -> list[MemoryFile]:
    """Recall relevant memories by keyword matching.

    Matches query tokens against memory name + description + content.
    Returns up to `limit` memories ranked by TF-like scoring.
    """
    query_tokens = set(query.lower().split())
    if not query_tokens:
        return []

    # Gather all memories from both scopes
    all_memories = []
    all_memories.extend(await store.list_all("project"))
    all_memories.extend(await store.list_all("user"))

    # Score each memory by keyword overlap
    scored = []
    for entry in all_memories:
        mf = await store.read(entry.name)
        if mf is None:
            continue
        text = f"{mf.name} {mf.description} {mf.content}".lower()
        score = sum(1 for token in query_tokens if token in text)
        if score > 0:
            scored.append((score, mf))

    # Sort by descending score, then by recency (we don't have recency, so name)
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [mf for _, mf in scored[:limit]]
