"""Policy clause retrieval (FAISS + fastembed) for the Coverage and Assessment agents.

The policy wording PDF in the object store is the source; it is split per clause
(e.g. "5.3 Valuables limit. ...") so every deduction can cite clause id + text.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from io import BytesIO

import numpy as np

from shared import object_store
from shared.config import EMBEDDING_CACHE, EMBEDDING_MODEL, faiss_dir

WORDING_KEY = "policies/TRV-2026-wording.pdf"
CLAUSE_RE = re.compile(r"(?m)^(\d+\.\d+)\s+([^.\n]+)\.\s*(.*?)(?=^\d+\.\d+\s|^Section \d+|\Z)", re.S)


@lru_cache
def _embedder():
    from fastembed import TextEmbedding
    EMBEDDING_CACHE.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(EMBEDDING_MODEL, cache_dir=str(EMBEDDING_CACHE))


def embed(texts: list[str]) -> np.ndarray:
    v = np.array(list(_embedder().embed(texts)), dtype="float32")
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    return v


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(data)).pages)


def parse_clauses(text: str) -> list[dict]:
    out = []
    for cid, title, body in CLAUSE_RE.findall(text):
        body = " ".join(body.split())
        out.append({"clause_id": cid, "title": title.strip(), "text": f"{title.strip()}. {body}".strip()})
    return out


def build_index() -> int:
    import faiss
    clauses = parse_clauses(pdf_text(object_store.get(WORDING_KEY)))
    vecs = embed([f"{c['clause_id']} {c['text']}" for c in clauses])
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss_dir().mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(faiss_dir() / "policy_clauses.faiss"))
    (faiss_dir() / "policy_clauses.json").write_text(json.dumps(clauses, indent=1))
    _load.cache_clear()
    return len(clauses)


@lru_cache
def _load():
    import faiss
    index = faiss.read_index(str(faiss_dir() / "policy_clauses.faiss"))
    clauses = json.loads((faiss_dir() / "policy_clauses.json").read_text())
    return index, clauses


def search(query: str, k: int = 6) -> list[dict]:
    index, clauses = _load()
    scores, idx = index.search(embed([query]), k)
    return [{**clauses[i], "score": round(float(s), 4)} for s, i in zip(scores[0], idx[0]) if i >= 0]


def clause(clause_id: str) -> dict | None:
    _, clauses = _load()
    return next((c for c in clauses if c["clause_id"] == clause_id), None)


def all_clauses() -> list[dict]:
    return _load()[1]
