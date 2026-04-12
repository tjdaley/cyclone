"""
app/services/billing_service.py - Business logic for billing operations.

Handles rate resolution, pro-bono zeroing, natural-language entry parsing,
billing cycle closure, and the client financial balance calculation.

All database writes that require an audit log entry call AuditLogger
before returning.
"""
import json
from typing import Optional

from db.models.billing_entry import BillingEntry, BillingEntryInDB, EntryType
from db.models.billing_cycle import BillingCycleStatus
from db.repositories.billing_cycle import BillingCycleRepository
from db.repositories.billing_entry import BillingEntryRepository
from db.repositories.matter import MatterRateOverrideRepository, MatterRepository
from db.repositories.staff import StaffRepository
from db.repositories.trust_ledger import TrustLedgerRepository
from db.supabasemanager import DatabaseManager
from services.audit_logger import AuditLogger
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_NL_BILLING_SYSTEM_PROMPT = """\
You are a legal billing assistant. Today's date is {today}.

Parse the user's natural language billing entry into a structured JSON object \
with these fields:
- hours (float, must be one of: 0.1, 0.25, 0.5, 1.0 or multiples thereof)
- description (string — clean, professional billing language; do not include \
  the date, client name, or matter name in the description)
- entry_type: "time" | "expense" | "flat_fee"
- billable: true (default) | false
- invoice_date (string in YYYY-MM-DD format — the date the work was \
  performed, if mentioned or inferable from relative terms like "yesterday", \
  "last Friday", "on April 3". Set to null if not mentioned.)

Respond ONLY with valid JSON. No markdown fences, no explanation.
If a field cannot be determined, set it to null.\
"""


class ParsedBillingEntry:
    """Structured result from natural-language billing entry parsing."""

    def __init__(
        self,
        hours: Optional[float],
        description: Optional[str],
        entry_type: str,
        billable: bool,
        invoice_date: Optional[str],
    ):
        self.hours = hours
        self.description = description
        self.entry_type = entry_type
        self.billable = billable
        self.invoice_date = invoice_date

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedBillingEntry":
        """Construct from an LLM-returned dict."""
        return cls(
            hours=data.get("hours"),
            description=data.get("description"),
            entry_type=data.get("entry_type", "time"),
            billable=bool(data.get("billable", True)),
            invoice_date=data.get("invoice_date"),
        )


class BillingService:
    """
    Service encapsulating all billing business logic.

    Instantiated per-request via the ``get_billing_service`` dependency.
    """

    def __init__(self, manager: DatabaseManager):
        self._entry_repo = BillingEntryRepository(manager)
        self._cycle_repo = BillingCycleRepository(manager)
        self._matter_repo = MatterRepository(manager)
        self._staff_repo = StaffRepository(manager)
        self._override_repo = MatterRateOverrideRepository(manager)
        self._trust_repo = TrustLedgerRepository(manager)
        self._audit = AuditLogger(manager)

    # ── Rate resolution ────────────────────────────────────────────────────

    def resolve_rate(self, matter_id: int, staff_id: int) -> Optional[float]:
        """
        Resolve the effective hourly billing rate for a staff member on a matter.

        Resolution order:
        1. ``matter_rate_overrides`` record for this (matter, staff) pair
        2. ``Matter.rate_card`` by role name
        3. ``StaffMember.default_billing_rate``

        Returns ``None`` if no rate can be determined (admin with no rate set).
        Returns ``0.0`` if the matter is pro bono.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: Effective hourly rate in USD, or ``None``.
        :rtype: Optional[float]
        """
        matter = self._matter_repo.select_one(condition={"id": matter_id})
        if matter is None:
            LOGGER.error("BillingService.resolve_rate: matter not found: matter_id=%s", matter_id)
            raise ValueError("Matter not found: id=%s" % matter_id)

        if matter.is_pro_bono:
            return 0.0

        # 1. Per-matter staff override
        override = self._override_repo.get_for_staff(matter_id, staff_id)
        if override is not None:
            LOGGER.debug(
                "BillingService.resolve_rate: using override matter_id=%s staff_id=%s rate=%s",
                matter_id,
                staff_id,
                override.rate,
            )
            return override.rate

        staff = self._staff_repo.select_one(condition={"id": staff_id})
        if staff is None:
            LOGGER.error("BillingService.resolve_rate: staff not found: staff_id=%s", staff_id)
            raise ValueError("Staff not found: id=%s" % staff_id)

        # 2. Rate card by role
        role_rate = getattr(matter.rate_card, staff.role.value, None) if matter.rate_card else None
        if role_rate is not None:
            LOGGER.debug(
                "BillingService.resolve_rate: using rate_card role=%s rate=%s",
                staff.role.value,
                role_rate,
            )
            return float(role_rate)

        # 3. Staff default rate
        if staff.default_billing_rate is not None:
            LOGGER.debug(
                "BillingService.resolve_rate: using staff default rate=%s", staff.default_billing_rate,
            )
            return staff.default_billing_rate

        LOGGER.warning(
            "BillingService.resolve_rate: no rate found matter_id=%s staff_id=%s",
            matter_id,
            staff_id,
        )
        return None

    # ── Entry creation ──────────────────────────────────────────────────────

    def create_entry(
        self,
        entry: BillingEntry,
        supabase_uid: str,
    ) -> BillingEntryInDB:
        """
        Validate, apply pro-bono zeroing, and persist a billing entry.

        For TIME entries on a pro-bono matter, ``amount`` is forced to 0.0.

        :param entry: Validated billing entry domain model (not yet in DB).
        :type entry: BillingEntry
        :param supabase_uid: Auth UID of the creating user (for audit log).
        :type supabase_uid: str
        :return: Persisted billing entry record.
        :rtype: BillingEntryInDB
        """
        matter = self._matter_repo.select_one(condition={"id": entry.matter_id})
        if matter is None:
            raise ValueError("Matter not found: id=%s" % entry.matter_id)

        data = entry.model_dump()

        # Enforce pro-bono zero-rate for time entries
        if matter.is_pro_bono and entry.entry_type == EntryType.time:
            data["amount"] = 0.0
            data["rate"] = 0.0
            LOGGER.info(
                "BillingService.create_entry: pro-bono zeroed matter_id=%s", entry.matter_id,
            )

        # Compute amount from hours * rate if not explicitly set
        if entry.entry_type == EntryType.time and data.get("amount") is None:
            if data.get("hours") and data.get("rate") is not None:
                data["amount"] = round(data["hours"] * data["rate"], 2)

        created = self._entry_repo.insert(data)
        self._audit.log(
            supabase_uid=supabase_uid,
            action="billing_entry.created",
            entity_type="billing_entry",
            entity_id=str(created.id),
            after_json=created.model_dump(mode="json"),
        )
        LOGGER.info("BillingService.create_entry: created entry_id=%s", created.id)
        return created

    def update_entry(
        self,
        entry_id: int,
        updates: dict,
        supabase_uid: str,
    ) -> BillingEntryInDB:
        """
        Apply updates to a billing entry and write an audit record.

        :param entry_id: Primary key of the entry to update.
        :type entry_id: int
        :param updates: Dict of fields to change (must be validated by caller).
        :type updates: dict
        :param supabase_uid: Auth UID of the updating user.
        :type supabase_uid: str
        :return: Updated billing entry record.
        :rtype: BillingEntryInDB
        """
        before = self._entry_repo.select_one(condition={"id": entry_id})
        if before is None:
            raise ValueError("Billing entry not found: id=%s" % entry_id)
        if before.billed:
            raise ValueError("Cannot edit a billed entry: id=%s" % entry_id)

        updated = self._entry_repo.update(entry_id, updates)
        self._audit.log(
            supabase_uid=supabase_uid,
            action="billing_entry.updated",
            entity_type="billing_entry",
            entity_id=str(entry_id),
            before_json=before.model_dump(mode="json"),
            after_json=updated.model_dump(mode="json"),
        )
        LOGGER.info("BillingService.update_entry: updated entry_id=%s", entry_id)
        return updated

    def delete_entry(self, entry_id: int, supabase_uid: str) -> bool:
        """
        Delete an unbilled billing entry and write an audit record.

        :param entry_id: Primary key of the entry to delete.
        :type entry_id: int
        :param supabase_uid: Auth UID of the deleting user.
        :type supabase_uid: str
        :return: True on success.
        :rtype: bool
        :raises ValueError: If the entry is already billed.
        """
        before = self._entry_repo.select_one(condition={"id": entry_id})
        if before is None:
            raise ValueError("Billing entry not found: id=%s" % entry_id)
        if before.billed:
            raise ValueError("Cannot delete a billed entry: id=%s" % entry_id)

        self._entry_repo.delete(entry_id)
        self._audit.log(
            supabase_uid=supabase_uid,
            action="billing_entry.deleted",
            entity_type="billing_entry",
            entity_id=str(entry_id),
            before_json=before.model_dump(mode="json"),
        )
        LOGGER.info("BillingService.delete_entry: deleted entry_id=%s", entry_id)
        return True

    # ── Natural-language parsing ────────────────────────────────────────────

    def parse_natural_language(self, text: str) -> ParsedBillingEntry:
        """
        Parse a free-text billing description into a structured entry via LLM.

        Uses the fast LLM vendor. The result is a preview — not committed to
        the database until the attorney clicks Commit.

        :param text: Natural-language billing description, e.g.
                     ``"bill .25 to Anna Jones Divorce for drafting initial petition"``.
        :type text: str
        :return: Parsed billing entry fields.
        :rtype: ParsedBillingEntry
        :raises ValueError: If the LLM response cannot be parsed as JSON.
        """
        from datetime import date as date_type
        LOGGER.info("BillingService.parse_natural_language: parsing entry")
        prompt = _NL_BILLING_SYSTEM_PROMPT.format(today=date_type.today().isoformat())
        response_text = llm_service.complete_fast(prompt, text)
        try:
            data = json.loads(response_text)
            return ParsedBillingEntry.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            LOGGER.warning(
                "BillingService.parse_natural_language: LLM parse failure: %s", str(e),
            )
            raise ValueError("Could not parse billing entry — please clarify your description") from e

    # ── Balance calculation ────────────────────────────────────────────────

    def get_client_balance(self, matter_id: int) -> dict:
        """
        Compute the client financial balance for the ClientBalanceWidget.

        Formula: ``trust_balance − unbilled_time − unbilled_expenses``

        Returns a dict with ``balance``, ``trust_balance``,
        ``unbilled_total``, and ``status`` (``'green'``, ``'yellow'``, ``'red'``).

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: Balance summary dict.
        :rtype: dict
        """
        matter = self._matter_repo.select_one(condition={"id": matter_id})
        if matter is None:
            raise ValueError("Matter not found: id=%s" % matter_id)

        ledger_entries = self._trust_repo.get_by_matter(matter_id)
        trust_balance = sum(
            e.amount if e.transaction_type.value == "deposit" else -e.amount
            for e in ledger_entries
        )

        unbilled = self._entry_repo.get_unbilled_for_matter(matter_id)
        unbilled_total = sum(
            (e.amount or (e.hours or 0) * (e.rate or 0))
            for e in unbilled
            if e.billable
        )

        balance = trust_balance - unbilled_total
        threshold = matter.retainer_amount * matter.refresh_trigger_pct

        if balance < 0:
            status = "red"
        elif balance < threshold:
            status = "yellow"
        else:
            status = "green"

        return {
            "matter_id": matter_id,
            "trust_balance": round(trust_balance, 2),
            "unbilled_total": round(unbilled_total, 2),
            "balance": round(balance, 2),
            "status": status,
        }

    # ── Billing cycle closure ──────────────────────────────────────────────

    def close_billing_cycle(self, cycle_id: int, staff_id: int, supabase_uid: str) -> None:
        """
        Close a billing cycle: mark all open entries as billed and set cycle status to closed.

        The PDF bill generation and Stripe payment link creation are handled
        by separate service calls after this method returns.

        :param cycle_id: Primary key of the billing cycle to close.
        :type cycle_id: int
        :param staff_id: Staff member closing the cycle.
        :type staff_id: int
        :param supabase_uid: Auth UID for the audit log.
        :type supabase_uid: str
        :raises ValueError: If the cycle is already closed.
        """
        cycle = self._cycle_repo.select_one(condition={"id": cycle_id})
        if cycle is None:
            raise ValueError("Billing cycle not found: id=%s" % cycle_id)
        if cycle.status == BillingCycleStatus.closed:
            raise ValueError("Billing cycle is already closed: id=%s" % cycle_id)

        before_json = cycle.model_dump(mode="json")

        # Mark all entries in this cycle as billed
        entries = self._entry_repo.get_by_cycle(cycle_id)
        for entry in entries:
            self._entry_repo.update(entry.id, {"billed": True})

        # Close the cycle
        updated_cycle = self._cycle_repo.update(
            cycle_id,
            {"status": BillingCycleStatus.closed.value, "closed_by_staff_id": staff_id},
        )

        self._audit.log(
            supabase_uid=supabase_uid,
            action="billing_cycle.closed",
            entity_type="billing_cycle",
            entity_id=str(cycle_id),
            before_json=before_json,
            after_json=updated_cycle.model_dump(mode="json"),
        )
        LOGGER.info(
            "BillingService.close_billing_cycle: closed cycle_id=%s entries=%s",
            cycle_id,
            len(entries),
        )
