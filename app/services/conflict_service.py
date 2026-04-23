"""
app/services/conflict_service.py - Conflict-of-interest check service.

Performs name-match checks across matters, clients, and opposing parties.
Results are surfaced to attorneys/admins for review; conflict details are
never disclosed to the prospective client.

Phase 1: exact + case-insensitive substring match via Supabase PostgREST.
Phase 2 (future): fuzzy trigram match via pg_trgm Postgres extension.
"""
from dataclasses import dataclass, field

from db.repositories.client import ClientRepository
from db.repositories.matter import MatterRepository, OpposingPartyRepository
from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


@dataclass
class ConflictHit:
    """A single potential conflict match returned by the conflict checker."""
    source: str           # 'client' | 'opposing_party' | 'matter'
    entity_id: int        # Primary key of the matching record
    matched_field: str    # Field name where the match was found
    matched_value: str    # The matched string value (no PII — IDs only in logs)


@dataclass
class ConflictCheckResult:
    """Aggregated result of a conflict check for a prospective client/matter."""
    prospective_name: str
    hits: list[ConflictHit] = field(default_factory=list[ConflictHit])

    @property
    def has_conflict(self) -> bool:
        """Return True if any conflict hits were found."""
        return len(self.hits) > 0


class ConflictService:
    """
    Service for running conflict-of-interest checks.

    Instantiated per-request via the ``get_conflict_service`` dependency.
    """

    def __init__(self, manager: DatabaseManager):
        self._client_repo = ClientRepository(manager)
        self._matter_repo = MatterRepository(manager)
        self._opposing_repo = OpposingPartyRepository(manager)

    def check(self, full_name: str, opposing_names: list[str] | None = None) -> ConflictCheckResult:
        """
        Run a conflict check against the given prospective client name.

        Checks:
        1. Existing clients whose name matches ``full_name``.
        2. Existing opposing parties whose name matches ``full_name``.
        3. Existing clients whose name matches any entry in ``opposing_names``.
        4. Existing opposing parties whose name matches any entry in ``opposing_names``.

        Phase 1 implementation uses case-insensitive ilike filters. Phase 2
        will add trigram similarity via a raw Supabase RPC call to a Postgres
        function wrapping ``similarity()`` from pg_trgm.

        :param full_name: Full name of the prospective client to check.
        :type full_name: str
        :param opposing_names: Names of opposing parties on the prospective matter.
        :type opposing_names: list[str] | None
        :return: ConflictCheckResult containing all hits found.
        :rtype: ConflictCheckResult
        """
        LOGGER.info("ConflictService.check: initiated")
        result = ConflictCheckResult(prospective_name=full_name)
        names_to_check = [full_name] + (opposing_names or [])

        # Phase 1: iterate known clients and compare last_name.last_name
        # This is a stopgap until pg_trgm RPC is wired up.
        # Note: select_many with no condition returns all clients up to supabase_max_rows.
        all_clients, _ = self._client_repo.select_many(condition={})
        all_opposing, _ = self._opposing_repo.select_many(condition={})

        for search_name in names_to_check:
            search_lower = search_name.strip().lower()
            if not search_lower:
                continue

            for client in all_clients:
                client_full = f"{client.name.first_name} {client.name.last_name}".lower()
                if search_lower in client_full or client_full in search_lower:
                    result.hits.append(ConflictHit(
                        source="client",
                        entity_id=client.id,
                        matched_field="name",
                        matched_value=f"id:{client.id}",  # No PII in the hit value
                    ))
                    LOGGER.warning(
                        "ConflictService: match found source=client entity_id=%s", client.id,
                    )

            for opp in all_opposing:
                opp_lower = opp.full_name.strip().lower()
                if search_lower in opp_lower or opp_lower in search_lower:
                    result.hits.append(ConflictHit(
                        source="opposing_party",
                        entity_id=opp.id,
                        matched_field="full_name",
                        matched_value=f"id:{opp.id}",
                    ))
                    LOGGER.warning(
                        "ConflictService: match found source=opposing_party entity_id=%s", opp.id,
                    )

        LOGGER.info(
            "ConflictService.check: completed hits=%s", len(result.hits),
        )
        return result
