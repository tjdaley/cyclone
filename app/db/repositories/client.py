"""
app/db/repositories/client.py - Repository for the Client model.
"""
from typing import Optional

from db.models.client import ClientInDB, ClientStatus
from db_handler import BaseRepository, DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class ClientRepository(BaseRepository[ClientInDB]):
    """CRUD repository for the ``clients`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "clients", ClientInDB)

    def get_by_email(self, email: str) -> Optional[ClientInDB]:
        """
        Return the client with the given email address, or ``None`` if not found.

        :param email: Client email address.
        :type email: str
        :return: Matching client record, or ``None``.
        :rtype: Optional[ClientInDB]
        """
        LOGGER.debug("ClientRepository.get_by_email: email=%s", email)
        return self.select_one(condition={"email": email})

    def get_by_status(self, status: ClientStatus) -> list[ClientInDB]:
        """
        Return all clients with the given lifecycle status.

        :param status: ClientStatus value to filter by.
        :type status: ClientStatus
        :return: List of matching client records; empty list if none found.
        :rtype: list[ClientInDB]
        """
        LOGGER.debug("ClientRepository.get_by_status: status=%s", status.value)
        return self.select_many(condition={"status": status.value}, sort_by="created_at")[0]

    def email_exists(self, email: str) -> bool:
        """
        Check whether an email address is already registered (duplicate-guard).

        :param email: Email address to check.
        :type email: str
        :return: ``True`` if the email is already in use.
        :rtype: bool
        """
        return self.exists(field="email", value=email)
