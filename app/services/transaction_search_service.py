"""
app/services/transaction_search_service.py - Find and classify transactions.

Everything downstream of ingestion runs through here. Two axes do the work, and
they are deliberately not the same mechanism:

* **Category** — one per transaction, from a firm-wide hierarchy. It drives the
  Financial Information Statement, the personal income statement filed for a
  temporary orders hearing. Exactly one bucket per line, or the statement
  double-counts.
* **Tags** — many per transaction, in two layers. They drive the Rule 1006
  summaries behind waste, constructive fraud, and reimbursement claims. One
  line is routinely evidence in several exhibits at once.

The search itself has one non-obvious rule: **a matter's accounts are the
scope**. Transactions carry no matter_id, so every query resolves the matter's
accounts first. That is not just plumbing — it is what stops a crafted request
from reaching another matter's records.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from db.models.financial import TransactionTagLink
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
    TransactionCategoryRepository,
    TransactionTagLinkRepository,
    TransactionTagRepository,
)
from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

_ZERO = Decimal("0.00")

# A page of results. Generous because the common move is to filter down to an
# exhibit and then tag the whole thing at once.
MAX_PAGE_SIZE = 1000


class TransactionSearchService:
    """Filtered search over a matter's transactions, plus tagging and categorizing."""

    # ── Search ────────────────────────────────────────────────────────────

    def search(
        self,
        manager: DatabaseManager,
        matter_id: int,
        account_ids: Optional[list[int]] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        category_ids: Optional[list[int]] = None,
        include_subcategories: bool = True,
        uncategorized: bool = False,
        tag_ids: Optional[list[int]] = None,
        tag_match_all: bool = False,
        untagged: bool = False,
        text: Optional[str] = None,
        check_number: Optional[str] = None,
        checks_only: bool = False,
        include_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Run a filter and return one page of matching lines.

        :param matter_id: The matter to search. Always the outermost bound.
        :param account_ids: Narrow to these accounts. Ids not on the matter are
            dropped rather than trusted.
        :param date_from: Earliest transaction date, inclusive.
        :param date_to: Latest transaction date, inclusive.
        :param category_ids: Categories to include.
        :param include_subcategories: Expand each chosen category to its
            descendants. On by default: picking "Housing" and getting nothing
            because every line is filed under "Rent" is not a useful filter.
        :param uncategorized: Only lines with no category — the work queue for
            preparing an FIS.
        :param tag_ids: Tags to filter on.
        :param tag_match_all: Require every tag rather than any of them.
        :param untagged: Only lines carrying no tag at all.
        :param text: Case-insensitive substring of the description.
        :param check_number: One check, by number.
        :param checks_only: Every check on the account and nothing else — the
            starting point for asking where money went that the statement does
            not describe.
        :param include_deleted: Show lines somebody dropped from a statement.
            Off by default, so a dropped line cannot reach an exhibit through an
            oversight; on, it is how they are found again to restore.
        :param limit: Page size, capped at ``MAX_PAGE_SIZE``.
        :param offset: Rows to skip.
        :return: ``{"total": int, "items": [...], "sum_amount": str}``. Each
            item is the transaction plus its tag ids and enough account context
            to render a row without a second call.
        :rtype: dict[str, Any]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)
        link_repo = TransactionTagLinkRepository(manager)

        accounts = account_repo.get_by_matter(matter_id)
        if not accounts:
            return {"total": 0, "items": [], "sum_amount": "0.00"}

        allowed = {a.id for a in accounts}
        if account_ids:
            # Intersect rather than substitute. An id the caller sent that is
            # not on this matter is silently dropped; honouring it would let a
            # request reach across matters.
            scope = sorted(allowed & set(account_ids))
            if not scope:
                return {"total": 0, "items": [], "sum_amount": "0.00"}
        else:
            scope = sorted(allowed)

        # Tag filter first: it resolves to an id list the row query restricts to.
        # None means "no tag filter"; an empty list means "filtered, matched
        # nothing", and those must not collapse to the same thing.
        restrict_ids: Optional[list[int]] = None
        exclude_ids: Optional[list[int]] = None
        if untagged:
            # Expressed as "skip the tagged lines", not "list the untagged ones".
            # The complement is the whole production and would neither fit in a
            # PostgREST URL nor survive its max-rows cap; the tagged set is
            # manual work and stays small.
            exclude_ids = self._tagged_ids(manager, matter_id, scope)
        elif tag_ids:
            restrict_ids = link_repo.transactions_with_tags(tag_ids, match_all=tag_match_all)

        resolved_categories: Optional[list[int]] = None
        if category_ids and not uncategorized:
            category_repo = TransactionCategoryRepository(manager)
            resolved_categories = (
                category_repo.expand(category_ids) if include_subcategories else list(category_ids)
            )

        rows, total = transaction_repo.search(
            account_ids=scope,
            exclude_statement_ids=statement_repo.rejected_ids(matter_id),
            date_from=date_from,
            date_to=date_to,
            category_ids=resolved_categories,
            uncategorized=uncategorized,
            transaction_ids=restrict_ids,
            exclude_transaction_ids=exclude_ids,
            text=text,
            check_number=check_number,
            checks_only=checks_only,
            include_deleted=include_deleted,
            limit=min(limit, MAX_PAGE_SIZE),
            offset=offset,
        )

        tags_by_transaction = link_repo.for_transactions([r.id for r in rows])
        account_by_id = {a.id: a for a in accounts}
        items: list[dict[str, Any]] = []
        for row in rows:
            account = account_by_id.get(row.financial_account_id)
            items.append({
                **row.model_dump(mode="json"),
                "tag_ids": tags_by_transaction.get(row.id, []),
                "institution": account.institution if account else None,
                "account_last4": account.account_number_last4 if account else None,
            })

        # The page's own total, not the filter's. Summing every match would mean
        # a second full query, and a running total of what is on screen is what
        # an attorney is actually reading against.
        page_sum = sum((r.amount for r in rows), start=_ZERO)

        LOGGER.info(
            "transaction_search: matter=%s accounts=%d total=%d returned=%d",
            matter_id, len(scope), total, len(rows),
        )
        return {"total": total, "items": items, "sum_amount": str(page_sum)}

    @staticmethod
    def _tagged_ids(
        manager: DatabaseManager,
        matter_id: int,
        account_ids: list[int],
    ) -> list[int]:
        """
        Transactions *in this scope* that carry at least one tag.

        "Untagged" is the inverse of a join, which PostgREST cannot express, so
        the search skips this list instead.

        Narrowing to the scope's accounts is not an optimization. The firm-wide
        tags are applied across every matter, so the raw link rows for "Waste
        Claim" span the whole firm; excluding all of them would put every other
        case's line ids into this matter's query URL.

        :return: Tagged transaction ids within ``account_ids``.
        :rtype: list[int]
        """
        tag_repo = TransactionTagRepository(manager)
        link_repo = TransactionTagLinkRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        every_tag = [t.id for t in tag_repo.available_for_matter(matter_id, include_inactive=True)]
        candidates = link_repo.transactions_with_tags(every_tag, match_all=False)
        if not candidates:
            return []

        in_scope = set(account_ids)
        tagged: list[int] = []
        for chunk in (candidates[i:i + 200] for i in range(0, len(candidates), 200)):
            rows = transaction_repo.select_many(condition={"id": chunk})[0]
            tagged.extend(r.id for r in rows if r.financial_account_id in in_scope)

        # The list goes into a URL filter. A matter with this many tagged lines
        # is well past anything seen in practice, but say so rather than let a
        # request fail with an opaque 414.
        if len(tagged) > 2000:
            LOGGER.warning(
                "transaction_search: matter=%s has %d tagged lines; the untagged "
                "filter may exceed the request URI limit",
                matter_id, len(tagged),
            )
        return tagged

    # ── Categorizing ──────────────────────────────────────────────────────

    def set_category(
        self,
        manager: DatabaseManager,
        matter_id: int,
        transaction_ids: list[int],
        category_id: Optional[int],
    ) -> int:
        """
        File transactions under a category, or clear it with ``None``.

        :return: How many rows were changed.
        :rtype: int
        :raises ValueError: If the category does not exist, or if any
            transaction is not on this matter.
        """
        if category_id is not None:
            category = TransactionCategoryRepository(manager).select_one(condition={"id": category_id})
            if category is None:
                raise ValueError("No such category")

        transaction_repo = FinancialAccountTransactionRepository(manager)
        for transaction_id in self._verify_on_matter(manager, matter_id, transaction_ids):
            transaction_repo.update(transaction_id, {"category_id": category_id})

        LOGGER.info(
            "transaction_search.set_category: matter=%s count=%d category=%s",
            matter_id, len(transaction_ids), category_id,
        )
        return len(transaction_ids)

    # ── Tagging ───────────────────────────────────────────────────────────

    def apply_tag(
        self,
        manager: DatabaseManager,
        matter_id: int,
        transaction_ids: list[int],
        tag_id: int,
        staff_id: int,
        remove: bool = False,
    ) -> int:
        """
        Add or remove one tag across a set of transactions.

        Bulk on purpose. The workflow is "filter to the exhibit, then tag the
        result" — doing that a line at a time over a year of statements is the
        difference between a usable tool and a spreadsheet.

        :param tag_id: Must be a firm-wide tag, or one belonging to this matter.
        :param staff_id: Recorded on each link. Tagging is a judgment that gets
            cross-examined, so the record says who made it.
        :param remove: Remove the tag instead of applying it.
        :return: How many links were created or deleted. Re-applying a tag that
            is already there is a no-op, not an error, and is not counted.
        :rtype: int
        :raises ValueError: If the tag is not available on this matter, or if
            any transaction is not on it.
        """
        tag = TransactionTagRepository(manager).select_one(condition={"id": tag_id})
        if tag is None:
            raise ValueError("No such tag")
        if tag.matter_id is not None and tag.matter_id != matter_id:
            # A matter tag names facts from that case. Applying it elsewhere
            # would put another client's theory of the case on this record.
            raise ValueError("That tag belongs to a different matter")

        link_repo = TransactionTagLinkRepository(manager)
        verified = self._verify_on_matter(manager, matter_id, transaction_ids)

        changed = 0
        for transaction_id in verified:
            existing = link_repo.find_link(transaction_id, tag_id)
            if remove:
                if existing is not None:
                    link_repo.delete(existing.id)
                    changed += 1
            elif existing is None:
                link_repo.insert(TransactionTagLink(
                    transaction_id=transaction_id,
                    tag_id=tag_id,
                    tagged_by_staff_id=staff_id,
                ).model_dump())
                changed += 1

        LOGGER.info(
            "transaction_search.apply_tag: matter=%s tag=%s %s=%d",
            matter_id, tag_id, "removed" if remove else "applied", changed,
        )
        return changed

    # ── Shared ────────────────────────────────────────────────────────────

    @staticmethod
    def _verify_on_matter(
        manager: DatabaseManager,
        matter_id: int,
        transaction_ids: list[int],
    ) -> list[int]:
        """
        Confirm every id belongs to the matter before writing to it.

        The ids arrive from the client, and nothing about a transaction id says
        which matter it is on. Checking once here is what keeps every mutation
        below from having to think about it.

        :raises ValueError: Naming the count that did not belong, not the ids —
            echoing an id back confirms it exists.
        """
        if not transaction_ids:
            return []
        accounts = FinancialAccountRepository(manager).get_by_matter(matter_id)
        allowed = {a.id for a in accounts}
        if not allowed:
            raise ValueError("That matter has no financial accounts")

        transaction_repo = FinancialAccountTransactionRepository(manager)
        verified: list[int] = []
        for chunk in (transaction_ids[i:i + 200] for i in range(0, len(transaction_ids), 200)):
            rows = transaction_repo.select_many(condition={"id": chunk})[0]
            verified.extend(r.id for r in rows if r.financial_account_id in allowed)

        missing = len(transaction_ids) - len(verified)
        if missing:
            raise ValueError("%d transaction(s) are not on this matter" % missing)
        return verified


transaction_search_service = TransactionSearchService()
