"""
app/services/inventory_service.py - What each account was worth, and who holds it.

The one-page answer to *"what is in this estate?"* — every account on the
matter, its most recent statement balance, who holds it, and the Bates number of
the document that says so. Assets in one table, liabilities in another, each
totalled.

THREE DECISIONS SHAPE IT, AND NONE IS COSMETIC
────────────────────────────────────────────────────────────────────────────
**A liability prints NEGATIVE here, and that is a display decision.** A card
statement's ending balance is what the institution printed — "New Balance
$244.11" — a positive number meaning money owed, and `statement_service` stores
exactly that because every account type must satisfy
``beginning + sum(amounts) == ending``. An inventory asks a different question:
what does this account contribute to the estate. A $244.11 card balance
contributes **minus** $244.11, so the sign is flipped on the way out and the
stored value is untouched. Same inversion the FIS makes (``household_amount``),
for the same reason and nowhere else.

**A balance is as of a date, and the date is part of the answer.** There is no
"current balance" in this system and there should not be: every figure traces to
a statement somebody can pull. So each row carries the period end of the
statement it came from, and two accounts on one inventory routinely speak as of
different days — which is the truth, and is why the column is there rather than
a single "as of" in the heading.

**An account with no statement is listed, not omitted.** It has no balance, no
date and no Bates, and it is excluded from the total — but it appears, because
an account nobody has produced a statement for is exactly what the reader needs
to see. Omitting it would make the inventory look complete while being silently
short, which is the failure this codebase spends its effort avoiding. The count
of such accounts is reported beside the total.

WHO HOLDS IT
────────────────────────────────────────────────────────────────────────────
``ownership`` says whose the account is in this system's terms — client, the
other side, joint. The exhibit wants a party letter, and the letter depends on
which side our client is: ``matters.client_alignment``. With no alignment
recorded the letters fall back to C and O with a warning, because "P" would be
an assertion about a case nobody has told us the shape of.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from db.models.financial import AccountOwnership, AccountType, PropertyCharacter
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
)
from db_handler import DatabaseManager
from services.exhibit_service import Column, Exhibit, Row, caption_lines, money
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

ZERO = Decimal("0.00")

#: Account types that are money OWED. The same set `statement_service` inverts
#: its printed-total vocabulary for, and the FIS inverts its amounts for.
_LIABILITY_TYPES = frozenset({AccountType.credit_card, AccountType.loan})

#: How a party's role in the caption becomes a letter. Petitioner-side roles are
#: P and respondent-side R, because that is what the firm writes on a schedule.
_ALIGNMENT_LETTER = {
    "petitioner": "P",
    "counter_petitioner": "P",
    "plaintiff": "P",
    "applicant": "P",
    "movant": "P",
    "respondent": "R",
    "counter_respondent": "R",
    "defendant": "R",
}

_COLUMNS = (
    Column("Institution"),
    Column("Account"),
    Column("Held by", center=True),
    Column("As of"),
    Column("Balance", numeric=True, money=True),
    Column("Bates"),
)


def _is_liability(account: Any) -> bool:
    return account.account_type in _LIABILITY_TYPES


class InventoryService:
    """Account balances as an inventory: assets, liabilities, and the totals."""

    def inventory(
        self,
        manager: DatabaseManager,
        matter_id: int,
        matter: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        Every account on the matter with its latest statement balance.

        :param matter: The matter, when the caller already has it — only its
            ``client_alignment`` is read, to turn ownership into a party letter.
        :return: ``{"assets": [...], "liabilities": [...], "totals": {...},
            "legend": [...], "warnings": [...]}``. A row carries both the
            display string and the raw figure, so the UI and the exhibit format
            it the same way and neither has to parse the other's.
        :rtype: dict[str, Any]
        """
        every = FinancialAccountRepository(manager).get_by_matter(matter_id)
        # NO INTEREST, NOTHING TO INVENTORY. An account somebody characterized
        # as outside the marital estate (045) is on the matter so the
        # undisclosed scan stops reporting a $5 repayment to a sibling -- not
        # because the estate holds anything in it. Counted and declared, never
        # silently dropped.
        accounts = [a for a in every
                    if getattr(a, "property_character", None)
                    != PropertyCharacter.no_marital_interest]
        outside = len(every) - len(accounts)
        statement_repo = FinancialAccountStatementRepository(manager)

        letters, warnings = self._letters(matter)

        assets: list[dict[str, Any]] = []
        liabilities: list[dict[str, Any]] = []
        no_statement = 0

        for account in sorted(accounts, key=lambda a: (a.institution or "", a.id)):
            latest = self._latest_statement(statement_repo, account.id)
            liability = _is_liability(account)

            balance: Optional[Decimal] = None
            if latest is not None and latest.ending_balance is not None:
                # Flipped for a liability, and only here. See the module
                # docstring: the stored figure is what the institution printed.
                balance = -latest.ending_balance if liability else latest.ending_balance

            if latest is None or latest.ending_balance is None:
                no_statement += 1

            row = {
                "account_id": account.id,
                "institution": account.institution or "Unknown institution",
                "account_number_last4": account.account_number_last4,
                "account_type": account.account_type.value
                if hasattr(account.account_type, "value") else str(account.account_type),
                "held_by": letters.get(account.ownership, "?"),
                "as_of": latest.period_end.isoformat() if latest is not None else None,
                "balance": str(balance) if balance is not None else None,
                "bates": self._bates(latest),
                "statement_id": latest.id if latest is not None else None,
                "is_closed": bool(account.is_closed),
            }
            (liabilities if liability else assets).append(row)

        totals = {
            "assets": str(self._sum(assets)),
            "liabilities": str(self._sum(liabilities)),
            "net": str(self._sum(assets) + self._sum(liabilities)),
            "accounts": len(accounts),
            "without_balance": no_statement,
            "outside_estate": outside,
        }

        legend = [("J", "Joint")]
        for ownership, letter in letters.items():
            if ownership is AccountOwnership.client_sole:
                legend.insert(0, (letter, "Petitioner" if letter == "P"
                                  else "Respondent" if letter == "R" else "Our client"))
            elif ownership is AccountOwnership.opposing_sole:
                legend.insert(1, (letter, "Petitioner" if letter == "P"
                                  else "Respondent" if letter == "R" else "The other party"))

        return {
            "assets": assets,
            "liabilities": liabilities,
            "totals": totals,
            "legend": legend,
            "warnings": warnings,
        }

    # ── Pieces ────────────────────────────────────────────────────────────

    @staticmethod
    def _letters(matter: Optional[Any]) -> tuple[dict[Any, str], list[str]]:
        """
        Ownership → the letter a schedule prints, from the client's alignment.

        **Falls back to C and O rather than guessing P and R.** A letter is a
        claim about which side somebody is on, and an inventory that says "P"
        for a case where nobody recorded the alignment is asserting something
        the system was never told. The warning travels onto the exhibit.
        """
        alignment = getattr(matter, "client_alignment", None) if matter is not None else None
        alignment = getattr(alignment, "value", alignment)
        letter = _ALIGNMENT_LETTER.get(str(alignment or "").lower())

        warnings: list[str] = []
        if letter is None:
            warnings.append(
                "This matter has no client alignment recorded, so the Held by column says "
                "C for our client and O for the other party rather than P and R. Set it on "
                "the matter to print party letters."
            )
            ours, theirs = "C", "O"
        else:
            ours = letter
            theirs = "R" if letter == "P" else "P"

        return {
            AccountOwnership.client_sole: ours,
            AccountOwnership.opposing_sole: theirs,
            AccountOwnership.joint: "J",
            AccountOwnership.third_party: "3P",
            AccountOwnership.unknown: "?",
        }, warnings

    @staticmethod
    def _latest_statement(
        statement_repo: FinancialAccountStatementRepository, account_id: int,
    ) -> Optional[Any]:
        """
        The most recent statement on an account, by period end.

        Rejected extractions are excluded: a discarded read is not evidence of a
        balance, and taking one would put a figure on a schedule that no
        document supports.
        """
        held = [
            s for s in statement_repo.get_by_account(account_id)
            if getattr(s.review_status, "value", s.review_status) != "rejected"
        ]
        if not held:
            return None
        return max(held, key=lambda s: s.period_end)

    @staticmethod
    def _bates(statement: Optional[Any]) -> Optional[str]:
        """
        The stamp on the statement's first page.

        The balance is printed on the summary page, which is the first page on
        every statement seen so far; where it is not, the first page is still
        the citation a reader uses to find the document. Never constructed —
        it is what `bates_service` detected, or nothing.
        """
        if statement is None:
            return None
        return (statement.extraction or {}).get("bates_first")

    @staticmethod
    def _sum(rows: list[dict[str, Any]]) -> Decimal:
        """
        The total of the rows that HAVE a balance.

        Rows without one are not zero — nobody knows what they are — and the
        count of them is reported beside the total so it cannot read as
        complete.
        """
        return sum((Decimal(r["balance"]) for r in rows if r["balance"] is not None), ZERO)

    # ── The exhibit ───────────────────────────────────────────────────────

    def build_exhibit(
        self,
        manager: DatabaseManager,
        matter: Any,
        exhibit_name: str = "Account Inventory",
    ) -> Exhibit:
        """
        The inventory as a document: two tables, each ruled off with its total.

        Portrait, not landscape: six columns fit, and a schedule that fits is
        read beside other filings rather than turned sideways.
        """
        report = self.inventory(manager, matter.id, matter=matter)
        caption, warnings = caption_lines(matter, exhibit_name)
        warnings.extend(report["warnings"])

        rows: list[Row] = []
        for title, section, total in (
            ("Assets", report["assets"], report["totals"]["assets"]),
            ("Liabilities", report["liabilities"], report["totals"]["liabilities"]),
        ):
            if not section:
                continue
            rows.append(Row(cells=(title, "", "", "", "", ""), heading=True))
            for item in section:
                rows.append(Row(cells=(
                    item["institution"] + (" (closed)" if item["is_closed"] else ""),
                    item["account_number_last4"] or "—",
                    item["held_by"],
                    _date(item["as_of"]),
                    money(item["balance"]) if item["balance"] is not None else "no statement",
                    item["bates"] or "—",
                )))
            rows.append(Row(cells=("TOTAL", "", "", "", money(total), ""), rule=True))

        selection: list[tuple[str, str]] = [
            ("Accounts listed", str(report["totals"]["accounts"])),
            ("Balance shown", "The ending balance of each account's most recent statement, "
                              "as of the date in that column. Accounts speak as of different "
                              "days, which is what the dates are for"),
            ("Liabilities", "Shown as negative: a balance owed reduces the estate. The "
                            "statements themselves print these as positive amounts owed"),
        ]
        if report["totals"].get("outside_estate"):
            selection.append((
                "Not listed",
                "%d account(s) on this matter are characterized as outside the marital estate "
                "and are not part of it" % report["totals"]["outside_estate"],
            ))
        if report["totals"]["without_balance"]:
            # Never silent. A schedule that omitted them would read as covering
            # every account, and the ones missing a statement are the ones worth
            # chasing.
            selection.append((
                "Not totalled",
                "%d account(s) have no statement on file, so they are listed without a "
                "balance and are not included in either total"
                % report["totals"]["without_balance"],
            ))

        footnotes = ["%s = %s" % (letter, meaning) for letter, meaning in report["legend"]]

        return Exhibit(
            name=exhibit_name,
            caption=caption,
            columns=_COLUMNS,
            rows=tuple(rows),
            selection=tuple(selection),
            summary=(
                ("Assets", money(report["totals"]["assets"])),
                ("Liabilities", money(report["totals"]["liabilities"])),
                ("Net", money(report["totals"]["net"])),
            ),
            footnotes=tuple(footnotes),
            sources=tuple(self._sources(manager, matter.id)),
            warnings=warnings,
        )

    @staticmethod
    def _sources(manager: DatabaseManager, matter_id: int) -> list[tuple[str, str]]:
        """
        The documents this schedule rests on, grouped by upload.

        The Rule 1006 notice promises the underlying records are available; this
        says which, by the filename as produced and the Bates range on it. Same
        shape as the transaction export's.
        """
        statement_repo = FinancialAccountStatementRepository(manager)
        accounts = FinancialAccountRepository(manager).get_by_matter(matter_id)
        by_file: dict[str, tuple[Optional[str], Optional[str]]] = {}
        for account in accounts:
            latest = InventoryService._latest_statement(statement_repo, account.id)
            if latest is None:
                continue
            extraction = latest.extraction or {}
            name = extraction.get("source_filename") or "(filename not recorded)"
            first, last = extraction.get("bates_first"), extraction.get("bates_last")
            held = by_file.get(name)
            if held is None:
                by_file[name] = (first, last)
            else:
                by_file[name] = (held[0] or first, last or held[1])
        return [
            (name, "%s – %s" % (first, last) if first and last else "no Bates stamp detected")
            for name, (first, last) in sorted(by_file.items())
        ]


def _date(iso: Optional[str]) -> str:
    """A date as a schedule prints it: 09/30/2026, or an em dash."""
    if not iso:
        return "—"
    try:
        parsed = date.fromisoformat(iso)
    except ValueError:
        return iso
    return parsed.strftime("%m/%d/%Y")


inventory_service = InventoryService()
