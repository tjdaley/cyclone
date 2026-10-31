"""
app/routers/guest.py - Financial-professional access (052).

Two routers in one file, deliberately apart:

* ``staff_router`` — under ``/api/v1/matters/{id}/guests``: staff invite a
  professional, attest the client's consent, renew, change the designation,
  revoke, and read what the guest was provided. Staff roles only.
* ``router`` — under ``/api/v1/guest``: everything the guest can do. Every
  endpoint takes ``require_guest`` (who) and resolves scope through
  ``guest_access_service`` (which matters), and answers 404 for anything
  outside it.

**The financial_professional role appears in no staff role list.** Staff routes
are not matter-scoped; ``tests/test_guest_access.py`` enforces the separation.
"""
import threading
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from db.repositories.financial import (
    FinancialAccountTransactionRepository,
    TransactionCategoryRepository,
)
from db.repositories.matter import MatterRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_guest, require_role
from schemas.common import MessageResponse
from schemas.financial import (
    ExhibitExportRequest,
    InventoryReport,
    StatementPdfUrlResponse,
    TransactionExportRequest,
    TransactionSearchRequest,
)
from schemas.guest import (
    GuestAccount,
    GuestCategory,
    GuestConsentRequest,
    GuestConsentResponse,
    GuestDesignationRequest,
    GuestGrantResponse,
    GuestInviteRequest,
    GuestLedgerEntryResponse,
    GuestMatter,
    GuestRenewRequest,
    GuestRevokeRequest,
    GuestStatement,
    GuestTag,
    GuestTagApplyRequest,
    GuestTagWriteRequest,
    GuestTransaction,
    GuestTransactionPage,
)
from services import exhibit_service
from services.audit_logger import AuditLogger
from services.guest_access_service import (
    PURPOSE_REMINDER,
    GuestConflict,
    GuestDenied,
    guest_access_service,
)
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

_STAFF_ROLES = ["attorney", "admin", "paralegal"]

staff_router = APIRouter(prefix="/api/v1/matters", tags=["guest access (staff)"])
router = APIRouter(prefix="/api/v1/guest", tags=["guest"])

_NO_ACCESS = "Not found"


def _staff(request: Request, manager: Any):
    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")
    name = " ".join(p for p in (staff.name.first_name, staff.name.last_name) if p).strip()
    return staff, name or "A staff member"


def _send_mail(message: dict[str, str]) -> None:
    """
    Send one email, logging rather than raising: the consent stands either way.

    Run on its own thread by ``_send_mail_later``, NOT as a FastAPI background
    task. ``AuthMiddleware`` is a ``BaseHTTPMiddleware``, and under it a
    background task can hold the response open until it finishes — so an SMTP
    server slow to answer (up to ``mail_timeout_seconds``) kept the consent form
    spinning after the consent was already saved. Found in testing, 2026-10-31.
    """
    from services.email_service import email_service

    try:
        email_service.send(message["to"], message["subject"], message["body"])
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("guest.activation_email: not sent (%s)", type(e).__name__)


def _send_mail_later(message: dict[str, str]) -> None:
    """Hand the email to a daemon thread so the request returns at once."""
    threading.Thread(target=_send_mail, args=(message,), daemon=True,
                     name="guest-activation-mail").start()


def _grant_response(item: dict[str, Any]) -> GuestGrantResponse:
    grant, guest = item["grant"], item["guest"]
    return GuestGrantResponse(
        id=grant.id, guest_id=grant.guest_id,
        full_name=guest.full_name if guest else "—", email=guest.email if guest else "",
        organization=guest.organization if guest else None,
        profession=guest.profession if guest else None,
        designation=grant.designation, purpose=grant.purpose,
        status=item["effective_status"], expires_at=grant.expires_at,
        requested_by=grant.requested_by, activated_at=grant.activated_at,
        revoked_at=grant.revoked_at, revoked_by=grant.revoked_by,
        revoke_reason=grant.revoke_reason,
        consents=[GuestConsentResponse(method=c.method, consent_date=c.consent_date, note=c.note,
                                       attested_by=c.attested_by, created_at=c.created_at)
                  for c in item["consents"]],
        documents_provided=item["documents_provided"],
        last_signed_in_at=item["last_signed_in_at"], created_at=grant.created_at,
    )


def _one(manager: Any, matter_id: int, grant_id: int) -> GuestGrantResponse:
    item = next((i for i in guest_access_service.grants_for_matter(manager, matter_id)
                 if i["grant"].id == grant_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="No such access on this matter")
    return _grant_response(item)


def _export(exhibit: Any, fmt: str) -> Response:
    content, media_type, filename = exhibit_service.render(exhibit, fmt)
    headers = {
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "X-Exhibit-Rows": str(len(exhibit.rows)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Exhibit-Rows, X-Exhibit-Warnings",
    }
    if exhibit.warnings:
        headers["X-Exhibit-Warnings"] = quote(" | ".join(exhibit.warnings))
    return Response(content=content, media_type=media_type, headers=headers)


# ══ Staff ════════════════════════════════════════════════════════════════════

@staff_router.get("/{matter_id}/guests", response_model=list[GuestGrantResponse])
def list_guests(
    matter_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[GuestGrantResponse]:
    """Every financial professional with access to this matter, past and present."""
    return [_grant_response(i) for i in guest_access_service.grants_for_matter(manager, matter_id)]


@staff_router.post("/{matter_id}/guests", response_model=GuestGrantResponse, status_code=201)
def invite_guest(
    matter_id: int,
    body: GuestInviteRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> GuestGrantResponse:
    """
    Give a professional access to this matter, awaiting the client's consent.

    Nothing is visible to them until a staff member attests that consent.
    """
    staff, name = _staff(request, manager)
    try:
        grant = guest_access_service.invite(
            manager, matter_id, body.full_name, body.email, body.designation, staff.id, name,
            organization=body.organization, profession=body.profession,
            purpose=body.purpose, expires_in_days=body.expires_in_days,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GuestConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    AuditLogger(manager).log(
        action="guest_grant_created", entity_type="matter_guest_grants", entity_id=str(grant.id),
        supabase_uid=request.state.supabase_uid,
        after_json={"matter_id": matter_id, "guest_id": grant.guest_id,
                    "designation": getattr(grant.designation, "value", grant.designation)},
    )
    return _one(manager, matter_id, grant.id)


@staff_router.post("/{matter_id}/guests/{grant_id}/consent", response_model=GuestGrantResponse)
def attest_guest_consent(
    matter_id: int,
    grant_id: int,
    body: GuestConsentRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> GuestGrantResponse:
    """
    Attest that the client consented, and open the access.

    The record names the staff member making it. The guest is emailed from a
    separate thread, so a slow mail server never holds the request.
    """
    staff, name = _staff(request, manager)
    try:
        grant, message = guest_access_service.attest_consent(
            manager, matter_id, grant_id, body.method, body.consent_date, staff.id, name,
            note=body.note,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GuestConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    AuditLogger(manager).log(
        action="guest_consent_attested", entity_type="matter_guest_grants", entity_id=str(grant_id),
        supabase_uid=request.state.supabase_uid,
        after_json={"matter_id": matter_id, "method": body.method.value,
                    "consent_date": body.consent_date.isoformat()},
    )
    if message:
        _send_mail_later(message)
    return _one(manager, matter_id, grant.id)


@staff_router.post("/{matter_id}/guests/{grant_id}/revoke", response_model=GuestGrantResponse)
def revoke_guest(
    matter_id: int,
    grant_id: int,
    body: GuestRevokeRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> GuestGrantResponse:
    """Withdraw access to this matter — or, with ``everywhere``, to every matter."""
    staff, name = _staff(request, manager)
    try:
        grant = guest_access_service.revoke(manager, matter_id, grant_id, staff.id, name,
                                            body.reason)
        others = (guest_access_service.revoke_everywhere(manager, grant.guest_id, staff.id, name,
                                                         body.reason)
                  if body.everywhere else 0)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    AuditLogger(manager).log(
        action="guest_grant_revoked", entity_type="matter_guest_grants", entity_id=str(grant_id),
        supabase_uid=request.state.supabase_uid,
        after_json={"matter_id": matter_id, "everywhere": body.everywhere, "others_revoked": others},
    )
    return _one(manager, matter_id, grant_id)


@staff_router.post("/{matter_id}/guests/{grant_id}/renew", response_model=GuestGrantResponse)
def renew_guest(
    matter_id: int,
    grant_id: int,
    body: GuestRenewRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> GuestGrantResponse:
    """Run the access for ``days`` from today."""
    try:
        guest_access_service.renew(manager, matter_id, grant_id, body.days)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GuestConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    AuditLogger(manager).log(
        action="guest_grant_renewed", entity_type="matter_guest_grants", entity_id=str(grant_id),
        supabase_uid=request.state.supabase_uid, after_json={"days": body.days},
    )
    return _one(manager, matter_id, grant_id)


@staff_router.patch("/{matter_id}/guests/{grant_id}", response_model=GuestGrantResponse)
def set_guest_designation(
    matter_id: int,
    grant_id: int,
    body: GuestDesignationRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> GuestGrantResponse:
    """Testifying, consulting, or neither. It decides what is disclosable, so it is audited."""
    try:
        guest_access_service.set_designation(manager, matter_id, grant_id, body.designation)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    AuditLogger(manager).log(
        action="guest_designation_changed", entity_type="matter_guest_grants",
        entity_id=str(grant_id), supabase_uid=request.state.supabase_uid,
        after_json={"designation": body.designation.value},
    )
    return _one(manager, matter_id, grant_id)


@staff_router.get("/{matter_id}/guests/{grant_id}/documents",
                  response_model=list[GuestLedgerEntryResponse])
def guest_documents(
    matter_id: int,
    grant_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[GuestLedgerEntryResponse]:
    """Every statement made available to this guest under this grant — for a disclosure."""
    try:
        entries = guest_access_service.ledger(manager, matter_id, grant_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return [GuestLedgerEntryResponse(**e.model_dump()) for e in entries]


@staff_router.post("/{matter_id}/guests/{grant_id}/documents/export")
def export_guest_documents(
    matter_id: int,
    grant_id: int,
    body: ExhibitExportRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """The provided-documents list in any of the four formats."""
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    try:
        exhibit = guest_access_service.ledger_exhibit(manager, matter, grant_id, body.exhibit_name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _export(exhibit, body.format)


# ══ Guest ════════════════════════════════════════════════════════════════════

def _denied() -> HTTPException:
    return HTTPException(status_code=404, detail=_NO_ACCESS)


@router.post("/session", response_model=MessageResponse)
def open_session(
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> MessageResponse:
    """The guest's app opened. Recorded so revocation can be audited."""
    guest_access_service.record_sign_in(manager, guest_id)
    return MessageResponse(message=PURPOSE_REMINDER)


@router.get("/matters", response_model=list[GuestMatter])
def guest_matters(
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestMatter]:
    """The matters this guest may open right now."""
    try:
        items = guest_access_service.matters(manager, guest_id)
    except GuestDenied:
        return []
    matters = [GuestMatter(
        id=i["matter"].id,
        name=i["matter"].short_name or i["matter"].matter_name,
        designation=i["grant"].designation,
        expires_at=i["grant"].expires_at,
        purpose_reminder=PURPOSE_REMINDER,
    ) for i in items]
    # By name, so the picker reads like a list somebody can scan.
    return sorted(matters, key=lambda m: (m.name or "").casefold())


@router.get("/matters/{matter_id}/accounts", response_model=list[GuestAccount])
def guest_accounts(
    matter_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestAccount]:
    try:
        accounts = guest_access_service.accounts(manager, guest_id, matter_id)
    except GuestDenied:
        raise _denied()
    return [GuestAccount(
        id=a.id, institution=a.institution, account_type=a.account_type,
        account_number_last4=a.account_number_last4, name_on_account=a.name_on_account,
        ownership=a.ownership, property_character=a.property_character,
        is_closed=bool(a.is_closed), currency=getattr(a, "currency", None) or "USD",
    ) for a in accounts]


@router.get("/accounts/{account_id}/statements", response_model=list[GuestStatement])
def guest_statements(
    account_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestStatement]:
    try:
        statements = guest_access_service.statements(manager, guest_id, account_id)
    except GuestDenied:
        raise _denied()
    out = []
    for s in statements:
        extraction = s.extraction or {}
        out.append(GuestStatement(
            id=s.id, financial_account_id=s.financial_account_id,
            period_start=s.period_start, period_end=s.period_end,
            beginning_balance=s.beginning_balance, ending_balance=s.ending_balance,
            reconciled=bool(s.reconciled),
            bates_first=extraction.get("bates_first"), bates_last=extraction.get("bates_last"),
            source_filename=extraction.get("source_filename"), has_pdf=bool(s.storage_path),
        ))
    return out


def _guest_transaction(row: dict[str, Any], currency: str = "USD") -> GuestTransaction:
    """Only the named fields cross; flags, corrections and provenance never do."""
    return GuestTransaction(
        id=row["id"], statement_id=row["statement_id"],
        financial_account_id=row["financial_account_id"],
        transaction_date=row.get("transaction_date"), posted_date=row.get("posted_date"),
        description=row.get("description") or "", counterparty=row.get("counterparty"),
        location=row.get("location"), amount=row["amount"],
        running_balance=row.get("running_balance"), check_number=row.get("check_number"),
        category_id=row.get("category_id"), physical_page_number=row.get("physical_page_number"),
        bates_number=row.get("bates_number"), institution=row.get("institution"),
        account_last4=row.get("account_last4"), currency=row.get("currency") or currency,
        tag_ids=row.get("tag_ids") or [],
    )


@router.get("/statements/{statement_id}/transactions", response_model=list[GuestTransaction])
def guest_statement_transactions(
    statement_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestTransaction]:
    try:
        rows = guest_access_service.statement_transactions(manager, guest_id, statement_id)
    except GuestDenied:
        raise _denied()
    return [_guest_transaction(r.model_dump()) for r in rows]


@router.get("/statements/{statement_id}/pdf-url", response_model=StatementPdfUrlResponse)
def guest_statement_pdf(
    statement_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> StatementPdfUrlResponse:
    """The statement's own source document — part of what the guest is provided."""
    try:
        statement = guest_access_service.statement_pdf_path(manager, guest_id, statement_id)
    except GuestDenied:
        raise _denied()
    if not statement.storage_path:
        raise HTTPException(status_code=404, detail="No source PDF was stored for this statement")
    url = StorageService(manager).get_signed_url(statement.storage_path, expires_in=300)
    if not url:
        raise HTTPException(status_code=404, detail="The source PDF could not be opened. Try again "
                                                    "shortly, or ask the firm for a copy.")
    return StatementPdfUrlResponse(
        url=url, expires_in=300,
        page=FinancialAccountTransactionRepository(manager).first_page(statement_id),
        source_filename=(statement.extraction or {}).get("source_filename"),
    )


@router.post("/matters/{matter_id}/transactions/search", response_model=GuestTransactionPage)
def guest_search(
    matter_id: int,
    body: TransactionSearchRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> GuestTransactionPage:
    """The matter's transactions. Tag filters and tag chips cover this guest's own tags only."""
    try:
        result = guest_access_service.search(manager, guest_id, matter_id, body.model_dump())
    except GuestDenied:
        raise _denied()
    return GuestTransactionPage(total=result["total"], sum_amount=result["sum_amount"],
                                items=[_guest_transaction(r) for r in result["items"]])


@router.post("/matters/{matter_id}/transactions/export")
def guest_export_transactions(
    matter_id: int,
    body: TransactionExportRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> Response:
    criteria = body.model_dump(exclude={"format", "exhibit_name"})
    try:
        exhibit = guest_access_service.export_transactions(
            manager, guest_id, matter_id, body.exhibit_name.strip() or "Financial Summary", criteria)
    except GuestDenied:
        raise _denied()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _export(exhibit, body.format)


@router.get("/matters/{matter_id}/inventory", response_model=InventoryReport)
def guest_inventory(
    matter_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> InventoryReport:
    try:
        return InventoryReport(**guest_access_service.inventory(manager, guest_id, matter_id))
    except GuestDenied:
        raise _denied()


@router.post("/matters/{matter_id}/inventory/export")
def guest_export_inventory(
    matter_id: int,
    body: ExhibitExportRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> Response:
    try:
        exhibit = guest_access_service.export_inventory(
            manager, guest_id, matter_id, body.exhibit_name.strip() or "Account Inventory")
    except GuestDenied:
        raise _denied()
    return _export(exhibit, body.format)


@router.get("/categories", response_model=list[GuestCategory])
def guest_categories(
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestCategory]:
    """The firm's chart of accounts, by name, so filed lines can be read. Never written."""
    return [GuestCategory(id=c.id, description=c.description, parent_id=c.parent_id)
            for c in TransactionCategoryRepository(manager).get_ordered()]


@router.get("/matters/{matter_id}/tags", response_model=list[GuestTag])
def guest_tags(
    matter_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> list[GuestTag]:
    """This guest's own tags on this matter. Nobody else's are ever listed."""
    try:
        guest_access_service.require_grant(manager, guest_id, matter_id)
        tags = guest_access_service.own_tags(manager, guest_id, matter_id)
    except GuestDenied:
        raise _denied()
    return [GuestTag(id=t.id, label=t.label, description=t.description, color=t.color,
                     is_active=t.is_active) for t in tags]


@router.post("/matters/{matter_id}/tags", response_model=GuestTag, status_code=201)
def guest_create_tag(
    matter_id: int,
    body: GuestTagWriteRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> GuestTag:
    try:
        tag = guest_access_service.create_tag(manager, guest_id, matter_id, body.label or "",
                                              body.description, body.color)
    except GuestDenied:
        raise _denied()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return GuestTag(id=tag.id, label=tag.label, description=tag.description, color=tag.color,
                    is_active=tag.is_active)


@router.patch("/tags/{tag_id}", response_model=GuestTag)
def guest_update_tag(
    tag_id: int,
    body: GuestTagWriteRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> GuestTag:
    try:
        tag = guest_access_service.update_tag(manager, guest_id, tag_id,
                                              body.model_dump(exclude_unset=True))
    except GuestDenied:
        raise _denied()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return GuestTag(id=tag.id, label=tag.label, description=tag.description, color=tag.color,
                    is_active=tag.is_active)


@router.delete("/tags/{tag_id}", status_code=204)
def guest_delete_tag(
    tag_id: int,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> None:
    """Delete their own tag, and take it off every line they put it on."""
    try:
        guest_access_service.delete_tag(manager, guest_id, tag_id)
    except GuestDenied:
        raise _denied()


@router.post("/matters/{matter_id}/transactions/tag", response_model=dict)
def guest_apply_tag(
    matter_id: int,
    body: GuestTagApplyRequest,
    guest_id: int = Depends(require_guest),
    manager: Any = Depends(get_db_manager),
) -> dict[str, int]:
    """Put their own tag on lines of this matter, or take it off. The one write."""
    try:
        changed = guest_access_service.apply_tag(manager, guest_id, matter_id,
                                                 body.transaction_ids, body.tag_id, body.remove)
    except GuestDenied:
        raise _denied()
    return {"changed": changed}
