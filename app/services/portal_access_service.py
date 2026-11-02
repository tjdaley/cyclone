"""
app/services/portal_access_service.py - Whether a client can sign in to the portal.

A LOGIN EMAIL ON A CLIENT RECORD GRANTS NOTHING
────────────────────────────────────────────────────────────────────────────
``clients.auth_email`` is required on every client and was backfilled from the
contact email (020), so it cannot mean "this person may sign in": treating it
that way would open the portal to every client the firm has ever had, and a
client reads the whole matter, the other side's accounts included. Access is
the ``client`` row in ``user_roles``, and only a deliberate act here creates it.

The row starts with ``supabase_uid`` null. First sign-in links it through
``POST /api/v1/auth/correlate-staff``, the same path staff and guests take.

ONE ADDRESS, ONE PERSON
────────────────────────────────────────────────────────────────────────────
Correlation links every unlinked row matching the address, and ``require_client``
then serves the first ``client_id`` it finds. So an address already held by
another client would show one client somebody else's divorce, and an address
held by staff or a guest would change what that person's login can reach. Both
are refused here, which is the only point anything can catch them.

Likewise the stored address cannot drift from the row: changing a login email
while access exists would leave the old address able to activate, or the old
Google account still linked. ``check_login_email_change`` makes that a refusal.
"""
from typing import Any, Optional

from db.models.user_role import UserRole, UserRoleType
from db.repositories.client import ClientRepository
from db.repositories.user_role import UserRoleRepository
from db_handler import DatabaseManager
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class PortalAccessConflict(Exception):
    """The request cannot be honoured as things stand; the message says why."""


def normalize_email(email: Optional[str]) -> str:
    """The form correlation compares against: Supabase stores addresses lowercased."""
    return (email or "").strip().lower()


def _role_value(row: Any) -> str:
    return getattr(row.role, "value", row.role)


class PortalAccessService:
    """Give, revoke and report a client's portal access."""

    @staticmethod
    def _rows(manager: DatabaseManager, client_id: int) -> list[Any]:
        return [r for r in UserRoleRepository(manager).get_by_client(client_id)
                if _role_value(r) == UserRoleType.client.value]

    @staticmethod
    def _describe(client_id: int, rows: list[Any]) -> dict[str, Any]:
        if not rows:
            status = "none"
        elif any(r.supabase_uid for r in rows):
            status = "active"
        else:
            status = "awaiting_sign_in"
        return {
            "client_id": client_id,
            "status": status,
            "auth_email": next((r.auth_email for r in rows if r.auth_email), None),
        }

    def status(self, manager: DatabaseManager, client_id: int) -> dict[str, Any]:
        """
        Where this client stands.

        :raises LookupError: No such client.
        """
        if ClientRepository(manager).select_one(condition={"id": client_id}) is None:
            raise LookupError("Client not found")
        return self._describe(client_id, self._rows(manager, client_id))

    def give(
        self, manager: DatabaseManager, client_id: int, actor_uid: Optional[str],
    ) -> dict[str, Any]:
        """
        Let this client sign in with the login email on their record.

        Giving access twice leaves one row, so a double click is harmless.

        :raises LookupError: No such client.
        :raises ValueError: The record's login email cannot be an address.
        :raises PortalAccessConflict: Somebody else signs in with that address.
        """
        client = ClientRepository(manager).select_one(condition={"id": client_id})
        if client is None:
            raise LookupError("Client not found")
        existing = self._rows(manager, client_id)
        if existing:
            return self._describe(client_id, existing)

        email = normalize_email(client.auth_email)
        if "@" not in email or " " in email:
            raise ValueError("Enter the Google address the client will sign in with, "
                             "and save it, before giving portal access")

        role_repo = UserRoleRepository(manager)
        holders = [r for r in role_repo.select_many(condition={})[0]
                   if normalize_email(r.auth_email) == email]
        if holders:
            held_by = _role_value(holders[0])
            who = {
                UserRoleType.client.value: "another client",
                UserRoleType.financial_professional.value: "a financial professional",
            }.get(held_by, "a staff member")
            raise PortalAccessConflict(
                "That address already signs in to Cyclone as %s. "
                "Each person needs their own Google address." % who)

        row = role_repo.insert(UserRole(
            auth_email=email, role=UserRoleType.client, client_id=client_id,
        ).model_dump())
        AuditLogger(manager).log(
            supabase_uid=actor_uid,
            action="client_portal.access_given",
            entity_type="user_role",
            entity_id=str(row.id),
            after_json=row.model_dump(),
        )
        LOGGER.info("portal_access.give: client=%s role_id=%s", client_id, row.id)
        return self._describe(client_id, [row])

    def revoke(
        self, manager: DatabaseManager, client_id: int, actor_uid: Optional[str],
    ) -> dict[str, Any]:
        """
        Take access away. Effective on the client's next request, because
        ``require_client`` re-reads the role on every one. Tags they applied stay.

        :raises LookupError: No such client.
        """
        if ClientRepository(manager).select_one(condition={"id": client_id}) is None:
            raise LookupError("Client not found")
        role_repo = UserRoleRepository(manager)
        audit = AuditLogger(manager)
        for row in self._rows(manager, client_id):
            audit.log(
                supabase_uid=actor_uid,
                action="client_portal.access_revoked",
                entity_type="user_role",
                entity_id=str(row.id),
                before_json=row.model_dump(),
            )
            role_repo.delete(row.id)
            LOGGER.info("portal_access.revoke: client=%s role_id=%s", client_id, row.id)
        return self._describe(client_id, [])

    def has_access(self, manager: DatabaseManager, client_id: int) -> bool:
        """True while a role row exists, linked or not."""
        return bool(self._rows(manager, client_id))

    def check_login_email_change(
        self, manager: DatabaseManager, client_id: int, new_email: Optional[str],
    ) -> None:
        """
        Refuse a new login email while access is in place.

        :raises PortalAccessConflict: Access exists under a different address.
        """
        target = normalize_email(new_email)
        if any(normalize_email(r.auth_email) != target for r in self._rows(manager, client_id)):
            raise PortalAccessConflict(
                "This client has portal access under their current login email. "
                "Revoke access, change the email, then give access again.")


portal_access_service = PortalAccessService()
