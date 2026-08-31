"""
app/db/repositories/financial.py - CRUD for accounts, statements, transactions.
"""
from datetime import date
from typing import Optional

from db.models.financial import (
    FinancialAccountInDB,
    FinancialAccountStatementInDB,
    FinancialAccountTransactionInDB,
    StatementReviewStatus,
    TransactionCategoryInDB,
    TransactionTagInDB,
    TransactionTagLinkInDB,
)
from db_handler import BaseRepository, DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


def _chunks(items: list[int], size: int) -> list[list[int]]:
    """Split an id list so a PostgREST ``in_`` filter stays inside the URI limit."""
    return [items[i:i + size] for i in range(0, len(items), size)]


class FinancialAccountRepository(BaseRepository[FinancialAccountInDB]):
    """CRUD for the ``financial_accounts`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "financial_accounts", FinancialAccountInDB)

    def get_by_matter(self, matter_id: int) -> list[FinancialAccountInDB]:
        """Return every account on a matter, institution first."""
        return self.select_many(condition={"matter_id": matter_id}, sort_by="institution")[0]

    def find_match(
        self,
        matter_id: int,
        institution: str,
        last4: Optional[str],
    ) -> Optional[FinancialAccountInDB]:
        """
        Find the account a statement belongs to.

        Institution plus the last four digits is the dedup key — the same pair
        the unique index enforces. Without a last four there is nothing solid
        to match on, so this returns None and the caller flags the statement
        rather than guessing: attaching a statement to the wrong account is far
        worse than leaving it for an attorney.

        :param matter_id: Matter to search within.
        :param institution: Institution name as printed.
        :param last4: Last four digits of the account number, if known.
        :return: The matching account, or None.
        :rtype: Optional[FinancialAccountInDB]
        """
        if not last4:
            return None
        wanted = institution.strip().lower()
        for account in self.get_by_matter(matter_id):
            if account.account_number_last4 == last4 and account.institution.strip().lower() == wanted:
                return account
        return None

    def others_with_last4(
        self,
        matter_id: int,
        institution: str,
        last4: Optional[str],
    ) -> list[FinancialAccountInDB]:
        """
        Accounts on this matter ending in the same four digits under a different name.

        The dedup key is institution *plus* last four, which means a misread
        institution opens a second account for one that already exists. That is
        the single most common way this data goes wrong, because the institution
        is the field extraction gets wrong: it is often printed only in the
        letterhead graphic, and the nearest other company name on the page
        belongs to whoever printed the form.

        This does not merge anything. Two different institutions really can share
        a last four, and joining accounts on a guess would be worse than the
        split. It reports, and a person decides.

        :return: Same last four, different institution. Empty when there is
            nothing to look at, including when the number was unreadable.
        :rtype: list[FinancialAccountInDB]
        """
        if not last4:
            return []
        wanted = institution.strip().lower()
        return [
            a for a in self.get_by_matter(matter_id)
            if a.account_number_last4 == last4 and a.institution.strip().lower() != wanted
        ]

    def succession_chains(self, matter_id: int) -> list[list[FinancialAccountInDB]]:
        """
        Group a matter's accounts into successor chains, oldest first.

        An account that is reissued — a card replaced after fraud, a bank
        migration, a rollover — arrives as several accounts with different
        numbers. Read separately they look like three half-produced accounts
        with alarming gaps; read as a chain they are one continuous history and
        the gaps close. ``antecedent_account_id`` records that link, and this
        is what the completeness view walks.

        Cycles are tolerated rather than trusted: a chain stops once it revisits
        an account, so a bad edit degrades to a short chain instead of hanging.

        :param matter_id: Matter whose accounts to group.
        :return: One list per chain, each ordered predecessor → successor.
            Standalone accounts come back as single-element chains.
        :rtype: list[list[FinancialAccountInDB]]
        """
        accounts = self.get_by_matter(matter_id)
        by_id = {a.id: a for a in accounts}
        # A successor points backwards, so heads are the accounts nothing points at.
        successor_of: dict[int, int] = {}
        for account in accounts:
            if account.antecedent_account_id in by_id:
                successor_of[account.antecedent_account_id] = account.id

        heads = [
            a for a in accounts
            if a.antecedent_account_id is None or a.antecedent_account_id not in by_id
        ]
        chains: list[list[FinancialAccountInDB]] = []
        placed: set[int] = set()
        for head in heads:
            chain: list[FinancialAccountInDB] = []
            current: Optional[int] = head.id
            while current is not None and current not in placed:
                placed.add(current)
                chain.append(by_id[current])
                current = successor_of.get(current)
            chains.append(chain)

        # Anything left is inside a cycle; surface it rather than dropping it.
        orphans = [a for a in accounts if a.id not in placed]
        if orphans:
            LOGGER.warning(
                "financial: matter=%s has %d account(s) in an antecedent cycle",
                matter_id, len(orphans),
            )
            chains.extend([a] for a in orphans)
        return chains


class FinancialAccountStatementRepository(BaseRepository[FinancialAccountStatementInDB]):
    """CRUD for the ``financial_account_statements`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "financial_account_statements", FinancialAccountStatementInDB)

    def get_by_account(self, financial_account_id: int) -> list[FinancialAccountStatementInDB]:
        """Return an account's statements in period order."""
        return self.select_many(
            condition={"financial_account_id": financial_account_id},
            sort_by="period_start",
        )[0]

    def get_by_matter(self, matter_id: int) -> list[FinancialAccountStatementInDB]:
        """Return every statement on a matter."""
        return self.select_many(condition={"matter_id": matter_id}, sort_by="period_start")[0]

    def needing_review(self, matter_id: int) -> list[FinancialAccountStatementInDB]:
        """The exceptions queue: statements an attorney still has to look at."""
        return self.select_many(
            condition={"matter_id": matter_id, "review_status": StatementReviewStatus.needs_review.value},
            sort_by="period_end",
        )[0]

    def rejected_ids(self, matter_id: int) -> list[int]:
        """
        Statements whose extraction was thrown away.

        Rejecting flips a status rather than deleting rows, so the lines are
        still there. Every search excludes them — a discarded extraction must
        never reach an exhibit.
        """
        rows = self.select_many(
            condition={"matter_id": matter_id, "review_status": StatementReviewStatus.rejected.value},
        )[0]
        return [r.id for r in rows]

    def find_overlapping(
        self,
        financial_account_id: int,
        period_start: date,
        period_end: date,
    ) -> list[FinancialAccountStatementInDB]:
        """
        Statements on this account whose period shares a day with the given one.

        ``find_period`` only catches an *exact* repeat, and the same document
        does not always yield the same period twice. These statements print two
        different date ranges — the header says "5/01/24-5/31/24" while the
        account summary below it says "5/01/24 thru 6/02/24" — so a re-ingest
        can legitimately pick the other one and slip past an equality check.

        Overlap is reported, never skipped on. Consecutive statements do not
        share days, so an overlap is nearly always the same statement twice —
        but "nearly always" is not grounds for silently discarding evidence.

        :return: Overlapping statements, excluding an exact match and anything
            already rejected.
        :rtype: list[FinancialAccountStatementInDB]
        """
        return [
            s for s in self.get_by_account(financial_account_id)
            if s.review_status != StatementReviewStatus.rejected
            and not (s.period_start == period_start and s.period_end == period_end)
            and s.period_start <= period_end and period_start <= s.period_end
        ]

    def find_period(
        self,
        financial_account_id: int,
        period_start: date,
        period_end: date,
    ) -> Optional[FinancialAccountStatementInDB]:
        """
        Find an existing statement for the same account and period.

        Guards against ingesting the same PDF twice, which is routine when a
        production arrives in overlapping batches. Rejected rows do not count —
        a discarded extraction should not block a re-run.
        """
        for existing in self.get_by_account(financial_account_id):
            if existing.review_status == StatementReviewStatus.rejected:
                continue
            if existing.period_start == period_start and existing.period_end == period_end:
                return existing
        return None


class FinancialAccountTransactionRepository(BaseRepository[FinancialAccountTransactionInDB]):
    """CRUD for the ``financial_account_transactions`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "financial_account_transactions", FinancialAccountTransactionInDB)

    def get_by_statement(
        self,
        statement_id: int,
        include_deleted: bool = False,
    ) -> list[FinancialAccountTransactionInDB]:
        """
        Return a statement's lines in printed order.

        Dropped lines are excluded unless asked for. That default is what makes
        the soft delete honest: a line someone removed must not turn up in a
        reconciliation, an exhibit, or a total merely because a caller forgot a
        flag. ``include_deleted`` exists for the two callers that genuinely need
        every row — showing a person what they removed, and counting what a
        cascade is about to take.
        """
        rows = self.select_many(condition={"statement_id": statement_id}, sort_by="line_no")[0]
        return rows if include_deleted else [r for r in rows if r.deleted_at is None]

    def get_by_account(
        self,
        financial_account_id: int,
        include_deleted: bool = False,
    ) -> list[FinancialAccountTransactionInDB]:
        """
        Return an account's whole history in date order.

        This is the query behind every waste and reimbursement exhibit, which is
        why dropped lines are excluded by default.
        """
        rows = self.select_many(
            condition={"financial_account_id": financial_account_id},
            sort_by="transaction_date",
        )[0]
        return rows if include_deleted else [r for r in rows if r.deleted_at is None]

    def search(
        self,
        account_ids: list[int],
        exclude_statement_ids: Optional[list[int]] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        category_ids: Optional[list[int]] = None,
        uncategorized: bool = False,
        transaction_ids: Optional[list[int]] = None,
        exclude_transaction_ids: Optional[list[int]] = None,
        text: Optional[str] = None,
        check_number: Optional[str] = None,
        checks_only: bool = False,
        include_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[FinancialAccountTransactionInDB], int]:
        """
        Filtered search across a matter's transactions.

        This is the one place in the repository layer that builds a PostgREST
        query by hand instead of passing a condition dict. The condition dict
        supports equality, IN, and null checks only — it has no range operator
        and no pattern match, so a date window and a description search cannot
        be expressed through it at all. Building the query here keeps that
        knowledge inside the database layer, where the rest of it already lives.

        ``account_ids`` is required, and it is what scopes a search to a matter:
        transactions carry no ``matter_id``, so the caller resolves the matter's
        accounts first. That also means a caller cannot reach another matter's
        lines by guessing an id.

        :param account_ids: Accounts to search within — the matter's accounts.
        :param exclude_statement_ids: Statements whose lines to omit; used to
            keep rejected extractions out of every result set.
        :param date_from: Earliest transaction date, inclusive.
        :param date_to: Latest transaction date, inclusive.
        :param category_ids: Categories to include, already expanded to
            descendants by the caller.
        :param uncategorized: Return only lines with no category. Overrides
            ``category_ids`` when both are given.
        :param transaction_ids: Restrict to these ids — how the tag filter is
            applied, the tags living in a join table.
        :param exclude_transaction_ids: Omit these ids. This is how "untagged"
            is expressed: naming the tagged lines to skip, rather than listing
            the complement. Tagging is manual, so the tagged set is small and
            bounded while the complement is the whole production.
        :param text: Case-insensitive substring of the description.
        :param check_number: One check, by number. A check is the only debit
            that does not say where the money went, so this is how a payment
            gets traced back to the instrument that made it.
        :param checks_only: Every check on the account and nothing else.
        :param include_deleted: Show lines a person dropped from their
            statement. Off by default — a dropped line must never reach a total
            or an exhibit through an oversight.
        :param limit: Page size.
        :param offset: Rows to skip.
        :return: ``(rows, total_matching)``. The total counts every match, not
            just this page, so the UI can say how large the exhibit would be.
        :rtype: tuple[list[FinancialAccountTransactionInDB], int]
        """
        if not account_ids:
            return [], 0
        # An empty id list means the tag filter matched nothing. That is a real
        # empty result, not an absent filter, so return before querying.
        if transaction_ids is not None and not transaction_ids:
            return [], 0

        query = (
            self.manager.client.table(self.table_name)
            .select("*", count="exact")
            .in_("financial_account_id", account_ids)
        )
        if exclude_statement_ids:
            query = query.not_.in_("statement_id", exclude_statement_ids)
        if transaction_ids is not None:
            query = query.in_("id", transaction_ids)
        if exclude_transaction_ids:
            query = query.not_.in_("id", exclude_transaction_ids)
        if date_from is not None:
            query = query.gte("transaction_date", date_from.isoformat())
        if date_to is not None:
            query = query.lte("transaction_date", date_to.isoformat())
        if uncategorized:
            query = query.is_("category_id", "null")
        elif category_ids:
            query = query.in_("category_id", category_ids)
        if check_number:
            query = query.eq("check_number", check_number.strip().rstrip("*").strip())
        elif checks_only:
            query = query.not_.is_("check_number", "null")
        if not include_deleted:
            query = query.is_("deleted_at", "null")
        if text:
            # Escape the pattern wildcards so a literal % or _ typed into the
            # search box matches itself rather than everything.
            escaped = text.replace("%", "\\%").replace("_", "\\_")
            query = query.ilike("description", "*" + escaped + "*")

        query = (
            query
            .order("transaction_date", desc=False)
            .order("id", desc=False)
            .range(offset, offset + limit - 1)
        )
        result = query.execute()
        rows = [FinancialAccountTransactionInDB(**row) for row in (result.data or [])]
        return rows, result.count or 0


class TransactionCategoryRepository(BaseRepository[TransactionCategoryInDB]):
    """CRUD for the firm-wide ``transaction_categories`` tree."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "transaction_categories", TransactionCategoryInDB)

    def get_all(self, include_inactive: bool = False) -> list[TransactionCategoryInDB]:
        """
        The whole tree in display order.

        Small enough to read whole — a chart of accounts is tens of rows, and
        every caller that needs one node needs its ancestors or descendants too.
        """
        condition: dict = {} if include_inactive else {"is_active": True}
        return self.select_many(condition=condition, sort_by="display_order")[0]

    def expand(self, category_ids: list[int]) -> list[int]:
        """
        Widen a selection to include every descendant.

        Filtering on "Housing" has to catch the rent filed under "Rent", or the
        parent headings are useless as filters.

        Walks iteratively with a seen-set rather than recursing, so a cycle
        introduced by a bad edit terminates instead of exhausting the stack.

        :param category_ids: The categories the user picked.
        :return: Those ids plus every descendant, deduplicated.
        :rtype: list[int]
        """
        if not category_ids:
            return []
        children: dict[int, list[int]] = {}
        for category in self.get_all(include_inactive=True):
            if category.parent_id is not None:
                children.setdefault(category.parent_id, []).append(category.id)

        seen: set[int] = set()
        pending = list(category_ids)
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(children.get(current, []))
        return sorted(seen)

    def in_use(self, category_id: int) -> int:
        """How many transactions are filed under a category. Guards deletion."""
        result = (
            self.manager.client.table("financial_account_transactions")
            .select("id", count="exact")
            .eq("category_id", category_id)
            .limit(1)
            .execute()
        )
        return result.count or 0


class TransactionTagRepository(BaseRepository[TransactionTagInDB]):
    """CRUD for ``transaction_tags`` — firm-wide and matter-scoped labels."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "transaction_tags", TransactionTagInDB)

    def available_for_matter(
        self,
        matter_id: int,
        include_inactive: bool = False,
    ) -> list[TransactionTagInDB]:
        """
        Every tag offered on a matter: the firm-wide layer plus the case's own.

        Two queries rather than one, because PostgREST cannot express
        "matter_id is null OR matter_id = 7" through a condition without
        dropping to a raw ``or=`` string that is harder to read than this is to
        run.
        """
        firm = self.select_many(condition={"matter_id": None}, sort_by="display_order")[0]
        mine = self.select_many(condition={"matter_id": matter_id}, sort_by="display_order")[0]
        tags = firm + mine
        if not include_inactive:
            tags = [t for t in tags if t.is_active]
        return tags

    def in_use(self, tag_id: int) -> int:
        """How many transactions carry a tag. Guards deletion."""
        result = (
            self.manager.client.table("financial_account_transaction_tags")
            .select("id", count="exact")
            .eq("tag_id", tag_id)
            .limit(1)
            .execute()
        )
        return result.count or 0


class TransactionTagLinkRepository(BaseRepository[TransactionTagLinkInDB]):
    """CRUD for the ``financial_account_transaction_tags`` join table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "financial_account_transaction_tags", TransactionTagLinkInDB)

    def for_transactions(self, transaction_ids: list[int]) -> dict[int, list[int]]:
        """
        Tag ids per transaction, for decorating a page of search results.

        :return: ``{transaction_id: [tag_id, ...]}``; untagged lines are absent.
        :rtype: dict[int, list[int]]
        """
        if not transaction_ids:
            return {}
        by_transaction: dict[int, list[int]] = {}
        for chunk in _chunks(transaction_ids, 200):
            for link in self.select_many(condition={"transaction_id": chunk})[0]:
                by_transaction.setdefault(link.transaction_id, []).append(link.tag_id)
        return by_transaction

    def transactions_with_tags(self, tag_ids: list[int], match_all: bool) -> list[int]:
        """
        Which transactions carry these tags.

        :param tag_ids: Tags the user filtered on.
        :param match_all: True to require every tag — narrowing an exhibit to
            the intersection; False to accept any of them.
        :return: Matching transaction ids.
        :rtype: list[int]
        """
        if not tag_ids:
            return []
        links = self.select_many(condition={"tag_id": tag_ids})[0]
        by_transaction: dict[int, set[int]] = {}
        for link in links:
            by_transaction.setdefault(link.transaction_id, set()).add(link.tag_id)
        if match_all:
            wanted = set(tag_ids)
            return sorted(tid for tid, tags in by_transaction.items() if wanted <= tags)
        return sorted(by_transaction)

    def find_link(self, transaction_id: int, tag_id: int) -> Optional[TransactionTagLinkInDB]:
        """The existing link for a pair, if the tag is already applied."""
        return self.select_one(condition={"transaction_id": transaction_id, "tag_id": tag_id})
