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

## Implementation (release 0.2.0, decisions D-08, D-12, D-13)
| Component | Technology | Code |
|---|---|---|
| Orchestrator flow + HITL | LangGraph `StateGraph`, `interrupt()`, SQLite checkpointer | `agents/orchestrator/src/claim_graph.py` |
| Routing rules | Python (deterministic) | `agents/orchestrator/src/routing.py` |
| Confidence (ADR-002) | Python, combined signals | `agents/orchestrator/src/confidence.py` |
| Sub-agents | Manifest-loaded modules; each LLM task is a LangGraph `create_react_agent` (tools from `attach:`, Pydantic `response_format`) | `agents/*/src/agent.py`, `shared/registry.py`, `shared/agent_runtime.py` |
| Model gateway (ADR-005) | OpenRouter (Claude Haiku 5.5 / Sonnet 5.5) via `ChatOpenAI`; scripted mock model offline | `shared/llm.py`, `deploy/models.yaml` |
| Clause retrieval | FAISS + fastembed, exposed to models as TOOL-POLICY-WORDING | `shared/rag.py`, `tools/policy_wording/` |
| Tool contracts + permissions | Mock systems behind a permission gateway | `tools/*/impl.py`, `shared/tool_gateway.py` |
| Approval token (ADR-003) | Signed JWT, single use | `shared/hitl/approval_token.py` |
| Workbench (ADR-004 stand-in) | Streamlit + FastAPI | `frontend/`, `backend/` |
| Telemetry | OpenTelemetry/OpenInference -> Arize Phoenix (cost tracking with catalog prices); local span sink -> `llm_calls`, `tool_calls` | `shared/telemetry.py` |
