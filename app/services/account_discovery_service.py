"""
app/services/account_discovery_service.py - Accounts the transactions mention.

A production shows you the accounts someone chose to produce. The transactions
in it name the accounts they did not: money moves between accounts, and the
statement of the account you *do* have prints the number of the one you do not.

    Transfer from XXX4070 to XXX9260: Conf #:19842192
    Transfer to DDA        Acct No.  86110018909-D
    INTERNET XFER FROM CHKG 8098386837

Every one of those names a second account. Matching them against the accounts
actually filed on the matter leaves the difference: numbers that exist, that
money demonstrably moved through, and that nobody produced a statement for.

This finds candidates, not conclusions. A reference is a printed fragment on a
statement, and the reasoning from "XXX4070 appears on a First Financial
statement" to "there is a First Financial account ending 4070" is an inference
the tool makes explicit rather than hides — see ``institution_inferred``.
"""
import re
from collections import OrderedDict
from decimal import Decimal
from typing import Any, Optional

from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
)
from db_handler import DatabaseManager
from services.exhibit_service import Column, Exhibit, caption_lines, money
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

ZERO = Decimal("0.00")

# Rows per page. PostgREST caps a request at its max-rows setting regardless of
# what is asked for, so anything that can exceed one page has to be paged or it
# silently returns the first page and looks complete.
_PAGE = 1000

# A description has to be about a transfer before any number in it is read as
# an account. Without this, every REF# and confirmation number on the statement
# becomes an "undisclosed account".
_TRANSFER = re.compile(r"\b(?:transfer|xfer)\b", re.I)

# The same gate, pushed to the database so the scan fetches the transfer lines
# instead of every line on the matter. These two substrings are exactly what
# ``_TRANSFER`` matches, so the query and the parser cannot drift apart: any
# row the regex would accept is a row one of these searches returns.
_TRANSFER_TERMS = ("transfer", "xfer")

# What the exhibit shows. The money columns are the point of the document: an
# account nobody produced that received six figures is a different fact from one
# mentioned once, and the reader should not have to add the rows up to see it.
_EXHIBIT_COLUMNS = (
    Column("Institution"),
    Column("Last 4"),
    Column("As printed"),
    Column("Mentions", numeric=True),
    Column("Received from", numeric=True, money=True),
    Column("Sent to", numeric=True, money=True),
    Column("Net", numeric=True, money=True),
    Column("First seen"),
    Column("Last seen"),
    Column("Referenced on"),
)

# Confirmation, reference, and transaction numbers sit inside transfer
# descriptions and look exactly like account numbers. Removed before anything
# else is read. "transaction" is spelled out rather than matched as trans\w*,
# which would also swallow "Transfer" — the word the whole scan depends on.
_NOISE = re.compile(
    r"\b(?:conf|ref|trace|seq|auth|transaction)\w*\W{0,3}#?\s*:?\s*\d+", re.I
)

# Trailing card timestamps: "231212 103658".
_TIMESTAMP = re.compile(r"\b\d{6}\s+\d{6}\b")

# The masked form a statement prints for another account. Banks disagree about
# the mask character and about whether a space follows it:
#
#   First Financial   Transfer from XXX4070 to XXX9260
#   Chase             Online Transfer To Chk ...9323
#   others            ****1234, ####5678, ••••1234
#
# So the rule is "two or more mask characters, then digits" rather than a
# literal XX. Two or more matters: a single dot is the one in "Acct No." and in
# every decimal amount on the statement, and a single hyphen is in every date.
_MASKED = re.compile(r"(?:x{2,}|[*.#•·]{2,})\s*(\d{3,})\b", re.I)

# An explicit account number: "Acct No. 81110044625", "Account # 1234".
_ACCT = re.compile(r"\bacc?t\.?\s*(?:no\.?|number|#)?\s*:?\s*(\d{4,})\b", re.I)

# A label then a number, after a direction word: "FROM CHKG 8098386837",
# "TRANSFER FROM CHASE 4321". The label is what may name an institution.
_LABELLED = re.compile(r"\b(?:to|from)\s+([A-Za-z][A-Za-z&.\-' ]{1,24}?)\s+(\d{4,})\b", re.I)

# Words that appear where an institution name would and are not one. Everything
# here describes a KIND of account or a channel; anything else in that position
# is treated as a name, which is how "CHASE 4321" is told from "CHKG 4321".
_NOT_AN_INSTITUTION = {
    "dda", "chkg", "chk", "checking", "sav", "savings", "sv", "mma", "mmkt",
    "money market", "cd", "loan", "ln", "credit", "card", "acct", "account",
    "acct no", "no", "number", "sweep", "transfer", "xfer", "internet",
    "online", "mobile", "inst", "web", "ppd", "arc", "tel", "ach", "wire",
    "deposit", "withdrawal", "payment", "pmt", "the", "my", "your", "a",
}


def _clean(description: str) -> str:
    """Strip the numbers that are not account numbers before reading the rest."""
    text = _NOISE.sub(" ", description or "")
    return _TIMESTAMP.sub(" ", text)


def _institution(label: str) -> Optional[str]:
    """
    The institution name inside a label, if the label is one at all.

    Stopwords are stripped from both ends rather than the whole label being
    compared against a phrase list: what sits before an account number is
    account-type words in any combination — "DDA Acct No", "CHKG", "Savings
    Acct" — and enumerating the combinations is a losing game. What remains
    after the known words come off is a name, or nothing.
    """
    words = [w for w in re.split(r"[\s.]+", label or "") if w]
    while words and words[0].lower().strip("'-") in _NOT_AN_INSTITUTION:
        words.pop(0)
    while words and words[-1].lower().strip("'-") in _NOT_AN_INSTITUTION:
        words.pop()
    return " ".join(words) or None


def _references(description: str) -> list[tuple[str, Optional[str]]]:
    """
    Account references in one description, in the order they are printed.

    The three patterns overlap: "Acct No. 86110018909" is matched both as an
    explicit account number and as "a word, then a number". Each run of digits
    is therefore claimed once, by the most specific pattern that reaches it, or
    the line is counted twice and the account appears to have moved twice the
    money it did.

    :return: ``(number, label)`` per reference, where the label is an
        institution name read off the page, or None. The same description can
        name two accounts ("from XXX4070 to XXX9260") — both come back, and the
        caller drops the one that is its own account.
    :rtype: list[tuple[str, Optional[str]]]
    """
    if not description or not _TRANSFER.search(description):
        return []
    text = _clean(description)

    found: list[tuple[int, str, Optional[str]]] = []
    claimed: list[tuple[int, int]] = []
    # Most specific context first. A match that says "Acct No." outright beats
    # the same digits read as a label followed by a number.
    for pattern, digit_group in ((_ACCT, 1), (_MASKED, 1), (_LABELLED, 2)):
        for match in pattern.finditer(text):
            start, end = match.span(digit_group)
            if any(start < taken_end and taken_start < end for taken_start, taken_end in claimed):
                continue
            claimed.append((start, end))
            label = _institution(match.group(1)) if digit_group == 2 else None
            found.append((start, match.group(digit_group), label))

    return [(number, label) for _, number, label in sorted(found)]


def _last4(number: str) -> Optional[str]:
    digits = "".join(c for c in number if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


class AccountDiscoveryService:
    """Finds accounts named in a matter's transactions but not produced."""

    def undisclosed(self, manager: DatabaseManager, matter_id: int) -> list[dict[str, Any]]:
        """
        Accounts the transactions reference that the matter does not hold.

        Direction comes from the **sign of the amount**, not from the words
        "to" and "from". A single description carries both — "Transfer from
        XXX9260 to XXX8909" — so reading the words means deciding which of the
        two numbers the sentence is about. The sign has no such ambiguity:
        money that left this account went to the other one.

        :return: One entry per distinct account, busiest first. Empty when the
            matter has no accounts or nothing references an outside number.
        :rtype: list[dict[str, Any]]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        accounts = account_repo.get_by_matter(matter_id)
        if not accounts:
            return []

        by_id = {a.id: a for a in accounts}
        # Every account already on the matter, by last four. A reference to one
        # of these is not a discovery — including the statement's own account,
        # which names itself constantly.
        known = {a.account_number_last4 for a in accounts if a.account_number_last4}

        rejected = statement_repo.rejected_ids(matter_id)
        scope = sorted(by_id)
        found: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        seen_rows: set[int] = set()
        scanned = 0

        for term in _TRANSFER_TERMS:
            offset = 0
            while True:
                rows, total = transaction_repo.search(
                    account_ids=scope, exclude_statement_ids=rejected,
                    text=term, limit=_PAGE, offset=offset,
                )
                if not rows:
                    break
                for row in rows:
                    # "Transfer" and "xfer" both appear in some descriptions, and
                    # a line counted twice doubles its money and its mention.
                    if row.id in seen_rows:
                        continue
                    seen_rows.add(row.id)
                    scanned += 1
                    self._collect(row, by_id, known, found)
                offset += len(rows)
                if offset >= total:
                    break

        results = sorted(
            found.values(),
            key=lambda entry: (-entry["mentions"], -abs(entry["net"]), entry["last4"]),
        )
        LOGGER.info(
            "account_discovery: matter=%s scanned %d transfer line(s), %d account(s) referenced but not held",
            matter_id, scanned, len(results),
        )
        return results

    @staticmethod
    def _collect(
        row: Any,
        by_id: dict[int, Any],
        known: set,
        found: "OrderedDict[str, dict[str, Any]]",
    ) -> None:
        """Fold one transaction's references into the running tally."""
        source = by_id.get(row.financial_account_id)
        for number, label in _references(row.description):
            last4 = _last4(number)
            if not last4 or last4 in known:
                continue

            entry = found.get(last4)
            if entry is None:
                entry = {
                    "last4": last4,
                    "reference": number,
                    # A named institution is read off the page. Without one, the
                    # assumption is that a transfer stays inside the bank whose
                    # statement it is printed on — usually right, sometimes not,
                    # and always marked so nobody mistakes it for the document.
                    "institution": label or (source.institution if source else None),
                    "institution_inferred": label is None,
                    "mentions": 0,
                    "money_in": ZERO,
                    "money_out": ZERO,
                    "net": ZERO,
                    "first_seen": None,
                    "last_seen": None,
                    "seen_on": [],
                    "examples": [],
                }
                found[last4] = entry
            elif label and entry["institution_inferred"]:
                # A later mention that names the bank outright beats an earlier
                # inference from the statement it sat on.
                entry["institution"] = label
                entry["institution_inferred"] = False

            # The longest form of the number is the most useful to quote.
            if len(number) > len(entry["reference"]):
                entry["reference"] = number

            entry["mentions"] += 1
            amount = row.amount or ZERO
            if amount < 0:
                entry["money_out"] += -amount
            else:
                entry["money_in"] += amount
            entry["net"] = entry["money_in"] - entry["money_out"]

            when = row.transaction_date
            if when:
                if entry["first_seen"] is None or when < entry["first_seen"]:
                    entry["first_seen"] = when
                if entry["last_seen"] is None or when > entry["last_seen"]:
                    entry["last_seen"] = when

            if source is not None:
                label_ = "%s%s" % (
                    source.institution,
                    " ····%s" % source.account_number_last4 if source.account_number_last4 else "",
                )
                if label_ not in entry["seen_on"]:
                    entry["seen_on"].append(label_)
            if len(entry["examples"]) < 3 and row.description not in entry["examples"]:
                entry["examples"].append(row.description)


    def build_exhibit(
        self,
        manager: DatabaseManager,
        matter: Any,
        exhibit_name: str = "Accounts Referenced But Not Produced",
    ) -> Exhibit:
        """
        The same list as an exhibit — the attachment to a motion to compel.

        Deliberately not routed through the transaction exhibit: the rows are
        accounts, not lines, so forcing them into a Date/Bates/Amount table would
        leave most columns blank and drop the two figures that make the point,
        which are how much moved and over what period.

        The dagger is carried into the document with its footnote. A mark whose
        explanation stayed behind on the screen is worse than no mark — the
        reader sees a qualification and cannot tell what is being qualified.
        """
        found = self.undisclosed(manager, matter.id)
        caption, warnings = caption_lines(matter, exhibit_name)

        rows = tuple(
            (
                "%s%s" % (entry["institution"] or "Unknown institution",
                          " †" if entry["institution_inferred"] else ""),
                entry["last4"],
                entry["reference"],
                str(entry["mentions"]),
                str(entry["money_in"]),
                str(entry["money_out"]),
                str(entry["net"]),
                entry["first_seen"].isoformat() if entry["first_seen"] else "",
                entry["last_seen"].isoformat() if entry["last_seen"] else "",
                "; ".join(entry["seen_on"]),
            )
            for entry in found
        )

        total_in = sum((entry["money_in"] for entry in found), start=ZERO)
        total_out = sum((entry["money_out"] for entry in found), start=ZERO)
        accounts = FinancialAccountRepository(manager).get_by_matter(matter.id)

        footnotes = []
        if any(entry["institution_inferred"] for entry in found):
            footnotes.append(
                "† The description gave an account number but no bank, so the account is assumed "
                "to be held at the institution whose statement the transfer was printed on. "
                "That assumption is not evidence and should be confirmed."
            )

        return Exhibit(
            name=exhibit_name,
            caption=caption,
            columns=_EXHIBIT_COLUMNS,
            rows=rows,
            selection=(
                ("Source", "Transfer descriptions on the statements produced in this matter"),
                ("Compared against", "The %d account%s currently on this matter"
                                     % (len(accounts), "" if len(accounts) == 1 else "s")),
                ("Matched on", "The last four digits of the account number"),
                ("Direction", "Taken from the sign of each amount, not from the words "
                              "\"to\" and \"from\" — a description often carries both"),
                ("Accounts listed", str(len(rows))),
            ),
            summary=(
                ("Accounts referenced but not produced", str(len(rows))),
                ("Total received from them", money(total_in)),
                ("Total sent to them", money(total_out)),
            ),
            footnotes=tuple(footnotes),
            warnings=warnings,
        )


account_discovery_service = AccountDiscoveryService()
