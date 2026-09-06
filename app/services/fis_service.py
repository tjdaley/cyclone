"""
app/services/fis_service.py - The Financial Information Statement.

Averages a person's income and expenses over a window of **whole months**, one
line per category, and reports what it could not see.

Three things make this harder than dividing by the number of months, and all
three are the same failure: a document that looks complete and understates.

**Recurrence.** A payment made quarterly or annually buys coverage that extends
past the window. $3,600 of property tax paid once in January reads as $1,800 a
month over January-February and $1,200 over January-March -- same facts, a
different sworn figure every time the report is re-run. Those lines are computed
from the trailing twelve months instead, which is stable whenever it runs, sums
two tax parcels correctly, and still finds a payment that falls outside the
window entirely.

**Coverage.** The denominator is a claim. Dividing by eight months asserts that
we have eight months of statements; if the production covers five, every line is
understated by a proportion nobody can see on the page. So the statement reports
its own gaps, per account, and carries them onto the exhibit rather than leaving
them on screen.

**Uncategorized money.** A line nobody filed appears nowhere, while the net total
still looks authoritative. It gets its own row at the foot, with a count, so
"is this finished?" has an answer.

Everything here honours the sign convention the rest of the system uses: an
amount is signed by how it moved the balance, so income sums positive, expenses
negative, and a refund reduces its own expense line with no special-casing.
"""
from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from db.models.financial import AccountType, FisRecurrence
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
    FisCategorySettingRepository,
    TransactionCategoryRepository,
)
from db_handler import DatabaseManager
from services.exhibit_service import Column, Exhibit, Row, caption_lines, money
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")

# Rows per fetch. PostgREST caps a request at its own max-rows setting whatever
# is asked for, so the loop pages on what came back rather than what it wanted.
_PAGE = 1000

# A ceiling on the scan, purely to stop a runaway. Far above any real matter,
# and hitting it is reported: an FIS computed from part of the transactions
# would understate every line, which is the one thing this module exists to
# prevent.
_MAX_ROWS = 200_000


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    """Move a (year, month) by a number of months, in either direction."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def _months_between(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Whole months from start to end, counting both ends."""
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


# Accounts whose balance is money OWED rather than money HELD.
_LIABILITY = frozenset({AccountType.credit_card, AccountType.loan})


def household_amount(row: Any, accounts: dict[int, Any]) -> Decimal:
    """
    One transaction, signed by its effect on the household rather than on the
    account it was printed on.

    Everywhere else in this system an amount is signed by how it moves the
    balance the institution prints, which is what makes
    ``beginning + sum == ending`` hold for every account type. On a credit card
    that means a purchase is POSITIVE, because it raises the balance owed.

    The FIS asks a different question — what did this household earn and spend —
    and under the printed convention the two answers are opposites for a
    liability account. Left alone, $500 of groceries on a debit card (-500) and
    $500 on a credit card (+500) cancel to nothing, and a month lived entirely on
    plastic reports as income.

    So amounts from a credit card or a loan are inverted here, and only here. The
    stored value is untouched: reconciliation still needs the printed sign, and
    the transaction exhibit still shows what the statement shows.

    A card PAYMENT inverts to positive under this rule, which is correct only
    because the payment must be excluded from the statement anyway — it is the
    same money as the withdrawal from checking, and counting both is the
    double-count `include_in_fis` exists to prevent.
    """
    amount = row.amount or ZERO
    account = accounts.get(row.financial_account_id)
    if account is not None and account.account_type in _LIABILITY:
        return -amount
    return amount


class FisService:
    """Builds a Financial Information Statement from a matter's transactions."""

    def build(
        self,
        manager: DatabaseManager,
        matter_id: int,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        account_ids: Optional[list[int]] = None,
        client_id: Optional[int] = None,
        opposing_party_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Compute the statement.

        The window is expressed in whole months on purpose. A part-month makes
        "average monthly" indefensible -- you cannot divide by three-and-a-bit
        and call the result a monthly figure -- so the caller picks a first and
        a last month and this fills in the days.

        :param matter_id: The matter. Always the outermost bound; account ids
            not on it are dropped rather than trusted.
        :param account_ids: Accounts to include; omit for every account on the
            matter. **This is what decides whose statement it is**, so the
            person whose payment schedules apply is named separately.
        :param client_id: Our client, when the statement is theirs.
        :param opposing_party_id: The other side, when it is theirs.
        :return: The window, one line per category in the chart's order, the
            net, the uncategorized and excluded totals, coverage, and warnings.
        :rtype: dict[str, Any]
        """
        if (end_year, end_month) < (start_year, start_month):
            raise ValueError("The last month cannot fall before the first")

        window_start = date(start_year, start_month, 1)
        window_end = _month_end(end_year, end_month)
        window_months = _months_between((start_year, start_month), (end_year, end_month))

        # Twelve whole months ending with the window's last month. Sub-monthly
        # lines are computed from this instead, which is why it is fetched even
        # when it reaches back before the window the user asked for.
        trailing_year, trailing_month = _shift(end_year, end_month, -11)
        trailing_start = date(trailing_year, trailing_month, 1)

        account_repo = FinancialAccountRepository(manager)
        accounts = account_repo.get_by_matter(matter_id)
        if not accounts:
            return self._empty(window_start, window_end, window_months)

        allowed = {a.id: a for a in accounts}
        if account_ids:
            scope = sorted(set(allowed) & set(account_ids))
            if not scope:
                return self._empty(window_start, window_end, window_months)
        else:
            scope = sorted(allowed)

        warnings: list[str] = []
        rows = self._fetch(
            manager, matter_id, scope,
            min(window_start, trailing_start), window_end, warnings,
        )

        # Two aggregations from one fetch: the window the user asked for, and
        # the trailing year the sub-monthly lines need.
        window_totals: dict[Optional[int], Decimal] = {}
        window_counts: dict[Optional[int], int] = {}
        trailing_totals: dict[Optional[int], Decimal] = {}
        undated = 0
        for row in rows:
            when = row.transaction_date
            if when is None:
                undated += 1
                continue
            key = row.category_id
            amount = household_amount(row, allowed)
            if trailing_start <= when <= window_end:
                trailing_totals[key] = trailing_totals.get(key, ZERO) + amount
            if window_start <= when <= window_end:
                window_totals[key] = window_totals.get(key, ZERO) + amount
                window_counts[key] = window_counts.get(key, 0) + 1

        if undated:
            # A line with no date cannot be placed in a month, so it is in no
            # average. Saying so is the difference between a figure that is low
            # and a figure that is low for a reason nobody was told.
            warnings.append(
                "%d transaction%s carries no date and could not be placed in a month. "
                "Those amounts are in no figure below."
                % (undated, "" if undated == 1 else "s")
            )

        categories = TransactionCategoryRepository(manager).get_ordered(include_inactive=True)
        settings = FisCategorySettingRepository(manager).resolve(
            client_id=client_id, opposing_party_id=opposing_party_id,
        )

        lines, net, excluded = self._lines(
            categories, settings, window_totals, window_counts,
            trailing_totals, window_months,
        )

        uncategorized_total = window_totals.get(None, ZERO)
        uncategorized_count = window_counts.get(None, 0)
        if uncategorized_count:
            warnings.append(
                "%d transaction%s in this window %s not been filed under a category, and "
                "appear in no line below."
                % (uncategorized_count,
                   "" if uncategorized_count == 1 else "s",
                   "has" if uncategorized_count == 1 else "have")
            )

        coverage = self._coverage(
            manager, matter_id, scope, allowed,
            (start_year, start_month), (end_year, end_month), warnings,
        )

        LOGGER.info(
            "fis_service.build: matter=%s accounts=%d window=%s..%s months=%d lines=%d",
            matter_id, len(scope), window_start, window_end, window_months, len(lines),
        )

        return {
            "window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "months": window_months,
                "trailing_start": trailing_start.isoformat(),
            },
            "accounts": [self._label(allowed[i]) for i in scope],
            "lines": lines,
            "net_monthly": str(net),
            "uncategorized": {
                "count": uncategorized_count,
                "total": str(_cents(uncategorized_total)),
                "monthly": str(_cents(uncategorized_total / window_months)),
            },
            "excluded": excluded,
            "coverage": coverage,
            "warnings": warnings,
        }

    # -- Pieces ------------------------------------------------------------

    @staticmethod
    def _label(account: Any) -> str:
        last4 = getattr(account, "account_number_last4", None)
        return "%s%s" % (account.institution, " x%s" % last4 if last4 else "")

    @staticmethod
    def _empty(window_start: date, window_end: date, months: int) -> dict[str, Any]:
        return {
            "window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "months": months,
                "trailing_start": window_start.isoformat(),
            },
            "accounts": [],
            "lines": [],
            "net_monthly": "0.00",
            "uncategorized": {"count": 0, "total": "0.00", "monthly": "0.00"},
            "excluded": [],
            "coverage": {"complete": False, "accounts": []},
            "warnings": ["No accounts on this matter fall within the selection."],
        }

    def _fetch(
        self,
        manager: DatabaseManager,
        matter_id: int,
        scope: list[int],
        start: date,
        end: date,
        warnings: list[str],
    ) -> list[Any]:
        """
        Every transaction in the span, paged to exhaustion.

        Deliberately not capped the way an export is. An exhibit that stops at
        five thousand rows is a summary of the wrong set and says so on its own
        face; an FIS that stopped would silently understate every line it
        touched, and the figure is sworn to.
        """
        transaction_repo = FinancialAccountTransactionRepository(manager)
        rejected = FinancialAccountStatementRepository(manager).rejected_ids(matter_id)

        rows: list[Any] = []
        while True:
            page, total = transaction_repo.search(
                account_ids=scope, exclude_statement_ids=rejected,
                date_from=start, date_to=end, limit=_PAGE, offset=len(rows),
            )
            if not page:
                break
            rows.extend(page)
            if len(rows) >= total:
                break
            if len(rows) >= _MAX_ROWS:
                warnings.append(
                    "This selection holds more than %d transactions and the scan stopped "
                    "there. Every figure below is understated -- narrow the accounts or "
                    "the window." % _MAX_ROWS
                )
                break
        return rows

    def _lines(
        self,
        categories: list[Any],
        settings: dict[int, Any],
        window_totals: dict[Optional[int], Decimal],
        window_counts: dict[Optional[int], int],
        trailing_totals: dict[Optional[int], Decimal],
        window_months: int,
    ) -> tuple[list[dict[str, Any]], Decimal, list[dict[str, Any]]]:
        """
        One row per category, in the chart's own order, with its monthly figure.

        The chart is the form. ``display_order`` sorts across the whole tree, so
        the order a court expects is a property of the data rather than
        something reconstructed here.
        """
        by_id = {c.id: c for c in categories}
        depth: dict[int, int] = {}

        def depth_of(category_id: int) -> int:
            if category_id in depth:
                return depth[category_id]
            category = by_id.get(category_id)
            depth[category_id] = (
                0 if category is None or category.parent_id is None
                else depth_of(category.parent_id) + 1
            )
            return depth[category_id]

        # Already in reading order from `get_ordered` — depth-first, siblings by
        # display_order. Re-sorting flat here is exactly the bug that put a
        # utility's children underneath a different branch.
        ordered = categories

        lines: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        net = ZERO

        for category in ordered:
            window_total = window_totals.get(category.id, ZERO)
            trailing_total = trailing_totals.get(category.id, ZERO)
            count = window_counts.get(category.id, 0)

            if not category.include_in_fis:
                # Money that moved without being income or expense -- a transfer
                # between the parties' own accounts, a stock split. Reported so
                # a reader can see it was set aside deliberately rather than
                # lost, but never in the net.
                if count:
                    excluded.append({
                        "category_id": category.id,
                        "label": category.description,
                        "total": str(_cents(window_total)),
                        "transaction_count": count,
                    })
                continue

            setting = settings.get(category.id)
            recurrence = setting.recurrence if setting else None
            stated = setting.stated_annual_amount if setting else None

            if stated is not None:
                monthly = Decimal(str(stated)) / 12
                basis = "stated"
            elif recurrence is not None and recurrence.is_sub_monthly:
                monthly = trailing_total / 12
                basis = "trailing_year"
            else:
                monthly = window_total / window_months
                basis = "window"

            monthly = _cents(monthly)
            # The net sums the ROUNDED lines so the column on the exhibit adds
            # up to the total printed beneath it. A net computed from unrounded
            # figures can differ from the visible column by a few cents, and on
            # a sworn document that is a question nobody wants to be asked.
            net += monthly

            lines.append({
                "category_id": category.id,
                "parent_id": category.parent_id,
                "label": category.description,
                "depth": depth_of(category.id),
                "monthly": str(monthly),
                "window_total": str(_cents(window_total)),
                "trailing_year_total": str(_cents(trailing_total)),
                "transaction_count": count,
                "basis": basis,
                "recurrence": recurrence.value if recurrence else None,
                # Suppressed for monthly: it is the assumption already, and
                # printing it beside every line is noise that hides the lines
                # where the schedule is the point.
                "legend": (recurrence.legend
                           if recurrence and recurrence != FisRecurrence.monthly else None),
                "note": setting.note if setting else None,
            })

        self._mark_empty(lines)
        return lines, _cents(net), excluded

    @staticmethod
    def _mark_empty(lines: list[dict[str, Any]]) -> None:
        """
        Flag the rows a compressed statement drops.

        A leaf is empty when its own figure is zero. A heading is empty when its
        own figure is zero **and** nothing beneath it survives -- a heading can
        hold transactions directly, and dropping it would drop the money.

        Computed here rather than in the client so the screen and the exhibit
        cannot disagree about which lines a compressed statement shows.
        """
        kept: dict[int, bool] = {
            line["category_id"]: Decimal(line["monthly"]) != ZERO for line in lines
        }

        # Deepest first, so a child's verdict is settled before its parent asks.
        for line in sorted(lines, key=lambda row: row["depth"], reverse=True):
            parent_id = line["parent_id"]
            if kept.get(line["category_id"]) and parent_id in kept:
                kept[parent_id] = True

        for line in lines:
            line["empty"] = not kept.get(line["category_id"], False)

    def _coverage(
        self,
        manager: DatabaseManager,
        matter_id: int,
        scope: list[int],
        accounts: dict[int, Any],
        start: tuple[int, int],
        end: tuple[int, int],
        warnings: list[str],
    ) -> dict[str, Any]:
        """
        Which months of the window each account actually has a statement for.

        The denominator is a claim. Dividing by eight months asserts eight
        months of records; if the production holds five, every line is
        understated by a proportion invisible on the page. This is the question
        the compliance matrix asks, answered for one window.
        """
        statements = FinancialAccountStatementRepository(manager).get_by_matter(matter_id)

        wanted: list[tuple[int, int]] = []
        cursor = start
        while cursor <= end:
            wanted.append(cursor)
            cursor = _shift(cursor[0], cursor[1], 1)

        per_account: list[dict[str, Any]] = []
        complete = True
        for account_id in scope:
            held: set[tuple[int, int]] = set()
            for statement in statements:
                if statement.financial_account_id != account_id:
                    continue
                cursor = (statement.period_start.year, statement.period_start.month)
                last = (statement.period_end.year, statement.period_end.month)
                while cursor <= last:
                    held.add(cursor)
                    cursor = _shift(cursor[0], cursor[1], 1)

            missing = [m for m in wanted if m not in held]
            if missing:
                complete = False
            per_account.append({
                "account_id": account_id,
                "label": self._label(accounts[account_id]),
                "months_in_window": len(wanted),
                "months_held": len(wanted) - len(missing),
                "missing_months": ["%04d-%02d" % m for m in missing],
            })

        if not complete:
            worst = max(per_account, key=lambda a: len(a["missing_months"]))
            warnings.append(
                "Statements are missing for %d of the %d months in this window (%s is the "
                "worst affected). Every average below divides by %d months regardless, so "
                "the figures are understated."
                % (len(worst["missing_months"]), len(wanted), worst["label"], len(wanted))
            )

        return {"complete": complete, "accounts": per_account}

    # -- The schedule behind the statement --------------------------------

    def build_schedule(
        self,
        manager: DatabaseManager,
        matter: Any,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        account_ids: Optional[list[int]] = None,
        client_id: Optional[int] = None,
        opposing_party_id: Optional[int] = None,
        category_ids: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """
        Every transaction behind the statement, grouped by category.

        This is the answer to "what exactly is in Miscellaneous?" -- the question
        that, unanswered, costs a witness their credibility. Each group carries
        its transactions with full provenance and, above all, **the arithmetic
        that turns them into the figure on the statement**.

        **The monthly figures come from the FIS itself, not from recomputing.**
        A schedule that derived its own numbers could disagree with the summary
        it backs, and a disagreement between an exhibit and its own backup is the
        worst thing either could do. That costs a second pass over the
        transactions, and it is worth it.

        **A sub-monthly line shows its TRAILING-YEAR transactions.** This is the
        trap the method exists to avoid: the statement says $300/month for
        property taxes while the window holds a single payment of $3,600 -- or
        none at all, if it was paid the November before. A schedule showing only
        the window would appear to contradict the summary on its face. Every
        group says which span it covers, and why.

        :param category_ids: Restrict to these categories, for when one line is
            in dispute. Omit for the whole schedule.
        :return: The window, the groups in chart order, and any warnings.
        :rtype: dict[str, Any]
        """
        statement = self.build(
            manager, matter.id, start_year, start_month, end_year, end_month,
            account_ids=account_ids, client_id=client_id,
            opposing_party_id=opposing_party_id,
        )
        window = statement["window"]
        window_start = date.fromisoformat(window["start"])
        window_end = date.fromisoformat(window["end"])
        trailing_start = date.fromisoformat(window["trailing_start"])
        months = window["months"]

        accounts = FinancialAccountRepository(manager).get_by_matter(matter.id)
        allowed = {a.id: a for a in accounts}
        scope = sorted(set(allowed) & set(account_ids)) if account_ids else sorted(allowed)
        if not scope:
            return {"window": window, "accounts": [], "groups": [],
                    "warnings": statement["warnings"]}

        warnings = list(statement["warnings"])
        rows = self._fetch(
            manager, matter.id, scope,
            min(window_start, trailing_start), window_end, warnings,
        )

        # Which document each line came off. Storage renames every upload to a
        # job id, so without this the Bates number is the only handle on the
        # source -- and an unstamped page has none.
        documents: dict[int, str] = {}
        for record in FinancialAccountStatementRepository(manager).get_by_matter(matter.id):
            name = (record.extraction or {}).get("source_filename")
            if name:
                documents[record.id] = name

        wanted = set(category_ids) if category_ids else None
        by_category: dict[Optional[int], list[Any]] = {}
        for row in rows:
            by_category.setdefault(row.category_id, []).append(row)

        groups: list[dict[str, Any]] = []
        for entry in statement["lines"]:
            if wanted is not None and entry["category_id"] not in wanted:
                continue

            # The span the statement actually used for this line, so the
            # transactions shown are the ones the figure came from.
            trailing = entry["basis"] == "trailing_year"
            span_start = trailing_start if trailing else window_start
            lines = [
                row for row in by_category.get(entry["category_id"], [])
                if row.transaction_date is not None
                and span_start <= row.transaction_date <= window_end
            ]
            if not lines and Decimal(entry["monthly"]) == ZERO:
                continue

            total = _cents(sum((household_amount(row, allowed) for row in lines),
                               start=ZERO))
            groups.append({
                "category_id": entry["category_id"],
                "label": entry["label"],
                "depth": entry["depth"],
                "basis": entry["basis"],
                "recurrence": entry["recurrence"],
                "legend": entry["legend"],
                "monthly": entry["monthly"],
                "total": str(total),
                "span": "trailing_year" if trailing else "window",
                "span_start": span_start.isoformat(),
                "span_end": window_end.isoformat(),
                "derivation": self._derivation(entry, len(lines), total, months, window_end),
                "transactions": [
                    self._detail(row, allowed, documents)
                    for row in sorted(lines, key=lambda r: (r.transaction_date, r.id))
                ],
            })

        # Unfiled money last, and only when there is any. It is not a category,
        # it is unfinished work -- and it is exactly what opposing counsel will
        # ask to see.
        unfiled = [
            row for row in by_category.get(None, [])
            if row.transaction_date is not None
            and window_start <= row.transaction_date <= window_end
        ]
        if unfiled and wanted is None:
            total = _cents(sum((household_amount(row, allowed) for row in unfiled),
                               start=ZERO))
            groups.append({
                "category_id": None,
                "label": "Not yet filed under a category",
                "depth": 0,
                "basis": "window",
                "recurrence": None,
                "legend": None,
                "monthly": str(_cents(total / months)),
                "total": str(total),
                "span": "window",
                "span_start": window_start.isoformat(),
                "span_end": window_end.isoformat(),
                "derivation": "%d transaction(s) totalling %s, appearing in no line of the "
                              "statement." % (len(unfiled), money(str(total))),
                "transactions": [
                    self._detail(row, allowed, documents)
                    for row in sorted(unfiled, key=lambda r: (r.transaction_date, r.id))
                ],
            })

        LOGGER.info("fis_service.build_schedule: matter=%s groups=%d", matter.id, len(groups))
        return {
            "window": window,
            "accounts": statement["accounts"],
            "groups": groups,
            "warnings": warnings,
        }

    @staticmethod
    def _describe(line: dict[str, Any]) -> str:
        """
        The description, with the check number folded in only when it is missing.

        Most banks already print "CHECK 2495" in the description, so a column of
        its own was mostly blank and cost width the description needed. Some do
        not, and losing the number there would lose the only handle on a debit
        that says nothing about where the money went — so it is appended when it
        is not already in the text.
        """
        description = line["description"] or ""
        number = (line.get("check_number") or "").strip()
        if number and number not in description:
            return "%s (check %s)" % (description, number) if description else "Check %s" % number
        return description


    @staticmethod
    def _derivation(entry: dict[str, Any], count: int, total: Decimal,
                    months: int, window_end: date) -> str:
        """
        How this group's transactions become the figure on the statement.

        Printed on the exhibit. It is the sentence a witness reads out instead of
        guessing, and it is why the schedule is worth handing over rather than
        merely holding.
        """
        if entry["basis"] == "stated":
            annual = Decimal(entry["monthly"]) * 12
            return (
                "Entered as %s per year, which is %s per month. The %d transaction(s) below "
                "are shown for reference and were not used to compute it."
                % (money(str(annual)), money(entry["monthly"]), count)
            )
        if entry["basis"] == "trailing_year":
            return (
                "%d transaction(s) totalling %s over the twelve months to %s, divided by "
                "twelve, is %s per month. This line is %s, so it is averaged over a year "
                "rather than over the period of the statement."
                % (count, money(str(total)), window_end.isoformat(),
                   money(entry["monthly"]), entry["legend"] or "not paid monthly")
            )
        return (
            "%d transaction(s) totalling %s over %d month(s) is %s per month."
            % (count, money(str(total)), months, money(entry["monthly"]))
        )

    @staticmethod
    def _detail(row: Any, accounts: dict[int, Any], documents: dict[int, str]) -> dict[str, Any]:
        """One transaction, with everything needed to find it in the production."""
        account = accounts.get(row.financial_account_id)
        label = "Unknown account"
        if account is not None:
            last4 = account.account_number_last4
            label = "%s%s" % (account.institution, " x%s" % last4 if last4 else "")
        return {
            "id": row.id,
            "date": row.transaction_date.isoformat() if row.transaction_date else None,
            "description": (row.description or "").replace("\n", " ").strip(),
            "amount": str(household_amount(row, accounts)),
            "check_number": row.check_number,
            "account": label,
            "bates_number": row.bates_number,
            "page": row.physical_page_number,
            "document": documents.get(row.statement_id),
            "statement_id": row.statement_id,
            # Who filed this line. The review queue is built on it: a paralegal
            # checking a rule's work needs to see which lines are the rule's.
            "category_source": getattr(row, "category_source", None),
            "category_rule_id": getattr(row, "category_rule_id", None),
            "reviewed": getattr(row, "category_reviewed_at", None) is not None,
        }

    # -- The exhibit ------------------------------------------------------

    def build_exhibit(
        self,
        manager: DatabaseManager,
        matter: Any,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        account_ids: Optional[list[int]] = None,
        client_id: Optional[int] = None,
        opposing_party_id: Optional[int] = None,
        exhibit_name: str = "Financial Information Statement",
        compressed: bool = True,
    ) -> Exhibit:
        """
        The statement as a document.

        Two columns and no headings, because that is the form: a label and a
        figure, with the indentation carrying the hierarchy. `compressed` drops
        the lines with nothing in them -- the version that goes to mediation --
        while the full form is the one a court expects, blank lines included,
        because a blank line is itself an answer.

        Everything that would make a figure wrong travels with it. The coverage
        gaps and the unfiled transactions are footnotes directly beneath the
        table, not notes left behind on the screen that produced it.
        """
        statement = self.build(
            manager, matter.id, start_year, start_month, end_year, end_month,
            account_ids=account_ids, client_id=client_id,
            opposing_party_id=opposing_party_id,
        )
        caption, warnings = caption_lines(matter, exhibit_name)
        window = statement["window"]

        rows: list[Row] = []
        for entry in statement["lines"]:
            if compressed and entry["empty"]:
                continue
            label = entry["label"]
            if entry["legend"]:
                label = "%s (%s)" % (label, entry["legend"])
            if entry["note"]:
                label = "%s -- %s" % (label, entry["note"])
            figure = "" if Decimal(entry["monthly"]) == ZERO else entry["monthly"]
            rows.append(Row(
                cells=(label, figure),
                depth=entry["depth"],
                heading=entry["depth"] == 0,
            ))

        # The net is the last row of the form, ruled off, rather than a totals
        # block underneath it. That is where the paper form puts it.
        rows.append(Row(
            cells=("NET CASH FLOW PER MONTH", statement["net_monthly"]),
            rule=True,
        ))

        footnotes: list[str] = list(statement["warnings"])
        if any(e["basis"] == "trailing_year" for e in statement["lines"]):
            footnotes.append(
                "Lines marked with a payment frequency less often than monthly are computed "
                "from the twelve months ending %s and divided by twelve, so a payment made "
                "outside the period above is still reflected."
                % window["end"]
            )
        if any(e["basis"] == "stated" for e in statement["lines"]):
            footnotes.append(
                "One or more lines were entered as an annual figure rather than derived from "
                "the transactions."
            )
        for entry in statement["excluded"]:
            footnotes.append(
                "Excluded as neither income nor expense: %s, %s across %d transaction(s)."
                % (entry["label"], money(entry["total"]), entry["transaction_count"])
            )

        selection: list[tuple[str, str]] = [
            ("Accounts", ", ".join(statement["accounts"]) or "None"),
            ("Period", "%s through %s" % (window["start"], window["end"])),
            ("Averaged over", "%d whole month%s"
                              % (window["months"], "" if window["months"] == 1 else "s")),
            ("Statement coverage",
             "Complete" if statement["coverage"]["complete"]
             else "INCOMPLETE -- see the note above the totals"),
            ("Form", "Condensed: lines with no amount omitted" if compressed
                     else "Full: every line on the form, including those with no amount"),
        ]
        if statement["uncategorized"]["count"]:
            selection.append((
                "Not yet filed",
                "%d transaction(s), %s, appearing in no line above"
                % (statement["uncategorized"]["count"],
                   money(statement["uncategorized"]["total"])),
            ))

        return Exhibit(
            name=exhibit_name,
            caption=caption,
            columns=(Column("Category"), Column("Monthly", numeric=True, money=True)),
            rows=tuple(rows),
            selection=tuple(selection),
            footnotes=tuple(footnotes),
            warnings=warnings,
            show_headers=False,
        )

    def build_schedule_exhibit(
        self,
        manager: DatabaseManager,
        matter: Any,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        account_ids: Optional[list[int]] = None,
        client_id: Optional[int] = None,
        opposing_party_id: Optional[int] = None,
        category_ids: Optional[list[int]] = None,
        exhibit_name: str = "Schedule of Transactions by Category",
    ) -> Exhibit:
        """
        The schedule as a document -- the backup handed up on cross.

        Every group opens with its category and closes with the arithmetic that
        produced the figure on the statement, so the two documents visibly agree.
        The category also rides on every data row, which keeps the CSV
        pivotable: delete the headings in a spreadsheet and each line still
        knows where it belongs.
        """
        schedule = self.build_schedule(
            manager, matter, start_year, start_month, end_year, end_month,
            account_ids=account_ids, client_id=client_id,
            opposing_party_id=opposing_party_id, category_ids=category_ids,
        )
        caption, warnings = caption_lines(matter, exhibit_name)
        window = schedule["window"]

        rows: list[Row] = []
        for group in schedule["groups"]:
            heading = group["label"]
            if group["legend"]:
                heading = "%s (%s)" % (heading, group["legend"])
            # The heading sits flush left whatever the category's depth in the
            # chart. Indenting it by depth pushed a nested category like
            # "Property Insurance" inward and made the group harder to find, not
            # easier — the chart's shape is the statement's job, not this one's.
            rows.append(Row(
                cells=(heading, "", "", "", ""), depth=0, heading=True,
            ))
            for line in group["transactions"]:
                # The category is not repeated on every line. It is on the
                # heading above and the total below, and the width it was
                # costing belongs to the description, which is what a reader is
                # actually looking at.
                rows.append(Row(cells=(
                    line["date"] or "",
                    line["account"],
                    self._describe(line),
                    "%s%s" % (line["bates_number"] or "",
                              " p.%d" % line["page"] if line["page"] else ""),
                    line["amount"],
                ), depth=1))
            # The derivation sits in the description column of the ruled row, so
            # a reader following the total across finds the sentence explaining
            # it rather than having to look elsewhere on the page.
            rows.append(Row(cells=(
                group["label"], "", group["derivation"], "TOTAL", group["total"],
            ), depth=0, rule=True))

        selection: list[tuple[str, str]] = [
            ("Accounts", ", ".join(schedule["accounts"]) or "None"),
            ("Period", "%s through %s" % (window["start"], window["end"])),
            ("Groups", str(len(schedule["groups"]))),
        ]
        if category_ids:
            selection.append(("Restricted to", "%d selected categor%s"
                              % (len(category_ids), "y" if len(category_ids) == 1 else "ies")))

        accounts_by_id = {
            a.id: a.account_type
            for a in FinancialAccountRepository(manager).get_by_matter(matter.id)
        }
        liability_ids = [
            a_id for a_id, kind in accounts_by_id.items()
            if kind in _LIABILITY and (not account_ids or a_id in account_ids)
        ]

        footnotes = list(schedule["warnings"])
        if any(g["span"] == "trailing_year" for g in schedule["groups"]):
            footnotes.append(
                "A group averaged over a year covers the twelve months to %s, which reaches "
                "before the period above. That is how the figure on the Financial Information "
                "Statement was computed, and the transactions shown are the ones it used."
                % window["end"]
            )
        if liability_ids:
            footnotes.append(
                "Amounts on a credit card or loan are shown by their effect on the household, "
                "not on the account. A purchase reads as money spent here, where the card "
                "statement prints it as an increase in the balance owed."
            )
        footnotes.append(
            "Every figure here is the same figure that appears on the Financial Information "
            "Statement for the same period and accounts."
        )

        return Exhibit(
            name=exhibit_name,
            caption=caption,
            columns=(
                # One column doing two jobs: the category on a heading row, the
                # date on the lines beneath it. That is how the screen reads it,
                # and it buys the description most of a column of width.
                Column("Category / Date"),
                Column("Account"),
                Column("Description"),
                Column("Bates"),
                Column("Amount", numeric=True, money=True),
            ),
            rows=tuple(rows),
            selection=tuple(selection),
            footnotes=tuple(footnotes),
            warnings=warnings,
            # Six columns of provenance do not fit a portrait page without
            # squeezing the description, which is the column a reader is
            # actually looking at.
            landscape=True,
        )


fis_service = FisService()
