"""Tolerant access to client-defined roster / dependant attributes.

``Employee`` and ``Dependant`` store their fields in a free-form
``attribute_values`` dict whose keys are chosen per client. These tuples list
the common spellings for each logical field so every read path agrees on which
key holds a given value. Shared by the benefit statement and the fact-find form
so a new column alias only has to be added in one place.
"""
from __future__ import annotations

import re
from datetime import date, datetime

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


def first_value(values: dict, keys: tuple[str, ...]) -> str | None:
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
    attribute_values: dict | None, keys: tuple[str, ...] = EMPLOYEE_ID_KEYS
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


def is_valid_sg_nric(raw: object | None) -> bool:
    """True when ``raw`` passes the Singapore NRIC/FIN checksum.

    Used only to *warn* about likely-malformed IDs on upload — never to block a
    row (foreign/blank IDs are legitimate). Validates S/T/F/G/M-series numbers;
    anything not matching the ``<letter><7 digits><letter>`` shape is treated as
    not-an-NRIC (returns False) so the caller can decide whether to warn.
    """
    canon = normalize_nric(raw)
    if not canon or len(canon) != 9:
        return False
    prefix, digits, suffix = canon[0], canon[1:8], canon[8]
    if prefix not in "STFGM" or not digits.isdigit() or not suffix.isalpha():
        return False
    total = sum(int(d) * w for d, w in zip(digits, _NRIC_WEIGHTS, strict=True))
    if prefix in "TG":
        total += 4
    elif prefix == "M":
        total += 3
    table = next(t for k, t in _NRIC_CHECK.items() if prefix in k)
    return table[total % 11] == suffix


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d")


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


def age_from_attrs(attribute_values: dict | None, ref: date) -> int | None:
    """Member/dependant age (last birthday) as of ``ref`` from their roster DOB,
    or None when there's no parseable DOB. Single source of truth for the
    employee + flex pricing read paths so both age the same person identically."""
    dob = parse_dob(first_value(attribute_values or {}, DOB_KEYS))
    return age_as_of(dob, ref) if dob else None


def anb_from_attrs(attribute_values: dict | None, ref: date) -> int | None:
    """Like ``age_from_attrs`` but returns age NEXT birthday as of ``ref`` (the
    renewal date) — for eligibility-limit comparisons and insurance rate-band
    lookups, which are quoted in ANB terms."""
    dob = parse_dob(first_value(attribute_values or {}, DOB_KEYS))
    return age_next_birthday_as_of(dob, ref) if dob else None


def band_for_age(bands: list | None, age: int | None) -> dict | None:
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
