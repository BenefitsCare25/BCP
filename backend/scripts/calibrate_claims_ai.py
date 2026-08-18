"""Generate the runtime claims-AI confidence profile from a gold JSONL set."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.claims_ai_evaluation import (  # noqa: E402
    build_confidence_profile,
    load_evaluation_cases,
    write_profile,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=BACKEND_ROOT / "evals" / "claims_ai" / "gold.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "config" / "claims_ai_thresholds.json",
    )
    parser.add_argument("--max-false-accept-rate", type=float, default=0.05)
    parser.add_argument("--min-scope-samples", type=int, default=5)
    args = parser.parse_args()

    cases, dataset_hash = load_evaluation_cases(args.dataset)
    profile = build_confidence_profile(
        cases,
        dataset_sha256=dataset_hash,
        max_false_accept_rate=args.max_false_accept_rate,
        min_scope_samples=args.min_scope_samples,
    )
    write_profile(profile, args.output)
    print(json.dumps({"output": str(args.output), "flows": profile["flows"]}, indent=2))


if __name__ == "__main__":
    main()
