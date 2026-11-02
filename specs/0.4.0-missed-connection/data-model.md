# Data Model: Missed Connection Benefit (0.4.0)

Changes to the claim data used by the agents and the mock systems. No new data category (R10).

## Claim (claims system, `claims` table)

| Field | Change | Notes |
|---|---|---|
| `claim_type_declared`, `claim_type` | new value `missed_connection` | category `delay` |
| `journey_direction` | new: `outward` \| `return` | with `trip_id` identifies the journey (R5) |
| `itinerary_json` | new: list of legs (below) | extracted, not customer-typed |
| `refund_declared_usd` | new, default null | refund or credit for the unused onward ticket; null = not declared → requested |
| `lines_json` | each line gains `benefit` (`delay` \| `missed_connection`) and `receipt_fingerprint` | R1, R6 |

## Itinerary leg (in `itinerary_json` and in document facts)

| Field | Type | Rule |
|---|---|---|
| `flight_no` | string | normalised upper case, no spaces |
| `flight_date` | date | |
| `origin`, `destination` | IATA code | |
| `scheduled_dep`, `scheduled_arr` | datetime (local, ISO 8601) | from booking / boarding pass |
| `booking_ref` | string | redacted in logs |
| `role` | `inbound` \| `onward` \| `other` | set by code (R2) |

## Connection (derived, in verification output and decision package)

| Field | Type | Rule |
|---|---|---|
| `airport` | IATA code | inbound destination = onward origin |
| `booked_connection_min` | int | onward `scheduled_dep` − inbound `scheduled_arr` |
| `mct_min`, `mct_source`, `mct_as_of` | int, string, date | from the reference; fallback 60, source `fallback` |
| `connection_time_ok` | bool | `booked_connection_min >= mct_min` (equal passes) |
| `single_ticket` | bool | one booking reference across inbound and onward legs |
| `inbound_arrival_delay_min`, `inbound_cause` | int, string | from flight status |
| `inbound_delay_verified` | bool | source found, delay > 0, cause present, consistent with documents |

## Journey benefits (read from claims system, R5)

| Field | Type |
|---|---|
| `paid_delay_usd`, `paid_missed_connection_usd` | float, approved amounts on other claims of the same journey |
| `paid_fingerprints` | list of receipt fingerprints of paid lines |

## Flight (mock flight-status source, `flights` table)

Adds `scheduled_arr`, `actual_arr`; `arrival_delay_minutes` is computed in the tool output (R3).

## Minimum connection time reference (`reference/minimum-connection-times.yaml`)

```yaml
as_of: 2026-11-01
source: "<publication>"
owner: product-owner
fallback_minutes: 60
airports:
  FRA: 45
  LHR: 60
```

## Decision package additions

- per line: `benefit`, clause (`3.7`, `3.4`, `3.5`, `3.6`, `6.1`, `6.2`, `7.2`), type A/B
- `connection` object (above), `journey_benefits` used, shared limit remaining

## State and validation rules

- Missed connection payable only when `inbound_delay_verified` and `connection_time_ok` and
  `single_ticket` (REQ-B-012); otherwise rejection (row 5) or investigation (row 6), per research R8.
- Shared limit remaining = plan `delay_limit` − (`paid_delay_usd` + `paid_missed_connection_usd`),
  floored at 0.
