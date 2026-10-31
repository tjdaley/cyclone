"""
app/db/repositories/guest.py - Guests, their grants, consents, ledger and sign-ins (052).
"""
from db.models.guest import (
    GuestConsentInDB,
    GuestDocumentLedgerEntryInDB,
    GuestProfessionalInDB,
    GuestSignInInDB,
    MatterGuestGrantInDB,
)
from db_handler import BaseRepository, DatabaseManager


class GuestProfessionalRepository(BaseRepository[GuestProfessionalInDB]):
    """CRUD for ``guest_professionals``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "guest_professionals", GuestProfessionalInDB)

    def by_email(self, email: str) -> GuestProfessionalInDB | None:
        """
        The guest with this address, case-insensitively.

        Read whole and compared here: the table is small, and an ``ilike`` on an
        address containing ``_`` or ``%`` would match more than one person.
        """
        wanted = (email or "").strip().lower()
        rows = self.select_many(condition={})[0]
        return next((g for g in rows if (g.email or "").strip().lower() == wanted), None)


class MatterGuestGrantRepository(BaseRepository[MatterGuestGrantInDB]):
    """CRUD for ``matter_guest_grants``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_guest_grants", MatterGuestGrantInDB)

    def for_guest(self, guest_id: int) -> list[MatterGuestGrantInDB]:
        return self.select_many(condition={"guest_id": guest_id}, sort_by="id")[0]

    def for_matter(self, matter_id: int) -> list[MatterGuestGrantInDB]:
        return self.select_many(condition={"matter_id": matter_id}, sort_by="id")[0]


class GuestConsentRepository(BaseRepository[GuestConsentInDB]):
    """CRUD for ``guest_consents``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "guest_consents", GuestConsentInDB)

    def for_grants(self, grant_ids: list[int]) -> list[GuestConsentInDB]:
        if not grant_ids:
            return []
        return self.select_many(condition={"grant_id": grant_ids}, sort_by="id")[0]


class GuestDocumentLedgerRepository(BaseRepository[GuestDocumentLedgerEntryInDB]):
    """CRUD for ``guest_document_ledger``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "guest_document_ledger", GuestDocumentLedgerEntryInDB)

    def for_grant(self, grant_id: int) -> list[GuestDocumentLedgerEntryInDB]:
        return self.select_many(condition={"grant_id": grant_id}, sort_by="period_start")[0]


class GuestSignInRepository(BaseRepository[GuestSignInInDB]):
    """CRUD for ``guest_sign_ins``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "guest_sign_ins", GuestSignInInDB)

    def latest(self, guest_id: int) -> GuestSignInInDB | None:
        rows = self.select_many(condition={"guest_id": guest_id}, sort_by="signed_in_at")[0]
        return rows[-1] if rows else None
