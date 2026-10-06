from datetime import datetime

from pydantic import BaseModel


class EnrollmentEventOut(BaseModel):
    id: str
    enrollment_id: str
    kind: str
    title: str
    message: str
    reason: str | None
    created_at: datetime
    read_at: datetime | None
    email_status: str
    email_detail: str | None
    window_name: str
    closes_at: datetime


class EnrollmentNoticesOut(BaseModel):
    items: list[EnrollmentEventOut]
    unread: int
