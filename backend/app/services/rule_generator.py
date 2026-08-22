"""Description-to-rule generator.

Ported verbatim from the validated browser prototype's `descriptionToRule`
(see plan §Reference + brief §8.2-8.3). Emits JSONLogic-style predicates.

Critical behavior preserved from the prototype:
- AND/OR detection (§8.3): "Grade 8 to 15 and Bargainable Staff" → UNION,
  but "Grade 8 to 15 who are Bargainable" → INTERSECTION.
- WICA occupation classes ride alongside class conditions.
- Confidence reflects whether the parse was specific (single condition or
  union/intersection of recognized parts) or fell through to needs-review.
"""
from __future__ import annotations

import re
from typing import Any

from app.schemas.rule import RuleEnvelope

# ── Grade patterns ──────────────────────────────────────────────────────────
_RX_GRADE_RANGE = re.compile(
    r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:to|-|–|—)\s*0?(\d+)"  # noqa: RUF001 — Excel ranges use real en/em dashes
)
_RX_GRADE_ABOVE = re.compile(
    r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:&|and|or)\s*above"
)
_RX_GRADE_BELOW = re.compile(
    r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:&|and|or)\s*below"
)
_RX_GRADE_PLUS = re.compile(r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*\+")

# ── Salary patterns ─────────────────────────────────────────────────────────
_RX_SALARY_LT = re.compile(
    r"(?:earning|salary)?\s*(?:less than|<|under)\s*\$?\s*([\d,]+)"
)
_RX_SALARY_GT = re.compile(
    r"(?:earning|salary)?\s*(?:more than|>|over|above)\s*\$?\s*([\d,]+)"
)

# ── AND/OR detection helpers (§8.3) ─────────────────────────────────────────
_RX_GRADE_AND_CLASS = re.compile(r"grade.+ and (bargainable|intern|contract)")
_RX_GRADE_WHO_CLASS = re.compile(
    r"grade.+ who (?:are|is) (bargainable|intern|contract)"
)
_RX_GRADE_WITH_CLASS = re.compile(r"grade.+ with (bargainable|intern|contract)")

# ── Pass patterns ───────────────────────────────────────────────────────────
# `s[\s\-]*pass` tolerates the real-world spellings "S-Pass", "S Pass" and
# "S- Pass" (dash followed by a space). The `(?<![a-z])` guard keeps it from
# firing inside words like "his passport". Older `s[ -]?pass` allowed only a
# single separator and silently dropped S-Pass holders on STM-style slips.
_SPASS = r"(?<![a-z])s[\s\-]*pass"
_RX_WP_AND_SP = re.compile(rf"work permit\s*(?:&|and|or|\/)\s*{_SPASS}")
_RX_NON_WP_LOOSE = re.compile(r"non[- ]?wp")
_RX_WP_SP_SLASH = re.compile(r"wp\/sp")
_RX_NON_WP_SP_SLASH = re.compile(r"non[- ]?wp\/sp")
_RX_SPASS = re.compile(_SPASS)
_RX_WP = re.compile(r"work permit")
_RX_FOREIGN_WORKER = re.compile(r"foreign worker")

# ── Catch-all "All Employees" ──────────────────────────────────────────────
_RX_ALL_EMPLOYEES = re.compile(
    r"^\s*all\s+(employees?|staffs?|members?)\s*(?:\(|$|(?:and|&)\s+their\s+(eligible\s+)?(dependants?|dependents?))",
    re.IGNORECASE,
)

# ── Plan-N: TITLE (Job category: codes) ────────────────────────────────────
_RX_PLAN_PREFIX = re.compile(
    r"^\s*plan\s+[A-Za-z0-9/]+\s*:\s*(.+)$", re.IGNORECASE
)
_RX_JOB_CATEGORY = re.compile(
    r"\bjob\s+category\s*:\s*([^)]+?)(?:\)|$)", re.IGNORECASE
)
_RX_CODE_RANGE = re.compile(r"^(.+?)\s+to\s+(.+)$", re.IGNORECASE)
_RX_SINGLE_CODE = re.compile(r"^([A-Z]*)(\d+)$")

# Open-ended bands ("SM and above") only enumerate the grades that existed when
# the slip was written, so a newer/higher grade (E10 above a listed E7-E9) falls
# out. When a band is qualified "… and above" / "… and below" we extend each
# numeric grade run to this ceiling so the open-ended intent is honored without
# unbounded enumeration. Real job grades don't approach this; the margin is safe.
_GRADE_CEILING = 30


def _expand_code_range(
    token: str, *, open_above: bool = False, open_below: bool = False
) -> list[str] | None:
    """Expand one job-category token into the concrete grade codes it covers.

    Group placement slips enumerate eligible grades as compact ranges:
        "A1 to A9" → A1…A9     "AA to AG" → AA…AG
        "E7 to E9" → E7,E8,E9  "99"       → 99
    The varying part is the final character of a shared prefix (numeric or
    alphabetic); pure-numeric ranges may span widths ("9 to 12"). When the band
    is open-ended, numeric runs extend to ``_GRADE_CEILING`` (above) or 1
    (below) so "SM and above" catches grades senior to the listed maximum.
    Returns the uppercased code list, or ``None`` for an unrecognized shape so
    the caller leaves it for review instead of emitting a wrong rule.
    """
    token = token.strip().upper()
    if not token:
        return None

    def _num_run(prefix: str, lo: int, hi: int) -> list[str]:
        if open_above:
            hi = max(hi, _GRADE_CEILING)
        if open_below:
            lo = 1
        return [f"{prefix}{n}" for n in range(lo, hi + 1)] if lo <= hi else []

    m = _RX_CODE_RANGE.match(token)
    if m is None:  # single code, e.g. "99" / "A7" / "E7" (with open-ended tail)
        single = _RX_SINGLE_CODE.match(token)
        if single and (open_above or open_below):
            prefix, num = single.group(1), int(single.group(2))
            return _num_run(prefix, num, num) or None
        return [token]

    lo, hi = m.group(1).strip(), m.group(2).strip()
    if lo.isdigit() and hi.isdigit():  # "9 to 12" — any width
        a, b = int(lo), int(hi)
        return _num_run("", a, b) or None
    if len(lo) == len(hi) and lo[:-1] == hi[:-1] and lo[:-1]:  # shared prefix
        prefix, a, b = lo[:-1], lo[-1], hi[-1]
        if a.isdigit() and b.isdigit() and int(a) <= int(b):
            return _num_run(prefix, int(a), int(b)) or None
        if a.isalpha() and b.isalpha() and ord(a) <= ord(b):
            return [f"{prefix}{chr(n)}" for n in range(ord(a), ord(b) + 1)]
    return None

# ── Role hierarchy ─────────────────────────────────────────────────────────
# Order matters — longer / more specific matches first.
_ROLE_PATTERNS: list[tuple[str, str]] = [
    (r"\bdeputy\s+ceo\b", "DEPUTY_CEO"),
    (r"(?<!deputy\s)\bg?ceo\b", "CEO"),
    (r"\bg?coo\b", "COO"),
    (r"\bcfo\b", "CFO"),
    (r"\bcto\b", "CTO"),
    (r"\bcso\b", "CSO"),
    (r"\bsenior\s+managing\s+director\b", "MANAGING_DIRECTOR"),
    (r"\bmanaging\s+director\b", "MANAGING_DIRECTOR"),
    (r"\bexecutive\s+director\b", "EXECUTIVE_DIRECTOR"),
    (r"\bsenior\s+director\b", "SENIOR_DIRECTOR"),
    (r"\bevp\b", "EVP"),
    (r"\bsvp\b", "SVP"),
    (r"\bsenior\s+manager\b", "SENIOR_MANAGER"),
    (r"\bdirector\b", "DIRECTOR"),
]

# ── Class N codes ──────────────────────────────────────────────────────────
_RX_CLASS_CODE = re.compile(r"\bclass\s+(\d+|[A-Z]\d?)\s+(employees?|staff)?", re.IGNORECASE)

# ── Geography ──────────────────────────────────────────────────────────────
_RX_BASED_IN = re.compile(r"\bbased\s+in\s+([A-Z][A-Za-z]+)", re.IGNORECASE)

# Trailing clauses to ignore when matching role/employment patterns.
_RX_TRAILING_DEPENDENTS = re.compile(
    r"\s+(?:and|&)\s+(their|the)\s+(eligible\s+)?(dependants?|dependents?).*$",
    re.IGNORECASE,
)
_RX_TRAILING_OPTION = re.compile(r"\s*\(option\s+\d+\)\s*$", re.IGNORECASE)


Rule = dict[str, Any]


def description_to_rule(desc: str) -> RuleEnvelope:
    """Convert a category description into a JSONLogic predicate.

    Returns a `RuleEnvelope` with the rule, a human-readable rendering,
    and a generator confidence. Any rule with confidence < 0.85 is marked
    `needs_review` so the Phase-4 AI fallback knows to pick it up.
    """
    if not desc:
        return RuleEnvelope(
            rule=None,
            human_readable="(empty description)",
            confidence=0.0,
            needs_review=True,
        )

    # Strip trailing dependent / option clauses so role/employment matchers
    # don't get confused by "... and their Eligible Dependents (Option 2)".
    cleaned = _RX_TRAILING_OPTION.sub("", desc).strip()
    cleaned = _RX_TRAILING_DEPENDENTS.sub("", cleaned).strip()

    # "All Employees" / "All staff" → catch-all (vacuously true).
    if _RX_ALL_EMPLOYEES.match(cleaned):
        return RuleEnvelope(
            rule={"and": []},
            human_readable="all employees (no filter)",
            confidence=0.75,
            needs_review=True,
        )

    d = cleaned.lower().strip()
    conditions: list[Rule] = []
    notes: list[str] = []
    grade_cond: Rule | None = None
    grade_note = ""

    # ── Plan-prefix unwrap: "Plan 1: GCEO and GCOO (Job category: 99)" → ──
    #    title = "GCEO and GCOO (Job category: 99)" so role/job_category match.
    plan_match = _RX_PLAN_PREFIX.match(cleaned)
    if plan_match:
        d = plan_match.group(1).lower().strip()

    # ── Job category code ──
    # Slips enumerate eligible grades as code ranges ("A1 to A9, AA to AG, ...").
    # Expand them into a concrete membership set the matcher evaluates against the
    # employee's job_category (derived from the roster Job Grade column). This is
    # the deterministic signal for the senior bands (e.g. GHS "SM and above") that
    # carry no tier label the name matcher can latch onto.
    job_match = _RX_JOB_CATEGORY.search(d)
    if job_match:
        raw_codes = job_match.group(1).strip().rstrip(",.")
        parts = [p.strip() for p in raw_codes.split(",") if p.strip()]
        # "SM and above" → open-ended numeric runs. Only treat the band as
        # open-ended when the seniority is expressed as a tier label, NOT when an
        # explicit Hay-grade clause is present ("Grade 8 and above"): there the
        # "above" qualifies the grade number and is handled by the grade rule, so
        # the job-category list must stay literal rather than balloon to the cap.
        has_grade_clause = bool(
            _RX_GRADE_RANGE.search(d)
            or _RX_GRADE_ABOVE.search(d)
            or _RX_GRADE_BELOW.search(d)
            or _RX_GRADE_PLUS.search(d)
        )
        open_above = not has_grade_clause and bool(re.search(r"\b(?:and|&)\s+above\b", d))
        open_below = not has_grade_clause and bool(re.search(r"\b(?:and|&)\s+below\b", d))
        codes: list[str] = []
        expandable = bool(parts)
        for part in parts:
            expanded = _expand_code_range(
                part, open_above=open_above, open_below=open_below
            )
            if expanded is None:
                expandable = False  # unrecognized shape — don't emit a wrong rule
                break
            codes.extend(expanded)
        codes = list(dict.fromkeys(codes))  # dedupe, preserve order
        if expandable and codes:
            if len(codes) == 1:
                conditions.append({"=": ["job_category", codes[0]]})
                notes.append(f"job_category = {codes[0]}")
            else:
                conditions.append({"in": ["job_category", codes]})
                notes.append(f"job_category ∈ {len(codes)} codes")

    # ── Grade ──
    # Captures EVERY grade band in the text and unions them. A single category
    # often spans split bands, e.g. "Grade 08 to 10 / Grade 11 to 17" — the
    # old first-match-only logic silently dropped grades 11-17.
    grade_cond, grade_note = _detect_grade(d)
    if grade_cond is not None:
        conditions.append(grade_cond)
        notes.append(grade_note)
    grade_matched = grade_cond is not None

    grade_and_class_union = (
        grade_matched
        and bool(_RX_GRADE_AND_CLASS.search(d))
        and not _RX_GRADE_WHO_CLASS.search(d)
        and not _RX_GRADE_WITH_CLASS.search(d)
    )

    # ── Salary ──
    if m := _RX_SALARY_LT.search(d):
        v = int(m.group(1).replace(",", ""))
        conditions.append({"<": ["salary", v]})
        notes.append(f"salary < {v}")
    elif m := _RX_SALARY_GT.search(d):
        v = int(m.group(1).replace(",", ""))
        conditions.append({">": ["salary", v]})
        notes.append(f"salary > {v}")

    # ── Pass ──
    pass_cond, pass_note = _detect_pass(d)
    if pass_cond is not None:
        conditions.append(pass_cond)
        notes.append(pass_note)

    # ── Role hierarchy (CEO, CFO, EVP, Director etc.) ──
    role_matches = _detect_roles(d)
    if role_matches:
        if len(role_matches) == 1:
            conditions.append({"=": ["role", role_matches[0]]})
            notes.append(f"role = {role_matches[0]}")
        else:
            conditions.append({"in": ["role", role_matches]})
            notes.append(f"role ∈ {role_matches}")

    # ── Class N code ──
    class_code_match = _RX_CLASS_CODE.search(d)
    if class_code_match:
        code = class_code_match.group(1).upper()
        conditions.append({"=": ["class_code", code]})
        notes.append(f"class_code = {code}")

    # ── Geography ──
    geo_match = _RX_BASED_IN.search(d)
    if geo_match:
        country = geo_match.group(1).capitalize()
        conditions.append({"=": ["location", country]})
        notes.append(f"location = {country}")

    # ── Class / occupation ──
    class_conditions, class_notes = _detect_class_and_occupation(d)

    # ── Compose ──
    if grade_and_class_union and grade_cond is not None and class_conditions:
        # The eligible set is grade-bracket OR class-bracket. Shared
        # conditions (salary, pass) apply to both branches. The grade part is
        # tracked explicitly (not assumed to be conditions[0]) so a leading
        # job_category condition can't be mistaken for the grade bracket.
        other = [c for c in conditions if c is not grade_cond]
        other_notes = [
            n for c, n in zip(conditions, notes, strict=False) if c is not grade_cond
        ]
        group_a: Rule = (
            {"and": [grade_cond, *other]} if other else grade_cond
        )
        class_part: Rule = (
            class_conditions[0]
            if len(class_conditions) == 1
            else {"and": class_conditions}
        )
        group_b: Rule = (
            {"and": [class_part, *other]} if other else class_part
        )
        rule: Rule | None = {"or": [group_a, group_b]}
        other_str = (" AND " + " AND ".join(other_notes)) if other_notes else ""
        human_readable = f"({grade_note} OR {' + '.join(class_notes)}){other_str}"
        confidence = 0.75
    else:
        all_conds = [*conditions, *class_conditions]
        all_notes = [*notes, *class_notes]
        if not all_conds:
            rule = None
            confidence = 0.20
            human_readable = "(unmapped — needs admin)"
        elif len(all_conds) == 1:
            rule = all_conds[0]
            confidence = 0.85
            human_readable = all_notes[0]
        else:
            rule = {"and": all_conds}
            confidence = 0.75
            human_readable = " AND ".join(all_notes)

    return RuleEnvelope(
        rule=rule,
        human_readable=human_readable,
        confidence=confidence,
        needs_review=confidence < 0.85,
    )


def _detect_roles(d: str) -> list[str]:
    """Find executive role mentions in a category description.

    Returns a list of canonical role codes, deduplicated while preserving
    order of first appearance.
    """
    found: list[str] = []
    seen: set[str] = set()
    for pattern, role in _ROLE_PATTERNS:
        if re.search(pattern, d, re.IGNORECASE) and role not in seen:
            found.append(role)
            seen.add(role)
    return found


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent integer ranges.

    [(8, 10), (11, 17)] → [(8, 17)] (adjacent: 10 and 11 touch).
    [(8, 10), (14, 17)] → [(8, 10), (14, 17)] (gap at 11-13 preserved).
    """
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = [ordered[0]]
    for lo, hi in ordered[1:]:
        last_lo, last_hi = merged[-1]
        if lo <= last_hi + 1:  # overlap or adjacency
            merged[-1] = (last_lo, max(last_hi, hi))
        else:
            merged.append((lo, hi))
    return merged


def _detect_grade(d: str) -> tuple[Rule | None, str]:
    """Detect every grade band in a description and union them.

    Handles split bands ("08 to 10 / 11 to 17"), open-ended bounds
    ("16 and above", "10 and below", "8+"), and combinations. Adjacent /
    overlapping ranges collapse into one `between`; disjoint bands become an
    `or`. Returns (rule, human_readable) or (None, "") when no grade present.
    """
    ranges: list[tuple[int, int]] = []
    for m in _RX_GRADE_RANGE.finditer(d):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        ranges.append((lo, hi))

    ge: int | None = None
    if m := _RX_GRADE_ABOVE.search(d):
        ge = int(m.group(1))
    elif m := _RX_GRADE_PLUS.search(d):
        ge = int(m.group(1))
    le: int | None = None
    if m := _RX_GRADE_BELOW.search(d):
        le = int(m.group(1))

    if not ranges and ge is None and le is None:
        return None, ""

    parts: list[Rule] = []
    part_notes: list[str] = []
    for lo, hi in _merge_ranges(ranges):
        parts.append({"between": ["grade", lo, hi]})
        part_notes.append(f"grade in [{lo}, {hi}]")
    if ge is not None:
        parts.append({">=": ["grade", ge]})
        part_notes.append(f"grade ≥ {ge}")
    if le is not None:
        parts.append({"<=": ["grade", le]})
        part_notes.append(f"grade ≤ {le}")

    if len(parts) == 1:
        return parts[0], part_notes[0]
    return {"or": parts}, " OR ".join(part_notes)


def _detect_pass(d: str) -> tuple[Rule | None, str]:
    # Insurers write this union in either order. Detect the two concepts
    # symmetrically before the legacy separator-specific patterns so
    # "S-Pass or Work Permit" cannot silently collapse to S-Pass only.
    if _RX_WP.search(d) and _RX_SPASS.search(d):
        if _RX_NON_WP_LOOSE.search(d):
            return {"not_in": ["pass", ["WP", "SP"]]}, "pass ∉ [WP, SP]"
        return {"in": ["pass", ["WP", "SP"]]}, "pass ∈ [WP, SP]"
    if _RX_WP_AND_SP.search(d):
        if _RX_NON_WP_LOOSE.search(d):
            return {"not_in": ["pass", ["WP", "SP"]]}, "pass ∉ [WP, SP]"
        return {"in": ["pass", ["WP", "SP"]]}, "pass ∈ [WP, SP]"
    if _RX_WP_SP_SLASH.search(d):
        if _RX_NON_WP_SP_SLASH.search(d):
            return {"not_in": ["pass", ["WP", "SP"]]}, "pass ∉ [WP, SP]"
        return {"in": ["pass", ["WP", "SP"]]}, "pass ∈ [WP, SP]"
    if _RX_SPASS.search(d):
        return {"=": ["pass", "SP"]}, "pass = SP"
    if _RX_WP.search(d):
        return {"=": ["pass", "WP"]}, "pass = WP"
    if _RX_FOREIGN_WORKER.search(d):
        return {"in": ["pass", ["WP", "SP", "EP"]]}, "pass ∈ [WP, SP, EP]"
    return None, ""


def _detect_class_and_occupation(d: str) -> tuple[list[Rule], list[str]]:
    conds: list[Rule] = []
    notes: list[str] = []
    # Class is a single-valued attribute, so alternative class memberships
    # (Bargainable / Intern / Contract …) must be OR-ed, never AND-ed. The old
    # code appended each as its own equality, which the caller then AND-ed into
    # an impossible "class = BARGAINABLE AND class ∈ [INTERN, CONTRACT]". We
    # collect the alternatives and collapse them into one `in`/`=` predicate.
    class_values: list[str] = []

    has_firefighter = bool(re.search(r"fire\s?fighter", d))
    # "Non-bargainable" is a negation, not a positive class filter. Allow any
    # run of spaces/dashes between the tokens ("non-bargainable",
    # "non bargainable", "non - bargainable") so the negation is never lost.
    non_bargainable = bool(re.search(r"non[\s-]*bargainable", d))
    # A *parenthetical* "(incl./including … Bargainable …)" means bargainable
    # staff are merely flagged as included in a broader population, not the
    # restriction — so don't narrow to class=BARGAINABLE. This is deliberately
    # limited to text inside parentheses: a top-level enumeration like
    # "Including Bargainable Employees and Interns" is a real class list and
    # must keep BARGAINABLE.
    incl_bargainable = bool(
        re.search(r"\([^)]*?(?:incl\.?|including)[^)]*?\bbargainable\b[^)]*?\)", d)
    )

    # Firefighter is an independent job_function filter, applying across any
    # class (e.g. "Employees (incl. Bargainable) who serve as firefighters").
    if has_firefighter:
        conds.append({"=": ["job_function", "FIRE_FIGHTER"]})
        notes.append("job_function = FIRE_FIGHTER")

    if non_bargainable:
        conds.append({"!=": ["class", "BARGAINABLE"]})
        notes.append("class != BARGAINABLE")
    elif "bargainable" in d and not incl_bargainable:
        class_values.append("BARGAINABLE")

    if "intern" in d and "industrial" not in d and "overseas" not in d:
        class_values.extend(["INTERN", "CONTRACT"])
    if "intern (overseas" in d or re.search(r"overseas[- ]named", d):
        class_values.append("INTERN_OVERSEAS")
    if re.search(r"industrial attachment|industrial student", d):
        class_values.append("INDUSTRIAL_STUDENT")
    if "board of directors" in d:
        class_values.append("BOARD_OF_DIRECTORS")
    if re.search(r"postee|secondee|seconded overseas", d):
        class_values.append("SECONDEE")

    # Collapse the alternative class memberships into a single predicate.
    if class_values:
        uniq = list(dict.fromkeys(class_values))  # dedupe, preserve order
        if len(uniq) == 1:
            conds.append({"=": ["class", uniq[0]]})
            notes.append(f"class = {uniq[0]}")
        else:
            conds.append({"in": ["class", uniq]})
            notes.append(f"class ∈ {uniq}")

    # WICA occupations — only one fires (else-if chain in prototype).
    if "management" in d and re.search(r"admin|administrative", d):
        conds.append({"=": ["occupation", "MGMT_ADMIN"]})
        notes.append("occupation = MGMT_ADMIN")
    elif "manufacturing assistant" in d:
        conds.append({"=": ["occupation", "MANUFACTURING"]})
        notes.append("occupation = MANUFACTURING")
    elif "forklift" in d:
        conds.append({"=": ["occupation", "FORKLIFT"]})
        notes.append("occupation = FORKLIFT")
    elif "all others" in d and "engineer" in d:
        conds.append({"=": ["occupation", "ALL_OTHERS"]})
        notes.append("occupation = ALL_OTHERS")

    return conds, notes
