"""Panel clinic workbook parsing + the shared clinic search used by the
member portal and the broker employee-view preview.

Workbook format (the transformed panel listing export):

    Code | Name | Zone | Area | Specialty | Doctor | Address1-3 | PostalCode |
    Country | PhoneNumber | MonToFri | Saturday | Sunday | PublicHoliday |
    Latitude | Longitude | GoogleMapURL

Headers are matched case-/punctuation-insensitively, so minor supplier
variations ("Postal Code", "phone_number") still map. Only `Name` is
required per row; rows without coordinates import but are counted in the
diagnostics (they can't distance-sort, they still list and search).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook as OpenpyxlWorkbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PanelClinic, PanelListing, PolicyYearPanel
from app.models.panel_clinic import clinic_type_label
from app.schemas.panel import (
    ClinicFilters,
    ClinicOut,
    ClinicSearchOut,
    ClinicTypeFacet,
)
from app.services.excel_reader import open_workbook

# Canonical column order for exports — mirrors the import format so a
# downloaded list re-uploads unchanged.
EXPORT_HEADERS = [
    "Code",
    "Name",
    "Zone",
    "Area",
    "Specialty",
    "Doctor",
    "Address1",
    "Address2",
    "Address3",
    "PostalCode",
    "Country",
    "PhoneNumber",
    "MonToFri",
    "Saturday",
    "Sunday",
    "PublicHoliday",
    "Latitude",
    "Longitude",
    "GoogleMapURL",
]

_HEADER_FIELDS: dict[str, str] = {
    "code": "code",
    "name": "name",
    "clinicname": "name",
    "zone": "zone",
    "region": "zone",
    "area": "area",
    "specialty": "specialty",
    "speciality": "specialty",
    "doctor": "doctor",
    "address1": "address1",
    "address2": "address2",
    "address3": "address3",
    "address": "address1",
    "postalcode": "postal_code",
    "postcode": "postal_code",
    "country": "country",
    "phonenumber": "phone",
    "phone": "phone",
    "tel": "phone",
    "montofri": "mon_fri",
    "monfri": "mon_fri",
    "weekday": "mon_fri",
    "saturday": "sat",
    "sat": "sat",
    "sunday": "sun",
    "sun": "sun",
    # NOTE: deliberately no bare "ph" alias — a supplier phone column headed
    # "Ph"/"P.H." would collide and import phone numbers into the hours JSON.
    "publicholiday": "public_holiday",
    "latitude": "latitude",
    "lat": "latitude",
    "longitude": "longitude",
    "lng": "longitude",
    "long": "longitude",
    "googlemapurl": "google_map_url",
    "googlemapsurl": "google_map_url",
    "mapurl": "google_map_url",
}

_WS_RE = re.compile(r"\s+")


@dataclass
class ClinicRow:
    name: str
    code: str | None = None
    zone: str | None = None
    area: str | None = None
    specialty: str | None = None
    doctor: str | None = None
    address: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    hours: dict[str, str] | None = None
    latitude: float | None = None
    longitude: float | None = None
    google_map_url: str | None = None


@dataclass
class PanelParseResult:
    clinics: list[ClinicRow] = field(default_factory=list)
    rows_total: int = 0
    skipped_no_name: int = 0
    missing_coordinates: int = 0


class PanelParseError(ValueError):
    """Workbook is not a recognizable panel listing (no Name column)."""


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    return text or None


def _clean_postal(value: Any) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    # Numeric cells arrive as floats ("760618.0") — strip the decimal tail.
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text[:16]


def _coord(value: Any, lo: float, hi: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or not (lo <= num <= hi):
        return None
    return num


def parse_panel_workbook(path: Path | str) -> PanelParseResult:
    """Parse the first sheet of a panel listing workbook into clinic rows."""
    result = PanelParseResult()
    with open_workbook(path) as wb:
        if not wb.sheet_names:
            raise PanelParseError("Workbook has no sheets")
        sheet = wb.sheet(wb.sheet_names[0])

    if not sheet.rows:
        raise PanelParseError("Workbook is empty")

    header_row = sheet.rows[0]
    columns: dict[int, str] = {}
    for idx, cell in enumerate(header_row):
        mapped = _HEADER_FIELDS.get(_norm_header(cell))
        if mapped:
            columns[idx] = mapped
    if "name" not in columns.values():
        raise PanelParseError(
            "Could not find a 'Name' column — is this a panel clinic listing?"
        )

    for row in sheet.rows[1:]:
        values: dict[str, Any] = {}
        for idx, fieldname in columns.items():
            if idx < len(row):
                values[fieldname] = row[idx]
        if not any(v is not None and str(v).strip() for v in values.values()):
            continue  # fully blank row
        result.rows_total += 1

        name = _clean(values.get("name"))
        if name is None:
            result.skipped_no_name += 1
            continue

        latitude = _coord(values.get("latitude"), -90.0, 90.0)
        longitude = _coord(values.get("longitude"), -180.0, 180.0)
        if latitude is None or longitude is None:
            latitude = longitude = None
            result.missing_coordinates += 1

        address = ", ".join(
            part
            for part in (
                _clean(values.get("address1")),
                _clean(values.get("address2")),
                _clean(values.get("address3")),
            )
            if part
        ) or None

        hours = {
            key: text
            for key in ("mon_fri", "sat", "sun", "public_holiday")
            if (text := _clean(values.get(key)))
        }

        map_url = _clean(values.get("google_map_url"))
        if map_url is not None and not map_url.lower().startswith(("http://", "https://")):
            map_url = None
        if map_url is None and latitude is not None and longitude is not None:
            map_url = f"https://maps.google.com/?q={latitude},{longitude}"

        result.clinics.append(
            ClinicRow(
                name=name[:255],
                code=(_clean(values.get("code")) or "")[:64] or None,
                zone=(_clean(values.get("zone")) or "")[:64] or None,
                area=(_clean(values.get("area")) or "")[:64] or None,
                specialty=(_clean(values.get("specialty")) or "")[:128] or None,
                doctor=(_clean(values.get("doctor")) or "")[:255] or None,
                address=address,
                postal_code=_clean_postal(values.get("postal_code")),
                country=(_clean(values.get("country")) or "")[:64] or None,
                phone=(_clean(values.get("phone")) or "")[:128] or None,
                hours=hours or None,
                latitude=latitude,
                longitude=longitude,
                google_map_url=map_url[:512] if map_url else None,
            )
        )

    if not result.clinics:
        raise PanelParseError("No clinic rows found in the workbook")
    return result


def replace_listing_clinics(
    db: Session, listing: PanelListing, clinics: list[ClinicRow]
) -> None:
    """Replace the listing's clinics wholesale (flush only — caller commits)."""
    db.execute(delete(PanelClinic).where(PanelClinic.panel_listing_id == listing.id))
    for row in clinics:
        db.add(
            PanelClinic(
                panel_listing_id=listing.id,
                code=row.code,
                name=row.name,
                zone=row.zone,
                area=row.area,
                specialty=row.specialty,
                doctor=row.doctor,
                address=row.address,
                postal_code=row.postal_code,
                country=row.country,
                phone=row.phone,
                hours=row.hours,
                latitude=row.latitude,
                longitude=row.longitude,
                google_map_url=row.google_map_url,
            )
        )
    db.flush()


def export_listing_workbook(clinics: list[PanelClinic]) -> bytes:
    """Serialize clinics back to the canonical import format (round-trips)."""
    wb = OpenpyxlWorkbook()
    ws = wb.active
    ws.title = "Clinics"
    ws.append(EXPORT_HEADERS)
    for c in clinics:
        hours = c.hours or {}
        ws.append(
            [
                c.code,
                c.name,
                c.zone,
                c.area,
                c.specialty,
                c.doctor,
                c.address,
                None,
                None,
                c.postal_code,
                c.country,
                c.phone,
                hours.get("mon_fri"),
                hours.get("sat"),
                hours.get("sun"),
                hours.get("public_holiday"),
                c.latitude,
                c.longitude,
                c.google_map_url,
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Policy-year tagging helpers ──────────────────────────────────────────────


def carry_over_panel_tags(
    db: Session, new_year, source_policy_year_id: str | None = None
) -> int:
    """Copy panel-listing tags onto a freshly created policy year, so 'which
    panel networks does this company use' behaves like a per-company setting
    that survives renewals.

    `source_policy_year_id` names the year to copy FROM — callers that clone a
    specific year (copy-from-year) must pass it, otherwise the tags would come
    from whichever year is most recent rather than the one being cloned.
    Omit it for a plain new year, where 'most recent prior year' is right.
    Mirrors `panel_cards.carry_over_card_assignments`.

    Flush only — the caller (policy-year create) owns the commit. Returns the
    number of tags copied.
    """
    from app.models import PolicyYear  # local: avoid widening module imports

    prior_year_id = source_policy_year_id
    if prior_year_id is None:
        prior_year_id = db.execute(
            select(PolicyYear.id)
            .where(
                PolicyYear.client_id == new_year.client_id,
                PolicyYear.id != new_year.id,
                PolicyYear.start_date < new_year.start_date,
            )
            .order_by(PolicyYear.start_date.desc())
            .limit(1)
        ).scalar_one_or_none()
    if prior_year_id is None:
        return 0
    listing_ids = list(
        db.scalars(
            select(PolicyYearPanel.panel_listing_id).where(
                PolicyYearPanel.policy_year_id == prior_year_id
            )
        )
    )
    for listing_id in listing_ids:
        db.add(
            PolicyYearPanel(policy_year_id=new_year.id, panel_listing_id=listing_id)
        )
    if listing_ids:
        db.flush()
    return len(listing_ids)


# ── Clinic search (portal + broker preview share this) ───────────────────────

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def clinic_out(
    clinic: PanelClinic,
    listing: PanelListing,
    distance_km: float | None = None,
    *,
    type_label: str | None = None,
    panel_label: str | None = None,
) -> ClinicOut:
    """The one PanelClinic+PanelListing → API payload mapping (locator, broker
    preview). Pass precomputed labels when serializing many rows of one listing."""
    return ClinicOut(
        id=clinic.id,
        name=clinic.name,
        code=clinic.code,
        zone=clinic.zone,
        area=clinic.area,
        specialty=clinic.specialty,
        doctor=clinic.doctor,
        address=clinic.address,
        postal_code=clinic.postal_code,
        phone=clinic.phone,
        hours=clinic.hours,
        latitude=clinic.latitude,
        longitude=clinic.longitude,
        google_map_url=clinic.google_map_url,
        clinic_type=listing.clinic_type,
        country=listing.country,
        type_label=type_label
        or clinic_type_label(listing.country, listing.clinic_type),
        panel_label=panel_label or listing.display_label(),
        distance_km=distance_km,
    )


_Row = tuple[PanelClinic, PanelListing]


def _matches_q(clinic: PanelClinic, needle: str) -> bool:
    return any(
        needle in value.lower()
        for value in (
            clinic.name,
            clinic.area or "",
            clinic.address or "",
            clinic.postal_code or "",
            clinic.doctor or "",
        )
    )


def search_policy_year_clinics(
    db: Session,
    policy_year_id: str,
    *,
    clinic_type: str | None = None,
    country: str | None = None,
    area: str | None = None,
    q: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ClinicSearchOut:
    """Clinics from the panel listings tagged to a policy year.

    The clinic-type facet is computed over the UNfiltered tag set (the chips
    must always offer every available type); the areas facet respects the
    selected type/country so the dropdown never offers a dead-end combination.
    Distance sorting is in-process — tagged panels are bounded (hundreds to a
    few thousand rows) — and payloads are built only for the returned page.
    """
    rows: list[_Row] = list(
        db.execute(
            select(PanelClinic, PanelListing)
            .join(PanelListing, PanelClinic.panel_listing_id == PanelListing.id)
            .join(
                PolicyYearPanel,
                PolicyYearPanel.panel_listing_id == PanelListing.id,
            )
            .where(PolicyYearPanel.policy_year_id == policy_year_id)
        ).all()
    )

    type_counts: dict[tuple[str, str], int] = {}
    for _clinic, listing in rows:
        key = (listing.country, listing.clinic_type)
        type_counts[key] = type_counts.get(key, 0) + 1

    typed = [
        r
        for r in rows
        if (not clinic_type or r[1].clinic_type == clinic_type)
        and (not country or r[1].country == country)
    ]
    areas = sorted({r[0].area for r in typed if r[0].area})

    filtered = typed
    if area:
        area_lower = area.lower()
        filtered = [r for r in filtered if (r[0].area or "").lower() == area_lower]
    if q:
        needle = q.lower()
        filtered = [r for r in filtered if _matches_q(r[0], needle)]

    have_origin = lat is not None and lng is not None
    # Sort lightweight (distance, row) pairs; serialize only the page slice.
    scored: list[tuple[float | None, _Row]] = [
        (
            round(haversine_km(lat, lng, c.latitude, c.longitude), 2)
            if have_origin and c.latitude is not None and c.longitude is not None
            else None,
            (c, listing),
        )
        for c, listing in filtered
    ]
    if have_origin:
        # Nearest first; clinics without coordinates sink to the end (still
        # alphabetical there).
        scored.sort(key=lambda s: (s[0] is None, s[0] or 0.0, s[1][0].name))
    else:
        scored.sort(key=lambda s: s[1][0].name)

    labels: dict[str, tuple[str, str]] = {}
    for _c, listing in filtered:
        if listing.id not in labels:
            labels[listing.id] = (
                clinic_type_label(listing.country, listing.clinic_type),
                listing.display_label(),
            )

    page = scored[offset : offset + limit]
    return ClinicSearchOut(
        total=len(scored),
        offset=offset,
        limit=limit,
        located=have_origin,
        items=[
            clinic_out(
                clinic,
                listing,
                distance,
                type_label=labels[listing.id][0],
                panel_label=labels[listing.id][1],
            )
            for distance, (clinic, listing) in page
        ],
        filters=ClinicFilters(
            clinic_types=[
                ClinicTypeFacet(
                    clinic_type=ct,
                    country=cc,
                    label=clinic_type_label(cc, ct),
                    count=count,
                )
                for (cc, ct), count in sorted(type_counts.items())
            ],
            areas=areas,
        ),
    )
