"""What a site shows before anyone signs in.

Unauthenticated: registered in `main.py` outside the broker gate. The answer
depends only on the host the request arrived on (`tenant_resolution`): which
broker firm it serves and how that firm's staff sign in. It names nothing a
visitor could not learn by opening the sign-in page, so it is cacheable for a
minute.

The response also carries the brand the host shows (`services/brand.py`): the
firm's, or one of its companies' when `?company=<slug>` names a company of the
HOST's firm. Slugs are unique per firm only, so a slug is never looked up in
another firm; an unknown slug answers the firm's brand. Brand images are served
from here too, but only to hosts of a firm whose brand rows reference them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entra import AUTHORITY
from app.core.identity_providers import SignInMethods, firm_sign_in_methods
from app.core.rate_limit import limiter
from app.core.settings import Settings, get_settings
from app.core.storage import StorageScopeError
from app.core.tenancy_host import normalize_slug
from app.core.tenant_resolution import FirmContext, request_firm
from app.db.session import get_db
from app.models import BrokerFirm, Client
from app.models.platform import DOMAIN_SURFACE_STAFF
from app.services.brand import (
    DEFAULT_BRAND,
    PLATFORM_NAME,
    Brand,
    readable_foreground,
    resolve_brand,
)
from app.services.brand_assets import (
    ASSET_ID,
    asset_url,
    content_type_for,
    firm_references_asset,
    read_asset,
)

router = APIRouter(prefix="/public", tags=["public"])

_CACHE_SECONDS = 60
_ASSET_MAX_AGE = 31536000
_MAX_SLUG = 63


class SiteFirm(BaseModel):
    name: str
    slug: str | None


class EntraSignIn(BaseModel):
    """What the browser needs to start Microsoft sign-in (MSAL)."""

    tenant_id: str
    client_id: str
    authority: str
    scopes: list[str]


class StaffSignIn(BaseModel):
    entra: EntraSignIn | None = None
    local: bool = False


class BrandOut(BaseModel):
    """The brand a page shows. Foregrounds are the readable text colour (black
    or white) on the primary and accent colours; image URLs are null when the
    slot is empty."""

    product_name: str
    short_name: str
    primary_color: str
    accent_color: str
    primary_foreground: str
    accent_foreground: str
    logo_url: str | None
    mark_url: str | None
    favicon_url: str | None
    support_email: str
    support_phone: str | None
    show_platform_attribution: bool
    platform_name: str


def brand_out(brand: Brand) -> BrandOut:
    """The public shape of a resolved brand (also the settings' `effective`)."""
    return BrandOut(
        product_name=brand.product_name,
        short_name=brand.short_name,
        primary_color=brand.primary_color,
        accent_color=brand.accent_color,
        primary_foreground=readable_foreground(brand.primary_color),
        accent_foreground=readable_foreground(brand.accent_color),
        logo_url=asset_url(brand.assets.get("logo")),
        mark_url=asset_url(brand.assets.get("mark")),
        favicon_url=asset_url(brand.assets.get("favicon")),
        support_email=brand.support_email,
        support_phone=brand.support_phone,
        show_platform_attribution=brand.show_platform_attribution,
        platform_name=PLATFORM_NAME,
    )


class SiteOut(BaseModel):
    firm: SiteFirm | None = None
    staff_sign_in: StaffSignIn = Field(default_factory=StaffSignIn)
    brand: BrandOut = Field(default_factory=lambda: brand_out(DEFAULT_BRAND))


def _api_scope(settings: Settings) -> str:
    """The delegated scope the SPA requests: on the API's Application ID URI,
    which is `api://<client id>` unless the audience is itself a URI."""
    audience = settings.entra_audience
    resource = audience if "://" in audience else f"api://{settings.entra_client_id}"
    return f"{resource.rstrip('/')}/access_as_user"


def _staff_sign_in(methods: SignInMethods, settings: Settings) -> StaffSignIn:
    # Mock mode (development) offers nothing: the SPA then signs in as the
    # demo user. The password endpoints still answer there.
    if settings.auth_mode != "entra":
        return StaffSignIn()
    entra = None
    if methods.entra is not None and settings.entra_client_id:
        entra = EntraSignIn(
            tenant_id=methods.entra.tenant_id,
            client_id=settings.entra_client_id,
            authority=f"{AUTHORITY}/{methods.entra.tenant_id}",
            scopes=["openid", "profile", "email", _api_scope(settings)],
        )
    return StaffSignIn(entra=entra, local=methods.local)


def _company_id(db: Session, firm_id: str, slug: str | None) -> str | None:
    """The company of `firm_id` with this slug, if any (never another firm's)."""
    slug = normalize_slug(slug or "")
    if not slug or len(slug) > _MAX_SLUG:
        return None
    return db.scalars(
        select(Client.id).where(Client.broker_firm_id == firm_id, Client.slug == slug)
    ).first()


def _site(db: Session, ctx: FirmContext | None, company: str | None) -> SiteOut:
    firm = db.get(BrokerFirm, ctx.firm_id) if ctx is not None else None
    if ctx is None or firm is None:
        return SiteOut()
    staff = (
        _staff_sign_in(firm_sign_in_methods(db, firm.id), get_settings())
        if ctx.serves(DOMAIN_SURFACE_STAFF)
        else StaffSignIn()
    )
    brand = resolve_brand(db, firm.id, _company_id(db, firm.id, company))
    return SiteOut(
        firm=SiteFirm(name=firm.name, slug=firm.slug),
        staff_sign_in=staff,
        brand=brand_out(brand),
    )


@router.get("/site", response_model=SiteOut)
@limiter.limit("60/minute")
def get_site(
    request: Request,
    response: Response,
    company: str | None = Query(None),
    db: Session = Depends(get_db),
) -> SiteOut:
    """The firm this host serves, the staff sign-in methods it offers and the
    brand it shows (a company's, for `company=<slug>` of this firm).

    A host that names no firm, or does not serve the staff app, offers no staff
    sign-in; nor does mock mode. A host that names no firm shows the default
    brand.
    """
    response.headers["Cache-Control"] = f"public, max-age={_CACHE_SECONDS}"
    return _site(db, request_firm(request), company)


@router.get("/brand-assets/{asset_id}", include_in_schema=False)
@limiter.limit("120/minute")
def get_brand_asset(
    request: Request, asset_id: str, db: Session = Depends(get_db)
) -> Response:
    """One brand image of the host's firm. Ids are content hashes, so the bytes
    behind a URL never change and may be cached forever. An image this host's
    firm does not use is a 404, whichever other firm uses it."""
    ctx = request_firm(request)
    if (
        ctx is None
        or not ASSET_ID.fullmatch(asset_id)
        or not firm_references_asset(db, ctx.firm_id, asset_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        data = read_asset(ctx.firm_id, asset_id)
    except (FileNotFoundError, StorageScopeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from None
    return Response(
        content=data,
        media_type=content_type_for(asset_id),
        headers={
            "Cache-Control": f"public, max-age={_ASSET_MAX_AGE}, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
