"""
app/services/client_portal_service.py - What a client may see, and the one thing they may do.

THE CLIENT IS THE FIRST CALLER WHO IS NOT ON THE FIRM'S SIDE OF THE TABLE
────────────────────────────────────────────────────────────────────────────
Every access decision in Cyclone until now has been made between staff, who are
all entitled to see a matter's records. A wrong filter there shows a paralegal
something they could have opened anyway. Here a wrong filter crosses a party
boundary.

`SupabaseManager` holds the service-role key and bypasses RLS by design
(CLAUDE.md §7), so the database will not catch a mistake. **Every portal
endpoint therefore resolves its matters through `matter_ids` here, and nothing
else.** One function to get right, the way `_verify_on_matter` is the one
function for staff.

WHAT IS DELIBERATELY NOT HERE
────────────────────────────────────────────────────────────────────────────
Tom's call is that a client sees the whole matter, both sides' accounts
included. Three things still do not reach the portal, and none of them is about
trusting the client:

  * **Undisclosed accounts, and the creditor and custodian triage queues.**
    Those are unreviewed candidates. "Possible undisclosed account at Wells
    Fargo" in front of a client before counsel has formed a view is a phone
    call nobody wants, and the exhibit itself says a candidate is not a finding.
  * **Extraction provenance** — which model read which pages. True, irrelevant,
    and an invitation.
  * **Statement flags as staff read them.** "Extraction very likely stopped
    early — re-ingest before relying on it" is written for a paralegal. A
    client reading it about their own bank statement concludes the system is
    broken. The portal reports a simplified state instead.

TAGGING IS THE ONE WRITE
────────────────────────────────────────────────────────────────────────────
And it is bounded four ways: the matter must allow it (`client_tagging_enabled`,
turned off for hearing prep), the tag must be one the firm published
(`client_visible`), the transaction must be on their matter, and a tag staff
have CLEARED can never be re-applied by the client — only by staff. That last
one is why clearing is a suppression rather than a delete; see migration 044.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from db.models.financial import TagSource, TransactionTagLink
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    TransactionTagLinkRepository,
    TransactionTagRepository,
)
from db.repositories.matter import MatterRepository
from db_handler import DatabaseManager
from services.transaction_search_service import transaction_search_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class PortalDenied(Exception):
    """The caller asked for something outside their own matters."""


class TaggingClosed(Exception):
    """Tagging is turned off on this matter — hearing prep, usually."""


class ClientPortalService:
    """Read-only matter data for a client, plus tagging."""

    # ── Scope ─────────────────────────────────────────────────────────────

    @staticmethod
    def matters(manager: DatabaseManager, client_id: int) -> list[Any]:
        """
        Every matter this client is the client on.

        :param client_id: From ``user_roles``, never from the request body.
        """
        return MatterRepository(manager).select_many(
            condition={"client_id": client_id}, sort_by="id")[0]

    def matter_ids(self, manager: DatabaseManager, client_id: int) -> set[int]:
        """The ids of those matters — the boundary every other method checks."""
        return {m.id for m in self.matters(manager, client_id)}

    def require_matter(self, manager: DatabaseManager, client_id: int, matter_id: int) -> Any:
        """
        The matter, if it is theirs.

        :raises PortalDenied: If it is not. The message never says whether the
            matter exists — an answer that distinguished "not yours" from "no
            such matter" would confirm other clients' matter ids by exhaustion.
        """
        for matter in self.matters(manager, client_id):
            if matter.id == matter_id:
                return matter
        raise PortalDenied("That matter is not available on this account")

    def require_transaction(
        self, manager: DatabaseManager, client_id: int, transaction_id: int,
    ) -> tuple[Any, int]:
        """
        A transaction and the matter it sits on, if that matter is theirs.

        Transactions carry no matter id — the account is the boundary, exactly
        as it is for staff — so this resolves the account and then the matter.

        :return: ``(transaction, matter_id)``.
        :raises PortalDenied: If the line is not on one of their matters.
        """
        from db.repositories.financial import FinancialAccountTransactionRepository

        transaction = FinancialAccountTransactionRepository(manager).select_one(
            condition={"id": transaction_id})
        if transaction is None:
            raise PortalDenied("That transaction is not available on this account")
        account = FinancialAccountRepository(manager).select_one(
            condition={"id": transaction.financial_account_id})
        if account is None or account.matter_id not in self.matter_ids(manager, client_id):
            raise PortalDenied("That transaction is not available on this account")
        return transaction, account.matter_id

    # ── Reading ───────────────────────────────────────────────────────────

    def accounts(self, manager: DatabaseManager, client_id: int, matter_id: int) -> list[Any]:
        """Every account on the matter, both sides. Read-only here."""
        self.require_matter(manager, client_id, matter_id)
        return FinancialAccountRepository(manager).get_by_matter(matter_id)

    def statements(self, manager: DatabaseManager, client_id: int, account_id: int) -> list[Any]:
        """
        The statements held for one account.

        Rejected extractions are excluded: a discarded read is not a document
        the client holds, and showing it invites a question with no useful
        answer.
        """
        account = FinancialAccountRepository(manager).select_one(condition={"id": account_id})
        if account is None or account.matter_id not in self.matter_ids(manager, client_id):
            raise PortalDenied("That account is not available on this account")
        repo = FinancialAccountStatementRepository(manager)
        return [s for s in repo.get_by_account(account_id)
                if getattr(s.review_status, "value", s.review_status) != "rejected"]

    def search(
        self, manager: DatabaseManager, client_id: int, matter_id: int, **filters: Any,
    ) -> dict[str, Any]:
        """
        The matter's transactions, with each line's tags and what was cleared.

        The staff search does the work — one implementation, so the client
        cannot see a different set from the one their attorney sees. What is
        added is `cleared_tag_ids`, which the portal greys out: a client whose
        tag simply vanished re-applies it, is refused, and reports a bug.
        """
        self.require_matter(manager, client_id, matter_id)
        result = transaction_search_service.search(manager, matter_id, **filters)
        ids = [row["id"] for row in result["items"]]
        cleared = TransactionTagLinkRepository(manager).cleared_for_transactions(ids)
        for row in result["items"]:
            row["cleared_tag_ids"] = cleared.get(row["id"], [])
        return result

    def tags(self, manager: DatabaseManager, client_id: int, matter_id: int) -> list[Any]:
        """
        The tags the firm has published to clients on this matter.

        Opt-in (044): the firm's own list carries litigation strategy, so a tag
        reaches a client only when somebody ticked the box.
        """
        self.require_matter(manager, client_id, matter_id)
        return [
            tag for tag in TransactionTagRepository(manager).available_for_matter(matter_id)
            if tag.client_visible
        ]

    # ── The one write ─────────────────────────────────────────────────────

    def apply_tag(
        self,
        manager: DatabaseManager,
        client_id: int,
        transaction_id: int,
        tag_id: int,
    ) -> str:
        """
        Put a client's tag on a line.

        :return: ``"applied"``, or ``"already"`` when it was already there.
        :raises PortalDenied: Not their line, or not a tag published to them.
        :raises TaggingClosed: Tagging is off on this matter, or staff have
            cleared this exact tag from this exact line. Both are refusals the
            client cannot work around, and the sentence says which.
        """
        transaction, matter_id = self.require_transaction(manager, client_id, transaction_id)
        matter = self.require_matter(manager, client_id, matter_id)
        if not getattr(matter, "client_tagging_enabled", False):
            raise TaggingClosed(
                "Tagging is paused on this matter while your attorney's office prepares. "
                "Your existing tags are safe.")

        tag = next((t for t in self.tags(manager, client_id, matter_id) if t.id == tag_id), None)
        if tag is None:
            raise PortalDenied("That tag is not available on this matter")

        link_repo = TransactionTagLinkRepository(manager)
        existing = link_repo.find_link(transaction_id, tag_id)
        if existing is not None:
            if existing.cleared_at is not None:
                # THE ASYMMETRY, AND IT IS THE POINT. Staff reviewed this exact
                # tag on this exact line and took it off. Staff can put it back;
                # the client cannot, or clearing would be a suggestion.
                raise TaggingClosed(
                    "Your attorney's office has reviewed this one and removed that tag, "
                    "so it cannot be put back here.")
            return "already"

        link_repo.insert(TransactionTagLink(
            transaction_id=transaction_id,
            tag_id=tag_id,
            source=TagSource.client,
            tagged_by_client_id=client_id,
        ).model_dump())
        LOGGER.info("client_portal.apply_tag: matter=%s transaction=%s tag=%s client=%s",
                    matter_id, transaction_id, tag_id, client_id)
        return "applied"

    def remove_tag(
        self,
        manager: DatabaseManager,
        client_id: int,
        transaction_id: int,
        tag_id: int,
    ) -> str:
        """
        Take a client's own tag off again.

        **Only their own, and only a live one.** Removing a tag staff applied
        would let a client edit the firm's work product, and "removing" a
        cleared one is already true.

        :return: ``"removed"``, or ``"absent"`` when there was nothing to remove.
        """
        _, matter_id = self.require_transaction(manager, client_id, transaction_id)
        matter = self.require_matter(manager, client_id, matter_id)
        if not getattr(matter, "client_tagging_enabled", False):
            raise TaggingClosed(
                "Tagging is paused on this matter while your attorney's office prepares. "
                "Your existing tags are safe.")

        link_repo = TransactionTagLinkRepository(manager)
        existing = link_repo.find_link(transaction_id, tag_id)
        if existing is None or existing.cleared_at is not None:
            return "absent"
        if existing.source != TagSource.client or existing.tagged_by_client_id != client_id:
            raise PortalDenied("That tag was applied by your attorney's office")

        # A deletion, not a clearing: the client taking back their own guess is
        # not a suppression of anybody's assertion, and it leaves the pair free
        # so they can change their mind again.
        link_repo.delete(existing.id)
        LOGGER.info("client_portal.remove_tag: matter=%s transaction=%s tag=%s client=%s",
                    matter_id, transaction_id, tag_id, client_id)
        return "removed"

    # ── What staff see of all this ────────────────────────────────────────

    @staticmethod
    def clear_tag(
        manager: DatabaseManager,
        link_id: int,
        staff_id: int,
    ) -> Any:
        """
        Staff remove a client's tag, permanently as far as the client is concerned.

        Used by the staff review screen, which works link by link rather than
        in bulk: clearing is a judgment about one assertion.
        """
        link_repo = TransactionTagLinkRepository(manager)
        link = link_repo.select_one(condition={"id": link_id})
        if link is None:
            raise ValueError("No such tag")
        if link.cleared_at is not None:
            return link
        return link_repo.update(link_id, {
            "cleared_at": datetime.now(timezone.utc).isoformat(),
            "cleared_by_staff_id": staff_id,
        })

    @staticmethod
    def restore_tag(manager: DatabaseManager, link_id: int, staff_id: int) -> Any:
        """
        Put back a tag staff cleared, as a STAFF tag.

        Staff can undo their own clearing — the asymmetry is with the client,
        not with the decision. It becomes a staff tag because applying it is now
        their judgment, which is also what stops a restored tag reading as the
        client's assertion in an exhibit.
        """
        link_repo = TransactionTagLinkRepository(manager)
        link = link_repo.select_one(condition={"id": link_id})
        if link is None:
            raise ValueError("No such tag")
        return link_repo.update(link_id, {
            "cleared_at": None,
            "cleared_by_staff_id": None,
            "source": TagSource.staff.value,
            "tagged_by_staff_id": staff_id,
            "tagged_by_client_id": None,
        })

    @staticmethod
    def review_list(
        manager: DatabaseManager,
        matter_id: int,
        include_cleared: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Every client tag on a matter, for the staff review screen.

        Scoped through the matter's accounts, like every other transaction
        query: client tags are rows in a firm-wide table, so asking for "client
        tags" without that scope returns every case's.
        """
        from db.repositories.financial import FinancialAccountTransactionRepository

        accounts = FinancialAccountRepository(manager).get_by_matter(matter_id)
        if not accounts:
            return []
        account_by_id = {a.id: a for a in accounts}

        link_repo = TransactionTagLinkRepository(manager)
        condition: dict[str, Any] = {"source": TagSource.client.value}
        if not include_cleared:
            condition["cleared_at"] = None
        links = link_repo.select_many(condition=condition, sort_by="created_at")[0]
        if not links:
            return []

        transaction_repo = FinancialAccountTransactionRepository(manager)
        transactions = {
            t.id: t for t in transaction_repo.select_many(
                condition={"id": [link.transaction_id for link in links]})[0]
        }
        labels = {t.id: t.label for t in TransactionTagRepository(manager)
                  .available_for_matter(matter_id, include_inactive=True)}

        out: list[dict[str, Any]] = []
        for link in links:
            transaction = transactions.get(link.transaction_id)
            if transaction is None or transaction.financial_account_id not in account_by_id:
                continue
            account = account_by_id[transaction.financial_account_id]
            out.append({
                "link_id": link.id,
                "transaction_id": transaction.id,
                "tag_id": link.tag_id,
                "tag_label": labels.get(link.tag_id, "#%d" % link.tag_id),
                "matter_id": matter_id,
                "transaction_date": transaction.transaction_date,
                "description": transaction.description,
                "amount": transaction.amount,
                "institution": account.institution,
                "account_last4": account.account_number_last4,
                "tagged_at": link.created_at,
                "cleared_at": link.cleared_at,
            })
        return out

    @staticmethod
    def client_tag_counts(manager: DatabaseManager, client_id: Optional[int] = None) -> dict[str, int]:
        """
        How much client tagging has been done, and how much of it staff removed.

        **The cleared count is a coaching signal**, not bookkeeping: a client
        cleared forty times is mis-tagging systematically, and nobody would
        otherwise ever see that.
        """
        repo = TransactionTagLinkRepository(manager)
        condition: dict[str, Any] = {"source": TagSource.client.value}
        if client_id is not None:
            condition["tagged_by_client_id"] = client_id
        links = repo.select_many(condition=condition)[0]
        return {
            "applied": len(links),
            "cleared": sum(1 for link in links if link.cleared_at is not None),
        }


client_portal_service = ClientPortalService()
