"""
app/services/compliance_service.py - What a production holds, and what it does not.

The grid a firm builds by hand in a spreadsheet: accounts down, months across,
a Bates number in every cell where a statement exists. Its value is entirely in
the blanks. A motion to compel needs to name what is missing, and defending one
needs to show that what is missing was never produced because it never existed.

Two things make this more than a pivot table.

**A blank is not always a gap.** An account opened in March 2021 has no January
statement and never will. Without somewhere to record that, every such account
reports a year of holes and somebody either chases documents nobody has or
spends an afternoon proving they do not exist. ``StatementBoundary`` is where a
person records it, and this module is why it exists.

**A month with a statement in it is not a month that is covered.** Statement
periods do not line up with calendar months — they run 4 March to 5 April — so
a cell can be filled while days at either end of the month are missing. The
matrix answers "which months have a statement"; the coverage summary underneath
answers "which days are actually accounted for", and only the second one belongs
in a motion.
"""
from collections import OrderedDict
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Optional

from db.models.financial import (
    AccountType,
    ProductionResponsibility,
    StatementBoundary,
    StatementReviewStatus,
)
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
)
from db.repositories.matter import MatterRepository
from db_handler import DatabaseManager
from services.exhibit_service import Column, Exhibit, Row, caption_lines
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# Accounts group by kind, the way the tabs of the hand-built workbook do. The
# order is the reading order of a property inventory — what the money sits in,
# then what is owed, then what is locked away — not alphabetical, which would
# put Brokerage between an account's two credit cards.
_TYPE_ORDER = (
    AccountType.checking,
    AccountType.savings,
    AccountType.credit_card,
    AccountType.loan,
    AccountType.brokerage,
    AccountType.retirement,
    AccountType.hsa,
    AccountType.other,
)

_TYPE_LABEL = {
    AccountType.checking: "Checking",
    AccountType.savings: "Savings",
    AccountType.credit_card: "Credit cards",
    AccountType.loan: "Loans",
    AccountType.brokerage: "Brokerage",
    AccountType.retirement: "Retirement",
    AccountType.hsa: "HSA",
    AccountType.other: "Other",
}

_ONE_DAY = timedelta(days=1)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Account and type lead, and BOTH ARE CSV-ONLY. The printed exhibit names the
# account in a centred title above its grid: repeated down a column the name
# wraps and takes width from twelve months that need it more. The CSV has no
# titles, and a grid whose rows do not say which account they belong to cannot
# be sorted, filtered, or pivoted — which is the whole point of the file, since
# it replaces a workbook built by hand.
_EXHIBIT_COLUMNS = (
    Column("Account", csv_only=True),
    Column("Type", csv_only=True),
    Column("Year"),
    # The months are centred: they hold a Bates number where there is one and a
    # bare X where the statement was produced unstamped, and a lone mark hanging
    # off the left edge of a wide cell reads as an accident rather than an entry.
) + tuple(Column(month, center=True) for month in _MONTHS)

_SIDE_TITLE = {
    "client": "Statements our client is responsible for producing",
    "opposing": "Statements the other side is responsible for producing",
}

# Which accounts each report covers. An account both sides were ordered to
# produce appears on both — with a different look-back on each, because the two
# requests asked for different things.
_SIDE_INCLUDES = {
    "client": (ProductionResponsibility.client, ProductionResponsibility.both),
    "opposing": (ProductionResponsibility.opposing, ProductionResponsibility.both),
}


def _month_key(when: date) -> str:
    return "%04d-%02d" % (when.year, when.month)


def _type_rank(account: Any) -> int:
    try:
        return _TYPE_ORDER.index(account.account_type)
    except ValueError:
        return len(_TYPE_ORDER)


class ComplianceService:
    """Builds the statements-held grid and the coverage gaps beneath it."""

    def matrix(
        self,
        manager: DatabaseManager,
        matter_id: int,
        side: Optional[str] = None,
        today: Optional[date] = None,
    ) -> dict[str, Any]:
        """
        Every account on a matter, the months it has statements for, and the
        days it does not.

        **There are two of these reports, not one**, because there are two
        discovery requests running in opposite directions. Opposing counsel
        propounds on us and sets how far back WE must go; we propound on them
        and set how far back THEY must go. Same grid, different accounts,
        different look-back — and an account both sides were ordered to produce
        appears on both, bounded differently on each, because the two requests
        asked for different things.

        :param side: ``"client"`` for what we must produce, ``"opposing"`` for
            what they must, or None for every account over the range the data
            itself covers. The unscoped view is for looking at the matter; the
            scoped ones are what a motion is built from.
        :param today: Overridable for tests. An obligation runs "through the
            present", and the present is not a constant.
        :return: ``{"years", "accounts", "totals", "scope"}``.
        :rtype: dict[str, Any]
        """
        today = today or date.today()
        empty: dict[str, Any] = {
            "years": [], "accounts": [],
            "totals": {"accounts": 0, "statements": 0, "gaps": 0, "unassigned": 0},
            "scope": {"side": side, "since": None, "through": None, "bounded": False},
        }

        accounts = FinancialAccountRepository(manager).get_by_matter(matter_id)
        if not accounts:
            return empty

        since = self._lookback(manager, matter_id, side)
        wanted = _SIDE_INCLUDES.get(side or "")
        scope = {
            "side": side,
            "since": since.isoformat() if since else None,
            "through": today.isoformat() if since else None,
            "bounded": since is not None,
        }
        # Accounts nobody has assigned are counted but not shown on a scoped
        # report. Dropping them silently would let an unmarked account escape
        # both reports and never be chased; the count is what says so.
        unassigned = sum(
            1 for a in accounts
            if a.production_responsibility == ProductionResponsibility.unknown
        ) if wanted else 0
        if wanted:
            accounts = [a for a in accounts if a.production_responsibility in wanted]
        if not accounts:
            return {**empty, "scope": scope,
                    "totals": {**empty["totals"], "unassigned": unassigned}}

        statements = FinancialAccountStatementRepository(manager).get_by_matter(matter_id)
        # A rejected extraction is not evidence of anything. It is filtered here
        # rather than in the query so the same read serves every account.
        keep = {a.id for a in accounts}
        live = [s for s in statements
                if s.review_status != StatementReviewStatus.rejected
                and s.financial_account_id in keep]

        by_account: dict[int, list[Any]] = {}
        for statement in live:
            by_account.setdefault(statement.financial_account_id, []).append(statement)

        # ONE YEAR RANGE FOR THE WHOLE REPORT, not one per account. An account
        # whose grid stopped at its own last statement would show no blank
        # months at all, which is the opposite of what this is for.
        #
        # With a look-back the range is the OBLIGATION — from the date the
        # request reaches back to, through today — so a year nobody produced
        # anything for is a row of blanks rather than simply absent. That is the
        # finding the unscoped view structurally cannot make.
        if since:
            year_range = list(range(since.year, max(since.year, today.year) + 1))
        else:
            years = sorted({s.period_end.year for s in live})
            year_range = list(range(years[0], years[-1] + 1)) if years else []

        rows = [
            self._account_row(
                account,
                sorted(by_account.get(account.id, []), key=lambda s: (s.period_start, s.id)),
                since, today,
            )
            for account in sorted(
                accounts, key=lambda a: (_type_rank(a), (a.institution or "").lower(),
                                         a.account_number_last4 or "")
            )
        ]

        LOGGER.info(
            "compliance_service.matrix: matter=%s side=%s %d account(s), %d statement(s), "
            "%d gap(s), %d unassigned",
            matter_id, side or "all", len(rows), len(live),
            sum(len(r["gaps"]) for r in rows), unassigned,
        )
        return {
            "years": year_range,
            "accounts": rows,
            "totals": {
                "accounts": len(rows),
                "statements": len(live),
                "gaps": sum(len(r["gaps"]) for r in rows),
                "unassigned": unassigned,
            },
            "scope": scope,
        }

    @staticmethod
    def _lookback(
        manager: DatabaseManager, matter_id: int, side: Optional[str],
    ) -> Optional[date]:
        """
        How far back the request governing this side reaches.

        The matter names these for who PRODUCES, not for who asked. The date
        opposing counsel propounded is the one that binds our client, and
        reading it the other way round is the mistake the naming exists to
        prevent.
        """
        if side not in _SIDE_INCLUDES:
            return None
        matter = MatterRepository(manager).select_one(condition={"id": matter_id})
        if matter is None:
            return None
        return (matter.client_produces_since if side == "client"
                else matter.opposing_produces_since)

    def _account_row(
        self,
        account: Any,
        statements: list[Any],
        since: Optional[date] = None,
        today: Optional[date] = None,
    ) -> dict[str, Any]:
        """One account: its filled cells, its known edges, and its gaps."""
        cells: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
        opening: Optional[str] = None
        closing: Optional[str] = None

        for statement in statements:
            extraction = statement.extraction or {}
            # Keyed on the month the statement CLOSES in. A period running 4
            # March to 5 April belongs to April by every convention a bank
            # uses — it is the April statement — and keying on the start would
            # file it a month early and make consecutive statements look like
            # they skip months.
            key = _month_key(statement.period_end)
            cells.setdefault(key, []).append({
                "statement_id": statement.id,
                "bates": extraction.get("bates_first"),
                "bates_last": extraction.get("bates_last"),
                "source_filename": extraction.get("source_filename"),
                "boundary": statement.boundary.value
                if hasattr(statement.boundary, "value") else statement.boundary,
                "review_status": statement.review_status.value
                if hasattr(statement.review_status, "value") else statement.review_status,
                "reconciled": statement.reconciled,
                "period_start": statement.period_start.isoformat(),
                "period_end": statement.period_end.isoformat(),
                "has_pdf": bool(statement.storage_path),
            })
            # THE LIFE OF THE ACCOUNT IS NOT THE CELL IT SITS IN. A statement
            # is placed by the month it CLOSES in, but the account existed from
            # the day that statement's period OPENED — a first statement running
            # 4 January to 3 February sits in the February cell while January is
            # a month the account was already alive for.
            #
            # Taking the cell's month would em-dash January as "before the
            # account existed", which is an assertion, and a false one. Blank is
            # a question; a wrong question is recoverable and a wrong assertion
            # is not.
            if statement.boundary == StatementBoundary.opening:
                opening = _month_key(statement.period_start)
            if statement.boundary == StatementBoundary.closing:
                closing = key

        gaps, before, after = self._coverage(statements, since, today)
        return {
            "account_id": account.id,
            "production_responsibility": account.production_responsibility.value
            if hasattr(account.production_responsibility, "value")
            else account.production_responsibility,
            "institution": account.institution,
            "last4": account.account_number_last4,
            "account_type": account.account_type.value
            if hasattr(account.account_type, "value") else account.account_type,
            "type_label": _TYPE_LABEL.get(account.account_type, "Other"),
            "label": "%s%s" % (account.institution,
                               " ····%s" % account.account_number_last4
                               if account.account_number_last4 else ""),
            "is_closed": bool(getattr(account, "is_closed", False)),
            "cells": dict(cells),
            "opening_month": opening,
            "closing_month": closing,
            "statements": len(statements),
            "gaps": gaps,
            "nothing_before": before,
            "nothing_after": after,
        }

    @staticmethod
    def _coverage(
        statements: list[Any],
        since: Optional[date] = None,
        today: Optional[date] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str], Optional[str]]:
        """
        The days this account is not accounted for.

        **A filled cell is not a covered month.** Periods run 4 March to 5
        April, so a month can hold a statement and still be missing days at
        either end — and a motion has to name days, not months.

        Three kinds of hole, and the boundary markers decide whether the outer
        two are holes at all:

        * **Between two statements.** Always a gap. Consecutive statements abut
          — one ends the day before the next begins — so any daylight between
          them is missing, whatever the edges say: the account demonstrably
          existed on both sides of it.
        * **Before the earliest.** Only when that statement is not marked
          ``opening``. If it is, there is nothing before it to want.
        * **After the latest.** Only when that statement is not marked
          ``closing``.

        **The look-back changes the shape of the outer two, not just their
        size.** Unbounded they can only be open-ended — "anything before 4
        December 2019" — because without a request there is no date to run back
        to and inventing one would assert a requirement nobody set. Given the
        date a request reaches back to, they become ordinary gaps with two ends,
        which is what a motion can actually ask for. A gap wholly before the
        look-back stops being a gap at all: nobody asked for it.

        :param since: The earliest date the governing request reaches back to.
        :param today: The other end of "through the present".
        :return: ``(gaps, nothing_before, nothing_after)``.
        """
        if not statements:
            # Nothing produced. Bounded, the whole window is the gap — which is
            # a finding, and an unbounded report cannot make it.
            if since and today and since <= today:
                return ([{"start": since.isoformat(), "end": today.isoformat(),
                          "days": (today - since).days + 1}], None, None)
            return [], None, None

        gaps: list[dict[str, Any]] = []
        ordered = sorted(statements, key=lambda s: (s.period_start, s.period_end))

        def add(start: date, end: date) -> None:
            """Record a hole, clipped to the window somebody actually asked for."""
            if since and start < since:
                start = since
            if today and end > today:
                end = today
            if end >= start:
                gaps.append({"start": start.isoformat(), "end": end.isoformat(),
                             "days": (end - start).days + 1})

        first, last = ordered[0], ordered[-1]

        # Leading edge. Bounded it is a real gap; unbounded it can only be
        # reported as an open-ended date, which is what `nothing_before` is for.
        before: Optional[str] = None
        if first.boundary != StatementBoundary.opening:
            if since:
                add(since, first.period_start - _ONE_DAY)
            else:
                before = first.period_start.isoformat()

        for previous, following in zip(ordered, ordered[1:]):
            start = previous.period_end + _ONE_DAY
            end = following.period_start - _ONE_DAY
            if end < start:
                # Overlapping or abutting. An overlap is somebody else's problem
                # — OVERLAPPING_PERIOD flags it at ingest — and is certainly not
                # a hole.
                continue
            add(start, end)

        after: Optional[str] = None
        if last.boundary != StatementBoundary.closing:
            if since and today:
                add(last.period_end + _ONE_DAY, today)
            else:
                after = last.period_end.isoformat()

        gaps.sort(key=lambda g: g["start"])
        return gaps, before, after


    def build_exhibit(
        self,
        manager: DatabaseManager,
        matter: Any,
        side: Optional[str] = None,
        exhibit_name: str = "Statements Produced and Not Produced",
        today: Optional[date] = None,
    ) -> Exhibit:
        """
        The same report as a document — the attachment to a motion to compel.

        **Landscape, and not by preference.** Thirteen months across plus the
        account is fourteen columns; in portrait the Bates numbers wrap and the
        grid stops being scannable, which is the only thing a grid is for.

        The colour the screen uses cannot survive into a .docx cell, so the
        daggers carry the meaning instead and the legend travels with them —
        a mark whose explanation stayed behind on the screen is worse than no
        mark, because the reader can see something is qualified and cannot tell
        what.

        **The gaps are `findings`, not rows.** They are prose of no fixed width,
        one per account, and the most important content in the document. A cell
        of a fourteen-column table is the wrong container and a footnote is the
        wrong weight.
        """
        report = self.matrix(manager, matter_id=matter.id, side=side, today=today)
        caption, warnings = caption_lines(matter, exhibit_name)

        rows: list[Row] = []
        findings: list[tuple[str, str]] = []
        blank = ("",) * len(_MONTHS)

        for account in report["accounts"]:
            # EACH GRID GETS ITS OWN PAGE IN THE PDF. These are read one account
            # at a time across a counsel table, and an account whose years
            # straddle a fold is the one thing the grid must not do — the reader
            # loses the column headings and has to count months.
            #
            # Marked on the first row of the account's block, whichever that is:
            # putting it on the year row when a type heading precedes it would
            # strand the heading at the foot of the previous page.
            # The title carries both facts, so the grid beneath it is nothing
            # but months. Grouping headings would be a second kind of title for
            # a document where every account already has its own page.
            title = "%s — %s" % (account["label"], account["type_label"])
            block: list[Row] = [Row(
                cells=(title, account["type_label"], "") + blank,
                full_width=True,
            )]

            for year in report["years"]:
                cells = [account["label"], account["type_label"], str(year)]
                for month in range(1, 13):
                    held = account["cells"].get("%04d-%02d" % (year, month), [])
                    if held:
                        cells.append(" ".join(
                            "%s%s" % (
                                # Statements are produced unstamped more often
                                # than they should be. An X still says the month
                                # is accounted for; the word "held" read as a
                                # value and invited somebody to look for it in
                                # the production.
                                cell["bates"] or "X",
                                " †" if cell["boundary"] == "opening"
                                else " ‡" if cell["boundary"] == "closing" else "",
                            )
                            for cell in held
                        ))
                    else:
                        # An em dash where the account did not yet exist, blank
                        # where a statement is simply absent. The distinction is
                        # the report: one is an answer, the other is a question.
                        key = "%04d-%02d" % (year, month)
                        outside = (
                            (account["opening_month"] and key < account["opening_month"])
                            or (account["closing_month"] and key > account["closing_month"])
                        )
                        cells.append("—" if outside else "")
                # NO DEPTH. It was here to indent the years under a type
                # heading that no longer exists — the title carries the type
                # now. Left in, every renderer indents the first column: the
                # DOCX pushed "2019" far enough right to wrap it onto four
                # lines inside a column wide enough to hold it twice over.
                block.append(Row(cells=tuple(cells)))

            # Every account but the first starts a page. The first does not: it
            # follows the caption on page one, where a forced break would leave
            # a page holding nothing but the caption.
            if rows and block:
                block[0] = replace(block[0], page_break=True)
            rows.extend(block)

            findings.append((account["label"], self._coverage_sentence(account)))

        scope = report["scope"]
        selection: list[tuple[str, str]] = [
            ("Report", _SIDE_TITLE.get(side or "", "Every account on this matter")),
        ]
        if scope["bounded"]:
            selection.append(("Period requested",
                              "%s through %s" % (scope["since"], scope["through"])))
        else:
            selection.append((
                "Period requested",
                "Not recorded — the grid covers the statements produced, and the gaps "
                "below can only name what falls between them",
            ))
        selection.extend([
            ("Placed by", "The month each statement CLOSES in. Periods do not follow "
                          "calendar months, so a filled month is not necessarily a "
                          "covered one"),
            ("Accounts listed", str(report["totals"]["accounts"])),
            ("Statements held", str(report["totals"]["statements"])),
        ])
        if report["totals"]["unassigned"]:
            # Said on the document, not just on the screen. A report that
            # quietly omitted accounts would be read as covering all of them.
            selection.append((
                "Not included",
                "%d account(s) on this matter have no side recorded as responsible for "
                "producing them and appear on neither report"
                % report["totals"]["unassigned"],
            ))

        footnotes = ["† Opening statement — the account's first, so nothing is missing before it.",
                     "‡ Closing statement — its last, so nothing is missing after it.",
                     "An em dash marks a month outside the account's life, established by those "
                     "markers. A blank marks a month for which no statement was produced."]

        return Exhibit(
            name=exhibit_name,
            caption=caption,
            columns=_EXHIBIT_COLUMNS,
            rows=tuple(rows),
            selection=tuple(selection),
            summary=(
                ("Accounts", str(report["totals"]["accounts"])),
                ("Statements produced", str(report["totals"]["statements"])),
                ("Periods not accounted for", str(report["totals"]["gaps"])),
            ),
            findings=tuple(findings),
            findings_title="Not accounted for",
            footnotes=tuple(footnotes),
            warnings=warnings,
            landscape=True,
            # Twelve months of nine-character Bates numbers need 648pt, and
            # landscape letter at one-inch margins leaves exactly 648pt — so
            # December fell off the edge by a hair. Half an inch buys a whole
            # extra column of slack and still clears the non-printable border of
            # the copiers and scanners an exhibit passes through.
            margin_inches=0.5,
        )

    @staticmethod
    def _coverage_sentence(account: dict[str, Any]) -> str:
        """
        One account's gaps, in the words a request for production uses.

        Days, never months: a motion asks for "1 February 2020 through 3 March
        2020", and the grid above can only say "no March cell".
        """
        if account["statements"] == 0:
            return "No statements produced for this account."

        parts: list[str] = []
        if account["nothing_before"]:
            parts.append("anything before %s, the earliest produced (not marked as the "
                         "account's first)" % account["nothing_before"])
        parts.extend(
            "%s to %s (%d day%s)" % (gap["start"], gap["end"], gap["days"],
                                     "" if gap["days"] == 1 else "s")
            for gap in account["gaps"]
        )
        if account["nothing_after"]:
            parts.append("anything after %s, the latest produced (not marked as the "
                         "account's last)" % account["nothing_after"])

        if not parts:
            return "Complete — every day of the period requested is accounted for."
        return "; ".join(parts) + "."


compliance_service = ComplianceService()
