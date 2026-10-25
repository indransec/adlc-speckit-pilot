# Risk tier assessment

| Criterion | Assessment |
|---|---|
| Customer-facing outcome | Yes: claim decisions and payments |
| Takes actions in systems | Yes: claim updates, payment instructions (gated) |
| Personal data | Yes: name, passport, contact, bank details |
| Special-category data | No (medical out of MVP scope) |
| Financial impact per decision | Up to USD 1,000 autonomous; higher amounts human-approved |
| Regulatory exposure | Claims handling conduct rules; data protection (e.g. DPDP Act); AI governance expectations (e.g. NAIC model bulletin where applicable) |

**Tier: HIGH.** Named approvers required at G2 (Architecture + Security), G4 (RAI/Risk)
and G5 (CAB + Head of Claims).

## Key mitigations
- Deterministic routing; agent autonomy limited to row 1 of the routing matrix.
- All full or partial rejections require human approval; type B deductions need L1 + L2.
- Payment only with a signed approval token.
- Shadow mode before any customer impact; kill switch.

## Data classification
| Data | Class | Handling |
|---|---|---|
| Claimant identity | Confidential - PII | Redact in logs and non-essential prompts |
| Bank details | Restricted | Never sent to models; used only by Settlement |
| Travel documents | Confidential | Stored in document store; extracted fields only in prompts |
| Claim outcome | Confidential | Audit retained [N] years |
