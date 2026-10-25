# Architecture

## Topology
```
Claim submitted
   |
Orchestrator (deterministic flow + routing rules)
   |-- Intake agent          classify, register, acknowledge
   |-- Document agent        extract, cross-check, chase missing docs
   |-- Coverage agent        policy validity, benefits, exclusions
   |-- Verification agent    flight status and fact checks
   |-- Fraud screening       SIU model score + anomaly flags (flag only)
   |-- Assessment agent      amount (via rules engine), deduction labels A/B, decision package
   |
Routing rules (code) --> row 1 straight-through
                     --> L1 queue / L1 + L2 queue / investigation / SIU
   |
Settlement agent  (only with approval token)
Communication agent (approved templates)
```

## Principles
1. Routing, authority limits and calculations are code, not prompts (ADR-001).
2. Agents recommend; the orchestrator routes; humans or rules authorise payments.
3. Each agent has only the tools in `tool-permission-matrix.yaml`.
4. Every step writes to the audit trail with release ID and agent version.

## Routing matrix
See `routing-matrix.md` (source of truth for `agents/orchestrator/src/routing.py`).

## Implementation (release 0.1.0, decision D-08)
| Component | Technology | Code |
|---|---|---|
| Orchestrator flow + HITL | LangGraph `StateGraph`, `interrupt()`, SQLite checkpointer | `agents/orchestrator/src/claim_graph.py` |
| Routing rules | Python (deterministic) | `agents/orchestrator/src/routing.py` |
| Confidence (ADR-002) | Python, combined signals | `agents/orchestrator/src/confidence.py` |
| Sub-agents | Manifest-loaded modules, Pydantic structured outputs | `agents/*/src/agent.py`, `shared/registry.py` |
| Model gateway (ADR-005) | OpenRouter (Claude Haiku 5.5 / Sonnet 5.5), mock fallback | `shared/llm.py`, `deploy/models.yaml` |
| Clause retrieval | FAISS + fastembed | `shared/rag.py` |
| Tool contracts + permissions | Mock systems behind a permission gateway | `tools/*/impl.py`, `shared/tool_gateway.py` |
| Approval token (ADR-003) | Signed JWT, single use | `shared/hitl/approval_token.py` |
| Workbench (ADR-004 stand-in) | Streamlit + FastAPI | `frontend/`, `backend/` |
| Telemetry | OpenTelemetry/OpenInference -> Arize Phoenix | `shared/telemetry.py` |
