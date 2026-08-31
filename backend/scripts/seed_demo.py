"""Seed Singapore default schema + demo broker firm/client/policy year/products.

Idempotent: re-running won't duplicate. Run once after `alembic upgrade head`.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.auth import (
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    DEMO_USER_EMAIL,
    DEMO_USER_ID,
)
from app.db.session import SessionLocal, engine
from app.db.tenancy import provision_firm_schema, set_search_path
from app.models import (
    BrokerFirm,
    Client,
    EmployeeAttributeSchema,
    PolicyYear,
    Product,
    User,
)
from app.models.policy_year import PolicyYearStatus
from app.services.rule_generator import _ROLE_PATTERNS
from scripts.seed_insurers import seed_insurers

# A second client under the same demo firm so the in-app client switcher has
# somewhere to switch to in local dev.
DEMO_CLIENT_2_ID = "00000000-0000-0000-0000-000000000021"

# Derive `role` from the employee's raw `category` text using the SAME patterns
# the rule generator extracts category-side role conditions from. The symmetry
# is the point: a category whose description yields `role = EVP` only matches an
# employee whose `category` ALSO yields `role = EVP`. Without this derivation the
# role attribute is never populated and every role-based eligibility rule is
# inert — collapsing matches onto whichever product happens to fuzzy-match the
# tier label. Most-specific patterns come first (mirrors `_detect_roles`).
_ROLE_DERIVATION_RULE = {
    "op": "regex_case",
    "source": "category",
    "cases": [{"pattern": pattern, "value": role} for pattern, role in _ROLE_PATTERNS],
}

SINGAPORE_ATTRIBUTES: list[dict[str, Any]] = [
    {
        "attribute_id": "grade",
        "display_name": "Hay Grade (derived)",
        "data_type": "integer",
        "is_required": False,
        "is_pii": False,
        "description": "Hay job grade number (typically 1-30)",
        "derived_from": "category",
        "derivation_rule": {
            "op": "regex_extract",
            "source": "category",
            # Grab the first standalone 1-2 digit grade token. The grade is
            # always the first number in these categories, so this covers:
            #   "17 Married plus 2 children" -> 17 (not the child count),
            #   "Grade 17" / "Hay Job Grade 17" / "Class 18 and above" -> 17/18,
            #   "Thailand 11 to 15 Single" / "Australia 16 to 17" -> 11/16
            #     (overseas rows carry a country prefix before the band).
            # The previous "^\s*|grade|class"-anchored pattern missed the
            # country-prefixed overseas rows, leaving them with no grade.
            "pattern": r"\b(\d{1,2})\b",
            "group": 1,
            "cast": "int",
        },
    },
    {
        "attribute_id": "pass",
        "display_name": "Employment Pass",
        "data_type": "enum",
        "enum_values": ["EP", "SP", "WP", "PR", "CITIZEN"],
        "is_required": False,
        "is_pii": False,
        "description": "Work pass / residency supplied directly by the employee listing",
    },
    {
        "attribute_id": "class",
        "display_name": "Employment Class",
        "data_type": "enum",
        "enum_values": [
            "PROFESSIONAL",
            "BARGAINABLE",
            "INTERN",
            "CONTRACT",
            "APPRENTICE",
            "SECONDEE",
            "INDUSTRIAL_STUDENT",
            "BOARD_OF_DIRECTORS",
            "INTERN_OVERSEAS",
        ],
        "is_required": False,
        "is_pii": False,
        "description": "Employment classification used for category matching",
        "derived_from": "category",
        "derivation_rule": {
            "op": "regex_case",
            "source": "category",
            "cases": [
                {"pattern": r"(?i)bargainable", "value": "BARGAINABLE"},
                {"pattern": r"(?i)professional", "value": "PROFESSIONAL"},
                {"pattern": r"(?i)\bintern(?:s)?\b(?!.*oversea)", "value": "INTERN"},
                {"pattern": r"(?i)intern.*oversea|oversea.*intern", "value": "INTERN_OVERSEAS"},
                {"pattern": r"(?i)contract", "value": "CONTRACT"},
                {"pattern": r"(?i)apprentice", "value": "APPRENTICE"},
                {"pattern": r"(?i)secondee", "value": "SECONDEE"},
                {"pattern": r"(?i)industrial\s*student", "value": "INDUSTRIAL_STUDENT"},
                {"pattern": r"(?i)board\s*of\s*directors|\bbod\b", "value": "BOARD_OF_DIRECTORS"},
            ],
        },
    },
    {
        "attribute_id": "occupation",
        "display_name": "Occupation (WICA)",
        "data_type": "enum",
        "enum_values": ["MGMT_ADMIN", "MANUFACTURING", "FORKLIFT", "ALL_OTHERS"],
        "is_required": False,
        "is_pii": False,
        "description": "WICA occupation grouping",
    },
    {
        "attribute_id": "job_function",
        "display_name": "Job Function",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Specific function (e.g. FIRE_FIGHTER)",
    },
    {
        "attribute_id": "nationality",
        "display_name": "Nationality",
        "data_type": "string",
        "is_required": False,
        "is_pii": True,
    },
    {
        "attribute_id": "category",
        "display_name": "Employee Category (roster)",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Classification supplied directly in the employee listing.",
    },
    {
        "attribute_id": "division",
        "display_name": "Division",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Organisational division supplied in the employee listing.",
    },
    {
        "attribute_id": "department",
        "display_name": "Department",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Organisational department supplied in the employee listing.",
    },
    {
        "attribute_id": "cost_centre",
        "display_name": "Cost Centre",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Cost centre supplied in the employee listing.",
    },
    {
        "attribute_id": "salary",
        "display_name": "Monthly Salary (SGD)",
        "data_type": "decimal",
        "is_required": False,
        "is_pii": True,
    },
    {
        "attribute_id": "family_status",
        "display_name": "Family Status",
        "data_type": "enum",
        "enum_values": ["S", "M", "M1C", "M2C", "M3C"],
        "is_required": False,
        "is_pii": False,
        "derived_from": "category",
        "derivation_rule": {
            "op": "regex_case",
            "source": "category",
            "cases": [
                {
                    "pattern": (
                        r"(?i)(?:married|spouse).{0,40}"
                        r"(?:3|three|3\+|3\s*or\s*more)\s*(?:child|children|kids?)"
                    ),
                    "value": "M3C",
                },
                {
                    "pattern": r"(?i)(?:married|spouse).{0,40}(?:2|two)\s*(?:child|children|kids?)",
                    "value": "M2C",
                },
                {
                    "pattern": r"(?i)(?:married|spouse).{0,40}(?:1|one)\s*(?:child|children|kid)",
                    "value": "M1C",
                },
                {"pattern": r"(?i)married|\bspouse\b", "value": "M"},
                {"pattern": r"(?i)\bsingle\b|\bunmarried\b", "value": "S"},
            ],
        },
    },
    {
        "attribute_id": "is_fw",
        "display_name": "Is Foreign Worker",
        "data_type": "boolean",
        "is_required": False,
        "is_pii": False,
    },
    # New attributes for non-STM placement-slip vocabularies.
    {
        "attribute_id": "role",
        "display_name": "Executive Role (derived)",
        "data_type": "enum",
        "enum_values": [
            "CEO",
            "GCEO",
            "DEPUTY_CEO",
            "CFO",
            "COO",
            "GCOO",
            "CTO",
            "CSO",
            "EVP",
            "SVP",
            "SENIOR_DIRECTOR",
            "EXECUTIVE_DIRECTOR",
            "MANAGING_DIRECTOR",
            "DIRECTOR",
            "SENIOR_MANAGER",
            "MANAGER",
            "OFFICER",
        ],
        "is_required": False,
        "is_pii": False,
        "description": "Executive / management role used in role-based eligibility rules",
        "derived_from": "category",
        "derivation_rule": _ROLE_DERIVATION_RULE,
    },
    {
        "attribute_id": "class_code",
        "display_name": "Employment Class Code",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Numeric/letter class code (Class 1, Class 2, A7, L1 etc.)",
    },
    {
        "attribute_id": "location",
        "display_name": "Work Location",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Country or city where the employee is based",
    },
    {
        "attribute_id": "job_grade",
        "display_name": "Job Grade (roster)",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Raw job-grade code from the roster (e.g. E2, A9, J3)",
    },
    {
        "attribute_id": "job_category",
        "display_name": "Job Category Code (derived)",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Internal job category code (e.g. 99, A7, L1)",
        "derived_from": "job_grade",
        # The roster Job Grade IS the job-category code the slips enumerate
        # ("Job category: 99, A1 to A9, ..."). Pull the leading alphanumeric
        # token (tolerates stray whitespace); the rule evaluator compares
        # case-insensitively, so no upper-casing is needed here.
        "derivation_rule": {
            "op": "regex_extract",
            "source": "job_grade",
            "pattern": r"^\s*([A-Za-z0-9]+)",
            "group": 1,
        },
    },
    {
        "attribute_id": "entity",
        "display_name": "Entity",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": (
            "Legal entity (company / subsidiary) employing the member, from the "
            "roster's Entity column. Gates matching against a category's insured "
            "entities on multi-subsidiary schemes (e.g. WICA per-entity blocks)."
        ),
    },
    # ── Insurer-report fields (member-listing template, 2026-07) ─────────────
    {
        "attribute_id": "employment_status",
        "display_name": "Employment Status",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Employment basis (Permanent / Contract) for insurer listings.",
    },
    {
        "attribute_id": "designation",
        "display_name": "Designation",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Job title as reported to the insurer.",
    },
    {
        "attribute_id": "country_of_work",
        "display_name": "Country of Work",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Work location country for insurer listings.",
    },
    {
        "attribute_id": "bank_code",
        "display_name": "Bank Code",
        "data_type": "string",
        "is_required": False,
        "is_pii": True,
        "description": "Payroll bank code (insurer reimbursement listings).",
    },
    {
        "attribute_id": "branch_code",
        "display_name": "Branch Code",
        "data_type": "string",
        "is_required": False,
        "is_pii": True,
        "description": "Payroll bank branch code.",
    },
    {
        "attribute_id": "bank_account_no",
        "display_name": "Bank Account No.",
        "data_type": "string",
        "is_required": False,
        "is_pii": True,
        "description": "Payroll bank account number.",
    },
    {
        "attribute_id": "prior_year_cover",
        "display_name": "Has Insurance Cover Last Year",
        "data_type": "boolean",
        "is_required": False,
        "is_pii": False,
        "description": (
            "Whether the member held cover in the previous policy year. Roster "
            "flag wins; when absent, insurer reports derive it from the prior "
            "policy year's roster."
        ),
    },
    {
        "attribute_id": "leave_sell_eligible",
        "display_name": "Eligible to Sell Leave",
        "data_type": "boolean",
        "is_required": False,
        "is_pii": False,
        "description": (
            "Whether the member may sell annual leave during enrollment. "
            "Absent means eligible; an explicit false blocks sell elections."
        ),
    },
    {
        "attribute_id": "remarks",
        "display_name": "Remarks",
        "data_type": "string",
        "is_required": False,
        "is_pii": False,
        "description": "Free-text remarks carried onto insurer listings.",
    },
]

PRODUCT_CATALOG: list[dict[str, Any]] = [
    {"code": "GTL", "display_name": "Group Term Life", "has_dependants": False},
    {"code": "GHS", "display_name": "Group Hospital & Surgical", "has_dependants": True},
    {"code": "GMM", "display_name": "Group Major Medical", "has_dependants": True},
    {"code": "SP", "display_name": "Supplementary Plan", "has_dependants": True},
    {"code": "GPA", "display_name": "Group Personal Accident", "has_dependants": False},
    {"code": "GBT", "display_name": "Group Business Travel", "has_dependants": False},
    {"code": "WICA", "display_name": "Work Injury Compensation Act", "has_dependants": False},
    # New products observed across PNG / CBRE / Hartree / Placement Slips 2026
    {
        "code": "GCI",
        "display_name": "Group Critical Illness",
        "has_dependants": True,
    },
    {
        "code": "GDD",
        "display_name": "Group Death & Disability",
        "has_dependants": False,
    },
    {
        "code": "GCGP",
        "display_name": "Group Comprehensive General Practitioner",
        "has_dependants": True,
        "is_outpatient": True,
    },
    {
        "code": "GCSP",
        "display_name": "Group Comprehensive Specialist",
        "has_dependants": True,
        "is_outpatient": True,
    },
    {"code": "GD", "display_name": "Group Dental", "has_dependants": True},
    {
        "code": "GP",
        "display_name": "Group Clinical General Practitioner",
        "has_dependants": True,
        "is_outpatient": True,
    },
    {
        "code": "OSI",
        "display_name": "Group Secondment Insurance",
        "has_dependants": False,
        "is_outpatient": False,
    },
    {"code": "DENTAL", "display_name": "Group Dental", "has_dependants": True},
    # Line-tab products (Medical / Life). Global recognition rows so the parser
    # can resolve these codes off client slips; they surface no UI card until a
    # slip or manual setup creates categories/drafts for them.
    {"code": "GHS2", "display_name": "Group Hospital & Surgical (Plan 2)", "has_dependants": True},
    {"code": "GMM2", "display_name": "Group Major Medical (Plan 2)", "has_dependants": True},
    {
        "code": "GOSP",
        "display_name": "Group Outpatient Specialist",
        "has_dependants": True,
        "is_outpatient": True,
    },
    {
        "code": "GOGP",
        "display_name": "Group Outpatient General Practitioner",
        "has_dependants": True,
        "is_outpatient": True,
    },
    {"code": "IMP", "display_name": "International Medical Plan", "has_dependants": True},
    {"code": "MATERNITY", "display_name": "Group Maternity", "has_dependants": True},
    {"code": "VISION", "display_name": "Group Vision", "has_dependants": True},
    {"code": "WELLNESS", "display_name": "Group Wellness", "has_dependants": True},
    {"code": "GDI", "display_name": "Group Disability Income", "has_dependants": False},
    {"code": "GTPD", "display_name": "Group Total & Permanent Disability", "has_dependants": False},
]


# Alternate codes that refer to the same conceptual product.
# WICI (insurance) ↔ WICA (the Singapore Work Injury Compensation Act).
PRODUCT_CODE_ALIASES: dict[str, str] = {
    "WICI": "WICA",
}


def seed() -> None:
    db = SessionLocal()
    try:
        # Broker firm
        firm = db.get(BrokerFirm, DEMO_BROKER_FIRM_ID)
        if firm is None:
            firm = BrokerFirm(id=DEMO_BROKER_FIRM_ID, name="Demo Broker Firm")
            db.add(firm)

        # Client
        client = db.get(Client, DEMO_CLIENT_ID)
        if client is None:
            client = Client(
                id=DEMO_CLIENT_ID,
                name="STM (demo)",
                broker_firm_id=DEMO_BROKER_FIRM_ID,
            )
            db.add(client)

        # Second client under the same firm (for the client switcher).
        if db.get(Client, DEMO_CLIENT_2_ID) is None:
            db.add(
                Client(
                    id=DEMO_CLIENT_2_ID,
                    name="VDL (demo)",
                    broker_firm_id=DEMO_BROKER_FIRM_ID,
                )
            )

        # Demo broker_admin user — DB-backed identity matched by Entra oid or
        # email. In mock mode this row backs /me; the mock principal uses the
        # same id.
        if db.get(User, DEMO_USER_ID) is None:
            db.add(
                User(
                    id=DEMO_USER_ID,
                    external_id=None,
                    email=DEMO_USER_EMAIL,
                    display_name="Demo Broker Admin",
                    broker_firm_id=DEMO_BROKER_FIRM_ID,
                    role="broker_admin",
                    status="active",
                )
            )

        db.flush()

        # Singapore default attribute schema (global — client_id null).
        # Idempotent: missing rows are inserted; existing rows have their
        # derivation_rule / derived_from refreshed so re-running picks up
        # newly-added derivation logic without a migration.
        existing_attr_rows = {
            a.attribute_id: a
            for a in db.query(EmployeeAttributeSchema)
            .filter(EmployeeAttributeSchema.client_id.is_(None))
            .all()
        }
        for spec in SINGAPORE_ATTRIBUTES:
            spec = {
                **spec,
                "allow_matching": spec.get("allow_matching", True),
                "allow_ai_values": spec.get(
                    "allow_ai_values", not bool(spec.get("is_pii"))
                ),
            }
            existing_attribute = existing_attr_rows.get(str(spec["attribute_id"]))
            if existing_attribute is None:
                db.add(EmployeeAttributeSchema(client_id=None, **spec))
            else:
                existing_attribute.derived_from = spec.get("derived_from")
                existing_attribute.derivation_rule = spec.get("derivation_rule")
                existing_attribute.allow_matching = bool(spec["allow_matching"])
                existing_attribute.allow_ai_values = bool(spec["allow_ai_values"])

        # Product catalog (global — client_id null)
        # Idempotent: missing rows are inserted; existing rows have their
        # catalog fields re-synced so a registry rename (e.g. OSI → Group
        # Secondment Insurance) reaches DBs seeded before the change.
        existing_products = {
            p.code: p
            for p in db.query(Product).filter(Product.client_id.is_(None)).all()
        }
        for spec in PRODUCT_CATALOG:
            existing_product = existing_products.get(str(spec["code"]))
            if existing_product is None:
                db.add(Product(client_id=None, **spec))
            else:
                for field_name, value in spec.items():
                    setattr(existing_product, field_name, value)

        # Insurer name library (global — client_id null). Same idempotent
        # upsert; see scripts/seed_insurers.py.
        seed_insurers(db)

        db.commit()
    finally:
        db.close()

    # Provision the demo firm's schema (Postgres) and place its operational
    # data there. On SQLite this is a no-op and the policy year lands in the
    # single shared schema, exactly as before.
    provision_firm_schema(engine, DEMO_BROKER_FIRM_ID)
    with SessionLocal() as db:
        set_search_path(db, DEMO_BROKER_FIRM_ID)
        # `insurers` is a tenant table, so on Postgres the firm schema has its
        # own (empty) copy — the public seeding above is not visible to
        # requests. Seed it here too; a no-op re-run on SQLite.
        seed_insurers(db)
        existing_py = (
            db.query(PolicyYear)
            .filter(PolicyYear.client_id == DEMO_CLIENT_ID, PolicyYear.year == 2026)
            .one_or_none()
        )
        if existing_py is None:
            db.add(
                PolicyYear(
                    client_id=DEMO_CLIENT_ID,
                    year=2026,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                    status=PolicyYearStatus.draft,
                )
            )
            db.commit()

        # NOTE: flex price tags are NOT seeded. They are derived from the placement
        # slip by default (the per-product source defaults to "slip"), so the
        # enrollment panel + benefit statement price coverage straight from the
        # uploaded slip's premiums. A broker only configures the portal matrix when
        # they want to OVERRIDE the slip-derived tag for a product.
    print("Seed complete.")


if __name__ == "__main__":
    seed()
