"""
app/db/repositories/processed_inbound_email.py - Repository for processed_inbound_emails.
"""
from db_handler import BaseRepository, DatabaseManager

from db.models.processed_inbound_email import ProcessedInboundEmailInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class ProcessedInboundEmailRepository(BaseRepository[ProcessedInboundEmailInDB]):
    """CRUD repository for the ``processed_inbound_emails`` table (append-only)."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "processed_inbound_emails", ProcessedInboundEmailInDB)

    def already_processed(self, message_id: str) -> bool:
        """Durable dedup check — True if this Message-ID was already committed."""
        return self.exists(field="message_id", value=message_id)
