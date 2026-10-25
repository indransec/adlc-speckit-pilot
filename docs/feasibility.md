# Feasibility assessment (model, tool and data fit)

## 1. Agent vs. rules split
| Step | Best fit | Rationale |
|---|---|---|
| Classify claim, extract and cross-check documents | AI | Unstructured, varied documents |
| Interpret coverage against policy wording | AI | Natural-language clauses and exclusions |
| Label deductions type A / B and link evidence | AI + rule checks | Judgement that must be explainable |
| Excess, sub-limits, caps, currency, offsets | Rules | Exact arithmetic |
| Routing matrix, USD 1,000 aggregate, 25% net | Rules | Control; must be deterministic (ADR-001) |
| Fraud scoring | Existing SIU model + AI anomaly flags | Reuse existing capability |
| Payment release | Rules + approval token | No AI discretion |

**Conclusion:** hybrid design. Model risk is confined to document and judgement steps.

## 2. Model fit
| Need | Test | Result |
|---|---|---|
| Read scanned / photographed / handwritten documents | 50 anonymised documents | [accuracy %] |
| Reason over policy wording | 20 historical claims with known outcome | [agreement %] |
| Document languages | Top [5] languages from history | [result] |
| Data residency, no training on our data | Vendor / hosting confirmation | [confirmed?] |
| Cost per claim | Tokens x volume vs adjuster cost | [USD x] |
| Latency | Fits p95 <= 10 min | [yes/no] |

## 3. Tool and system fit
| System | Question | Status | Owner | Due |
|---|---|---|---|---|
| Policy admin | Read API incl. benefits and limits? | [Yes/No/Workaround] | | |
| Claims system | Create/update claim, route to work queue? | | | |
| Flight status data | Licensed historical delay source? | | | |
| Document store | Access by claim ID? | | | |
| Payments | Instruction API with approval check? | | | |
| SIU fraud model | Per-claim scoring call? | | | |
| Adjuster workbench | Show decision package, record L1/L2? | | | |

## 4. Data fit
- Historical decided claims available for golden set: [count] (target 300-500).
- Deduction reasons recorded in history? [yes/partly/no] -> labelling effort [days].
- Baseline KPIs measurable from existing data? [yes/no].
- Approval to use anonymised history for testing: [owner, status].

## 5. Spike results (20-30 historical claims, no build)
| Measure | Result | Notes |
|---|---|---|
| Extraction accuracy (key fields) | | |
| Coverage agreement with adjuster | | |
| Main failure types | | |

## 6. Verdict
**Go with conditions**
| Condition | Owner | Due |
|---|---|---|
| Policy API or agreed workaround | [Policy platform lead] | [6 Nov] |
| Flight data licence confirmed | [Procurement] | [6 Nov] |
| Anonymised claims extract approved | [Data owner] | [9 Nov] |
