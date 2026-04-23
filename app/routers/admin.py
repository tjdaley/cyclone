"""
app/routers/admin.py - Admin-only endpoints: user role management, audit log access.

All routes in this router require the 'admin' role.
"""
from typing import Any

from db_handler import SupabaseManager
from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.user_role import UserRole
from db.repositories.user_role import UserRoleRepository
from db.repositories.audit_log import AuditLogRepository
from dependencies import get_db_manager, require_role  # type: ignore
from schemas.common import DeletedResponse
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ── User Role Management ───────────────────────────────────────────────────

@router.get("/user-roles", response_model=list[dict[str, Any]])
def list_user_roles(
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[dict[str, Any]]:
    """Return all user role assignments."""
    repo = UserRoleRepository(manager)
    records, _ = repo.select_many(condition={}, sort_by="created_at")
    return [r.model_dump() for r in records]


@router.post("/user-roles", status_code=201)
def assign_role(
    body: dict[str, Any],
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> dict[str, Any]:
    """
    Assign an application role to a staff or client record.

    Body fields: role (str), auth_email (str|null), staff_id (int|null),
    client_id (int|null). ``supabase_uid`` is left null — it is written
    automatically when the user logs in for the first time.
    Role changes are written to the audit log.
    """
    try:
        role_obj = UserRole(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    repo = UserRoleRepository(manager)
    if role_obj.staff_id and repo.staff_has_role(role_obj.staff_id):
        raise HTTPException(
            status_code=409,
            detail="Staff member already has a role. Use PATCH to change it.",
        )

    record = repo.insert(role_obj.model_dump())
    audit = AuditLogger(manager)
    audit.log(
        supabase_uid=request.state.supabase_uid,
        action="user_role.assigned",
        entity_type="user_role",
        entity_id=str(record.id),
        after_json=record.model_dump(),
    )
    LOGGER.info("admin.assign_role: role=%s", role_obj.role)
    return record.model_dump()


@router.patch("/user-roles/{role_id}")
def update_role(
    role_id: int,
    body: dict[str, Any],
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> dict[str, Any]:
    """
    Update a user role assignment. Role changes are written to the audit log.
    """
    repo = UserRoleRepository(manager)
    existing = repo.select_one(condition={"id": role_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    updates = {k: v for k, v in body.items() if k in {"role", "staff_id", "client_id"}}
    if not updates:
        raise HTTPException(status_code=422, detail="No updatable fields provided")

    before_json = existing.model_dump()
    record = repo.update(role_id, updates)

    audit = AuditLogger(manager)
    audit.log(
        supabase_uid=request.state.supabase_uid,
        action="user_role.changed",
        entity_type="user_role",
        entity_id=str(role_id),
        before_json=before_json,
        after_json=record.model_dump(),
    )
    LOGGER.info("admin.update_role: role_id=%s", role_id)
    return record.model_dump()


@router.delete("/user-roles/{role_id}", response_model=DeletedResponse)
def revoke_role(
    role_id: int,
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """Revoke a user role assignment."""
    repo = UserRoleRepository(manager)
    existing = repo.select_one(condition={"id": role_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    audit = AuditLogger(manager)
    audit.log(
        supabase_uid=request.state.supabase_uid,
        action="user_role.revoked",
        entity_type="user_role",
        entity_id=str(role_id),
        before_json=existing.model_dump(),
    )
    repo.delete(role_id)
    LOGGER.info("admin.revoke_role: role_id=%s", role_id)
    return DeletedResponse(id=role_id)


# ── Audit Log ─────────────────────────────────────────────────────────────

@router.get("/audit-log/entity/{entity_type}/{entity_id}", response_model=list[dict[str, Any]])
def get_audit_log_for_entity(
    entity_type: str,
    entity_id: str,
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[dict[str, Any]]:
    """Return audit log entries for a specific entity."""
    repo = AuditLogRepository(manager)
    records = repo.get_by_entity(entity_type, entity_id)
    return [r.model_dump() for r in records]


@router.get("/audit-log/action/{action}", response_model=list[dict[str, Any]])
def get_audit_log_by_action(
    action: str,
    manager: SupabaseManager = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[dict[str, Any]]:
    """Return audit log entries for a specific action type."""
    repo = AuditLogRepository(manager)
    records = repo.get_by_action(action)
    return [r.model_dump() for r in records]  # type: ignore[list-item]
