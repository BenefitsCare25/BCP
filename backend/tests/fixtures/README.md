# Test fixtures

This directory is intentionally empty in source control. **Real placement
slips and rosters contain employee PII and must never be committed.** The
top-level `.gitignore` excludes every `.xls`/`.xlsx` in this tree.

## Generating synthetic data

```sh
cd backend
uv run python tests/fixtures/generate_synthetic_roster.py 100
```

Outputs to `tests/fixtures/rosters/`.

## Working with real client files locally

Place files under `tests/fixtures/placement_slips/` for ad-hoc parser checks.
They stay on your disk; the `.gitignore` keeps them out of any commit.

The four integration tests that currently reference real STM/VDL files
(`test_placement_slip_parser.py`, `test_activation_endpoint.py`,
`test_upload_endpoint.py`, `test_match_results_endpoint.py`) will skip
when the files are missing — fail loudly if you depend on them in CI.
