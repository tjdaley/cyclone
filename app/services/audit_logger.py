"""
app/services/audit_logger.py - Service for writing immutable audit log entries.

All sensitive actions (billing entry created/edited/deleted, bill sent,
fee agreement signed, billing cycle closed, user role changed, trust
ledger transaction posted) must call AuditLogger.log() before returning.
"""
from typing import Any, Optional

from db.models.audit_log import AuditLog
from db.repositories.audit_log import AuditLogRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class AuditLogger:
    """
    Service for appending entries to the ``audit_log`` table.

    Instantiated per-request via the ``get_audit_logger`` dependency so it
    shares the same ``DatabaseManager`` instance as other repositories.

    Usage::

        audit = AuditLogger(manager)
        audit.log(
            supabase_uid=request.state.supabase_uid,
            action="billing_entry.created",
            entity_type="billing_entry",
            entity_id=str(entry.id),
            after_json=entry.model_dump(),
        )
    """

    def __init__(self, manager: DatabaseManager):
        self._repo = AuditLogRepository(manager)

    def log(
        self,
        action: str,
        entity_type: str,
        supabase_uid: Optional[str] = None,
        entity_id: Optional[str] = None,
        before_json: Optional[dict[str, Any]] = None,
        after_json: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Write an audit log entry.

        All arguments except ``action`` and ``entity_type`` are optional so
        this method can be called uniformly across create, update, and delete
        actions.

        :param action: Dot-namespaced action identifier, e.g. ``'billing_entry.created'``.
        :type action: str
        :param entity_type: Type of entity affected, e.g. ``'billing_entry'``.
        :type entity_type: str
        :param supabase_uid: Auth UID of the acting user; None for system actions.
        :type supabase_uid: Optional[str]
        :param entity_id: String primary key of the affected record.
        :type entity_id: Optional[str]
        :param before_json: Record snapshot before the action (update/delete only).
        :type before_json: Optional[dict[str, Any]]
        :param after_json: Record snapshot after the action (create/update only).
        :type after_json: Optional[dict[str, Any]]
        """
        entry = AuditLog(
            supabase_uid=supabase_uid,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=before_json,
            after_json=after_json,
        )
        try:
            self._repo.insert(entry.model_dump())
            LOGGER.info(
                "Audit: action=%s entity_type=%s entity_id=%s uid=%s",
                action,
                entity_type,
                entity_id,
                supabase_uid,
            )
        except Exception as e:
            # Audit failures must never crash the primary operation.
            # Log at ERROR so on-call is alerted, but do not re-raise.
            LOGGER.error(
                "AuditLogger.log failed: action=%s entity_id=%s error=%s",
                action,
                entity_id,
                str(e),
            )
