"""Policy wording knowledge base (TOOL-POLICY-WORDING): FAISS search over the TRV-2026 clauses."""
from __future__ import annotations

from shared import rag

OPERATIONS = {"search": "read", "get_clause": "read"}


def search(query: str, k: int = 4) -> list[dict]:
    return [{"clause_id": h["clause_id"], "title": h["title"], "text": h["text"], "score": h["score"]}
            for h in rag.search(query, k=max(1, min(int(k), 8)))]


def get_clause(clause_id: str) -> dict | None:
    c = rag.clause(clause_id.strip())
    return {"clause_id": c["clause_id"], "title": c["title"], "text": c["text"]} if c else None
