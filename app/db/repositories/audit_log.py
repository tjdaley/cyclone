"""
app/db/repositories/audit_log.py - Repository for the AuditLog model.

Audit log entries are append-only. This repository exposes insert and
read operations only — never update or delete.
"""
from db.models.audit_log import AuditLogInDB
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class AuditLogRepository(BaseRepository[AuditLogInDB]):
    """Append-only repository for the ``audit_log`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "audit_log", AuditLogInDB)

    def get_by_entity(self, entity_type: str, entity_id: str) -> list[AuditLogInDB]:
        """
        Return all audit events for a specific entity record, oldest first.

        :param entity_type: Entity type string, e.g. ``'billing_entry'``.
        :type entity_type: str
        :param entity_id: String representation of the entity primary key.
        :type entity_id: str
        :return: List of audit log entries.
        :rtype: list[AuditLogInDB]
        """
        LOGGER.debug("AuditLogRepository.get_by_entity: type=%s id=%s", entity_type, entity_id)
        return self.select_many(
            condition={"entity_type": entity_type, "entity_id": entity_id},
            sort_by="created_at",
        )[0]

    def get_by_uid(self, supabase_uid: str) -> list[AuditLogInDB]:
        """
        Return all audit events performed by a user, most recent first.

        :param supabase_uid: Supabase Auth UID of the acting user.
        :type supabase_uid: str
        :return: List of audit log entries.
        :rtype: list[AuditLogInDB]
        """
        LOGGER.debug("AuditLogRepository.get_by_uid")
        return self.select_many(
            condition={"supabase_uid": supabase_uid},
            sort_by="created_at",
            sort_direction="desc",
        )[0]

    def get_by_action(self, action: str) -> list[AuditLogInDB]:
        """
        Return all audit events of a specific action type, most recent first.

        :param action: Action string, e.g. ``'billing_entry.created'``.
        :type action: str
        :return: List of audit log entries.
        :rtype: list[AuditLogInDB]
        """
        LOGGER.debug("AuditLogRepository.get_by_action: action=%s", action)
        return self.select_many(
            condition={"action": action},
            sort_by="created_at",
            sort_direction="desc",
        )[0]
