"""Offline evaluation and confidence-threshold calibration for claims AI."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

VALID_FLOWS = frozenset({"intake", "review"})


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    flow: str
    confidence: float
    correct: bool
    model: str
    document_type: str
    task: str


def _scope(value: str) -> str:
    return " ".join(value.split()).casefold()


def load_evaluation_cases(path: Path) -> tuple[list[EvaluationCase], str]:
    raw = path.read_bytes()
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc
        case_id = str(item.get("id") or "").strip()
        flow = _scope(str(item.get("flow") or ""))
        if not case_id or case_id in seen:
            raise ValueError(f"Line {line_number} has a missing or duplicate id")
        if flow not in VALID_FLOWS:
            raise ValueError(f"Line {line_number} has unsupported flow {flow!r}")
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError(f"Line {line_number} confidence must be numeric")
        if not 0 <= float(confidence) <= 1:
            raise ValueError(f"Line {line_number} confidence must be between 0 and 1")
        if not isinstance(item.get("correct"), bool):
            raise ValueError(f"Line {line_number} correct must be boolean")
        seen.add(case_id)
        cases.append(
            EvaluationCase(
                case_id=case_id,
                flow=flow,
                confidence=float(confidence),
                correct=item["correct"],
                model=_scope(str(item.get("model") or "")),
                document_type=_scope(str(item.get("document_type") or "")),
                task=_scope(str(item.get("task") or "unspecified")),
            )
        )
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    return cases, hashlib.sha256(raw).hexdigest()


def _calibrate(
    cases: list[EvaluationCase], max_false_accept_rate: float
) -> float | None:
    """Lowest threshold whose auto-accepted cases meet the error-rate policy."""
    candidates = sorted({case.confidence for case in cases})
    for threshold in candidates:
        accepted = [case for case in cases if case.confidence >= threshold]
        if not accepted:
            continue
        false_accepts = sum(not case.correct for case in accepted)
        if false_accepts / len(accepted) <= max_false_accept_rate:
            return round(threshold, 4)
    return None


def _metrics(cases: list[EvaluationCase], threshold: float) -> dict[str, Any]:
    accepted = [case for case in cases if case.confidence >= threshold]
    correct = sum(case.correct for case in cases)
    false_accepts = sum(not case.correct for case in accepted)
    return {
        "samples": len(cases),
        "accuracy": round(correct / len(cases), 4),
        "threshold": threshold,
        "auto_accept_coverage": round(len(accepted) / len(cases), 4),
        "false_accept_rate": round(false_accepts / len(accepted), 4) if accepted else 0.0,
    }


def build_confidence_profile(
    cases: list[EvaluationCase],
    *,
    dataset_sha256: str,
    max_false_accept_rate: float = 0.05,
    min_scope_samples: int = 5,
) -> dict[str, Any]:
    if not 0 <= max_false_accept_rate < 1:
        raise ValueError("max_false_accept_rate must be between 0 and 1")
    if min_scope_samples < 2:
        raise ValueError("min_scope_samples must be at least 2")

    flows: dict[str, Any] = {}
    for flow in sorted(VALID_FLOWS):
        flow_cases = [case for case in cases if case.flow == flow]
        if not flow_cases:
            raise ValueError(f"Dataset has no {flow} cases")
        default = _calibrate(flow_cases, max_false_accept_rate)
        if default is None:
            raise ValueError(
                f"No {flow} confidence threshold satisfies the false-accept policy"
            )
        profile: dict[str, Any] = {
            "default": default,
            "by_model": {},
            "by_document_type": {},
            "by_model_document_type": {},
            "metrics": _metrics(flow_cases, default),
        }
        groups: dict[str, dict[str, list[EvaluationCase]]] = {
            "by_model": defaultdict(list),
            "by_document_type": defaultdict(list),
            "by_model_document_type": defaultdict(list),
        }
        for case in flow_cases:
            if case.model:
                groups["by_model"][case.model].append(case)
            if case.document_type:
                groups["by_document_type"][case.document_type].append(case)
            if case.model and case.document_type:
                groups["by_model_document_type"][
                    f"{case.model}|{case.document_type}"
                ].append(case)
        for group_name, scopes in groups.items():
            for key, scoped_cases in sorted(scopes.items()):
                if len(scoped_cases) >= min_scope_samples:
                    threshold = _calibrate(
                        scoped_cases, max_false_accept_rate
                    )
                    if threshold is not None:
                        profile[group_name][key] = threshold
        flows[flow] = profile

    return {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("Asia/Singapore")).isoformat(),
        "dataset_sha256": dataset_sha256,
        "policy": {
            "max_false_accept_rate": max_false_accept_rate,
            "min_scope_samples": min_scope_samples,
        },
        "flows": flows,
    }


def write_profile(profile: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "EvaluationCase",
    "build_confidence_profile",
    "load_evaluation_cases",
    "write_profile",
]
