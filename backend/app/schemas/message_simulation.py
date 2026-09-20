"""Synthetic-only message rehearsal; no real recipient identifiers accepted."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.claims import ClaimMessageOut, ConversationSubjectOut


class SimulatedMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    author: Literal["member", "broker"]
    body: str = Field(min_length=1, max_length=2000)


class MessageSimulationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["inpatient", "outpatient", "flex"] = "outpatient"
    messages: list[SimulatedMessageIn] = Field(default_factory=list, max_length=20)


class MessageSimulationOut(BaseModel):
    simulation: Literal[True] = True
    recipient: Literal["test.member@example.invalid"] = "test.member@example.invalid"
    subject: ConversationSubjectOut
    member_view: list[ClaimMessageOut]
    broker_view: list[ClaimMessageOut]
