"""Import a company's own Employee Listing: preview, then apply.

Preview reads the workbook, suggests block → product and listing wording →
slip category decisions (reusing the company's reviewed mapping), diffs the
members against the roster with the same movement engine as the listing
template, and reports cover inconsistencies. Apply writes the members, records
each person's listed cover per product and re-matches, in one transaction.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import tenant_or_global
from app.models import Category, Dependant, Employee, EmployeeAttributeSchema, PolicyYear
from app.models.employee_listing import ElLayoutProfile, ListingAssignment
from app.schemas.employee_listing import (
    JoinerProductOut,
    JoinerRuleOut,
    ListingApplyOut,
    ListingBlockOut,
    ListingCategoryOption,
    ListingColumnOut,
    ListingIssueOut,
    ListingLabelOut,
    ListingLayoutOut,
    ListingMappingIn,
    ListingPreviewOut,
    ListingProductOut,
)
from app.services.adc import (
    StaleListingPreview,
    apply_plan,
    evaluate_records,
    missing_digest,
)
from app.services.el_import.checks import cover_checks
from app.services.el_import.joiner_rules import (
    ProductRules,
    derive_product_rules,
    persist_rules,
    rows_from_listing,
)
from app.services.el_import.mapping import (
    NOT_COVERED,
    CategoryOption,
    LabelSuggestion,
    block_label_stats,
    category_options,
    company_products,
    normalize_label,
    suggest_block_products,
    suggest_labels,
)
from app.services.el_import.records import PII_ATTRIBUTES, ListingRecords, build_records
from app.services.el_import.underwriting import apply_listing_underwriting
from app.services.el_workbook import ElCover, ElFormatError, ElLayout, ElWorkbook
from app.services.el_workbook import read_employee_listing as read_workbook
from app.services.el_workbook.values import text
from app.services.matching_engine import insured_names, normalize_entity
from app.services.roster_dedup import employee_nric

_DECISION_ROLES = {"category", "plan_type", "family_group", "admin_type"}


class UnresolvedListingMapping(ValueError):
    """A listed category wording has neither a slip category nor "not covered"."""


class NotEmployeeListing(ValueError):
    """The workbook is not laid out as a company listing (e.g. the member template)."""


def _read(path: Path | str) -> ElWorkbook:
    try:
        workbook = read_workbook(path)
    except ElFormatError as exc:
        raise NotEmployeeListing(str(exc)) from exc
    if workbook.layout.banner_row < 0:
        # No EMPLOYEE / DEPENDANTS / product banners: the member-listing
        # template (header on row 1, separate Dependants sheet) or another
        # flat roster, which the template sync reads instead.
        raise NotEmployeeListing(
            "This file has no EMPLOYEE / DEPENDANTS / product banner row, so it is "
            "not a company Employee Listing."
        )
    return workbook


def _profile(db: Session, client_id: str) -> ElLayoutProfile | None:
    return db.execute(
        select(ElLayoutProfile).where(ElLayoutProfile.client_id == client_id)
    ).scalar_one_or_none()


def _known_attribute_ids(db: Session, client_id: str) -> frozenset[str]:
    return frozenset(db.execute(
        select(EmployeeAttributeSchema.attribute_id).where(
            tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
        )
    ).scalars())


def _layout_out(layout: ElLayout) -> ListingLayoutOut:
    def cols(columns: Any) -> list[ListingColumnOut]:
        return [ListingColumnOut(letter=c.letter, header=c.header, role=c.role) for c in columns]

    return ListingLayoutOut(
        sheet=layout.sheet,
        header_row=layout.header_row + 1,
        reference_date=layout.reference_date,
        blocks=[
            ListingBlockOut(index=i, kind=b.kind, banner=b.banner, code_hint=b.code_hint,
                            columns=cols(b.columns))
            for i, b in enumerate(layout.blocks)
        ],
        trailing=cols(layout.trailing),
    )


def _option_out(option: CategoryOption) -> ListingCategoryOption:
    return ListingCategoryOption(
        product_code=option.product_code, category_id=option.category_id,
        category_label=option.category_label, plan_code=option.plan_code,
    )


def _resolve(
    db: Session,
    client_id: str,
    policy_year_id: str,
    workbook: ElWorkbook,
    mapping: ListingMappingIn | None,
) -> tuple[list[Any], dict[int, list[str]], dict[int, list[CategoryOption]],
           list[LabelSuggestion], ElLayoutProfile | None]:
    """Products, block → products and label decisions: saved, then suggested,
    then the broker's explicit choices from ``mapping``."""
    products = company_products(db, client_id, policy_year_id)
    codes = {p.code for p in products}
    profile = _profile(db, client_id)
    block_products = suggest_block_products(workbook, products)
    if profile is not None:
        for key, saved in (profile.block_products or {}).items():
            chosen = [c for c in saved if c in codes]
            if int(key) in block_products and chosen:
                block_products[int(key)] = chosen
    for key, chosen in (mapping.block_products if mapping else {}).items():
        if key.isdigit() and int(key) in block_products:
            block_products[int(key)] = [c for c in chosen if c in codes]

    options = {
        i: category_options(db, policy_year_id, products, chosen)
        for i, chosen in block_products.items()
    }
    decisions = suggest_labels(
        block_label_stats(workbook), options, profile.label_map if profile else {}
    )
    for decision in decisions:
        explicit = (mapping.labels if mapping else {}).get(str(decision.block), {}).get(
            decision.key
        )
        if explicit is None:
            continue
        if explicit.not_covered:
            decision.choice, decision.not_covered = None, True
        else:
            decision.choice = next(
                (o for o in decision.options if o.category_id == explicit.category_id), None
            )
            decision.not_covered = False
        decision.source, decision.confidence = "reviewed", 1.0
    return products, block_products, options, decisions, profile


def _joiner_rules(
    workbook: ElWorkbook,
    records: ListingRecords,
    decisions: list[LabelSuggestion],
    block_products: dict[int, list[str]],
) -> list[ProductRules]:
    rows = rows_from_listing(
        workbook,
        {r.row: r.attributes for r in records.employees},
        {(d.block, d.key): d for d in decisions},
        block_products,
    )
    return [
        rules for code, product_rows in sorted(rows.items())
        if (rules := derive_product_rules(code, product_rows)) is not None
    ]


def _joiner_out(
    rules: list[ProductRules], options: dict[int, list[CategoryOption]]
) -> list[JoinerProductOut]:
    labels = {o.category_id: o.category_label for opts in options.values() for o in opts}
    return [
        JoinerProductOut(
            product_code=product.product_code,
            attributes=list(product.attributes),
            rules=[
                JoinerRuleOut(
                    category_id=category_id,
                    category_label=labels.get(category_id, category_id),
                    rule=product.readable[category_id],
                    listed=product.members.get(category_id, 0),
                    exceptions=product.exceptions.get(category_id, 0),
                )
                for category_id in product.rules
            ],
            exceptions=sum(product.exceptions.values()),
        )
        for product in rules
    ]


def preview(
    db: Session,
    client_id: str,
    policy_year_id: str,
    path: Path | str,
    mapping: ListingMappingIn | None = None,
) -> ListingPreviewOut:
    workbook = _read(path)
    products, block_products, options, decisions, profile = _resolve(
        db, client_id, policy_year_id, workbook, mapping
    )
    records = _with_sole_entity(
        db, policy_year_id, build_records(workbook, _known_attribute_ids(db, client_id))
    )
    plan, members = evaluate_records(
        db, policy_year_id, client_id, records.employees, records.dependants
    )
    members.missing_digest = missing_digest(plan)
    return ListingPreviewOut(
        layout=_layout_out(workbook.layout),
        products=[ListingProductOut(code=p.code, display_name=p.display_name)
                  for p in sorted(products, key=lambda p: p.code)],
        block_products={str(i): codes for i, codes in block_products.items()},
        labels=[
            ListingLabelOut(
                block=d.block, key=d.key, label=d.stats.label,
                employees=d.stats.employees, dependants=d.stats.dependants,
                plans=[p for p, _ in d.stats.plans.most_common()],
                choice=_option_out(d.choice) if d.choice else None,
                not_covered=d.not_covered, confidence=d.confidence, source=d.source,
            )
            for d in decisions
        ],
        options={str(i): [_option_out(o) for o in opts] for i, opts in options.items()},
        members=members,
        issues=[ListingIssueOut(row=i.row, field=i.field, code=i.code, message=i.message)
                for i in workbook.issues],
        checks=cover_checks(workbook, decisions),
        reused_profile=profile is not None,
        joiner_rules=_joiner_out(
            _joiner_rules(workbook, records, decisions, block_products), options
        ),
    )


def _with_sole_entity(
    db: Session, policy_year_id: str, records: ListingRecords
) -> ListingRecords:
    """Give listed employees the company's insured entity when the listing has
    no entity column and every slip category insures the same one entity.

    Categories are gated by insured entity; an employee without one matches
    none of them (GAS's five directors, added from the listing, did exactly
    that). With several insured entities nothing is assumed.
    """
    if "entity" in records.attribute_ids:
        return records
    names = {
        name.strip()
        for (insured,) in db.execute(
            select(Category.plan_assignments).where(Category.policy_year_id == policy_year_id)
        )
        for name in insured_names((insured or {}).get("insured"))
        if name.strip()
    }
    if len({normalize_entity(n) for n in names}) != 1:
        return records
    entity = sorted(names)[0]
    for record in records.employees:
        record.attributes.setdefault("entity", entity)
    return ListingRecords(
        records.employees, records.dependants,
        {**records.attribute_ids, "entity": "Insured entity (from the placement slip)"},
    )


def _ensure_attributes(db: Session, client_id: str, attribute_ids: dict[str, str]) -> None:
    """Declare the company-level attributes this listing writes.

    A global attribute typed as a number but holding the listing's text (GAS
    grades "SP"/"MSO") gets a company-level text definition, so rules compare
    the listing's own values.
    """
    rows = {
        (r.client_id, r.attribute_id): r
        for r in db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id),
                EmployeeAttributeSchema.attribute_id.in_(list(attribute_ids)),
            )
        ).scalars()
    }
    for attr_id, heading in attribute_ids.items():
        if (client_id, attr_id) in rows:
            continue
        global_row = rows.get((None, attr_id))
        if global_row is not None and global_row.data_type not in {"integer", "decimal"}:
            continue
        if global_row is not None and attr_id == "salary":
            continue
        is_pii = attr_id in PII_ATTRIBUTES
        db.add(EmployeeAttributeSchema(
            client_id=client_id, attribute_id=attr_id,
            display_name=(global_row.display_name if global_row else heading)[:255],
            data_type="string", is_pii=is_pii, allow_matching=not is_pii,
            allow_ai_values=False,
            description="Imported from the company's Employee Listing.",
        ))
    db.flush()


def _recorded(cover: ElCover) -> dict[str, Any]:
    return {
        k: (v.isoformat() if isinstance(v, date) else v)
        for k, v in cover.values.items()
        if k not in _DECISION_ROLES and v not in (None, "")
    }


def _write_assignments(
    db: Session,
    client_id: str,
    policy_year_id: str,
    workbook: ElWorkbook,
    decisions: dict[tuple[int, str], LabelSuggestion],
    product_ids: dict[str, str],
    block_products: dict[int, list[str]],
) -> tuple[int, int]:
    """Replace this year's listed cover with what the uploaded listing states.

    An employee listed with no cover at all still gets a row per listed
    product, with no category: the listing says they are not covered, so
    matching must not fall back to rules for them.
    """
    employees = list(db.execute(
        select(Employee).where(Employee.policy_year_id == policy_year_id,
                               Employee.client_id == client_id)
    ).scalars())
    by_staff = {e.staff_id.strip().lower(): e for e in employees if e.staff_id}
    by_nric = {e.national_id_normalized: e for e in employees if e.national_id_normalized}
    dependants: dict[str, list[Dependant]] = {}
    for dep in db.execute(
        select(Dependant).where(Dependant.policy_year_id == policy_year_id,
                                Dependant.client_id == client_id)
    ).scalars():
        dependants.setdefault(dep.employee_id or "", []).append(dep)

    db.execute(delete(ListingAssignment).where(
        ListingAssignment.policy_year_id == policy_year_id,
        ListingAssignment.client_id == client_id,
    ))
    emp_count = dep_count = 0
    for listed in workbook.employees:
        staff_id = text(listed.fields.get("staff_id")).lower()
        employee = by_staff.get(staff_id) if staff_id else None
        if employee is None:
            employee = by_nric.get(employee_nric({"id_no": listed.fields.get("national_id")}))
        if employee is None:
            continue
        own: dict[int, LabelSuggestion] = {}
        for cover in listed.covers:
            label = normalize_label(text(cover.values.get("category")))
            decision = decisions.get((cover.block, label))
            if decision is None or decision.choice is None:
                continue
            own[cover.block] = decision
            db.add(_assignment(client_id, policy_year_id, employee.id, None, cover,
                               decision, product_ids, listed.row))
            emp_count += 1
        if not listed.covers:
            for block, codes in block_products.items():
                for code in codes:
                    db.add(ListingAssignment(
                        client_id=client_id, policy_year_id=policy_year_id,
                        employee_id=employee.id, member_key=f"E:{employee.id}",
                        product_id=product_ids[code], category_id=None,
                        block_index=block, recorded={}, source_row=listed.row,
                    ))
        for listed_dep in listed.dependants:
            dependant = _find_dependant(dependants.get(employee.id, []), listed_dep.fields)
            if dependant is None:
                continue
            for cover in listed_dep.covers:
                key = normalize_label(text(cover.values.get("category")))
                decision = decisions.get((cover.block, key)) if key else own.get(cover.block)
                if decision is None or decision.choice is None:
                    continue
                db.add(_assignment(client_id, policy_year_id, employee.id, dependant.id,
                                   cover, decision, product_ids, listed_dep.row))
                dep_count += 1
    db.flush()
    return emp_count, dep_count


def _find_dependant(candidates: list[Dependant], fields: dict[str, Any]) -> Dependant | None:
    name = text(fields.get("name")).lower()
    dob = fields.get("date_of_birth")
    dob_text = dob.isoformat() if isinstance(dob, date) else ""
    for dep in candidates:
        attrs = dep.attribute_values or {}
        if text(attrs.get("dependant_name")).lower() == name and (
            not dob_text or str(attrs.get("date_of_birth") or "")[:10] == dob_text
        ):
            return dep
    return None


def _assignment(
    client_id: str, policy_year_id: str, employee_id: str, dependant_id: str | None,
    cover: ElCover, decision: LabelSuggestion, product_ids: dict[str, str], row: int,
) -> ListingAssignment:
    choice = decision.choice
    assert choice is not None
    return ListingAssignment(
        client_id=client_id,
        policy_year_id=policy_year_id,
        employee_id=employee_id,
        dependant_id=dependant_id,
        member_key=f"D:{dependant_id}" if dependant_id else f"E:{employee_id}",
        product_id=product_ids[choice.product_code],
        category_id=choice.category_id,
        block_index=cover.block,
        listed_category=text(cover.values.get("category"))[:512] or None,
        listed_plan=text(cover.values.get("plan_type"))[:255] or None,
        family_group=cover.values.get("family_group"),
        admin_type=cover.values.get("admin_type"),
        recorded=_recorded(cover),
        source_row=row,
    )


def _save_profile(
    db: Session, client_id: str, user: CurrentUser, workbook: ElWorkbook,
    block_products: dict[int, list[str]], decisions: list[LabelSuggestion],
    profile: ElLayoutProfile | None, filename: str | None,
) -> None:
    label_map: dict[str, Any] = {k: dict(v) for k, v in (profile.label_map or {}).items()} \
        if profile else {}
    for d in decisions:
        block = label_map.setdefault(str(d.block), {})
        if d.not_covered:
            block[d.key] = {NOT_COVERED: True, "label": d.stats.label}
        elif d.choice is not None:
            block[d.key] = {
                "product_code": d.choice.product_code,
                "category_signature": d.choice.signature,
                "plan_code": d.choice.plan_code,
                "label": d.stats.label,
            }
    layout = serialize_layout(workbook.layout)
    if profile is None:
        profile = ElLayoutProfile(client_id=client_id, sheet_name=workbook.layout.sheet,
                                  layout=layout, block_products={}, label_map={})
        db.add(profile)
    profile.sheet_name = workbook.layout.sheet
    profile.layout = layout
    profile.block_products = {str(i): codes for i, codes in block_products.items()}
    profile.label_map = label_map
    profile.source_filename = (filename or "")[:255] or None
    profile.created_by = user.user_id


def serialize_layout(layout: ElLayout) -> dict[str, Any]:
    def cols(columns: Any) -> list[dict[str, Any]]:
        return [{"index": c.index, "letter": c.letter, "header": c.header, "role": c.role}
                for c in columns]

    return {
        "sheet": layout.sheet,
        "banner_row": layout.banner_row,
        "header_row": layout.header_row,
        "reference_date": layout.reference_date.isoformat() if layout.reference_date else None,
        "blocks": [
            {"kind": b.kind, "banner": b.banner, "code_hint": b.code_hint,
             "columns": cols(b.columns)}
            for b in layout.blocks
        ],
        "trailing": cols(layout.trailing),
    }


def apply(
    db: Session,
    user: CurrentUser,
    client_id: str,
    policy_year_id: str,
    path: Path | str,
    mapping: ListingMappingIn,
    *,
    terminate_missing: bool = False,
    expected_missing_digest: str | None = None,
    source_filename: str | None = None,
) -> ListingApplyOut:
    workbook = _read(path)
    products, block_products, _options, decisions, profile = _resolve(
        db, client_id, policy_year_id, workbook, mapping
    )
    unresolved = [
        d.stats.label for d in decisions
        if d.choice is None and not d.not_covered and (d.stats.employees or d.stats.dependants)
    ]
    if unresolved:
        raise UnresolvedListingMapping(
            "Map each listed category to a slip category or mark it not covered: "
            + "; ".join(sorted(unresolved)[:10])
        )
    records = _with_sole_entity(
        db, policy_year_id, build_records(workbook, _known_attribute_ids(db, client_id))
    )
    _ensure_attributes(db, client_id, records.attribute_ids)
    plan, _ = evaluate_records(
        db, policy_year_id, client_id, records.employees, records.dependants
    )
    if terminate_missing and expected_missing_digest is not None and (
        missing_digest(plan) != expected_missing_digest
    ):
        raise StaleListingPreview(
            "The roster changed since this listing was previewed — upload it again "
            "and review the terminations."
        )
    by_key = {(d.block, d.key): d for d in decisions}
    product_ids = {p.code: p.id for p in products}
    counts: dict[str, int] = {}

    def record_cover(session: Session) -> None:
        counts["employees"], counts["dependants"] = _write_assignments(
            session, client_id, policy_year_id, workbook, by_key, product_ids,
            block_products,
        )
        _save_profile(session, client_id, user, workbook, block_products, decisions,
                      profile, source_filename)
        categories = {
            c.id: c for c in session.execute(
                select(Category).where(Category.policy_year_id == policy_year_id)
            ).scalars()
        }
        counts["rules"] = persist_rules(
            categories, _joiner_rules(workbook, records, decisions, block_products)
        )
        write_audit(
            session, user, action="employee_listing_import", entity_type="policy_year",
            entity_id=policy_year_id,
            after={"filename": source_filename, "listed_covers": counts["employees"],
                   "listed_dependant_covers": counts["dependants"]},
        )

    result = apply_plan(
        db, user, policy_year_id, client_id, plan,
        terminate_missing=terminate_missing, source_filename=source_filename,
        after_members=record_cover,
    )
    # Matching (inside apply_plan) has opened the underwriting cases; add what
    # the listing records about them.
    year = db.get(PolicyYear, policy_year_id)
    underwriting = apply_listing_underwriting(db, year) if year is not None else None
    db.commit()
    return ListingApplyOut(
        added=result.added, changed=result.changed, deleted=result.deleted,
        missing_terminated=result.missing_terminated, unchanged=result.unchanged,
        rematched=result.rematched, assignments=counts.get("employees", 0),
        dependant_assignments=counts.get("dependants", 0), profile_saved=True,
        flex_errors=result.flex_errors,
        joiner_rules_written=counts.get("rules", 0),
        underwriting_updated=underwriting.updated if underwriting else 0,
    )
