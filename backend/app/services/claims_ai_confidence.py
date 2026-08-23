"""Validated runtime access to the versioned claims-AI confidence profile."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

Flow = Literal["intake", "review"]
DEFAULT_THRESHOLDS: dict[Flow, float] = {"intake": 0.6, "review": 0.5}
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "claims_ai_thresholds.json"


def _scope(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _threshold(value: Any, where: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{where} must be numeric")
    threshold = float(value)
    if not 0 <= threshold <= 1:
        raise ValueError(f"{where} must be between 0 and 1")
    return threshold


def confidence_profile_path() -> Path:
    configured = os.environ.get("INSPRO_CLAIMS_AI_THRESHOLDS_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PROFILE_PATH


@lru_cache(maxsize=1)
def load_confidence_profile() -> dict[str, Any]:
    path = confidence_profile_path()
    try:
        raw_profile: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Claims AI confidence profile is unreadable: {path}") from exc
    if not isinstance(raw_profile, dict):
        raise RuntimeError("Claims AI confidence profile must be an object")
    profile: dict[str, Any] = raw_profile
    if profile.get("schema_version") != 1:
        raise RuntimeError("Claims AI confidence profile schema_version must be 1")
    dataset_hash = profile.get("dataset_sha256")
    if not isinstance(dataset_hash, str) or len(dataset_hash) != 64:
        raise RuntimeError("Claims AI confidence profile has no valid dataset hash")
    flows = profile.get("flows")
    if not isinstance(flows, dict):
        raise RuntimeError("Claims AI confidence profile has no flows object")
    for flow in DEFAULT_THRESHOLDS:
        item = flows.get(flow)
        if not isinstance(item, dict):
            raise RuntimeError(f"Claims AI confidence profile has no {flow} flow")
        _threshold(item.get("default"), f"flows.{flow}.default")
        for group in ("by_model", "by_document_type", "by_model_document_type"):
            values = item.get(group, {})
            if not isinstance(values, dict):
                raise RuntimeError(f"flows.{flow}.{group} must be an object")
            for key, value in values.items():
                _threshold(value, f"flows.{flow}.{group}.{key}")
    return profile


def confidence_threshold(
    flow: Flow,
    *,
    model: str | None = None,
    document_type: str | None = None,
) -> float:
    item = load_confidence_profile()["flows"][flow]
    model_key = _scope(model)
    document_key = _scope(document_type)
    combined = f"{model_key}|{document_key}"
    if model_key and document_key and combined in item["by_model_document_type"]:
        return float(item["by_model_document_type"][combined])
    if document_key and document_key in item["by_document_type"]:
        return float(item["by_document_type"][document_key])
    if model_key and model_key in item["by_model"]:
        return float(item["by_model"][model_key])
    return float(item["default"])


def reset_confidence_profile_for_tests() -> None:
    load_confidence_profile.cache_clear()


__all__ = [
    "confidence_profile_path",
    "confidence_threshold",
    "load_confidence_profile",
    "reset_confidence_profile_for_tests",
]
