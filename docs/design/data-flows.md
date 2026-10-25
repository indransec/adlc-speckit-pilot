# Data flows

| Flow | Data | Redaction / control |
|---|---|---|
| Customer -> claims system | Claim form, documents, bank details | TLS; stored in document store |
| Document store -> Document agent | Document images/text | Bank details masked before model call |
| Agents -> model endpoint | Extracted fields, policy clauses | PII minimised; no bank details; [region] hosting |
| Agents -> audit trail | Inputs, outputs, decisions | PII redacted in logs; full record in secure audit store |
| Settlement -> payments | Payee, amount, approval token | Never passes through a model |
Retention: audit [N] years; model call logs [90] days (redacted).
