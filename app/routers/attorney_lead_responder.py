"""
app/routers/attorney_lead_responder.py - Admin-only management of the
attorney → responders mapping.

The PUT endpoint diffs the requested set against the current set so a typical
edit causes minimal row churn (no global delete-and-reinsert). Role validation
happens here because the FK can't enforce it.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db.repositories.attorney_lead_responder import AttorneyLeadResponderRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/attorney-lead-responders", tags=["lead-responders"])


class ResponderSetResponse(BaseModel):
    attorney_staff_id: int
    responder_staff_ids: list[int] = Field(default_factory=list)


class ResponderSetRequest(BaseModel):
    responder_staff_ids: list[int] = Field(default_factory=list, description="Full replacement set")


@router.get("/{attorney_staff_id}", response_model=ResponderSetResponse)
def get_responders(
    attorney_staff_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> ResponderSetResponse:
    """Return the current set of responders for an attorney."""
    rows = AttorneyLeadResponderRepository(manager).get_by_attorney(attorney_staff_id)
    return ResponderSetResponse(
        attorney_staff_id=attorney_staff_id,
        responder_staff_ids=[r.responder_staff_id for r in rows],
    )


@router.put("/{attorney_staff_id}", response_model=ResponderSetResponse)
def set_responders(
    attorney_staff_id: int,
    body: ResponderSetRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> ResponderSetResponse:
    """
    Replace the set of responders for an attorney. Diffs against the current
    set and only inserts/deletes what changed.
    """
    staff_repo = StaffRepository(manager)
    attorney = staff_repo.select_one(condition={"id": attorney_staff_id})
    if attorney is None or attorney.role.value != "attorney":
        raise HTTPException(status_code=400, detail="Target must be a staff member with role='attorney'")

    requested: set[int] = set(body.responder_staff_ids)
    if attorney_staff_id in requested:
        raise HTTPException(status_code=400, detail="An attorney cannot be their own responder")

    # Validate each requested responder exists and is NOT an attorney.
    for rid in requested:
        responder = staff_repo.select_one(condition={"id": rid})
        if responder is None:
            raise HTTPException(status_code=400, detail="Responder staff id %s not found" % rid)
        if responder.role.value == "attorney":
            raise HTTPException(
                status_code=400,
                detail="Staff #%s has role='attorney' and cannot be a responder" % rid,
            )

    repo = AttorneyLeadResponderRepository(manager)
    current_rows = repo.get_by_attorney(attorney_staff_id)
    current_by_responder: dict[int, int] = {r.responder_staff_id: r.id for r in current_rows}

    to_add = requested - set(current_by_responder.keys())
    to_remove_ids = [row_id for rid, row_id in current_by_responder.items() if rid not in requested]

    for rid in to_add:
        repo.insert({"attorney_staff_id": attorney_staff_id, "responder_staff_id": rid})
    for row_id in to_remove_ids:
        repo.delete(row_id)

    LOGGER.info(
        "attorney_lead_responders.put: attorney=%s added=%s removed=%s",
        attorney_staff_id, len(to_add), len(to_remove_ids),
    )
    return ResponderSetResponse(
        attorney_staff_id=attorney_staff_id,
        responder_staff_ids=sorted(requested),
    )
