# Golden dataset

Target: 300-500 anonymised historical delay and baggage claims decided by adjusters.
Each case: documents, expected extraction, expected deductions (A/B with clause),
expected amount, expected route, and the REQ IDs it verifies.

`routing_cases.jsonl` holds synthetic seed cases (one per routing row) used to
exercise the routing eval end to end until the historical set is ready.
