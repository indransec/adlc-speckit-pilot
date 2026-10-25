# Approval token (ADR-003)

Issued by the orchestrator after the last required approval.
Fields: claim_id, approved_amount_usd, route_row, approvers [L1, L2], issued_at, expiry, signature.
The payments tool validates the signature, amount and expiry before creating an instruction.
