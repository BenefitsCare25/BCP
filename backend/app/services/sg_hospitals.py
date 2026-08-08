"""Singapore hospital registry — GRH (government/restructured) vs private.

Drives the inpatient document requirements: a Hospitalisation/Day Surgery
claim at a government hospital needs a Finalised Tax Invoice; a private (or
unlisted/overseas) hospital needs the full private set (summary + itemised
tax invoices + discharge summary). Broker-specified lists, 2026-07-21 —
bundled in-code like the diagnosis catalog (`sg_diagnoses.py`).
"""
from __future__ import annotations

SECTOR_GOVT = "govt"
SECTOR_PRIVATE = "private"

PRIVATE_HOSPITALS: tuple[str, ...] = (
    "Aptus Surgery Centre",
    "Cura Day Surgery Centre",
    "Farrer Park Hospital",
    "Gleneagles Hospital",
    "HMI Medical Centre",
    "Mount Alvernia Hospital",
    "Mount Elizabeth Novena Hospital",
    "Mount Elizabeth Orchard Hospital",
    "Novaaptus Surgery Centre",
    "Novena Surgery Centre",
    "Parkway East Hospital",
    "Raffles Hospital",
    "Solis & Luma",
    "Thomson Medical",
)

GOVT_HOSPITALS: tuple[str, ...] = (
    "Alexandra Hospital",
    "Changi General Hospital",
    "KK Women's & Children's Hospital",
    "Khoo Teck Puat Hospital",
    "National Cancer Centre Singapore",
    "National Heart Centre Singapore",
    "National University Hospital",
    "Ng Teng Fong General Hospital",
    "Sengkang General Hospital",
    "Singapore General Hospital",
    "Singapore National Eye Centre",
    "Tan Tock Seng Hospital",
    "Woodlands Hospital",
)


def _norm(name: str) -> str:
    # Fold curly apostrophes (U+2019) and ampersand spelling so a pasted or
    # previously stored name still classifies ("KK Women's and Children's
    # Hospital").
    return (
        " ".join(name.split())
        .lower()
        .replace(chr(0x2019), "'")
        .replace(" and ", " & ")
    )


_SECTOR_BY_NAME: dict[str, str] = {
    **{_norm(n): SECTOR_GOVT for n in GOVT_HOSPITALS},
    **{_norm(n): SECTOR_PRIVATE for n in PRIVATE_HOSPITALS},
}


def hospital_sector(name: str | None) -> str | None:
    """The sector of a listed hospital, or None when unlisted/overseas —
    the caller decides the unlisted default (currently: the private set)."""
    if not name:
        return None
    return _SECTOR_BY_NAME.get(_norm(name))


# This registry's vocabulary ("govt"/"private") vs the claim column's
# (`HOSPITAL_TYPE_*`). Two spellings of one fact, mapped in ONE place.
_CLAIM_TYPE_BY_SECTOR = {
    SECTOR_GOVT: "government",
    SECTOR_PRIVATE: "private",
}


def sector_from_provider(provider_name: str | None) -> str | None:
    """The claim's hospital sector DERIVED from its provider, as a
    `HOSPITAL_TYPE_*` value. None when the provider is unlisted or overseas.

    The sector was already being derived this way by the intake autofill and by
    the review's document-completeness check — but `Claim.hospital_type`, the
    column the claims report prints, was written by nothing at all. So the
    sector existed twice: computed where it was needed, and blank where it was
    reported, with a manual dropdown as the only thing that could ever fill the
    second. `resolve_hospital_type` collapses that.
    """
    sector = hospital_sector(provider_name)
    return _CLAIM_TYPE_BY_SECTOR.get(sector) if sector else None


def resolve_hospital_type(
    stated: str | None, provider_name: str | None
) -> str | None:
    """The sector to use: what an assessor STATED, else what the provider says.

    A stored value is an OVERRIDE — for an overseas admission, a provider the
    registry doesn't list, or a derivation an assessor knows is wrong. NULL
    therefore means "derive", not "unassessed", which is why the form's blank
    option names the derived answer rather than saying nobody has looked.

    Every reader must go through this — the report, the payload the form reads,
    and anything added later. A consumer that reads the raw column sees blank
    on the ordinary case, which is the shape of the bug this replaces.
    """
    if stated:
        return stated
    return sector_from_provider(provider_name)


def hospital_directory() -> list[dict[str, str]]:
    """Name + sector rows for the claim-form hospital picker."""
    return [
        *({"name": n, "sector": SECTOR_GOVT} for n in GOVT_HOSPITALS),
        *({"name": n, "sector": SECTOR_PRIVATE} for n in PRIVATE_HOSPITALS),
    ]
