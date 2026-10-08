"""Bounded, shared contracts for template editing and non-delivering previews."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.brand import DEFAULT_EMAIL_SENDER_NAME, DEFAULT_SUPPORT_EMAIL

Scope = Literal["firm", "company"]
Audience = Literal["employee", "hr"]
Purpose = Literal["general", "invitation", "password_reset"]


class TemplateContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=120)
    audience: Audience = "employee"
    purpose: Purpose = "general"
    subject: str = Field(default="", max_length=200)
    preheader: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    button_label: str = Field(default="", max_length=60)
    button_url: str = Field(default="", max_length=2000)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: TemplateContent
    revision: int = Field(default=0, ge=0)


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class BrandingContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Built-in values; the firm's resolved brand replaces them where branding
    # is unsaved (`services/email_template_store.branding_defaults`).
    sender_display_name: str = Field(default=DEFAULT_EMAIL_SENDER_NAME, max_length=120)
    support_email: str = Field(default=DEFAULT_SUPPORT_EMAIL, max_length=320)
    footer: str = Field(default="", max_length=1000)
    logo_url: str = Field(default="", max_length=2000)


class BrandingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: BrandingContent
    revision: int = Field(default=0, ge=0)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: TemplateContent
    recipient_id: str | None = Field(default=None, max_length=36)
    policy_year_id: str | None = Field(default=None, max_length=36)


class SelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_key: str = Field(min_length=1, max_length=80)
    policy_year_id: str | None = Field(default=None, max_length=36)
    recipient_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(
        min_length=1, max_length=10000
    )


class PrepareIn(SelectionIn):
    request_key: UUID
    review_token: str = Field(min_length=1, max_length=2000)
