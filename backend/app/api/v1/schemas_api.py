"""Layer 2 schema endpoints — employee attributes + products."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    can_write_global,
    load_editable_global,
    require_client_id,
    tenant_or_global,
)
from app.db.session import get_db
from app.models import EmployeeAttributeSchema, Product
from app.schemas.api import (
    AttributeSchemaCreate,
    AttributeSchemaOut,
    AttributeSchemaPatch,
    ProductCreate,
    ProductOut,
    ProductPatch,
)
from app.services import product_registry
from app.services.form_profiles import infer_profile
from app.services.matching_engine import insured_names

router = APIRouter(tags=["schemas"])

# Where a create lands: "company" = the caller's active client (default);
# "firm" = a shared firm-library default (client_id NULL) visible to every
# company, which only firm admins may write.
CatalogScope = Literal["company", "firm"]


def _resolve_create_client_id(scope: CatalogScope, user: CurrentUser) -> str | None:
    """The client_id a create should target for the requested scope.

    "firm" → NULL (firm library), admins only. "company" → the active client.
    """
    if scope == "firm":
        if not can_write_global(user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only admins can add firm-library defaults.",
            )
        return None
    return require_client_id(user)

# ProductPatch fields that live in product_metadata (classification overrides
# + insurer-report display code), not as Product columns.
_METADATA_PATCH_KEYS = (
    "line",
    "form_profile",
    "layout_family",
    "report_code",
    "entities",
)


def _product_out(p: Product) -> ProductOut:
    """Serialize a Product, including its computed classification."""
    meta = p.product_metadata or {}
    entry = product_registry.resolve_entry(p.code, meta)
    return ProductOut(
        id=p.id,
        client_id=p.client_id,
        code=p.code,
        display_name=p.display_name,
        insurer=p.insurer,
        participation_model=p.participation_model,
        has_dependants=p.has_dependants,
        is_outpatient=p.is_outpatient,
        line=p.line,
        form_profile=infer_profile(p.code, meta.get("form_profile")),
        layout_family=entry.layout_family,
        report_code=meta.get("report_code"),
        entities=insured_names(meta.get("entities")),
    )


def _load_editable_attribute(
    schema_id: str, user: CurrentUser, db: Session
) -> EmployeeAttributeSchema:
    return load_editable_global(
        EmployeeAttributeSchema, schema_id, user, db, "Attribute"
    )


def _load_editable_product(product_id: str, user: CurrentUser, db: Session) -> Product:
    return load_editable_global(Product, product_id, user, db, "Product")


@router.get("/schemas/employee-attributes", response_model=list[AttributeSchemaOut])
def list_employee_attributes(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EmployeeAttributeSchema]:
    return list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, user.client_id)
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/schemas/employee-attributes",
    response_model=AttributeSchemaOut,
    status_code=status.HTTP_201_CREATED,
)
def create_employee_attribute(
    payload: AttributeSchemaCreate,
    scope: CatalogScope = "company",
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmployeeAttributeSchema:
    client_id = _resolve_create_client_id(scope, user)
    existing = (
        db.execute(
            select(EmployeeAttributeSchema).where(
                EmployeeAttributeSchema.client_id.is_(None)
                if client_id is None
                else EmployeeAttributeSchema.client_id == client_id,
                EmployeeAttributeSchema.attribute_id == payload.attribute_id,
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing:
        where = "the firm library" if client_id is None else "this company"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Attribute {payload.attribute_id!r} already exists in {where}",
        )
    row = EmployeeAttributeSchema(client_id=client_id, **payload.model_dump())
    db.add(row)
    db.flush()
    write_audit(
        db,
        user,
        action="create",
        entity_type="employee_attribute_schema",
        entity_id=row.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch(
    "/schemas/employee-attributes/{schema_id}",
    response_model=AttributeSchemaOut,
)
def update_employee_attribute(
    schema_id: str,
    payload: AttributeSchemaPatch,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmployeeAttributeSchema:
    row = _load_editable_attribute(schema_id, user, db)
    before = {
        "display_name": row.display_name,
        "data_type": row.data_type,
        "enum_values": row.enum_values,
        "is_required": row.is_required,
        "is_pii": row.is_pii,
        "description": row.description,
    }
    patch = payload.model_dump(exclude_unset=True)
    for key, value in patch.items():
        setattr(row, key, value)
    db.flush()
    write_audit(
        db,
        user,
        action="update",
        entity_type="employee_attribute_schema",
        entity_id=row.id,
        before=before,
        after=patch,
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete(
    "/schemas/employee-attributes/{schema_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_employee_attribute(
    schema_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    row = _load_editable_attribute(schema_id, user, db)
    snapshot = {
        "attribute_id": row.attribute_id,
        "display_name": row.display_name,
        "data_type": row.data_type,
    }
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="employee_attribute_schema",
        entity_id=schema_id,
        before=snapshot,
    )
    db.commit()


@router.get("/schemas/products", response_model=list[ProductOut])
def list_products(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProductOut]:
    rows = (
        db.execute(
            select(Product).where(tenant_or_global(Product.client_id, user.client_id))
        )
        .scalars()
        .all()
    )
    return [_product_out(p) for p in rows]


@router.post(
    "/schemas/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    payload: ProductCreate,
    scope: CatalogScope = "company",
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductOut:
    client_id = _resolve_create_client_id(scope, user)
    existing = db.execute(
        select(Product).where(
            Product.client_id.is_(None)
            if client_id is None
            else Product.client_id == client_id,
            Product.code == payload.code,
        )
    ).scalar_one_or_none()
    if existing is not None:
        where = "the firm library" if client_id is None else "this company"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Product {payload.code!r} already exists in {where}.",
        )
    # Mostly str values; `entities` is a token list (see ProductCreate).
    metadata: dict[str, object] = {}
    if payload.line:
        metadata["line"] = payload.line
    if payload.form_profile:
        metadata["form_profile"] = payload.form_profile
    if payload.layout_family:
        metadata["layout_family"] = payload.layout_family
    if payload.report_code:
        metadata["report_code"] = payload.report_code
    if payload.entities:
        metadata["entities"] = insured_names(payload.entities)
    row = Product(
        client_id=client_id,
        code=payload.code,
        display_name=payload.display_name,
        participation_model=payload.participation_model,
        has_dependants=payload.has_dependants,
        is_outpatient=payload.is_outpatient,
        product_metadata=metadata or None,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user,
        action="create",
        entity_type="product",
        entity_id=row.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(row)
    return _product_out(row)


@router.patch("/schemas/products/{product_id}", response_model=ProductOut)
def update_product(
    product_id: str,
    payload: ProductPatch,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductOut:
    row = _load_editable_product(product_id, user, db)
    patch = payload.model_dump(exclude_unset=True)
    before: dict[str, object] = {}
    # Classification fields ride product_metadata, not columns. An explicit
    # null clears the override (falls back to the registry inference).
    meta_patch = {k: patch.pop(k) for k in _METADATA_PATCH_KEYS if k in patch}
    if meta_patch:
        before["product_metadata"] = dict(row.product_metadata or {})
        metadata = dict(row.product_metadata or {})
        for key, value in meta_patch.items():
            if key == "entities":
                # Token list; an explicit [] lifts the restriction entirely.
                value = insured_names(value) or None
            if value is None:
                metadata.pop(key, None)
            else:
                metadata[key] = value
        row.product_metadata = metadata or None
    for key, value in patch.items():
        existing = getattr(row, key)
        before[key] = existing.value if hasattr(existing, "value") else existing
        setattr(row, key, value)
    db.flush()
    write_audit(
        db,
        user,
        action="update",
        entity_type="product",
        entity_id=row.id,
        before=before,
        after=patch,
    )
    db.commit()
    db.refresh(row)
    return _product_out(row)


@router.delete(
    "/schemas/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_product(
    product_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    row = _load_editable_product(product_id, user, db)
    snapshot = {
        "code": row.code,
        "display_name": row.display_name,
    }
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="product",
        entity_id=product_id,
        before=snapshot,
    )
    db.commit()
