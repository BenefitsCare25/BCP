from app.services.member_counts import DraftCategory, _collapse_drafts


def test_repeated_plan_rows_share_one_employee_category() -> None:
    drafts = [
        DraftCategory("plan-1", "Managers", ["Example Pte Ltd"]),
        DraftCategory("plan-2", " managers ", ["example pte ltd"]),
    ]

    representatives, representative_by_key = _collapse_drafts(drafts)

    assert [draft.key for draft in representatives] == ["plan-1"]
    assert representative_by_key == {"plan-1": "plan-1", "plan-2": "plan-1"}


def test_same_wording_with_different_entities_stays_separate() -> None:
    drafts = [
        DraftCategory("entity-a", "All employees", ["Entity A"]),
        DraftCategory("entity-b", "All employees", ["Entity B"]),
    ]

    representatives, representative_by_key = _collapse_drafts(drafts)

    assert [draft.key for draft in representatives] == ["entity-a", "entity-b"]
    assert representative_by_key == {"entity-a": "entity-a", "entity-b": "entity-b"}
