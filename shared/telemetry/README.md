# Telemetry conventions (OpenTelemetry GenAI semantic conventions)

Every span carries:
| Attribute | Example |
|---|---|
| `adlc.system.id` | SYS-TRV-CLM |
| `adlc.system.release` | 0.1.0 |
| `adlc.agent.id` / `adlc.agent.version` | AGT-TRV-ASSESS / 0.1.0 |
| `claim.id` (hashed) | c_9f2... |
| `routing.row` | 2 |
| `gen_ai.*` | model, tokens, latency per OTel GenAI conventions |

No PII in span attributes.
