"""Load a synthetic case spec into the systems of record (SQLite + object store PDFs)."""
from __future__ import annotations

from shared import db, object_store
from synthetic import pdfs

USERS = [
    {"user_id": "l1.priya", "name": "Priya S. (L1 adjuster)", "role": "L1", "authority": 1},
    {"user_id": "l1.marco", "name": "Marco D. (L1 adjuster)", "role": "L1", "authority": 1},
    {"user_id": "l2.grace", "name": "Grace O. (L2 senior adjuster)", "role": "L2", "authority": 2},
    {"user_id": "l2.tomas", "name": "Tomas R. (L2 claims manager)", "role": "L2", "authority": 2},
    {"user_id": "siu.sam", "name": "Sam K. (SIU investigator)", "role": "SIU", "authority": 2},
]

CLAIM_COLS = {"claim_id", "policy_no", "trip_id", "claimant_name", "claimant_email", "claimant_phone",
              "claimant_address", "passport_no", "bank_account_holder", "bank_account_no", "bank_code",
              "nationality_group", "destination_region", "age_band", "channel", "flight_no", "flight_date",
              "booking_date", "claim_type_declared", "claimed_usd", "compensation_received_usd", "description",
              "submitted_at"}


def seed_users() -> None:
    for u in USERS:
        db.upsert("users", u)


def materialize(case: dict) -> str:
    p = dict(case["policy"])
    p["insured_persons"] = db.jdump(p["insured_persons"])
    db.upsert("policies", p)
    db.upsert("flights", case["flight"])
    for h in case.get("history", []):
        db.upsert("claims", {
            "claim_id": h["claim_id"], "policy_no": h["policy_no"], "trip_id": h["trip_id"],
            "claimant_name": h["claimant_name"], "bank_account_holder": h["claimant_name"],
            "flight_no": h["flight_no"], "flight_date": h["flight_date"], "claim_type_declared": h["claim_type"],
            "claim_type": h["claim_type"], "claimed_usd": h["claimed_usd"], "approved_usd": h["approved_usd"],
            "lines_json": db.jdump(h["lines"]), "status": h["status"], "submitted_at": h["submitted_at"],
            "closed_at": h["submitted_at"], "historical": 1, "description": "historical claim (synthetic)",
            "scenario": "history", "updated_at": db.now()})
    c = case["claim"]
    row = {k: c[k] for k in CLAIM_COLS}
    row.update({"lines_json": db.jdump(c["lines"]), "scenario": case["scenario"],
                "ground_truth": db.jdump({**case["expected"], "title": case["title"],
                                          "requirements": case["requirements"]}),
                "status": "new", "historical": 0, "updated_at": db.now()})
    db.upsert("claims", row)
    db.execute("DELETE FROM documents WHERE claim_id = ?", [c["claim_id"]])
    for n, d in enumerate(case["documents"], 1):
        data = pdfs.claim_form(c) if d["doc_type"] == "claim_form" else pdfs.RENDERERS[d["doc_type"]](d["fields"])
        key = f"claims/{c['claim_id']}/{d['filename']}"
        object_store.put(key, data)
        db.insert("documents", {"doc_id": f"{c['claim_id']}-D{n:02d}", "claim_id": c["claim_id"],
                                "doc_type": d["doc_type"], "filename": d["filename"], "object_key": key,
                                "uploaded_at": c["submitted_at"]})
    return c["claim_id"]


def add_document(claim_id: str, doc_type: str, fields: dict, filename: str) -> str:
    key = f"claims/{claim_id}/{filename}"
    object_store.put(key, pdfs.RENDERERS[doc_type](fields))
    n = (db.scalar("SELECT COUNT(*) FROM documents WHERE claim_id = ?", [claim_id]) or 0) + 1
    doc_id = f"{claim_id}-D{n:02d}"
    db.insert("documents", {"doc_id": doc_id, "claim_id": claim_id, "doc_type": doc_type, "filename": filename,
                            "object_key": key, "uploaded_at": db.now()})
    return doc_id
