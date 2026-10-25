# Telemetry conventions (OpenTelemetry GenAI semantic conventions)

Every span carries:
| Attribute | Example |
|---|---|
| `adlc.system.id` | SYS-TRV-CLM |
| `adlc.system.release` | 0.2.0 |
| `adlc.agent.id` / `adlc.agent.version` | AGT-TRV-ASSESS / 0.2.0 |
| `claim.id` (hashed) | c_9f2... |
| `routing.row` | 2 |
| `adlc.run_no`, `adlc.task` | 1, map_coverage |
| `llm.mode` | live / mock |
| `llm.*` (OpenInference) | model name, token counts, input/output messages on LLM spans |
| `tool.*` (gateway spans) | tool id, operation, access, caller, invoked_by (model/code), status |

Tokens and latency are never measured by agent code: they come from these spans. Phoenix
prices them with the deploy/models.yaml prices (registered at startup); the local sink in
`shared/telemetry.py` records the same spans into `llm_calls` / `tool_calls`.

No PII in span attributes.
