"""Participation-cell parsing: mode, audience split, direction, and scope."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Participation-domain vocabulary only. Deliberately NOT including generic
# tokens like "y"/"n"/"c"/"v" or eligibility phrases ("all staff") — those
# appear in mis-mapped columns and would misclassify silently.
_COMPULSORY_ALIASES: frozenset[str] = frozenset(
    {"compulsory", "mandatory", "required", "comp"}
)
_VOLUNTARY_ALIASES: frozenset[str] = frozenset({"voluntary", "optional", "vol"})
# Substrings that signal compulsory/voluntary when the cell isn't an exact match.
_COMPULSORY_SUBSTRINGS: tuple[str, ...] = ("compulsory", "mandatory", "required")
_VOLUNTARY_SUBSTRINGS: tuple[str, ...] = ("voluntary", "optional")


def _participation_mode(text_lower: str) -> str | None:
    """'compulsory' | 'voluntary' | None for a lowercased participation string.

    Exact tokens and substrings both count; a string mentioning BOTH (e.g.
    "voluntary top-up to compulsory plan") is ambiguous and returns None rather
    than letting check-order silently pick one. Single source of truth shared by
    :func:`normalize_participation` and the per-clause parser.
    """
    stripped = text_lower.strip()
    has_comp = stripped in _COMPULSORY_ALIASES or any(
        sub in text_lower for sub in _COMPULSORY_SUBSTRINGS
    )
    has_vol = stripped in _VOLUNTARY_ALIASES or any(
        sub in text_lower for sub in _VOLUNTARY_SUBSTRINGS
    )
    if has_comp and has_vol:
        return None
    if has_comp:
        return "compulsory"
    if has_vol:
        return "voluntary"
    return None


def normalize_participation(raw: str | None) -> str | None:
    """Map free-text slip values → canonical 'compulsory' | 'voluntary' | None.

    A cell that scopes employees and dependants separately ("Compulsory -
    Employees / Voluntary - Dependents") is ambiguous to the whole-string check,
    but the category's binary model describes the *member* — so fall back to the
    employee-scoped clause. Genuinely ambiguous cells (both modes, no audience
    split) still return None.
    """
    if not raw:
        return None
    mode = _participation_mode(raw.strip().lower())
    if mode is not None:
        return mode
    return parse_participation(raw).employee


# En/em-dashes the slip uses interchangeably with a hyphen ("Voluntary - Downgrade").
# Windows-1252 byte 0x96 survives some .xls reads as a literal "\x96".
_DASH_CHARS = "–—\x96"  # noqa: RUF001 — these dash glyphs are the literals being normalized


def _dash_norm(text: str) -> str:
    out = text
    for ch in _DASH_CHARS:
        out = out.replace(ch, "-")
    return out


@dataclass(frozen=True)
class ParticipationSpec:
    """Structured reading of a slip Participation cell.

    The cell carries more than the binary compulsory/voluntary that
    :func:`normalize_participation` returns: a voluntary employee tier may state
    an allowed change *direction* ("Voluntary - Downgrade / Upgrade"), a single
    cell may scope employees and dependants differently ("Compulsory -
    Employees / Voluntary - Dependents"), and a mode may be qualified by a
    location/population *scope* ("Compulsory - SG Office"). Enrollment uses
    these to restrict the election dropdown to the member's own cohort tiers in
    the allowed direction.
    """

    employee: str | None  # 'compulsory' | 'voluntary' | None
    dependant: str | None  # 'compulsory' | 'voluntary' | None
    direction: str | None  # 'upgrade' | 'downgrade' | 'both' | None
    raw: str
    scope: str | None = None  # location/population qualifier, e.g. "SG Office"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "employee": self.employee,
            "dependant": self.dependant,
            "direction": self.direction,
            "raw": self.raw,
            "scope": self.scope,
        }


def _clause_mode(clause_lower: str) -> str | None:
    """Compulsory/voluntary for a single clause (already lowercased)."""
    return _participation_mode(clause_lower)


# A mode word followed by a dash-qualified tail ("Compulsory - SG Office",
# "Voluntary Flex - SG Office"). The tail is a *scope* only when it isn't an
# audience word, a direction word, or another mode word.
_SCOPE_RE = re.compile(
    r"\b(?:compulsory|voluntary)\b[^-\n;/]*-\s*([^\n;/]+)", re.IGNORECASE
)


def _extract_scope(dash_normed: str) -> str | None:
    for m in _SCOPE_RE.finditer(dash_normed):
        tail = m.group(1).strip().rstrip("-").strip()
        if not tail:
            continue
        low = tail.lower()
        if "grade" in low:  # Upgrade / Downgrade → direction, not scope
            continue
        if low.startswith(("employee", "dependant", "dependent", "spouse", "child")):
            continue
        if _participation_mode(low) is not None:
            continue
        return tail
    return None


def parse_participation(raw: str | None) -> ParticipationSpec:
    """Parse a raw Participation cell into employee/dependant modes + direction.

    Handles single-mode cells ("Compulsory"), directional voluntary tiers
    ("Voluntary - Downgrade", "Voluntary - Downgrade / Upgrade"), combined
    employee/dependant cells ("Compulsory - Employees" + "Voluntary -
    Dependents", whether newline- or slash-separated), and location-scoped
    modes ("Compulsory - SG Office"). Direction is read across the whole cell;
    mode is read per audience clause.
    """
    text = (raw or "").strip()
    if not text:
        return ParticipationSpec(None, None, None, raw or "")
    dash_normed = _dash_norm(text)
    low = dash_normed.lower()
    has_up = "upgrade" in low
    has_down = "downgrade" in low
    direction = (
        "both" if has_up and has_down
        else "upgrade" if has_up
        else "downgrade" if has_down
        else None
    )

    employee: str | None = None
    dependant: str | None = None
    # Split into audience clauses. A "/" only separates clauses when it isn't part
    # of a direction phrase ("Downgrade / Upgrade"); strip those first so the
    # remaining slashes (e.g. "Compulsory - Employees / Voluntary - Dependents")
    # split cleanly.
    direction_free = re.sub(r"\b(up|down)grade\b", "", low)
    # Cell normalization upstream collapses the newline between an employee clause
    # and a dependant clause into a space ("compulsory - employees voluntary -
    # dependents"), which the separators below can't split. Re-insert a boundary
    # before a mode word that immediately follows an audience word.
    direction_free = re.sub(
        r"\b(employees?|depend\w*)\s+(compulsory|voluntary)\b",
        r"\1;\2",
        direction_free,
    )
    for clause in re.split(r"[\n;/]+", direction_free):
        clause = clause.strip()
        if not clause:
            continue
        mode = _clause_mode(clause)
        if mode is None:
            continue
        if "depend" in clause:
            dependant = mode
        else:
            employee = mode
    # No audience word at all (e.g. bare "Voluntary") → it describes the employee.
    # Call _participation_mode directly (not normalize_participation) so the
    # ambiguity fallback in normalize_participation can't recurse back here.
    if employee is None and dependant is None:
        employee = _participation_mode(low)
    return ParticipationSpec(
        employee, dependant, direction, raw or "", scope=_extract_scope(dash_normed)
    )
