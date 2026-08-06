"""Shared JSONLogic-style Rule schema.

Defined here in the spike so the parser, future RuleBuilder UI, and matching
engine all consume the same shape. The Rule type is recursive: a node is either
a logical combinator ({and|or|not: ...}) or a comparison ({op: [attr, value]}).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# We model Rule as a permissive dict for now — the spike's job is to validate
# that round-tripping works. Tightening to a discriminated union happens in
# Phase 1 once we have RuleBuilder round-trip tests.
RuleNode = dict[str, Any]


class RuleEnvelope(BaseModel):
    """A rule with its human-readable rendering and the generator's confidence."""

    model_config = ConfigDict(frozen=True)

    rule: RuleNode | None = Field(default=None, description="JSONLogic predicate tree")
    human_readable: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool
