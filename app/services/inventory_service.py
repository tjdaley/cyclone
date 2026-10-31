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

KEPT ON THE LIST, LEFT OUT OF THE TOTALS (050)
────────────────────────────────────────────────────────────────────────────
A stale account, or one the estate holds no interest in, can be *excluded* by a
person: still listed — the reader should see it was considered — but its balance
moves none of the totals. Every format marks it (grey on screen and in the PDF
and DOCX, a dagger everywhere, a CSV column to filter on), because a figure
sitting in a column that does not add up to the total beneath it is exactly the
question nobody wants asked on the stand. The decision carries who made it and
when; the reason is optional.

FOREIGN CURRENCY (051)
────────────────────────────────────────────────────────────────────────────
The inventory is the one report that has to add accounts together, so it is the
one place a foreign balance is converted — at a rate a person entered for the
matter, with its date and source, and the row says so in words: "Balance of
PKR 10,000,000.00 converted to USD at 0.0036 USD/PKR". Nothing stored changes:
the statement still says what the bank printed. A foreign account with no rate
is listed in its own currency and left out of the totals, counted, like an
account with no statement — a rupee figure in a dollar column is the mistake
this exists to prevent.
"""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from db.models.financial import (
    AccountOwnership,
    AccountType,
    InventoryExclusionReason,
    PropertyCharacter,
)
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    MatterCurrencyRateRepository,
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
    # The documents mark an excluded row with a dagger and grey; a spreadsheet
    # needs something it can filter on.
    Column("Excluded from totals", csv_only=True),
    # Balance is always dollars once converted; the CSV keeps what was printed.
    Column("Currency", csv_only=True),
    Column("Balance as printed", csv_only=True),
    Column("USD per unit", csv_only=True),
)

#: The canned reasons, as a reader should see them.
_EXCLUSION_LABEL = {
    InventoryExclusionReason.stale: "Stale",
    InventoryExclusionReason.no_ownership_interest: "No party has an ownership interest",
    InventoryExclusionReason.stipulated_separate_other_party:
        "Stipulated separate property of the other party",
    InventoryExclusionReason.other: "Other",
}

#: Marks an excluded row in every format, including markdown, which has no grey.
_EXCLUDED_MARK = "\u2020"
#: Marks a row whose balance is in, or was converted from, another currency.
_CURRENCY_MARK = "\u2021"
_CENT = Decimal("0.01")


def _rate_text(rate: Decimal) -> str:
    """A rate as a person typed it: 0.0036, not 0.0036000000 or 3.6E-3."""
    text = format(rate.normalize(), "f")
    return text if "." in text else text + ".0"


def _currency(account: Any) -> str:
    return (getattr(account, "currency", None) or "USD").upper()


def _exclusion_label(account: Any) -> Optional[str]:
    """The reason in words: the 'other' note when there is one, else the canned label."""
    reason = getattr(account, "inventory_exclusion_reason", None)
    note = (getattr(account, "inventory_exclusion_note", None) or "").strip()
    if reason is None:
        return note or None
    try:
        reason = InventoryExclusionReason(getattr(reason, "value", reason))
    except ValueError:
        return note or None
    if reason is InventoryExclusionReason.other:
        return note or "Other"
    return _EXCLUSION_LABEL[reason]


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
        # Only asked for when there is something to convert: an all-dollar matter
        # never touches the table, so a node without 051 behaves as it always did.
        rates = (self._rates(manager, matter_id, warnings)
                 if any(_currency(a) != "USD" for a in accounts) else {})

        assets: list[dict[str, Any]] = []
        liabilities: list[dict[str, Any]] = []
        no_statement = 0
        excluded_count = 0
        unconverted = 0
        foreign: set[str] = set()

        for account in sorted(accounts, key=lambda a: (a.institution or "", a.id)):
            latest = self._latest_statement(statement_repo, account.id)
            liability = _is_liability(account)

            currency = _currency(account)
            balance: Optional[Decimal] = None
            native: Optional[Decimal] = None
            rate = None
            note: Optional[str] = None
            if latest is not None and latest.ending_balance is not None:
                # Flipped for a liability, and only here. See the module
                # docstring: the stored figure is what the institution printed.
                native = -latest.ending_balance if liability else latest.ending_balance
                if currency == "USD":
                    balance = native
                else:
                    rate = rates.get(currency)
                    if rate is not None:
                        balance = (native * Decimal(str(rate.usd_per_unit))).quantize(
                            _CENT, rounding=ROUND_HALF_UP)
                        note = "Balance of %s converted to USD at %s USD/%s (rate as of %s%s)" % (
                            money(native, currency), _rate_text(Decimal(str(rate.usd_per_unit))),
                            currency, _date(rate.as_of.isoformat()),
                            "; " + rate.source if rate.source else "")
                    else:
                        note = ("Balance of %s. No USD rate is recorded for %s, so it is not "
                                "included in the totals" % (money(native, currency), currency))
            if currency != "USD":
                foreign.add(currency)

            excluded = bool(getattr(account, "excluded_from_inventory", False))
            if excluded:
                excluded_count += 1
            elif latest is None or latest.ending_balance is None:
                # Counted only on included rows: an excluded account is outside
                # the totals by decision, not for want of a statement.
                no_statement += 1
            elif balance is None:
                unconverted += 1

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
                "currency": currency,
                "native_balance": str(native) if currency != "USD" and native is not None else None,
                "usd_per_unit": _rate_text(Decimal(str(rate.usd_per_unit))) if rate else None,
                "rate_as_of": rate.as_of.isoformat() if rate else None,
                "conversion_note": note,
                "excluded": excluded,
                "exclusion_label": _exclusion_label(account) if excluded else None,
                "excluded_by": getattr(account, "inventory_excluded_by", None) if excluded else None,
                "excluded_at": getattr(account, "inventory_excluded_at", None) if excluded else None,
            }
            (liabilities if liability else assets).append(row)

        totals = {
            "assets": str(self._sum(assets)),
            "liabilities": str(self._sum(liabilities)),
            "net": str(self._sum(assets) + self._sum(liabilities)),
            "accounts": len(accounts),
            "without_balance": no_statement,
            "outside_estate": outside,
            "excluded": excluded_count,
            "unconverted": unconverted,
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
            "currencies": sorted(foreign),
            "rates": [
                {"currency": r.currency, "usd_per_unit": _rate_text(Decimal(str(r.usd_per_unit))),
                 "as_of": r.as_of, "source": r.source, "entered_by": r.entered_by}
                for code, r in sorted(rates.items()) if code in foreign
            ],
        }

    @staticmethod
    def _rates(manager: DatabaseManager, matter_id: int, warnings: list[str]) -> dict[str, Any]:
        """
        The matter's conversion rates, keyed by currency.

        A failure to read them never fails the inventory: every foreign row then
        shows as unconverted, out of the totals, and the warning says why — a
        visibly incomplete schedule rather than a broken page.
        """
        try:
            return MatterCurrencyRateRepository(manager).by_matter(matter_id)
        except Exception as e:  # noqa: BLE001 - the schedule still renders without them
            LOGGER.warning("inventory_service._rates: matter=%s could not read rates: %s",
                           matter_id, type(e).__name__)
            warnings.append("Conversion rates could not be read, so no foreign-currency "
                            "balance is included in the totals. Has migration 051 been run?")
            return {}

    def set_rate(
        self,
        manager: DatabaseManager,
        matter_id: int,
        currency: str,
        usd_per_unit: Decimal,
        as_of: date,
        staff_id: int,
        staff_name: str,
        source: Optional[str] = None,
    ) -> Any:
        """
        Record the rate the inventory uses for one currency on one matter.

        Replaces any earlier rate for that currency: the inventory states one
        figure per account, so it needs one rate per currency, and the audit log
        keeps what it used to be.

        :raises ValueError: For USD, a code that is not three letters, or a rate
            that is not positive.
        """
        code = (currency or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("A currency is a three-letter ISO code, such as PKR")
        if code == "USD":
            raise ValueError("Dollars need no rate")
        if usd_per_unit is None or usd_per_unit <= 0:
            raise ValueError("A rate must be greater than zero")
        repo = MatterCurrencyRateRepository(manager)
        data = {
            "matter_id": matter_id,
            "currency": code,
            "usd_per_unit": usd_per_unit,
            "as_of": as_of,
            "source": (source or "").strip() or None,
            "entered_by_staff_id": staff_id,
            "entered_by": staff_name,
        }
        held = repo.by_matter(matter_id).get(code)
        saved = repo.update(held.id, data) if held is not None else repo.insert(data)
        LOGGER.info("inventory_service.set_rate: matter=%s currency=%s staff=%s",
                    matter_id, code, staff_id)
        return saved

    def delete_rate(self, manager: DatabaseManager, matter_id: int, currency: str) -> bool:
        """Remove a matter's rate for one currency. True when there was one."""
        repo = MatterCurrencyRateRepository(manager)
        held = repo.by_matter(matter_id).get((currency or "").strip().upper())
        if held is None:
            return False
        repo.delete(held.id)
        return True

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
        The total of the included rows that HAVE a balance.

        Rows without one are not zero — nobody knows what they are — and the
        count of them is reported beside the total so it cannot read as
        complete. Excluded rows are listed and never summed.
        """
        return sum((Decimal(r["balance"]) for r in rows
                    if r["balance"] is not None and not r.get("excluded")), ZERO)

    # ── Excluding an account from the totals ──────────────────────────────

    def set_exclusion(
        self,
        manager: DatabaseManager,
        account_id: int,
        excluded: bool,
        staff_id: int,
        staff_name: str,
        reason: Optional[InventoryExclusionReason] = None,
        note: Optional[str] = None,
    ) -> Any:
        """
        Keep an account on the inventory but out of its totals, or put it back.

        Who and when are stamped here, never taken from the caller. The reason
        is optional — excluding from the grid in one click records none — but
        ``other`` without a note says nothing, so it is refused.

        Including an account again clears the reason and the attribution: the
        audit log keeps the history, and a stale "excluded by" on an included
        row would contradict the row.

        :return: The updated account.
        :raises LookupError: If the account does not exist.
        :raises ValueError: If the reason is ``other`` and there is no note.
        """
        repo = FinancialAccountRepository(manager)
        if repo.select_one(condition={"id": account_id}) is None:
            raise LookupError("Account not found")

        note = (note or "").strip() or None
        if excluded:
            if reason is InventoryExclusionReason.other and note is None:
                raise ValueError("Say why when the reason is Other")
            updates = {
                "excluded_from_inventory": True,
                "inventory_exclusion_reason": reason,
                # A note only means something beside "other"; a canned reason
                # speaks for itself.
                "inventory_exclusion_note":
                    note if reason is InventoryExclusionReason.other else None,
                "inventory_excluded_by_staff_id": staff_id,
                "inventory_excluded_by": staff_name,
                "inventory_excluded_at": datetime.now(timezone.utc),
            }
        else:
            updates = {
                "excluded_from_inventory": False,
                "inventory_exclusion_reason": None,
                "inventory_exclusion_note": None,
                "inventory_excluded_by_staff_id": None,
                "inventory_excluded_by": None,
                "inventory_excluded_at": None,
            }
        updated = repo.update(account_id, updates)
        LOGGER.info("inventory_service.set_exclusion: account=%s excluded=%s staff=%s",
                    account_id, excluded, staff_id)
        return updated

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
            rows.append(Row(cells=(title, "", "", "", "", "", "", "", "", ""), heading=True))
            for item in section:
                foreign_row = item["currency"] != "USD"
                if item["balance"] is not None:
                    shown = money(item["balance"])
                elif item["native_balance"] is not None:
                    # No rate: the figure in its own currency, out of the total.
                    shown = money(item["native_balance"], item["currency"])
                else:
                    shown = "no statement"
                rows.append(Row(cells=(
                    item["institution"] + (" (closed)" if item["is_closed"] else "")
                    + (" " + _CURRENCY_MARK if foreign_row and item["native_balance"] else "")
                    + (" " + _EXCLUDED_MARK if item["excluded"] else ""),
                    item["account_number_last4"] or "—",
                    item["held_by"],
                    _date(item["as_of"]),
                    shown,
                    item["bates"] or "—",
                    ("yes" + (": " + item["exclusion_label"] if item["exclusion_label"] else ""))
                    if item["excluded"] else "",
                    item["currency"],
                    item["native_balance"] or "",
                    item["usd_per_unit"] or "",
                ), muted=item["excluded"]))
            rows.append(Row(cells=("TOTAL", "", "", "", money(total), "", "", "", "", ""),
                            rule=True))

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
        if report["currencies"]:
            selection.append((
                "Currency",
                "Balances are in US dollars. Accounts kept in %s, marked %s, are converted at "
                "the rate stated beside each; the statements themselves are in that currency"
                % (", ".join(report["currencies"]), _CURRENCY_MARK),
            ))
        if report["totals"]["unconverted"]:
            selection.append((
                "Not converted",
                "%d account(s) are in a currency with no rate recorded, so they are listed in "
                "that currency and not included in either total" % report["totals"]["unconverted"],
            ))
        if report["totals"]["excluded"]:
            selection.append((
                "Excluded",
                "%d account(s), marked %s, are listed but not included in either total"
                % (report["totals"]["excluded"], _EXCLUDED_MARK),
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
        for item in report["assets"] + report["liabilities"]:
            if item["currency"] == "USD" or not item["conversion_note"]:
                continue
            label = item["institution"] + (
                " \u00b7\u00b7\u00b7\u00b7" + item["account_number_last4"]
                if item["account_number_last4"] else "")
            footnotes.append("%s %s: %s" % (_CURRENCY_MARK, label, item["conversion_note"]))
        # Directly below the table, where somebody meeting the dagger looks. The
        # reason travels; who excluded it does not -- that is the firm's own
        # record, not part of a schedule handed across the table.
        for item in report["assets"] + report["liabilities"]:
            if not item["excluded"]:
                continue
            label = item["institution"] + (
                " \u00b7\u00b7\u00b7\u00b7" + item["account_number_last4"]
                if item["account_number_last4"] else "")
            footnotes.append("%s %s: listed, not included in the totals%s" % (
                _EXCLUDED_MARK, label,
                " (%s)" % item["exclusion_label"] if item["exclusion_label"] else ""))

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
