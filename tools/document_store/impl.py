"""Mock document store (TOOL-DOCUMENT-STORE) over the local-disk object store."""
from __future__ import annotations

from shared import db, object_store
from shared.rag import pdf_text

OPERATIONS = {"list_documents": "read", "read_text": "read", "write_extracted": "write-extracted-fields"}


def list_documents(claim_id: str) -> list[dict]:
    return db.query("SELECT doc_id, doc_type, filename, uploaded_at FROM documents WHERE claim_id = ? "
                    "ORDER BY doc_id", [claim_id])


def read_text(doc_id: str, claim_id: str | None = None) -> str:
    d = db.one("SELECT object_key, claim_id FROM documents WHERE doc_id = ?", [doc_id])
    if not d:
        raise KeyError(doc_id)
    if claim_id and d["claim_id"] != claim_id:  # a model-chosen doc_id must belong to the claim in hand
        raise PermissionError(f"document {doc_id} does not belong to claim {claim_id}")
    return pdf_text(object_store.get(d["object_key"]))


def write_extracted(doc_id: str, fields: dict, certainty: float) -> None:
    db.update("documents", "doc_id", doc_id, {"extracted": db.jdump(fields), "extraction_certainty": certainty})
