"""Report version retention + movement diffs.

Persists a generated Reports Center document as an immutable (versioned) or
supersede-in-place (latest) record — bytes in the storage backend, metadata +
membership manifest in ``report_versions``. See ``report_registry.py`` for the
per-report classification and ``models/report_version.py`` for the row.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.core.pagination import MAX_LIMIT
from app.core.storage import (
    MAX_REPORT_BYTES,
    REPORT_SUFFIXES,
    document_path,
    get_storage,
)
from app.db.base import new_uuid
from app.models import (
    Category,
    Dependant,
    Employee,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    Plan,
    PolicyYear,
)
from app.models.report_version import (
    MODE_LATEST,
    ReportVersion,
)
from app.services.insurer_listings import membership_manifest
from app.services.insurer_reports import append_safe, autosize
from app.services.report_registry import (
    ReportSpec,
    build_report_bytes,
    mime_for,
    scope_key_for,
    spec_for,
)

# Tables whose changes make a report stale, beyond the shared roster + config
# set (Employee/Dependant/Category/Plan). Keyed by report_type.
_EXTRA_STALENESS_MODELS = {
    "benefit_selection": (
        Enrollment,
        EnrollmentElection,
        LeaveElection,
        EnrollmentWindow,
    ),
}


class ReportTooLargeError(Exception):
    """The generated report exceeds MAX_REPORT_BYTES."""


def _manifest_hash(manifest: dict | None) -> str:
    members = sorted((manifest or {}).get("members", []), key=lambda m: m["key"])
    payload = json.dumps(members, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _listing_signature(manifest: dict, masked: bool) -> str:
    """Dedup key for the insurer listings: the membership hash plus the masking
    choice. Masking is part of the signature because the manifest itself carries
    no NRIC — without it a masked and an unmasked save would collapse into one
    version and the broker could never retain the unmasked submission."""
    return f"{_manifest_hash(manifest)}:m{int(masked)}"


# The only OOXML package part openpyxl / python-docx stamp with a write
# timestamp (dcterms:created/modified); everything else serialises
# deterministically for identical data. Excluding it makes the package content
# a stable fingerprint.
_VOLATILE_OOXML = frozenset({"docProps/core.xml"})


def _content_signature(spec: ReportSpec, blob_bytes: bytes) -> str | None:
    """A stable, data-only fingerprint of a non-listing artifact, used to skip a
    no-op "save". Hashes the package's data parts directly — deterministic for
    identical data once the volatile ``docProps/core.xml`` timestamp is excluded
    — which is stable AND avoids re-parsing the freshly built document (openpyxl
    ``load_workbook``) on the save/download hot path. Masking is captured
    naturally (the masked NRIC lives in the cells). Returns None when no stable
    signature can be computed."""
    if spec.fmt not in ("xlsx", "docx"):
        return None
    try:
        with zipfile.ZipFile(BytesIO(blob_bytes)) as z:
            # Hash every package part except the volatile timestamp, in a stable
            # order — deterministic for identical data from both openpyxl (xlsx)
            # and python-docx (docx), with no workbook re-parse. Hashing ALL
            # parts (not just word/document.xml) is what makes docx correct: a
            # change confined to a header/footer, embedded numbering/styles, or
            # an image part still moves the fingerprint, so the no-op guard can't
            # silently drop a genuinely new version.
            h = hashlib.sha256()
            for name in sorted(z.namelist()):
                if name in _VOLATILE_OOXML:
                    continue
                h.update(name.encode("utf-8"))
                h.update(b"\x00")
                h.update(z.read(name))
            return h.hexdigest()
    except (KeyError, zipfile.BadZipFile):
        return None


def _summary(spec: ReportSpec, manifest: dict | None, params: dict) -> dict:
    out: dict = {"masked": bool(params.get("masked", True))}
    if manifest is not None:
        members = manifest.get("members", [])
        out["member_count"] = len(members)
        out["employee_count"] = sum(1 for m in members if m["role"] == "employee")
        out["dependant_count"] = sum(1 for m in members if m["role"] == "dependant")
        out["manifest_hash"] = _manifest_hash(manifest)
    return out


def _default_filename(
    report_type: str, scope_key: str | None, version_no: int, fmt: str
) -> str:
    stem = report_type.replace("_", "-")
    if scope_key:
        safe = "".join(c if c.isalnum() else "-" for c in scope_key).strip("-")
        stem = f"{stem}-{safe}"
    return f"{stem}-v{version_no}.{fmt}"


def latest_version(
    db: Session, py: PolicyYear, report_type: str, scope_key: str | None
) -> ReportVersion | None:
    return db.execute(
        select(ReportVersion)
        .where(
            ReportVersion.client_id == py.client_id,
            ReportVersion.policy_year_id == py.id,
            ReportVersion.report_type == report_type,
            ReportVersion.scope_key.is_(scope_key)
            if scope_key is None
            else ReportVersion.scope_key == scope_key,
        )
        .order_by(ReportVersion.version_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_versions(
    db: Session, py: PolicyYear, report_type: str, scope_key: str | None
) -> list[ReportVersion]:
    stmt = (
        select(ReportVersion)
        .where(
            ReportVersion.client_id == py.client_id,
            ReportVersion.policy_year_id == py.id,
            ReportVersion.report_type == report_type,
        )
        .order_by(ReportVersion.version_no.desc())
        .limit(MAX_LIMIT)
    )
    if scope_key is not None:
        stmt = stmt.where(ReportVersion.scope_key == scope_key)
    return list(db.execute(stmt).scalars().all())


def previous_version(db: Session, rv: ReportVersion) -> ReportVersion | None:
    """The version immediately below ``rv`` in its series (default movement
    baseline = "since the last submission")."""
    return db.execute(
        select(ReportVersion)
        .where(
            ReportVersion.client_id == rv.client_id,
            ReportVersion.policy_year_id == rv.policy_year_id,
            ReportVersion.report_type == rv.report_type,
            ReportVersion.scope_key.is_(rv.scope_key)
            if rv.scope_key is None
            else ReportVersion.scope_key == rv.scope_key,
            ReportVersion.version_no < rv.version_no,
        )
        .order_by(ReportVersion.version_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def create_version(
    db: Session,
    user: CurrentUser,
    py: PolicyYear,
    report_type: str,
    params: dict,
    label: str | None = None,
) -> tuple[ReportVersion, bool]:
    """Generate the report, retain the bytes, and record a version row. Returns
    ``(version, created)`` — ``created`` is False when the content is identical
    to the latest version (no-op guard), in which case the existing version is
    returned unchanged.

    Versioned reports append a new ``version_no``; latest-mode reports supersede
    the prior row + blob. Caller owns the audit write + commit.
    """
    spec = spec_for(report_type)
    # Enforce the retained-report format allowlist (defense in depth: a report
    # spec must produce a known document type, never an arbitrary blob).
    if f".{spec.fmt}" not in REPORT_SUFFIXES:
        raise ValueError(
            f"Report format {spec.fmt!r} is not an allowed retained-report type "
            f"({sorted(REPORT_SUFFIXES)})."
        )
    scope_key = scope_key_for(spec, params)
    masked = bool(params.get("masked", True))
    prior = latest_version(db, py, report_type, scope_key)
    prior_hash = (prior.summary or {}).get("content_hash") if prior else None

    manifest = (
        membership_manifest(db, py, params["insurer"]) if spec.has_movement else None
    )

    # No-op guard: if nothing changed since the last saved version, don't create
    # a duplicate (stops "save" spam from piling up identical rows). For listings
    # the manifest (+ masking) settles it WITHOUT building the workbook; other
    # reports need the bytes to fingerprint.
    manifest_sig = _listing_signature(manifest, masked) if manifest is not None else None
    if manifest_sig is not None and manifest_sig == prior_hash:
        return prior, False

    blob_bytes = build_report_bytes(db, py, report_type, params)
    if len(blob_bytes) > MAX_REPORT_BYTES:
        raise ReportTooLargeError(
            f"Report is {len(blob_bytes)} bytes (max {MAX_REPORT_BYTES})."
        )

    content_hash = manifest_sig or _content_signature(spec, blob_bytes)
    if content_hash is not None and content_hash == prior_hash:
        return prior, False

    next_no = (prior.version_no + 1) if prior else 1
    version_id = new_uuid()
    # Path segment must be filesystem-safe (no ':' on Windows, no separators);
    # the raw scope_key is preserved on the row, this is only the blob folder.
    scope_seg = (
        "".join(c if c.isalnum() else "-" for c in scope_key).strip("-")
        if scope_key
        else "_"
    )
    path = document_path(
        user.broker_firm_id,
        py.client_id,
        "report_version",
        f"{report_type}-{scope_seg}",
        version_id,
        f".{spec.fmt}",
    )
    saved = get_storage().save(BytesIO(blob_bytes), path)

    # Latest-mode keeps a single retained copy: drop the prior blob + row.
    if spec.mode == MODE_LATEST and prior is not None:
        get_storage().delete(prior.storage_path)
        db.delete(prior)

    summary = _summary(spec, manifest, params)
    if content_hash is not None:
        summary["content_hash"] = content_hash
    rv = ReportVersion(
        id=version_id,
        client_id=py.client_id,
        policy_year_id=py.id,
        report_type=report_type,
        scope_key=scope_key,
        version_no=next_no,
        mode=spec.mode,
        label=label,
        params=dict(params),
        summary=summary,
        manifest=manifest,
        file_name=_default_filename(report_type, scope_key, next_no, spec.fmt),
        mime_type=mime_for(spec.fmt),
        size_bytes=saved.size_bytes,
        sha256=saved.sha256,
        storage_path=saved.path,
        generated_by_user_id=user.user_id,
    )
    db.add(rv)
    db.flush()
    return rv, True


def load_version_blob(rv: ReportVersion) -> bytes:
    return get_storage().read(rv.storage_path)


def _max_data_change(db: Session, py: PolicyYear, extra_models=()):
    """Newest ``updated_at`` across the roster + config rows that feed the
    reports (plus any report-specific ``extra_models``). ADC inserts (new
    ``created_at`` == ``updated_at``) and soft-terminations (status/
    terminated_effective bump ``updated_at``) both move this, so it is a cheap
    gate for "did anything change since the version".
    """
    times = []
    for model in (Employee, Dependant, Category, Plan, *extra_models):
        t = db.execute(
            select(func.max(model.updated_at)).where(model.policy_year_id == py.id)
        ).scalar()
        if t is not None:
            times.append(t)
    return max(times) if times else None


def is_stale(db: Session, py: PolicyYear, rv: ReportVersion) -> bool:
    """True when live data drifted from the retained version, so a new one is
    due. Cheap ``max(updated_at)`` gate, confirmed for the insurer listings by
    re-hashing the membership manifest (precise incl. no-net-change edits)."""
    extra = _EXTRA_STALENESS_MODELS.get(rv.report_type, ())
    latest_change = _max_data_change(db, py, extra)
    if latest_change is None or latest_change <= rv.created_at:
        return False
    spec = spec_for(rv.report_type)
    stored_hash = (rv.summary or {}).get("manifest_hash")
    if spec.has_movement and stored_hash:
        current = membership_manifest(db, py, (rv.params or {}).get("insurer") or "")
        return _manifest_hash(current) != stored_hash
    return True


def report_status(
    db: Session, py: PolicyYear, report_type: str, scope_key: str | None
) -> dict:
    """Drive the UI: the latest retained version (if any) + whether it is stale."""
    spec = spec_for(report_type)
    rv = latest_version(db, py, report_type, scope_key)
    if rv is None:
        return {"latest": None, "is_stale": False, "has_movement": spec.has_movement}
    return {
        "latest": version_out(rv),
        "is_stale": is_stale(db, py, rv),
        "has_movement": spec.has_movement,
    }


def version_out(rv: ReportVersion) -> dict:
    return {
        "id": rv.id,
        "report_type": rv.report_type,
        "scope_key": rv.scope_key,
        "version_no": rv.version_no,
        "mode": rv.mode,
        "label": rv.label,
        "file_name": rv.file_name,
        "size_bytes": rv.size_bytes,
        "summary": rv.summary,
        "generated_by_user_id": rv.generated_by_user_id,
        "created_at": rv.created_at.isoformat() if rv.created_at else None,
    }


# ── Movement report (insurer listings) ──────────────────────────────────────

_TERMINATED = "terminated"
_CHANGE_FIELDS = ("member_id", "relationship")


def _newly_terminated(old: dict, new: dict) -> bool:
    """A member that stayed on the listing but just went terminated — a mid-year
    leaver reads as a DELETION on the movement report, not a field change (they
    remain on the full listing for the insurer to off-bill)."""
    return new.get("status") == _TERMINATED and old.get("status") != _TERMINATED


def _member_diffs(old: dict, new: dict) -> list[str]:
    diffs = []
    for f in _CHANGE_FIELDS:
        if old.get(f) != new.get(f):
            diffs.append(f"{f}: {old.get(f) or '—'} → {new.get(f) or '—'}")
    if old.get("coverage") != new.get("coverage"):
        diffs.append("coverage changed")
    return diffs


def _baseline_and_target(
    db: Session, rv: ReportVersion, since: ReportVersion | str | None
) -> tuple[list[dict], list[dict], str, str]:
    """Resolve (old_members, new_members, old_label, new_label) for a movement.

    ``since`` is a prior ReportVersion → diff(since → rv); the string ``"live"``
    → diff(rv → current roster) ("changes since this version"); ``None`` → diff
    from an empty baseline → rv (initial submission, everything is an addition).
    """
    insurer = (rv.params or {}).get("insurer") or ""
    target = (rv.manifest or {}).get("members", [])
    if since == "live":
        py = db.get(PolicyYear, rv.policy_year_id)
        live = membership_manifest(db, py, insurer).get("members", []) if py else []
        return target, live, f"v{rv.version_no}", "current roster"
    if since is None:
        return [], target, "initial", f"v{rv.version_no}"
    prior = (since.manifest or {}).get("members", [])
    return prior, target, f"v{since.version_no}", f"v{rv.version_no}"


def compute_movement(
    db: Session, rv: ReportVersion, since: ReportVersion | str | None
) -> Workbook:
    """Movement (adds/deletions/changes) of ``rv`` versus a baseline (see
    ``_baseline_and_target`` for the ``since`` semantics)."""
    insurer = (rv.params or {}).get("insurer") or ""
    old_members, new_members, old_label, new_label = _baseline_and_target(db, rv, since)

    old_by = {m["key"]: m for m in old_members}
    new_by = {m["key"]: m for m in new_members}
    common = old_by.keys() & new_by.keys()
    newly_terminated = {k for k in common if _newly_terminated(old_by[k], new_by[k])}

    additions = [new_by[k] for k in new_by.keys() - old_by.keys()]
    # Gone from the listing, plus mid-year leavers (still listed but now
    # terminated) — both are removals from the insurer's active cover.
    deletions = [old_by[k] for k in old_by.keys() - new_by.keys()]
    deletions += [new_by[k] for k in newly_terminated]
    changes = [
        (new_by[k], _member_diffs(old_by[k], new_by[k]))
        for k in common - newly_terminated
        if _member_diffs(old_by[k], new_by[k])
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Movement"
    append_safe(ws, [f"Movement report — {insurer or 'insurer'} ({old_label} → {new_label})"])
    append_safe(ws, [])

    def _section(title: str, rows: list, cols: list[str], to_row) -> None:
        append_safe(ws, [f"{title} ({len(rows)})"])
        append_safe(ws, cols)
        # Bold this section's column-header row (the one just written), not row 1.
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
        for r in rows:
            append_safe(ws, to_row(r))
        append_safe(ws, [])

    base_cols = ["Role", "Staff ID", "Name", "Member ID", "Relationship", "Status"]
    _section(
        "ADDITIONS", additions,
        [*base_cols, "Effective"],
        lambda m: [
            m["role"], m.get("staff_id") or "", m.get("name") or "",
            m.get("member_id") or "", m.get("relationship") or "",
            m.get("status") or "", "",
        ],
    )
    _section(
        "DELETIONS", deletions,
        [*base_cols, "Deletion Date"],
        lambda m: [
            m["role"], m.get("staff_id") or "", m.get("name") or "",
            m.get("member_id") or "", m.get("relationship") or "",
            m.get("status") or "", m.get("terminated_effective") or "",
        ],
    )
    _section(
        "CHANGES", changes,
        [*base_cols[:4], "Changed"],
        lambda pair: [
            pair[0]["role"], pair[0].get("staff_id") or "", pair[0].get("name") or "",
            pair[0].get("member_id") or "", "; ".join(pair[1]),
        ],
    )
    autosize(ws)
    return wb


def movement_summary(
    db: Session, rv: ReportVersion, since: ReportVersion | str | None
) -> dict:
    """Counts only (for the staleness banner) — same diff as compute_movement."""
    old_members, new_members, _ol, _nl = _baseline_and_target(db, rv, since)
    old_by = {m["key"]: m for m in old_members}
    new_by = {m["key"]: m for m in new_members}
    common = old_by.keys() & new_by.keys()
    newly_terminated = {k for k in common if _newly_terminated(old_by[k], new_by[k])}
    changed = sum(
        1 for k in common - newly_terminated if _member_diffs(old_by[k], new_by[k])
    )
    return {
        "added": len(new_by.keys() - old_by.keys()),
        "removed": len(old_by.keys() - new_by.keys()) + len(newly_terminated),
        "changed": changed,
    }
