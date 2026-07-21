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


def hospital_directory() -> list[dict[str, str]]:
    """Name + sector rows for the claim-form hospital picker."""
    return [
        *({"name": n, "sector": SECTOR_GOVT} for n in GOVT_HOSPITALS),
        *({"name": n, "sector": SECTOR_PRIVATE} for n in PRIVATE_HOSPITALS),
    ]
