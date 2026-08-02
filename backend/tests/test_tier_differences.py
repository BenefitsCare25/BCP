"""What changes between two benefit schedules — the enrollment page's other half.

A member choosing a plan was shown a direction word and a price; these cover the
diff that says what the price actually buys or gives up.
"""
from __future__ import annotations

from app.services.tier_differences import schedule_differences


def _sched(*rows: tuple[str, str | None]) -> dict:
    return {"items": [{"name": n, "value": v, "kind": "amount"} for n, v in rows]}


def test_only_differing_rows_are_reported() -> None:
    cur = _sched(("Daily Room & Board", "1 Bed Private"), ("Inpatient", "As charged"))
    new = _sched(("Daily Room & Board", "4 Bed Restr."), ("Inpatient", "As charged"))
    diffs = schedule_differences(cur, new)
    assert [d["benefit"] for d in diffs] == ["Daily Room & Board"]
    assert diffs[0] == {
        "group": None,
        "benefit": "Daily Room & Board",
        "qualifier": None,
        "current": "1 Bed Private",
        "elected": "4 Bed Restr.",
        "kind": "amount",
    }


def test_identical_schedules_report_nothing() -> None:
    """The life products: every plan shares one schedule and differs only in sum
    insured, which the tier already carries. Rendering a "what changes" block
    with nothing in it would be chrome describing nothing."""
    s = _sched(("Pays sum insured", "YES"), ("Waiting period", "90 days"))
    assert schedule_differences(s, s) == []


def test_a_benefit_the_elected_plan_drops_is_still_reported() -> None:
    """The most consequential difference there is, and the one a diff that only
    walks the elected plan's own rows would silently omit."""
    cur = _sched(("Overseas GP", "As charged"), ("Panel GP", "As charged"))
    new = _sched(("Panel GP", "As charged"))
    diffs = schedule_differences(cur, new)
    assert diffs == [
        {"group": None, "benefit": "Overseas GP", "qualifier": None,
         "current": "As charged", "elected": None, "kind": "amount"}
    ]


def test_sub_items_are_compared_and_qualified_by_parent() -> None:
    """GCSP's shape: both plans agree on every top-level benefit and differ only
    underneath them, so a top-level-only diff calls two different plans
    identical. "Per visit" also repeats under several parents on one GCGP
    schedule, so the label has to carry its parent."""
    def sched(panel: str, non_panel: str | None) -> dict:
        return {"items": [{
            "name": "Specialist Care", "value": None, "kind": "text",
            "sub_items": [
                {"name": "Panel Specialists", "value": panel, "kind": "amount"},
                {"name": "Non Panel Specialists", "value": non_panel, "kind": "amount"},
            ],
        }]}

    diffs = schedule_differences(sched("3000", None), sched("As charged", "1500"))
    by_name = {d["benefit"]: d for d in diffs}
    assert set(by_name) == {"Panel Specialists", "Non Panel Specialists"}
    # The parent rides as its own field rather than being joined into the name.
    assert all(d["group"] == "Specialist Care" for d in diffs)
    assert by_name["Non Panel Specialists"]["current"] is None
    assert by_name["Non Panel Specialists"]["elected"] == "1500"


def test_blank_on_both_sides_is_not_a_difference() -> None:
    cur = _sched(("Surgical Implants", None), ("Panel GP", "As charged"))
    new = _sched(("Surgical Implants", ""), ("Panel GP", "As charged"))
    assert schedule_differences(cur, new) == []


def test_malformed_schedules_render_nothing_rather_than_raising() -> None:
    """`Plan.benefit_schedule` is untyped JSON — seeded and hand-PATCHed rows
    are a bare list, and a member's page must never break on one."""
    assert schedule_differences(None, None) == []
    assert schedule_differences("nonsense", {"items": "nonsense"}) == []
    # A bare list (the legacy shape) still diffs.
    bare_cur = [{"name": "Panel GP", "value": "30"}]
    bare_new = [{"name": "Panel GP", "value": "50"}]
    assert schedule_differences(bare_cur, bare_new)[0]["elected"] == "50"


def test_cross_reference_annotations_are_not_a_change_in_cover() -> None:
    """CDL's GCSP prints "(not part of 1a)" under one plan and "(not part of
    1b)" under the other for the same benefit at the same limit — the text
    differs only because it cross-references a different row number. Reported
    as a change it reads as gibberish beside the rows that genuinely alter
    cover."""
    cur = _sched(("Other Diagnostic Test", "1500"), ("Referral note", "(not part of 1b)"))
    new = _sched(("Other Diagnostic Test", "1500"), ("Referral note", "(not part of 1a)"))
    assert schedule_differences(cur, new) == []


def test_an_annotation_replacing_a_real_value_is_still_a_change() -> None:
    """The filter is narrow on purpose: only when BOTH sides are wholly
    parenthetical. A real limit becoming a footnote is a change."""
    cur = _sched(("Outpatient Physio", "500"))
    new = _sched(("Outpatient Physio", "(not part of 1a)"))
    diffs = schedule_differences(cur, new)
    assert len(diffs) == 1
    assert diffs[0]["current"] == "500"


def test_bracketed_wording_is_split_off_the_headline() -> None:
    """The insurer writes its qualifiers inside the benefit name, and they run
    to more characters than the name itself. Inline they turned each row into a
    four-line paragraph and the member could not see where one changed benefit
    ended and the next began. Nothing is dropped — it is only placed."""
    long = (
        "Panel Specialists (on cashless basis) (including Specialist Outpatient "
        "Clinics in Govt Restructured hospitals - on reimbursement basis)"
    )
    cur = {"items": [{"name": "Specialist Care", "value": None, "kind": "text",
                      "sub_items": [{"name": long, "value": "3000"}]}]}
    new = {"items": [{"name": "Specialist Care", "value": None, "kind": "text",
                      "sub_items": [{"name": long, "value": "As charged"}]}]}
    d = schedule_differences(cur, new)[0]
    assert d["group"] == "Specialist Care"
    assert d["benefit"] == "Panel Specialists"
    assert d["qualifier"] == (
        "on cashless basis · including Specialist Outpatient Clinics in Govt "
        "Restructured hospitals - on reimbursement basis"
    )


def test_a_parents_own_qualifier_is_kept_too() -> None:
    """"Other Diagnostic Test / Scan (CT Scan, MRI Scan…)" — the bracket says
    what the benefit actually covers, so it must survive the split."""
    def sched(v: str) -> dict:
        return {"items": [{
            "name": "Other Diagnostic Test / Scan (CT Scan, MRI Scan, PET Scan)",
            "value": None,
            "sub_items": [{"name": "With referral letter", "value": v}],
        }]}
    d = schedule_differences(sched("500"), sched("1500"))[0]
    assert d["group"] == "Other Diagnostic Test / Scan"
    assert d["benefit"] == "With referral letter"
    assert d["qualifier"] == "CT Scan, MRI Scan, PET Scan"


def test_a_name_that_is_only_brackets_keeps_its_text() -> None:
    cur = _sched(("(a)", "30"))
    new = _sched(("(a)", "50"))
    assert schedule_differences(cur, new)[0]["benefit"] == "(a)"
