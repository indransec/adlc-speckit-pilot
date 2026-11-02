# Data flows

| Flow | Data | Redaction / control |
|---|---|---|
| Customer -> claims system | Claim form, documents, bank details | TLS; stored in document store |
| Document store -> Document agent | Document images/text | Bank details masked before model call |
| Agents -> model endpoint | Extracted fields, policy clauses | PII minimised; no bank details; [region] hosting |
| Agents -> audit trail | Inputs, outputs, decisions | PII redacted in logs; full record in secure audit store |
| Settlement -> payments | Payee, amount, approval token | Never passes through a model |
| Document store -> Document agent (0.4.0) | Bookings / boarding passes for each leg: name, booking reference, flight, date, seat | "Travel documents" (Confidential); barcode not decoded; booking reference redacted in logs |
| Verification -> flight-status source (0.4.0) | Flight number and date of each leg | No personal data sent |
| Verification <- MCT reference (0.4.0) | Airport code -> minutes | Reference data, no personal data (ADR-006) |
| Orchestrator <- claims system `journey_benefits` (0.4.0) | Paid amounts and receipt fingerprints for the same journey | Read only, code only, not sent to a model (ADR-007) |
Retention: audit [N] years; model call logs [90] days (redacted).
