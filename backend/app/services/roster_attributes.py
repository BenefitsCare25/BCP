"""Tolerant access to client-defined roster / dependant attributes.

``Employee`` and ``Dependant`` store their fields in a free-form
``attribute_values`` dict whose keys are chosen per client. These tuples list
the common spellings for each logical field so every read path agrees on which
key holds a given value. Shared by the benefit statement and the fact-find form
so a new column alias only has to be added in one place.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from app.models.employee import EMPLOYEE_STATUS_TERMINATED

NAME_KEYS = ("name", "dependant_name", "full_name", "employee_name")
REL_KEYS = ("relationship", "relation", "rel", "dependant_type", "type")
DOB_KEYS = ("dob", "date_of_birth", "birth_date", "dateOfBirth", "birthdate")
GENDER_KEYS = ("gender", "sex")
PASS_KEYS = ("pass", "pass_type", "work_pass", "residency")
# National-ID (NRIC/FIN) keys, per record type. The parser writes the
# employee's own id under ``id_no`` and, on dependant rows, the linked
# employee's id under ``employee_id_no`` and the dependant's own under
# ``dependant_id_no``.
EMPLOYEE_ID_KEYS = ("id_no", "nric", "fin", "nric_fin", "identification_no")
DEPENDANT_ID_KEYS = ("dependant_id_no", "id_no", "nric", "fin")
EMAIL_KEYS = (
    "email",
    "work_email",
    "email_address",
    "company_email",
    "e-mail",
    "e_mail",
    "personal_email",
)


def first_value(values: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first present, non-empty value among ``keys`` as a string."""
    for k in keys:
        v = values.get(k)
        if v not in (None, ""):
            return str(v)
    return None


# A dependant's relationship, read from the roster's own wording. Used to bucket
# a household into the composite tier vocabulary (EO/ES/EC/EF).
_SPOUSE_RE = re.compile(r"(?i)spouse|wife|husband|partner|married")
_CHILD_RE = re.compile(r"(?i)child|son|daughter|kid|dependent child")


def family_tier_bucket(dependant_values: object) -> str:
    """Canonical composite tier for a household: EO / ES / EC / EF.

    Takes an iterable of dependant ``attribute_values`` dicts (not ORM rows) so
    it stays model-free and can be shared by every surface that reports a
    member split — the fact-find member tables, the slip export's Basis-of-Cover
    count block, and anything added later. One implementation means those
    documents can never disagree about the same household.
    """
    has_spouse = has_child = False
    for values in dependant_values or ():
        rel = first_value(values or {}, REL_KEYS) or ""
        if _SPOUSE_RE.search(rel):
            has_spouse = True
        elif _CHILD_RE.search(rel):
            has_child = True
    if has_spouse and has_child:
        return "EF"
    if has_spouse:
        return "ES"
    if has_child:
        return "EC"
    return "EO"


def normalize_nric(raw: object | None) -> str | None:
    """Canonicalize an NRIC/FIN for identity comparison.

    Uppercases and strips every non-alphanumeric character (spaces, dashes,
    dots) so ``s1234567a`` / ``S-1234567-A`` / ``S1234567A`` collapse to one
    key. Returns None for blank/empty input. This is the single dedup key for a
    person — never compare raw ``id_no`` strings directly.
    """
    if raw is None:
        return None
    canon = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
    return canon or None


def nric_from_attrs(
    attribute_values: dict[str, Any] | None, keys: tuple[str, ...] = EMPLOYEE_ID_KEYS
) -> str | None:
    """Normalized NRIC/FIN pulled from a roster ``attribute_values`` blob."""
    return normalize_nric(first_value(attribute_values or {}, keys))


def mask_nric(raw: object | None) -> str:
    """PII-safe display form of an NRIC/FIN: keep the first + last two chars,
    mask everything in between (``S1234567A`` → ``S******7A``). Values shorter
    than 6 chars — where masking would reveal too large a fraction — are returned
    fully masked, as are blank values, so nothing sensitive leaks into a report
    (duplicate manifests, roster reports)."""
    canon = normalize_nric(raw)
    if not canon:
        return ""
    if len(canon) < 6:
        return "*" * len(canon)
    return f"{canon[0]}{'*' * (len(canon) - 3)}{canon[-2:]}"


# Trailing-checksum letter tables by prefix (Singapore NRIC/FIN algorithm).
_NRIC_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_NRIC_CHECK = {"ST": "JZIHGFEDCBA", "FG": "XWUTRQPNMLK", "M": "XWUTRQPNJLK"}


def looks_like_sg_nric(raw: object | None) -> bool:
    """True when ``raw`` has the SHAPE of an SG NRIC/FIN — S/T/F/G/M series,
    ``<letter><7 digits><letter>`` — whether or not the checksum passes.

    Split from ``is_valid_sg_nric`` because only the PAIR can produce a usable
    warning. A foreign passport or work-pass number fails the checksum test for
    a reason that is not an error — it simply is not an NRIC — so warning on
    every failure would fire for each foreign hire and be ignored. A value that
    looks like an NRIC and fails is the one that is probably a typo.
    """
    canon = normalize_nric(raw)
    if not canon or len(canon) != 9:
        return False
    prefix, digits, suffix = canon[0], canon[1:8], canon[8]
    return prefix in "STFGM" and digits.isdigit() and suffix.isalpha()


def sg_nric_check_letter(prefix: str, digits: str) -> str | None:
    """The trailing checksum letter for an S/T/F/G/M-series number (None when
    the inputs are not that shape).

    Public so test fixtures can MINT valid IDs from the very algorithm that
    validates them: a generator carrying its own copy is how fixture data comes
    to disagree with the checker it is meant to exercise.
    """
    if prefix not in "STFGM" or len(digits) != 7 or not digits.isdigit():
        return None
    total = sum(int(d) * w for d, w in zip(digits, _NRIC_WEIGHTS, strict=True))
    if prefix in "TG":
        total += 4
    elif prefix == "M":
        total += 3
    table = next(t for k, t in _NRIC_CHECK.items() if prefix in k)
    return table[total % 11]


def is_valid_sg_nric(raw: object | None) -> bool:
    """True when ``raw`` passes the Singapore NRIC/FIN checksum.

    Used only to *warn* about likely-malformed IDs on upload — never to block a
    row (foreign/blank IDs are legitimate). Anything not matching the NRIC shape
    returns False, so pair it with ``looks_like_sg_nric`` to tell "not an NRIC"
    from "a wrong NRIC" — which is what ``suspect_nric_warning`` does.
    """
    if not looks_like_sg_nric(raw):
        return False
    canon = normalize_nric(raw)
    return sg_nric_check_letter(canon[0], canon[1:8]) == canon[8]


# Enough masked samples to start looking; the count carries the scale.
_NRIC_WARN_SAMPLE = 5


def suspect_nric_warning(values: Iterable[object | None]) -> str | None:
    """One advisory line for IDs shaped like an NRIC that fail its checksum.

    ADVISORY ONLY, by design: the roster is the customer's record of their own
    staff, a checksum is evidence of a typo rather than proof, and refusing the
    row would turn one bad digit into a failed upload. Samples are MASKED — the
    message is rendered in a browser, and an ID being wrong does not make it
    any less personal data.
    """
    bad = [
        mask_nric(v)
        for v in values
        if looks_like_sg_nric(v) and not is_valid_sg_nric(v)
    ]
    if not bad:
        return None
    shown = ", ".join(bad[:_NRIC_WARN_SAMPLE])
    more = f" (+{len(bad) - _NRIC_WARN_SAMPLE} more)" if len(bad) > _NRIC_WARN_SAMPLE else ""
    return (
        f"{len(bad)} identification number(s) look like an NRIC/FIN but fail its "
        f"checksum — likely a typo. The rows were imported unchanged: {shown}{more}"
    )


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d")

# Roster spellings of "the day cover stops". One tuple, because the reports and
# the flex allowance have to agree on when a member left.
LAST_DAY_KEYS = ("last_day_of_service", "last_day", "termination_date")


def roster_date(value: object) -> date | str | None:
    """Coerce a roster date-ish value to a real ``date`` — fall back to the raw
    string when unparseable. Roster dates are stored ISO on ingest, but tolerate
    the common alternates + Excel serial numbers so a stray format lands as a
    real date rather than literal text."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # Excel serial date (days since 1899-12-30), if a numeric cell slipped through.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return date(1899, 12, 30) + timedelta(days=int(value))
        except (ValueError, OverflowError):
            return str(value)
    raw = str(value).strip().split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return str(value)


def last_day_of_service(member: Any) -> date | str | None:
    """The member's last day AS A SHEET PRINTS IT.

    Takes an Employee or a Dependant — both carry `terminated_effective` and
    `attribute_values`, and the listing sync writes the same two shapes to each.
    An unparseable roster value comes back as the raw string on purpose: a cell
    that reads "end of June" is worth printing, and dropping it would make a
    leaver look like they had no last day at all.
    """
    if getattr(member, "terminated_effective", None) is not None:
        return member.terminated_effective
    return roster_date(first_value(member.attribute_values or {}, LAST_DAY_KEYS))


def has_left(member: Any) -> bool:
    """Whether the member has ACTUALLY left, not merely whether a date is on file.

    `Last Day of Service` is a column of the member-listing template, so it
    round-trips on every sync and an ACTIVE row can legitimately carry a stale
    past date (a rehire, or a date nobody cleared) — which is exactly why
    `services/adc.py` terminates only on a NEWLY stated one. Reading the date
    alone would silently cut an active employee's wallet, every price tag drawn
    against it, and (since `member_access`) their portal sign-in, with nothing
    on screen explaining why.

    Takes an Employee or a Dependant; both carry `status`.
    """
    return getattr(member, "status", None) == EMPLOYEE_STATUS_TERMINATED


def resolved_last_day(member: Any) -> date | None:
    """The same value, as a real date, or None when it cannot be one.

    The DECIDING half of the pair above: only a real date can be compared
    against a policy period or used to size a pro-rated allowance. A raw string
    is treated exactly like a missing one — unknown, and therefore conservatively
    "still here".
    """
    value = last_day_of_service(member)
    return value if isinstance(value, date) else None


def cover_end(member: Any) -> date | None:
    """The member's last covered day, or None when they have not left.

    The two above, composed in the only order that is safe to act on: a date is
    only evidence once the row says the person actually went. Everything that
    SHORTENS what a member may do — the claim window, the portal access bound —
    must read this rather than `resolved_last_day` alone, or a stale
    `Last Day of Service` on an active row starts refusing a live employee's
    claims.

    ``insurer_reports.benefit_window`` deliberately does NOT use it: a report
    prints what is on file, and a sheet showing a date nobody cleared is a
    prompt to clear it, not a refusal aimed at a member.
    """
    return resolved_last_day(member) if has_left(member) else None


def iso_date(raw: object | None) -> str | None:
    """Normalize a date-ish value to an ISO ``YYYY-MM-DD`` string for display.

    Excel cells arrive as ``datetime`` objects and are stored stringified with a
    midnight time tail (``"1958-02-19 00:00:00"``); this strips it. Accepts
    ``date``/``datetime`` objects and the common roster string formats.
    Unrecognized values are returned stripped (never dropped, never raises).
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def parse_dob(raw: object | None) -> date | None:
    """Parse a roster date-of-birth into a ``date`` (None when unparseable).

    Accepts ``date``/``datetime`` objects and the common roster string formats,
    including the Excel ``"1958-02-19 00:00:00"`` midnight-tail form.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def age_as_of(dob: date, ref: date) -> int:
    """Completed age (last birthday) on ``ref`` — clamped to ≥ 0."""
    age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
    return max(age, 0)


def age_next_birthday_as_of(dob: date, ref: date) -> int:
    """Age next birthday (ANB) on ``ref`` — the insurance convention: completed
    age + 1. All eligibility age limits are stored in ANB terms relative to the
    renewal date, so eligibility comparisons must age members through here."""
    return age_as_of(dob, ref) + 1


def age_from_attrs(attribute_values: dict[str, Any] | None, ref: date) -> int | None:
    """Member/dependant age (last birthday) as of ``ref`` from their roster DOB,
    or None when there's no parseable DOB. Single source of truth for the
    employee + flex pricing read paths so both age the same person identically."""
    dob = parse_dob(first_value(attribute_values or {}, DOB_KEYS))
    return age_as_of(dob, ref) if dob else None


def anb_from_attrs(attribute_values: dict[str, Any] | None, ref: date) -> int | None:
    """Like ``age_from_attrs`` but returns age NEXT birthday as of ``ref`` (the
    renewal date) — for eligibility-limit comparisons and insurance rate-band
    lookups, which are quoted in ANB terms."""
    dob = parse_dob(first_value(attribute_values or {}, DOB_KEYS))
    return age_next_birthday_as_of(dob, ref) if dob else None


def band_for_age(bands: list[Any] | None, age: int | None) -> dict[str, Any] | None:
    """The first band whose ``[min, max]`` window contains ``age`` (a None bound is
    open-ended), or None. Shared by the flex price-tag age bands and the life
    voluntary-rate bands so 'which band is this member in?' is decided in ONE place
    — callers then read the band's ``label`` (matrix) or ``rate`` (premium)."""
    if age is None:
        return None
    for band in bands or []:
        if not isinstance(band, dict):
            continue
        lo, hi = band.get("min"), band.get("max")
        lo_ok = lo is None or (isinstance(lo, (int, float)) and age >= lo)
        hi_ok = hi is None or (isinstance(hi, (int, float)) and age <= hi)
        if lo_ok and hi_ok:
            return band
    return None
