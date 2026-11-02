# Contracts: tool changes (0.4.0)

Changes to tool contracts in `tools/*/tool.yaml`; Security reviews at G2
(`docs/design/tool-permission-matrix.yaml`, CODEOWNERS `/tools/`).

## TOOL-FLIGHT-STATUS `lookup` (changed output, R3)

Access `read`, side effects none, agent: verification (unchanged).

```yaml
input:  {flight_no: string, flight_date: date}
output:
  flight: object | null
    scheduled_dep: datetime
    actual_dep: datetime
    delay_minutes: int            # departure delay (existing)
    scheduled_arr: datetime       # new
    actual_arr: datetime          # new
    arrival_delay_minutes: int    # new, actual_arr - scheduled_arr
    cause: string
    cause_announced_at: datetime | null
```

## TOOL-CLAIMS-SYSTEM `journey_benefits` (new operation, R5)

Access `read`, side effects none, agent: orchestrator (already holds read on this tool, D-07).

```yaml
input:  {trip_id: string, direction: outward|return, exclude_claim_id: string}
output:
  paid_delay_usd: float
  paid_missed_connection_usd: float
  paid_fingerprints: [string]     # receipt fingerprints of approved lines
```

Not attached to any model (called by orchestrator code only).

## Claim submission (mock claims intake)

- `claim_type_declared` accepts `missed_connection`
- new optional `journey_direction` (`outward` | `return`) and `refund_declared_usd`
- documents: booking or boarding pass for each leg; receipts as today

## Decision package (workbench, ADR-004)

Adds the `connection` object, line `benefit`, and journey shared-limit figures (data-model.md).
