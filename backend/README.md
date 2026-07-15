# Inspro Backend — Spike

Pre-Phase-0 localhost spike for placement-slip extraction. Validates the
parser + rule generator port from `inspro_platform.html` before any Azure
or Entra setup work.

## Run

```sh
cd backend
uv sync
uv run uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

## Test

```sh
uv run pytest -v
```

All 27 tests should pass. The placement-slip fixtures under
`tests/fixtures/placement_slips/` are the real STM and VDL workbooks from
`C:/Users/huien/inspro/reference/`. Per the plan §Decisions, these will be
PII-scrubbed before any repo push (placement slips themselves carry no
employee PII, but the file paths still reveal client names).

## Layout

```
app/
  api/placement_slips.py      POST /api/v1/placement-slips/parse
  schemas/rule.py             Shared JSONLogic RuleEnvelope (Pydantic)
  services/
    excel_reader.py           .xls (xlrd) and .xlsx (openpyxl) behind one protocol
    placement_slip_parser.py  §8.1 — find Basis of Cover, walk rows, stop conditions
    rule_generator.py         §8.2 + §8.3 — description → JSONLogic + AND/OR detection
    rule_evaluator.py         JSONLogic evaluator (consumer for Phase 5 matcher)
tests/
  test_placement_slip_parser.py   parses real STM + VDL workbooks
  test_rule_generator.py          19 round-trip tests for the regex patterns
  test_upload_endpoint.py         FastAPI TestClient smoke
```

## What's deliberately out of scope here

- Auth (Phase 2)
- Postgres (Phase 1 — currently no persistence; parser is stateless)
- Background jobs (Phase 4 — parse runs synchronously in-process)
- AI fallback (Phase 4 will land a stub; Phase 8 hardens)
- Multi-tenancy, Blob upload, audit log (Phase 1–4)
