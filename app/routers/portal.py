"""
app/routers/portal.py - The client's own view of their matter.

Every endpoint here resolves its scope through ``client_portal_service``, which
reads the caller's matters from ``user_roles`` — never from the path or the
body. A client id in a request would be a request to read somebody else's case.

Read-only except for tagging. See the service's module docstring for what is
deliberately absent and why.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from db.repositories.financial import (
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
)
from dependencies import get_db_manager, require_client
from schemas.financial import ComplianceMatrix
from schemas.portal import (
    PortalAccount,
    PortalMatter,
    PortalStatement,
    PortalTag,
    PortalTagRequest,
    PortalTagResult,
    PortalTransaction,
    PortalTransactionPage,
)
from services.client_portal_service import PortalDenied, TaggingClosed, client_portal_service
from services.compliance_service import compliance_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/portal", tags=["client portal"])


def _guard(call):
    """
    Run a service call, turning its refusals into answers.

    ``PortalDenied`` is **404, not 403**: a 403 on a matter id confirms that the
    matter exists, which is an inventory of the firm's cases available to anyone
    with an account and a loop. Tagging refusals are 409 — the request was
    understood and the state refuses it — and their sentences are written to be
    shown to the client as they are.
    """
    try:
        return call()
    except PortalDenied as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TaggingClosed as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ── What the client may see ──────────────────────────────────────────────────

@router.get("/matters", response_model=list[PortalMatter])
def my_matters(
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> list[PortalMatter]:
    """Every matter this account is the client on."""
    return [
        PortalMatter(
            id=m.id, matter_name=m.matter_name, matter_number=m.matter_number,
            tagging_enabled=bool(getattr(m, "client_tagging_enabled", False)),
        )
        for m in client_portal_service.matters(manager, client_id)
    ]


@router.get("/matters/{matter_id}/accounts", response_model=list[PortalAccount])
def my_accounts(
    matter_id: int,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> list[PortalAccount]:
    """
    The accounts on the matter — both sides of the case.

    Tom's call: the client sees the whole matter. What they cannot do is change
    any of it, and nothing here is editable.
    """
    accounts = _guard(lambda: client_portal_service.accounts(manager, client_id, matter_id))
    statement_repo = FinancialAccountStatementRepository(manager)
    out = []
    for account in accounts:
        held = [s for s in statement_repo.get_by_account(account.id)
                if getattr(s.review_status, "value", s.review_status) != "rejected"]
        out.append(PortalAccount(
            id=account.id,
            institution=account.institution,
            account_type=account.account_type,
            account_number_last4=account.account_number_last4,
            name_on_account=account.name_on_account,
            is_closed=bool(account.is_closed),
            currency=getattr(account, "currency", None) or "USD",
            statements=len(held),
        ))
    return out


@router.get("/accounts/{account_id}/statements", response_model=list[PortalStatement])
def my_statements(
    account_id: int,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> list[PortalStatement]:
    """The statements held for one account, oldest first."""
    statements = _guard(lambda: client_portal_service.statements(manager, client_id, account_id))
    transaction_repo = FinancialAccountTransactionRepository(manager)
    out = []
    for statement in sorted(statements, key=lambda s: s.period_start):
        out.append(PortalStatement(
            id=statement.id,
            period_start=statement.period_start,
            period_end=statement.period_end,
            beginning_balance=statement.beginning_balance,
            ending_balance=statement.ending_balance,
            transactions=len(transaction_repo.get_by_statement(statement.id)),
            # The staff flags are not shown. "Still being checked" is the honest
            # client-facing version of an unreconciled statement, and it does
            # not read as an accusation about their bank.
            complete=bool(statement.reconciled),
        ))
    return out


@router.get("/statements/{statement_id}/pdf-url")
def my_statement_pdf(
    statement_id: int,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    A signed URL for the statement's source PDF.

    The same mechanism staff use, scoped the same way everything here is: the
    statement's account must be on one of the caller's matters.
    """
    statement = FinancialAccountStatementRepository(manager).select_one(
        condition={"id": statement_id})
    if statement is None:
        raise HTTPException(status_code=404, detail="That statement is not available")
    _guard(lambda: client_portal_service.statements(manager, client_id,
                                                    statement.financial_account_id))
    if not statement.storage_path:
        raise HTTPException(
            status_code=404,
            detail="The original document for this statement is not stored in the system.")
    url = StorageService().get_signed_url(statement.storage_path)
    if not url:
        raise HTTPException(status_code=502, detail="The document store could not be reached.")
    return {"url": url, "page": None}


@router.get("/matters/{matter_id}/compliance", response_model=ComplianceMatrix)
def my_compliance(
    matter_id: int,
    side: str = Query("client", pattern="^(client|opposing)$"),
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> ComplianceMatrix:
    """
    Which months have a statement, and which do not.

    The most useful screen in the portal: for a client it reads as a list of
    months to go and find, which is work only they can do.
    """
    _guard(lambda: client_portal_service.require_matter(manager, client_id, matter_id))
    return ComplianceMatrix(**compliance_service.matrix(manager, matter_id, side=side))


@router.get("/matters/{matter_id}/tags", response_model=list[PortalTag])
def my_tags(
    matter_id: int,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> list[PortalTag]:
    """The tags the firm has published to clients on this matter."""
    tags = _guard(lambda: client_portal_service.tags(manager, client_id, matter_id))
    return [PortalTag(id=t.id, label=t.label, description=t.description, color=t.color)
            for t in tags]


@router.get("/matters/{matter_id}/transactions", response_model=PortalTransactionPage)
def my_transactions(
    matter_id: int,
    account_id: Optional[int] = Query(default=None),
    text: Optional[str] = Query(default=None),
    amount_min: Optional[str] = Query(default=None, max_length=24),
    amount_max: Optional[str] = Query(default=None, max_length=24),
    tag_id: Optional[int] = Query(default=None),
    untagged: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> PortalTransactionPage:
    """
    The matter's transactions, filtered the way a client would want them.

    A deliberately smaller filter set than staff get — no categories, no
    date-provenance, no deleted lines — but the **amount range is here**,
    because searching for a figure is how somebody finds the transaction they
    are arguing about.
    """
    result = _guard(lambda: client_portal_service.search(
        manager, client_id, matter_id,
        account_ids=[account_id] if account_id else None,
        text=text, amount_min=amount_min, amount_max=amount_max,
        tag_ids=[tag_id] if tag_id else None, untagged=untagged,
        limit=limit, offset=offset,
    ))
    return PortalTransactionPage(
        total=result["total"],
        items=[PortalTransaction(**row) for row in result["items"]],
    )


# ── The one write ────────────────────────────────────────────────────────────

@router.post("/transactions/{transaction_id}/tags", response_model=PortalTagResult)
def tag_transaction(
    transaction_id: int,
    body: PortalTagRequest,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> PortalTagResult:
    """
    Put one of the firm's client tags on one line.

    Refused with a sentence the client can read when tagging is paused on the
    matter, or when the firm has already reviewed this tag on this line and
    taken it off.
    """
    outcome = _guard(lambda: client_portal_service.apply_tag(
        manager, client_id, transaction_id, body.tag_id))
    return _tag_state(manager, transaction_id, outcome)


@router.delete("/transactions/{transaction_id}/tags/{tag_id}", response_model=PortalTagResult)
def untag_transaction(
    transaction_id: int,
    tag_id: int,
    client_id: int = Depends(require_client),
    manager: Any = Depends(get_db_manager),
) -> PortalTagResult:
    """Take back one of their own tags. Never one the firm applied."""
    outcome = _guard(lambda: client_portal_service.remove_tag(
        manager, client_id, transaction_id, tag_id))
    return _tag_state(manager, transaction_id, outcome)


def _tag_state(manager: Any, transaction_id: int, outcome: str) -> PortalTagResult:
    """The line's tagging state after a change, so the UI never has to guess."""
    from db.repositories.financial import TransactionTagLinkRepository

    repo = TransactionTagLinkRepository(manager)
    return PortalTagResult(
        outcome=outcome,
        tag_ids=repo.for_transactions([transaction_id]).get(transaction_id, []),
        cleared_tag_ids=repo.cleared_for_transactions([transaction_id]).get(transaction_id, []),
    )
