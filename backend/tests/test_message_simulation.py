import pytest
from pydantic import ValidationError

from app.schemas.message_simulation import MessageSimulationIn
from app.services.message_simulation import simulate_messages


def test_simulation_uses_real_privacy_serializers_and_only_synthetic_identity():
    result = simulate_messages(
        MessageSimulationIn.model_validate(
            {
                "category": "flex",
                "messages": [
                    {"author": "member", "body": "Help with my test claim"},
                    {"author": "broker", "body": "Here is a test response"},
                ],
            }
        )
    )
    assert result.simulation is True
    assert result.recipient == "test.member@example.invalid"
    assert result.subject.claim_category == "flex"
    assert result.subject.reference_no == "TEST-0001"
    assert result.member_view[1].author_name == "Claims team"
    assert result.broker_view[1].author_name == "Test adviser"
    assert result.member_view[0].mine is True
    assert result.broker_view[0].mine is False


@pytest.mark.parametrize(
    "body",
    [
        {"employee_id": "real-member"},
        {"recipient": "real@example.com"},
        {"messages": [{"author": "member", "body": "   "}]},
        {"messages": [{"author": "member", "body": "x" * 2001}]},
        {"messages": [{"author": "member", "body": "test"}] * 21},
    ],
)
def test_simulation_rejects_real_recipient_fields_and_unbounded_content(body):
    with pytest.raises(ValidationError):
        MessageSimulationIn.model_validate(body)
