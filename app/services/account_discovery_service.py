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

from db.models.financial import AccountType
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
    PayeeClassificationRepository,
    TransactionCategoryRepository,
)
from db_handler import DatabaseManager
from services.category_rule_service import matches, prepare
from services.exhibit_service import Column, Exhibit, Row, caption_lines, money
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

ZERO = Decimal("0.00")

# Accounts that ARE a debt. Two uses, both in the creditor scan: they are
# excluded from the lines scanned (a payment landing on a card describes the
# account that funded it, not a creditor), and a produced one is what makes a
# creditor payment explained rather than a finding.
_LIABILITY_ACCOUNTS = frozenset({AccountType.credit_card, AccountType.loan})

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

# A payment is a transfer with only one side named, and a few banks name the
# other side anyway: "Payment To Chase Card Ending IN 9547". Those belong in the
# account list exactly as a transfer reference does. The vast majority name only
# a payee and are handled by ``creditors()`` further down.
_PAYMENT = re.compile(r"\b(?:payment|payments|pmt|autopay)\b", re.I)
_PAYMENT_TERMS = ("payment", "pmt", "autopay")

# The same idea for wires. "wire" alone would also pull every VERIZON WIRELESS
# line out of the database, which is cheap — the parser below rejects them — and
# is the price of not having to know every bank's phrasing in the query.
_WIRE_TERMS = ("wire",)


def _squash(text):
    """A bank name reduced to letters, for comparing two spellings of one bank."""
    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())

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

# The English form of a mask: "Chase Card Ending IN 9547", "account ending in
# 4448", "Card ending 1269". No mask characters at all, so ``_MASKED`` never saw
# it, and Chase writes every card payment this way.
#
# Two spellings, and the second one is why the account-type word is inside the
# alternation rather than optional: "ending 2024" appears in "for the period
# ending 2024", and a statement period is not an account. Either the word "in"
# follows "ending", or an account-type word precedes it. Both together are the
# common case and match the first branch.
_ENDING = re.compile(
    r"\b((?:[A-Za-z][A-Za-z&.'\-]*\s+){0,4}?)"
    r"(?:ending\s+in|(?:card|acct|account|chk|checking|savings|svgs|loan)\s+ending)"
    r"\s+#?\s*(\d{3,})\b",
    re.I,
)

# Words that appear where an institution name would and are not one. Everything
# here describes a KIND of account or a channel; anything else in that position
# is treated as a name, which is how "CHASE 4321" is told from "CHKG 4321".
_NOT_AN_INSTITUTION = {
    "dda", "chkg", "chk", "checking", "sav", "savings", "sv", "mma", "mmkt",
    "money market", "cd", "loan", "ln", "credit", "card", "acct", "account",
    "acct no", "no", "number", "sweep", "transfer", "xfer", "internet",
    "online", "mobile", "inst", "web", "ppd", "arc", "tel", "ach", "wire",
    "deposit", "withdrawal", "payment", "pmt", "the", "my", "your", "a",
    # ``_LABELLED`` eats the direction word itself, but ``_ENDING`` does not:
    # its label runs from wherever the phrase starts, so "Payment To Chase Card"
    # arrives whole and has to come off at both ends to leave "Chase".
    "to", "from", "autopay", "ending", "in",
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

    A payment is read too, but with a **narrower** pattern set. On a transfer,
    "FROM CHKG 8098386837" is a bank convention and the trailing run really is
    an account number. On a payment it is a confirmation number essentially
    always — "Zelle Payment To Kathy Gunn 20928990159" would otherwise be
    reported as an undisclosed account belonging to Kathy Gunn. So ``_LABELLED``
    is a transfer-only pattern, and a payment must name its account in a form
    that says so outright: a mask, an "Acct No.", or "ending in".

    :return: ``(number, label)`` per reference, where the label is an
        institution name read off the page, or None. The same description can
        name two accounts ("from XXX4070 to XXX9260") — both come back, and the
        caller drops the one that is its own account.
    :rtype: list[tuple[str, Optional[str]]]
    """
    if not description:
        return []
    transfer = bool(_TRANSFER.search(description))
    if not transfer and not _PAYMENT.search(description):
        return []
    text = _clean(description)

    found: list[tuple[int, str, Optional[str]]] = []
    claimed: list[tuple[int, int]] = []
    # Most specific context first. A match that says "Acct No." outright beats
    # the same digits read as a label followed by a number.
    patterns: list[tuple[Any, int]] = [(_ACCT, 1), (_ENDING, 2), (_MASKED, 1)]
    if transfer:
        patterns.append((_LABELLED, 2))
    for pattern, digit_group in patterns:
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



# ── Wires ────────────────────────────────────────────────────────────────────
#
# A wire names the sending INSTITUTION, not the sending account. There is no
# number to key on, which is why these cannot join the account list above:
#
#   Fedwire Credit Via: UBS Ag Stamford Branch/026007993 B/O: Kimberly Harrison
#   ... Bnf=Kimberly A Harrison ... Rfb=Fa35304Dom240605 ...
#
# What it does carry is the bank's ABA routing number, which is checksummed and
# therefore a reliable key — and, in the line above, the fact that the person
# who SENT the money is the person who RECEIVED it. Money moved from an account
# she controls somewhere else into an account we hold, and nobody produced the
# other side.

# Vocabulary, not just the word "wire" — a phone bill from a carrier with
# "Wireless" in its name is not a wire, and the tags below come from the Fedwire
# message format rather than from any one bank's phrasing, so they survive a
# change of institution.
_WIRE = re.compile(
    r"\b(?:fedwire|wire\s+(?:credit|debit|transfer|out|in)\b|incoming\s+wire|"
    r"outgoing\s+wire|int(?:er)?national\s+wire|intl\s+wire)\b"
    r"|\bB/O\s*[:=]|\bBNF\s*[:=]|\bORIG\s*[:=]",
    re.I,
)

# "UBS Ag Stamford Branch/026007993", "ABA/021000021", "Chase Bank NA / 111000025".
_BANK_ABA = re.compile(r"([A-Za-z][A-Za-z&.,'\-]*(?:[ ][A-Za-z&.,'\-]+){0,7})\s*/\s*(\d{9})\b")

# The Fedwire parties. Each runs until the next tag rather than to a fixed
# length, because banks pad them with address lines of unpredictable shape.
_NEXT_TAG = r"(?=\s+(?:Ref\b|Bnf\b|B/O\b|Rfb\b|Imad\b|Trn\b|OBI\b|BBI\b|US/|Ac-)|$)"
_BY_ORDER = re.compile(r"\bB/O\s*[:=]\s*(.+?)" + _NEXT_TAG, re.I)
_BENEFICIARY = re.compile(r"\bBnf\s*[:=]\s*(.+?)" + _NEXT_TAG, re.I)

# Words that appear in a party line and say nothing about who the party is.
_NOT_A_NAME = {
    "the", "and", "llc", "inc", "corp", "co", "ltd", "usa", "us", "united",
    "states", "america", "street", "st", "ave", "avenue", "road", "rd", "drive",
    "dr", "lane", "ln", "suite", "apt", "city", "county", "tx", "texas", "ca",
    "ny", "fl", "attn", "for", "credit", "acct", "account", "of", "care",
}


def _aba_is_valid(number: str) -> bool:
    """
    The ABA checksum, which is what makes a routing number safe to key on.

    A nine-digit run picked out of a wire could be anything. This one is
    self-checking: 3-7-1 weights, summed, must land on a multiple of ten. A
    number that fails is not a routing number, whatever it sits next to.
    """
    if len(number) != 9 or not number.isdigit():
        return False
    d = [int(c) for c in number]
    return (3 * (d[0] + d[3] + d[6])
            + 7 * (d[1] + d[4] + d[7])
            + (d[2] + d[5] + d[8])) % 10 == 0


def _name_tokens(text: Optional[str]) -> set[str]:
    """The words in a party line that could be part of somebody's name."""
    words = re.findall(r"[A-Za-z]{2,}", text or "")
    return {w.upper() for w in words if w.lower() not in _NOT_A_NAME}


def _same_party(originator: Optional[str], beneficiary: Optional[str]) -> bool:
    """
    Whether the sender and the receiver are the same person.

    Two shared name words, which is the rule `intake_service._match_confidence`
    already uses for people: a surname alone is not enough, because adverse
    parties in a family-law caption almost always share one. Here both lines are
    padded with address text, so comparing token sets is steadier than trying to
    decide which words are the name.

    This is the finding, not a detail. "Kimberly Harrison wired herself
    $198,101.18 from an account at UBS" is a different sentence from "money
    arrived from UBS", and only the first one is worth a motion.
    """
    shared = _name_tokens(originator) & _name_tokens(beneficiary)
    return len(shared) >= 2


def _wire_details(description: str) -> Optional[dict[str, Any]]:
    """
    The institution a wire came from or went to, if the line is a wire at all.

    :return: ``{institution, aba, originator, beneficiary, same_party}`` or None.
    :rtype: Optional[dict[str, Any]]
    """
    if not description or not _WIRE.search(description):
        return None

    institution = aba = None
    for name, number in _BANK_ABA.findall(description):
        if not _aba_is_valid(number):
            continue
        cleaned = " ".join(name.split()).strip(" .,-'")
        # A one-word "bank name" before a routing number is usually the tail of
        # a longer phrase the pattern could not see the start of.
        if len(cleaned) >= 3:
            institution, aba = cleaned, number
            break

    originator = _BY_ORDER.search(description)
    beneficiary = _BENEFICIARY.search(description)
    originator = originator.group(1).strip() if originator else None
    beneficiary = beneficiary.group(1).strip() if beneficiary else None

    if institution is None and originator is None:
        return None

    return {
        "institution": institution,
        "aba": aba,
        "originator": originator,
        "beneficiary": beneficiary,
        "same_party": _same_party(originator, beneficiary),
    }


# ── Creditors ────────────────────────────────────────────────────────────────
#
# The third shape of evidence, and the one no pattern can settle on its own.
#
# A payment to a creditor is a transfer with only one side named. Occasionally
# the bank prints the other side and ``_ENDING`` above catches it; overwhelmingly
# it does not, and what is left is a payee:
#
#     ACH PMT AMEX EPAYMENT 0005000008 ID #-M3630 TRACE #-091000014282361
#     10/16 Online Payment 22398267106 To City of Lewisville
#
# The first is a credit card nobody produced. The second is a water bill. They
# are the same sentence. Grammar cannot separate them and neither can any
# regular expression, because the difference is not in the text — it is in
# knowing what the counterparty is. Two sources answer that, in order:
#
#   1. The CATEGORY. Filing a line under "Credit Card Payments" is already the
#      assertion that the payee is a card issuer, and a paralegal makes it as
#      ordinary categorization work. Free, and explainable in one sentence.
#   2. A PAYEE CLASSIFICATION, for what nobody has categorized yet — including
#      the negative, which is the half that makes the report survive contact
#      with a real production. Without a way to say "Atmos Energy is not a
#      creditor" once, forty utilities come back on every matter and the list
#      stops being read.
#
# Everything else is reported as a CANDIDATE, ranked by money, for a person to
# rule on. That is the honest handling: the tool does not guess at what it
# cannot know, it shrinks the pile and asks.

# Noise that is not part of anybody's name. Removed before the payee is read,
# so the same creditor paid twelve times produces one group rather than twelve.
_PAYEE_LEADING_DATE = re.compile(r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+")
_PAYEE_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_PAYEE_PHONE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.]\d{4}\b")
# "ID #-M3630", "TRACE #-091000014282361", "Web ID: 3264681992", "Conf #:198421".
_PAYEE_ID = re.compile(
    r"\b(?:web\s+id|id|trace|conf|ref|seq|auth)\w*\s*[#:]*\s*[-:]?\s*[A-Z]?\d+", re.I
)
_PAYEE_DIGITS = re.compile(r"\b[\d-]{4,}\b")

# Phrases that describe the CHANNEL rather than the payee, removed wherever they
# appear. "CHK CARD PUR" is on every debit-card line at some banks and would
# otherwise split one merchant across as many groups as it has store numbers.
_PAYEE_NOISE = (
    "chk card pur", "card purchase", "pos purchase", "recurring payment",
    "preauthorized", "electronic payment", "ach pmt", "ach debit", "ach credit",
    "web id", "ppd id", "ccd id", "des:", "indn:",
)

# Connectives at either end. Stripped iteratively, longest first, because a
# description stacks them: "Withdrawal from AMEX EPAYMENT ACH PMT" is prefix,
# payee, and suffix in one line.
_PAYEE_LEAD = (
    # The payment rail is not the payee. A Zelle to a named person may be a
    # private loan being repaid, which is a debt somebody has to disclose — so
    # the name has to survive, and "Zelle Payment To" has to come off in front
    # of it. Venmo prints no name, so a Venmo line groups as Venmo.
    # "Venmo Payment" with no name after it is deliberately NOT here: stripping
    # it leaves an empty string, and a line that scrubs to nothing is dropped
    # without trace. Left alone, the trailing "Payment" comes off and the row
    # groups under VENMO — which is at least a visible fact about where money
    # went, for somebody to dismiss once.
    "zelle payment to", "zelle payment from", "venmo payment to",
    "online payment to", "online payment", "bill payment", "edipayment",
    "withdrawal from", "withdrawal to", "deposit from", "payment to",
    "payment from", "autopay", "epayment", "e-payment", "payments", "payment",
    "withdrawal", "deposit", "debit", "credit", "pmt", "ach", "to", "from",
)
_PAYEE_TRAIL = (
    "ach pmt", "e-payment", "epayment", "autopay", "ending in", "ending",
    "payments", "payment", "purchase", "debit", "credit", "pmt", "srvc",
    "- debit", "- credit", "web", "tel", "arc", "ppd",
)

# A two-letter state at the end of a card-purchase line, after the city.
_PAYEE_STATE = re.compile(r"\s+(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[ADLN]|K[SY]|LA|"
                          r"M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|"
                          r"V[AT]|W[AIVY]|DC)$", re.I)


def _strip_ends(text: str, phrases: tuple[str, ...], leading: bool) -> str:
    """Peel connectives off one end until nothing more comes away."""
    changed = True
    while changed and text:
        changed = False
        lowered = text.lower()
        for phrase in sorted(phrases, key=len, reverse=True):
            if leading and lowered.startswith(phrase + " "):
                text, changed = text[len(phrase) + 1:].strip(), True
                break
            if leading and lowered == phrase:
                return ""
            if not leading and lowered.endswith(" " + phrase):
                text, changed = text[:-(len(phrase) + 1)].strip(), True
                break
            if not leading and lowered == phrase:
                return ""
    return text


def _payee_key(description: Optional[str]) -> str:
    """
    A payment description reduced to who was paid.

    Every creditor in a production is paid repeatedly, and each line carries a
    different date, trace number, and ACH id. Grouping on the raw description
    gives one group per payment, which is a list of transactions rather than a
    list of creditors. Stripping what varies leaves what does not.

        ACH PMT AMEX EPAYMENT 0005000008 ID #-M3630 TRACE #-091000014282361
        -> AMEX EPAYMENT

        12/21 Payment To Chase Card Ending IN 9547
        -> CHASE CARD

    Deliberately not perfect, and it does not need to be. A payee that scrapes
    into two near-identical groups is a cosmetic problem a paralegal fixes once
    by ruling on it — after which the ruling's own pattern becomes the key and
    both groups collapse. What would *not* be recoverable is a scrub aggressive
    enough to merge two different creditors into one row, so every rule here
    removes bank furniture and none of them removes words.

    :rtype: str
    """
    text = _PAYEE_LEADING_DATE.sub("", description or "")
    text = _PAYEE_ID.sub(" ", text)
    text = _PAYEE_DATE.sub(" ", text)
    text = _PAYEE_PHONE.sub(" ", text)
    text = _TIMESTAMP.sub(" ", text)
    text = _PAYEE_DIGITS.sub(" ", text)

    lowered = text.lower()
    for phrase in _PAYEE_NOISE:
        while phrase in lowered:
            at = lowered.index(phrase)
            text = text[:at] + " " + text[at + len(phrase):]
            lowered = text.lower()

    text = " ".join(text.split()).strip(" -–—,:;#*")
    text = _strip_ends(text, _PAYEE_LEAD, leading=True)
    text = _PAYEE_STATE.sub("", text)
    text = _strip_ends(text, _PAYEE_TRAIL, leading=False)
    return " ".join(text.split()).strip(" -–—,:;#*").upper()


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

        for term in _TRANSFER_TERMS + _PAYMENT_TERMS:
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
            "account_discovery: matter=%s scanned %d line(s), %d account(s) referenced but not held",
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



    def referenced_institutions(
        self, manager: DatabaseManager, matter_id: int,
    ) -> list[dict[str, Any]]:
        """
        Institutions the wires name that the matter has no account at.

        A wire is a different shape of evidence from a transfer. A transfer
        prints the other account's number; a wire prints the other **bank** —
        its name and its routing number — and never the account. So these cannot
        join the account list, which is keyed on the last four digits of a number
        that is simply not there.

        What makes them worth reporting anyway is the size and the sender. Seven
        wires totalling $198,101.18 arrived at one Chase account from UBS, all
        of them *by order of the same person who received them*. That is money
        moving out of an account she controls and into one we hold, and nobody
        produced the other side.

        :return: One entry per institution, largest total first.
        :rtype: list[dict[str, Any]]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        accounts = account_repo.get_by_matter(matter_id)
        if not accounts:
            return []

        by_id = {a.id: a for a in accounts}
        held = {_squash(a.institution) for a in accounts if a.institution}
        rejected = statement_repo.rejected_ids(matter_id)
        scope = sorted(by_id)

        found: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        seen_rows: set[int] = set()

        for term in _WIRE_TERMS:
            offset = 0
            while True:
                rows, total = transaction_repo.search(
                    account_ids=scope, exclude_statement_ids=rejected,
                    text=term, limit=_PAGE, offset=offset,
                )
                if not rows:
                    break
                for row in rows:
                    if row.id in seen_rows:
                        continue
                    seen_rows.add(row.id)
                    self._collect_wire(row, by_id, held, found)
                offset += len(rows)
                if offset >= total:
                    break

        results = sorted(
            found.values(),
            key=lambda e: (-(e["money_in"] + e["money_out"]), e["institution"]),
        )
        LOGGER.info(
            "account_discovery: matter=%s scanned %d wire line(s), %d institution(s) "
            "referenced but not held", matter_id, len(seen_rows), len(results),
        )
        return results

    @staticmethod
    def _collect_wire(
        row: Any,
        by_id: dict[int, Any],
        held: set,
        found: "OrderedDict[str, dict[str, Any]]",
    ) -> None:
        """Fold one wire into the running tally for its institution."""
        details = _wire_details(row.description)
        if details is None or not details["institution"]:
            return

        name = details["institution"]
        # An institution the matter already holds an account at is not a
        # discovery. Compared on squashed names because "UBS" and "UBS AG
        # Stamford Branch" are the same bank written two ways.
        squashed = _squash(name)
        if any(known in squashed or squashed in known for known in held if known):
            return

        # The routing number is the identity when there is one: two statements
        # may write the bank's name differently, but the ABA is the same nine
        # checksummed digits either way.
        key = details["aba"] or squashed
        entry = found.get(key)
        if entry is None:
            entry = {
                "institution": name,
                "aba": details["aba"],
                "wires": 0,
                "money_in": ZERO,
                "money_out": ZERO,
                "net": ZERO,
                "first_seen": None,
                "last_seen": None,
                "same_party_wires": 0,
                "seen_on": [],
                "examples": [],
            }
            found[key] = entry
        elif len(name) > len(entry["institution"]):
            # Keep the fullest spelling of the name that any wire printed.
            entry["institution"] = name

        entry["wires"] += 1
        if details["same_party"]:
            entry["same_party_wires"] += 1

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

        source = by_id.get(row.financial_account_id)
        if source is not None:
            label = "%s%s" % (
                source.institution,
                " ····%s" % source.account_number_last4 if source.account_number_last4 else "",
            )
            if label not in entry["seen_on"]:
                entry["seen_on"].append(label)
        if len(entry["examples"]) < 3 and row.description not in entry["examples"]:
            entry["examples"].append(row.description)

    def creditors(
        self, manager: DatabaseManager, matter_id: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Creditors the matter pays but holds no account for, and the residue.

        Two lists, and the split is the whole point. The first is a finding: a
        payee this firm has said is a creditor, or one whose payments a person
        filed under a liability category. The second is a **question** — payees
        nobody has ruled on, ranked by money, for somebody to sort in a minute.
        Presenting the second as the first is the confident-wrong failure this
        module exists to avoid, so they never share a list.

        The scan runs over the matter's **deposit-side** accounts only. A
        payment arriving on a credit card ("Payment Thank You") says nothing
        about a creditor — it says something about the checking account that
        funded it, which is a different report and a different question.

        :return: ``(creditors, candidates)``, each largest total first.
        :rtype: tuple[list[dict[str, Any]], list[dict[str, Any]]]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        accounts = account_repo.get_by_matter(matter_id)
        if not accounts:
            return [], []

        by_id = {a.id: a for a in accounts}
        scope = sorted(a.id for a in accounts if a.account_type not in _LIABILITY_ACCOUNTS)
        if not scope:
            return [], []

        # Only a produced CREDIT account explains a creditor payment. A produced
        # Chase checking account says nothing about a Chase card.
        held = {
            _squash(a.institution) for a in accounts
            if a.institution and a.account_type in _LIABILITY_ACCOUNTS
        }
        rejected = statement_repo.rejected_ids(matter_id)
        liability = set(TransactionCategoryRepository(manager).liability_ids())
        rulings = self._rulings(manager, matter_id)

        found: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        seen_rows: set[int] = set()

        def absorb(rows: list[Any]) -> None:
            for row in rows:
                if row.id in seen_rows:
                    continue
                seen_rows.add(row.id)
                self._collect_creditor(row, by_id, liability, rulings, found)

        # Two fetches, because neither alone is enough. The text gate finds the
        # payments nobody has categorized — the whole of the candidate list. The
        # category fetch finds a card payment worded so plainly that no gate
        # word appears in it ("ACH DEBIT DISCOVER"), which a person has already
        # filed and which would otherwise be missing from the findings.
        for term in _PAYMENT_TERMS:
            absorb(self._page(transaction_repo, scope, rejected, text=term))
        if liability:
            absorb(self._page(transaction_repo, scope, rejected,
                              category_ids=sorted(liability)))

        creditors: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for entry in found.values():
            squashed = _squash(entry["creditor_name"] or entry["payee"])
            if squashed and any(k in squashed or squashed in k for k in held if k):
                continue  # an account at this creditor was produced
            (creditors if entry["reason"] != "unreviewed" else candidates).append(entry)

        creditors.sort(key=lambda e: (-e["money_out"], e["payee"]))
        candidates.sort(key=lambda e: (-e["money_out"], e["payee"]))
        LOGGER.info(
            "account_discovery: matter=%s scanned %d payment line(s), %d creditor(s), "
            "%d unreviewed payee(s)", matter_id, len(seen_rows), len(creditors),
            len(candidates),
        )
        return creditors, candidates

    @staticmethod
    def _page(
        transaction_repo: Any,
        scope: list[int],
        rejected: list[int],
        **criteria: Any,
    ) -> list[Any]:
        """Every matching row, paged. A creditor found on page two still counts."""
        collected: list[Any] = []
        offset = 0
        while True:
            rows, total = transaction_repo.search(
                account_ids=scope, exclude_statement_ids=rejected,
                limit=_PAGE, offset=offset, **criteria,
            )
            if not rows:
                break
            collected.extend(rows)
            offset += len(rows)
            if offset >= total:
                break
        return collected

    @staticmethod
    def _rulings(manager: DatabaseManager, matter_id: int) -> list[Any]:
        """
        The payee rulings that apply here, matter's own last.

        Order carries the override: ``_collect_creditor`` keeps the last ruling
        that matches, so a matter row beats the firm-wide row it contradicts.
        That is how a payee the firm calls a vendor can still be a creditor in
        one case — the client who really did borrow from a hardware store.

        A missing table is not an error. 033 may not be applied on this node yet,
        and the scan degrades to "everything is a candidate", which is the state
        it starts in anyway.
        """
        try:
            return PayeeClassificationRepository(manager).available_for_matter(matter_id)
        except Exception as e:  # noqa: BLE001 — a missing table must not break the report
            LOGGER.warning("account_discovery: payee classifications unavailable: %s", str(e))
            return []

    @staticmethod
    def _collect_creditor(
        row: Any,
        by_id: dict[int, Any],
        liability: set,
        rulings: list[Any],
        found: "OrderedDict[str, dict[str, Any]]",
    ) -> None:
        """Fold one payment into the running tally for its payee."""
        amount = row.amount or ZERO
        # Money leaving is a payment to a creditor. Money arriving from one is a
        # refund or a cash advance, and neither is evidence of an account the
        # other side failed to produce.
        if amount >= 0:
            return

        payee = _payee_key(row.description)
        if len(payee) < 3:
            return

        # A ruling is matched against the description, not the scraped payee: the
        # scrape may have eaten the very word the ruling names. The last match
        # wins, which is what makes the matter layer an override.
        prepared = prepare(row.description)
        ruling = None
        for candidate in rulings:
            if matches(prepared, candidate.pattern):
                ruling = candidate
        if ruling is not None and ruling.classification == "not_creditor":
            return

        if row.category_id is not None and row.category_id in liability:
            reason = "liability_category"
        elif ruling is not None:
            reason = "classified"
        else:
            reason = "unreviewed"

        # Once a payee is ruled on, the ruling's pattern is the identity. That is
        # what collapses "AT&T BILL PAYMENT DALLAS" and "ATT* BILL" — two scrapes
        # of one payee — into a single row the moment somebody says so.
        key = _squash(ruling.pattern) if ruling is not None else _squash(payee)
        entry = found.get(key)
        if entry is None:
            entry = {
                "payee": ruling.pattern.upper() if ruling is not None else payee,
                "creditor_name": ruling.creditor_name if ruling is not None else None,
                "creditor_type": ruling.creditor_type if ruling is not None else None,
                "reason": reason,
                "classification_id": ruling.id if ruling is not None else None,
                "payments": 0,
                "money_out": ZERO,
                "last4": [],
                "first_seen": None,
                "last_seen": None,
                "seen_on": [],
                "examples": [],
            }
            found[key] = entry
        elif reason == "liability_category" and entry["reason"] != "liability_category":
            # A category is a person's own filing and outranks a keyword ruling.
            entry["reason"] = "liability_category"

        entry["payments"] += 1
        entry["money_out"] += -amount

        # A payment that also printed a number is the strongest version of this
        # finding: the creditor AND the account, from one line. It is already in
        # the account list; carrying it here too means the reader of this row
        # does not have to cross-reference to see it.
        for number, _label in _references(row.description):
            last4 = _last4(number)
            if last4 and last4 not in entry["last4"]:
                entry["last4"].append(last4)

        when = row.transaction_date
        if when:
            if entry["first_seen"] is None or when < entry["first_seen"]:
                entry["first_seen"] = when
            if entry["last_seen"] is None or when > entry["last_seen"]:
                entry["last_seen"] = when

        source = by_id.get(row.financial_account_id)
        if source is not None:
            label = "%s%s" % (
                source.institution,
                " ····%s" % source.account_number_last4 if source.account_number_last4 else "",
            )
            if label not in entry["seen_on"]:
                entry["seen_on"].append(label)
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
        wired = self.referenced_institutions(manager, matter.id)
        # Findings only. The candidate list is payees nobody has ruled on, which
        # is a work queue and not evidence — putting it in a document filed with
        # a court would assert of the City of Lewisville exactly what it asserts
        # of American Express.
        owed, _candidates = self.creditors(manager, matter.id)
        caption, warnings = caption_lines(matter, exhibit_name)

        rows = list(
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

        # Wires get their own block rather than their own document. They are the
        # same finding — money moving somewhere nobody produced — reached by a
        # different route, and a reader should not have to hold two exhibits
        # side by side to see the whole of it.
        if wired:
            rows.append(Row(
                cells=("Institutions named by wires, with no account produced",
                       "", "", "", "", "", "", "", "", ""),
                heading=True,
            ))
            for entry in wired:
                note = ""
                if entry["same_party_wires"]:
                    note = " — %d of %d sent and received by the same party" % (
                        entry["same_party_wires"], entry["wires"])
                rows.append(Row(cells=(
                    entry["institution"],
                    "",
                    "ABA %s" % entry["aba"] if entry["aba"] else "",
                    str(entry["wires"]),
                    str(entry["money_in"]),
                    str(entry["money_out"]),
                    str(entry["net"]),
                    entry["first_seen"].isoformat() if entry["first_seen"] else "",
                    entry["last_seen"].isoformat() if entry["last_seen"] else "",
                    ("; ".join(entry["seen_on"]) + note).strip("; "),
                ), depth=1))

        # Creditors are the third block. A card issuer paid every month is an
        # account somebody holds, and the payments prove both that it exists and
        # roughly what it costs to service — which is the number a court cares
        # about even before the statements arrive.
        if owed:
            rows.append(Row(
                cells=("Creditors paid, with no account produced",
                       "", "", "", "", "", "", "", "", ""),
                heading=True,
            ))
            for entry in owed:
                rows.append(Row(cells=(
                    entry["creditor_name"] or entry["payee"],
                    ", ".join(entry["last4"]),
                    entry["payee"],
                    str(entry["payments"]),
                    "",
                    str(entry["money_out"]),
                    str(-entry["money_out"]),
                    entry["first_seen"].isoformat() if entry["first_seen"] else "",
                    entry["last_seen"].isoformat() if entry["last_seen"] else "",
                    "; ".join(entry["seen_on"]),
                ), depth=1))

        total_in = sum((e["money_in"] for e in found), start=ZERO)             + sum((e["money_in"] for e in wired), start=ZERO)
        total_out = (sum((e["money_out"] for e in found), start=ZERO)
                     + sum((e["money_out"] for e in wired), start=ZERO)
                     + sum((e["money_out"] for e in owed), start=ZERO))
        accounts = FinancialAccountRepository(manager).get_by_matter(matter.id)

        footnotes = []
        if wired:
            footnotes.append(
                "A wire names the bank that sent it and its routing number, never the account "
                "the money left. Those rows therefore identify an institution rather than an "
                "account, and the routing number is the identity — it is checksummed, where a "
                "bank's name is spelled differently by different statements."
            )
        if any(e["same_party_wires"] for e in wired):
            footnotes.append(
                "Where a wire was sent and received by the same party, the money moved out of "
                "an account that party controls and into one produced here. The account it "
                "left has not been produced."
            )
        if owed:
            footnotes.append(
                "A creditor is listed because payments to it were filed under a category that "
                "names a debt, or because it is a card issuer, lender, or mortgage servicer of "
                "record. Those payments identify a creditor rather than an account number: "
                "except where a last four is shown, the account number itself is not printed on "
                "the statements produced."
            )
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
            rows=tuple(rows),
            selection=(
                ("Source", "Transfer descriptions on the statements produced in this matter"),
                ("Compared against", "The %d account%s currently on this matter"
                                     % (len(accounts), "" if len(accounts) == 1 else "s")),
                ("Matched on", "The last four digits of the account number"),
                ("Direction", "Taken from the sign of each amount, not from the words "
                              "\"to\" and \"from\" — a description often carries both"),
                ("Wires", "Read for the sending institution and its routing number"),
                ("Creditors", "Payees whose payments are filed under a category naming a debt, "
                              "or that are card issuers, lenders or mortgage servicers of record"),
                ("Accounts listed", str(len(found))),
                ("Institutions listed", str(len(wired))),
                ("Creditors listed", str(len(owed))),
            ),
            summary=(
                ("Accounts referenced but not produced", str(len(found))),
                ("Institutions referenced but not produced", str(len(wired))),
                ("Creditors paid, with no account produced", str(len(owed))),
                ("Total received from them", money(total_in)),
                ("Total sent to them", money(total_out)),
            ),
            footnotes=tuple(footnotes),
            warnings=warnings,
        )


account_discovery_service = AccountDiscoveryService()
