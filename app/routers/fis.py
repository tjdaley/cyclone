"""
app/routers/fis.py - The Financial Information Statement.

Computed on demand rather than stored. The statement is a view of the
transactions as they stand right now, so re-filing one line changes it — which
is the point of the screen it serves: the FIS is a working surface, not a report
you regenerate elsewhere after fixing things.

Drilling into a line reuses ``POST /matters/{id}/transactions/search`` with the
category and the window, so there is one query path over transactions and no
second implementation to drift from it.
"""
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from db.models.financial import FisCategorySetting
from db.repositories.financial import FisCategorySettingRepository
from db.repositories.matter import MatterRepository
from dependencies import get_db_manager, require_role
from schemas.fis import (
    FisExportRequest,
    FisRequest,
    FisResponse,
    FisScheduleExportRequest,
    FisScheduleRequest,
    FisScheduleResponse,
    FisSettingRequest,
    FisSettingResponse,
)
from services import exhibit_service
from services.fis_service import fis_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["fis"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]


def _setting_response(record: Any) -> FisSettingResponse:
    return FisSettingResponse(
        **record.model_dump(),
        is_default=record.client_id is None and record.opposing_party_id is None,
    )


@router.post("/matters/{matter_id}/fis", response_model=FisResponse)
def build_fis(
    matter_id: int,
    body: FisRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> FisResponse:
    """
    Average a person's income and expenses over a window of whole months.

    Synchronous: this is a database read and some arithmetic — no LLM, no OCR —
    so haproxy's request ceiling is not in play (§11a). The scan is deliberately
    uncapped, unlike an export: an exhibit that stops at five thousand rows says
    so on its face, while an FIS that stopped would silently understate every
    line, and the figure is sworn to.
    """
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    try:
        result = fis_service.build(
            manager=manager,
            matter_id=matter_id,
            start_year=body.start_year,
            start_month=body.start_month,
            end_year=body.end_year,
            end_month=body.end_month,
            account_ids=body.account_ids,
            client_id=body.client_id,
            opposing_party_id=body.opposing_party_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return FisResponse(**result)


@router.post("/matters/{matter_id}/fis/export")
def export_fis(
    matter_id: int,
    body: FisExportRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    Export the statement -- the exhibit filed with a temporary orders motion.

    Two columns and no headings, because the FIS is a form: a label, a figure,
    and indentation carrying the hierarchy. The coverage gaps and the unfiled
    transactions travel as footnotes directly beneath the table, since on screen
    they are no use once the document has left.
    """
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    try:
        exhibit = fis_service.build_exhibit(
            manager, matter,
            start_year=body.start_year, start_month=body.start_month,
            end_year=body.end_year, end_month=body.end_month,
            account_ids=body.account_ids,
            client_id=body.client_id,
            opposing_party_id=body.opposing_party_id,
            exhibit_name=body.exhibit_name.strip() or "Financial Information Statement",
            compressed=body.compressed,
        )
        content, media_type, filename = exhibit_service.render(exhibit, body.format)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 -- a renderer failure must not read as an empty export
        LOGGER.error("fis.export: matter=%s format=%s failed: %s", matter_id, body.format, str(e))
        raise HTTPException(status_code=500, detail="Could not build the export") from e

    headers = {
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "X-Exhibit-Rows": str(len(exhibit.rows)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Exhibit-Rows, X-Exhibit-Warnings",
    }
    if exhibit.warnings:
        headers["X-Exhibit-Warnings"] = quote(" | ".join(exhibit.warnings))

    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/matters/{matter_id}/fis/schedule", response_model=FisScheduleResponse)
def build_schedule(
    matter_id: int,
    body: FisScheduleRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> FisScheduleResponse:
    """
    Every transaction behind the statement, grouped by category.

    The answer to "what exactly is in Miscellaneous?" -- and, before that, the
    review pass that finds the line filed under the wrong heading. Its monthly
    figures come from the statement itself rather than being recomputed, so the
    two documents cannot disagree.
    """
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    try:
        result = fis_service.build_schedule(
            manager, matter,
            start_year=body.start_year, start_month=body.start_month,
            end_year=body.end_year, end_month=body.end_month,
            account_ids=body.account_ids, client_id=body.client_id,
            opposing_party_id=body.opposing_party_id, category_ids=body.category_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return FisScheduleResponse(**result)


@router.post("/matters/{matter_id}/fis/schedule/export")
def export_schedule(
    matter_id: int,
    body: FisScheduleExportRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """The schedule as a document: the backup handed up on cross."""
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    try:
        exhibit = fis_service.build_schedule_exhibit(
            manager, matter,
            start_year=body.start_year, start_month=body.start_month,
            end_year=body.end_year, end_month=body.end_month,
            account_ids=body.account_ids, client_id=body.client_id,
            opposing_party_id=body.opposing_party_id, category_ids=body.category_ids,
            exhibit_name=body.exhibit_name.strip() or "Schedule of Transactions by Category",
        )
        content, media_type, filename = exhibit_service.render(exhibit, body.format)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 -- a renderer failure must not read as an empty export
        LOGGER.error("fis.export_schedule: matter=%s format=%s failed: %s",
                     matter_id, body.format, str(e))
        raise HTTPException(status_code=500, detail="Could not build the export") from e

    headers = {
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "X-Exhibit-Rows": str(len(exhibit.rows)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Exhibit-Rows, X-Exhibit-Warnings",
    }
    if exhibit.warnings:
        headers["X-Exhibit-Warnings"] = quote(" | ".join(exhibit.warnings))
    return Response(content=content, media_type=media_type, headers=headers)


@router.get("/fis-settings", response_model=list[FisSettingResponse])
def list_fis_settings(
    client_id: Optional[int] = Query(default=None),
    opposing_party_id: Optional[int] = Query(default=None),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[FisSettingResponse]:
    """
    Every payment schedule in force for one person, defaults included.

    ``is_default`` on each row says whether it is the firm's or this person's,
    because an editor that cannot tell them apart would write an inherited value
    back as an override and pin a default that should have kept moving.
    """
    settings = FisCategorySettingRepository(manager).resolve(
        client_id=client_id, opposing_party_id=opposing_party_id,
    )
    return [_setting_response(record) for record in settings.values()]


@router.put("/fis-settings", response_model=FisSettingResponse)
def upsert_fis_setting(
    body: FisSettingRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> FisSettingResponse:
    """
    Record a payment schedule, replacing this person's existing one.

    An upsert rather than create/update, because the caller is a picker on a
    line of a statement and has no reason to know whether a row exists. With
    neither party named it writes the firm-wide default.
    """
    if body.client_id is not None and body.opposing_party_id is not None:
        raise HTTPException(
            status_code=422,
            detail="A schedule belongs to our client or to the other side, not both",
        )

    repo = FisCategorySettingRepository(manager)
    existing = repo.find_for(
        body.category_id,
        client_id=body.client_id,
        opposing_party_id=body.opposing_party_id,
    )

    payload = FisCategorySetting(
        client_id=body.client_id,
        opposing_party_id=body.opposing_party_id,
        category_id=body.category_id,
        recurrence=body.recurrence,
        stated_annual_amount=body.stated_annual_amount,
        note=(body.note or "").strip() or None,
    ).model_dump()

    if existing is None:
        record = repo.insert(payload)
        LOGGER.info("fis: created schedule category=%s client=%s opposing=%s",
                    body.category_id, body.client_id, body.opposing_party_id)
    else:
        record = repo.update(existing.id, payload)
        LOGGER.info("fis: updated schedule id=%s category=%s", existing.id, body.category_id)

    return _setting_response(record)


@router.delete("/fis-settings/{setting_id}", status_code=204)
def delete_fis_setting(
    setting_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> None:
    """
    Drop one schedule, so the layer beneath it applies again.

    Deleting a person's row returns them to the firm default; deleting the firm
    default returns the category to averaging over the window. Both are
    meaningful, which is why this is a delete and not a flag.
    """
    repo = FisCategorySettingRepository(manager)
    if repo.select_one(condition={"id": setting_id}) is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    repo.delete(setting_id)
    LOGGER.info("fis: deleted schedule id=%s", setting_id)
