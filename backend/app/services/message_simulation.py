"""Render synthetic messages using production serializers without persisting."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models import Claim, ClaimMessage
from app.schemas.message_simulation import MessageSimulationIn, MessageSimulationOut
from app.services.claim_messages import broker_message_out, claim_subject, member_message_out


def simulate_messages(body: MessageSimulationIn) -> MessageSimulationOut:
    claim = Claim(
        id="simulation-claim",
        policy_year_id="simulation-year",
        reference_no="TEST-0001",
        claim_kind="flex" if body.category == "flex" else "insured",
        claim_type="Synthetic test claim",
        product_code="GHS" if body.category == "inpatient" else "GP",
        flex_category_name="Test benefit" if body.category == "flex" else None,
        incurred_date=date(2026, 1, 1),
        amount_claimed=Decimal("50"),
        currency="SGD",
        status="submitted",
    )
    now = datetime.now(UTC)
    messages = [
        ClaimMessage(
            id=f"simulation-message-{index}",
            claim_id=claim.id,
            author_type=entry.author,
            author_name="Test member" if entry.author == "member" else "Test adviser",
            subject="Test message",
            body=entry.body.strip(),
            created_at=now + timedelta(seconds=index),
        )
        for index, entry in enumerate(body.messages)
    ]
    return MessageSimulationOut(
        subject=claim_subject(claim),
        member_view=[member_message_out(message) for message in messages],
        broker_view=[broker_message_out(message) for message in messages],
    )
