"""
app/services/statement_service.py - Ingest bank, brokerage, and card statements.

A statement PDF becomes an account (matched or created), a statement row, and
its transactions. Extraction is slow, so the router queues a job and the worker
calls in here — the same path matter intake takes.

What makes this different from the other extractors is that the output is
evidence. Three rules follow from that:

* **One sign convention.** An amount is signed by how it moves the balance the
  institution prints. A deposit and a card purchase are both positive; a
  withdrawal and a card payment both negative. Every account type then
  reconciles with ``beginning + sum(amount) == ending``.
* **Every statement checks itself,** and a statement that does not tie is
  recorded as unreconciled with the exact delta. Nothing is invented to close
  the gap — a synthetic balancing row is precisely what gets cross-examined.
* **Inference is flagged.** A year derived from the period, a city split off a
  merchant line: useful, but inferred, so each leaves a flag naming the field.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from db.models.financial import (
    AccountOwnership,
    AccountType,
    DateProvenance,
    FinancialAccount,
    FinancialAccountStatement,
    FinancialAccountTransaction,
    StatementReviewStatus,
)
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
)
from db_handler import DatabaseManager
from services import bates_service
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# Statements are dense and repetitive; a long one can run past this, so the
# extractor is told to report truncation rather than silently stop.
_MAX_STATEMENT_CHARS = 60_000

ZERO = Decimal("0.00")

# Placeholders extraction falls back to when it cannot read a name. Matched
# case-insensitively so a vision lookup is triggered instead of an account
# being filed under a non-name.
_UNKNOWN_INSTITUTION = {"unknown institution", "unknown", "n/a", "none", "not stated"}

_INSTITUTION_PROMPT = (
    "This is one page of a bank, brokerage, or credit card statement. Name the financial "
    "institution that HOLDS THE ACCOUNT.\n\n"
    "Look at the letterhead at the top of the page — the logo, and any mailing address "
    "beside it. That is where the account holder's bank appears.\n\n"
    "Do NOT answer with any of these, which appear on statements but are not the "
    "institution:\n"
    "  - The form printer or core-banking software vendor, in small type at the very "
    "bottom, usually beside a revision date or a form number (for example "
    "'CSI REV 3/12/18', '3380-STMT', 'FISERV', 'Jack Henry'). That is who printed the "
    "form, not who holds the money.\n"
    "  - A card network (Visa, Mastercard), a processor, or an insurer of deposits (FDIC, "
    "NCUA, SIPC).\n"
    "  - The customer's own name, or a merchant named in a transaction.\n\n"
    "If the only candidate you can find is in the small print at the foot of the page, or "
    "you are not confident, reply exactly: UNKNOWN. An unknown institution is corrected in "
    "seconds; a wrong one silently splits an account in two.\n\n"
    "Reply with the institution's name and nothing else — no punctuation, no explanation."
)

# Core-banking and statement-printing vendors whose imprints sit in the footer of
# a statement. They are the single most likely wrong answer to "which institution
# issued this?", because they are the only other company named on the page — and
# the cost is high: a wrong name is half the account dedup key, so it opens a
# second account for a real one. Rejected outright rather than trusted.
_FORM_VENDORS = {
    "csi", "computer services", "computer services inc", "fiserv", "jack henry",
    "jack henry & associates", "fis", "fis global", "symitar", "q2", "ncr",
    "digital insight", "finastra", "temenos", "corelation", "shazam", "cetera",
    "visa", "mastercard", "fdic", "ncua", "sipc",
}

# Words that mark a name as a financial institution. A vendor's initials can
# legitimately open one — "CSI Federal Credit Union" is a bank whatever else
# starts with CSI — so any of these overrides the vendor check.
_INSTITUTION_WORDS = (
    "bank", "banc", "credit union", "savings", "trust", "financial", "federal",
    "brokerage", "securities", "investment", "fcu", "n a", "national association",
)


_STATEMENT_SYSTEM = """\
You are extracting a financial account statement for use as trial evidence in a
Texas family law case. Accuracy matters more than completeness: never invent a
value to make totals work.

A single PDF may contain more than one statement (a combined bank package, or
several months scanned together). Return every statement you find.

WHAT COUNTS AS A SEPARATE STATEMENT — read this before splitting anything.
A statement is one account over one period. Two blocks belong to the SAME
statement when they carry the same account number and the same statement dates,
no matter how many pages or tables lie between them. Emit a second statement
ONLY for a genuinely different account number or a genuinely different period.

Statements repeat their header — account number, statement dates, "Page 2 of 3"
— on every page. A repeated header is NOT a new statement.

CHECKS ARE TRANSACTIONS. A table headed "CHECKS IN SERIAL NUMBER ORDER",
"CHECKS PAID", or similar lists real debits — on many statements it is the ONLY
place a check appears. Extract every priced row: amount NEGATIVE, check_number
as printed without any trailing asterisk, description "Check 2495" when nothing
else is given. The table is laid out in COLUMN GROUPS read left to right
(Date/Number/Amount, then Date/Number/Amount again on the same rows), which the
extracted text interleaves into one sequence — read it as repeating triples. A
row whose amount is not a printed figure ("-See above-", blank) is already
itemised in the debit list; skip it or you will double-count.

These blocks are NOT transaction registers. Never turn one into transactions,
and never let one become a statement of its own:
  - DAILY ENDING BALANCE, or any table of dates against running balances. These
    are end-of-day snapshots of one figure, not money moving.
  - ACCOUNT SUMMARY / SUMMARY OF ACCOUNTS — totals and counts, not entries.
  - INTEREST RATE SUMMARY, rate tables, yield tables.
  - Checkbook reconciliation worksheets, and any blank form the customer fills in.
  - Check images, disclosures, error-resolution notices, marketing inserts.
A date column next to an amount column does not make a table a register. Ask
whether each row is a payment, deposit, or charge that moved the balance. A
check did; a balance, a total, or a rate did not.

Return ONLY a valid JSON object of the form {"statements": [ ... ]}. Each
statement object has:

- account:
  - institution: the bank, brokerage, or card issuer that HOLDS the account, as
    printed in the letterhead at the top of the page. Statements also name the
    company that PRINTED the form, in small type at the foot of the page beside
    a revision date or form number — "CSI REV 3/12/18", "3380-STMT", "Fiserv",
    "Jack Henry". That is a software vendor, not the account's institution;
    never report it. Do not report a card network (Visa, Mastercard) or a
    deposit insurer (FDIC, NCUA, SIPC) either. If the only name you can find is
    in that footer, or the letterhead is a graphic you cannot read, return null.
    A null institution is corrected in seconds; a wrong one silently splits one
    account into two.
  - account_type: one of "checking" | "savings" | "brokerage" | "credit_card" | \
"retirement" | "hsa" | "loan" | "other"
  - account_number_last4: string of the last four digits, or null if redacted
  - account_number_masked: the masked form exactly as printed, e.g. "ending in 4357"
  - name_on_account: string or null
- period:
  - start_date: "YYYY-MM-DD"
  - end_date: "YYYY-MM-DD"
- balances:
  - beginning_balance: number or null
  - ending_balance: number or null
  - printed_totals: object of any totals the statement itself prints, e.g.
    {"deposits_credits": 202100.41, "checks_debits": -195600.04, "fees": 0.00}
  - printed_counts: object of any transaction COUNTS the statement prints. Most
    statements say something like "24 Deposits/Credits" and "262 Checks/Debits"
    in the account summary. Report them as
    {"deposits_credits": 24, "checks_debits": 262}. Omit a key you cannot find;
    never estimate one from the rows you extracted.
- transactions: array, in printed order. Each has:
  - line_no: 1-based integer
  - transaction_date: "YYYY-MM-DD" or null
  - posted_date: "YYYY-MM-DD" or null
  - date_provenance: "printed" if the full date was printed, "derived" if you
    inferred the year from the statement period, "unknown" otherwise
  - description: the printed description as one line
  - description_lines: array of the raw printed lines for this entry
  - counterparty: the merchant or payee with card noise removed, or null.
    "FSP*PILOT POINT FEED S PILOT POINT TX" has counterparty "Pilot Point Feed".
  - location: trailing "City ST" if present, else null
  - amount: SIGNED number — see the sign rule below
  - running_balance: number or null, only if the statement prints one per line
  - physical_page_number: integer. The document text is broken up by lines of
    the form "<<<PAGE 7>>>". Report the number from the marker that most
    recently preceded this transaction. Null only if no marker precedes it.
  - check_number: string or null. Set it whenever a check number is printed for
    the entry, wherever it appears — "ARC CHECK # 2487" in the debit list and a
    row of the checks table are both checks. As printed, without any trailing
    asterisk.
  - bates_number: string or null. Productions are stamped with a Bates number in
    a corner of every page, e.g. "KF-000142", "SMITH 0087". If the page this
    line appears on carries one, copy it EXACTLY as printed, prefix and leading
    zeros included. If the page is unstamped, return null — never construct,
    guess, or extrapolate a Bates number from a neighbouring page.
  - flags: array of {code, severity, field_path, note} objects

SIGN RULE — this is the most important field. Sign each amount by how it moves
the balance the institution prints:
  * Deposit, credit, interest earned, dividend  -> POSITIVE
  * Withdrawal, debit, check, ATM, fee          -> NEGATIVE
  * Credit card purchase, cash advance, fee     -> POSITIVE (raises the balance owed)
  * Credit card payment or refund               -> NEGATIVE (lowers the balance owed)
So for EVERY account type: beginning_balance + sum(amounts) = ending_balance.
If your amounts do not satisfy that, re-check the signs before answering. Do NOT
add, drop, or alter a transaction to force it to balance — report what is printed.

FLAGS — record every inference you make. Use severity "info" for a routine
derivation and "warn" for something a human should look at. Codes:
  - YEAR_INFERRED       the line printed MM/DD and you took the year from the period
  - LOCATION_INFERRED   you split a city/state off the description
  - SIGN_ASSUMED        the direction was not explicit and you inferred it
  - AMOUNT_UNCLEAR      the figure is smudged, cut off, or ambiguous
  - DESCRIPTION_TRUNCATED the printed text ran off the page
Set field_path to the field the flag is about, e.g. "transaction_date".

If the document is not an account statement at all, return {"statements": []}.

Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


_METADATA_SYSTEM = """\
You are indexing a PDF of financial account statements. Identify each statement
and where it sits in the document. Do NOT extract transactions — another pass
does that.

The text is broken up by lines of the form "<<<PAGE 7>>>".

A statement is one account over one period. Two blocks belong to the SAME
statement when they carry the same account number and the same statement dates,
however many pages lie between them. Statements repeat their header on every
page; a repeated header is not a new statement.

Return ONLY a valid JSON object of the form {"statements": [ ... ]}. Each has:

- first_page / last_page: integers, the page range this statement occupies,
  from its first header to the last page belonging to it. Include its balance
  tables, check images, and disclosure pages — everything before the next
  statement starts.
- account:
  - institution: the bank, brokerage, or card issuer that HOLDS the account, as
    printed in the letterhead at the top of the page. Statements also name the
    company that PRINTED the form, in small type at the foot of the page beside
    a revision date or form number ("CSI REV 3/12/18", "3380-STMT", "Fiserv").
    That is a software vendor; never report it. If the only name you can find is
    in that footer, or the letterhead is a graphic you cannot read, return null.
  - account_type: one of "checking" | "savings" | "brokerage" | "credit_card" | \
"retirement" | "hsa" | "loan" | "other"
  - account_number_last4: string of the last four digits, or null if redacted
  - account_number_masked: the masked form exactly as printed
  - name_on_account: string or null
- period:
  - start_date / end_date: "YYYY-MM-DD"
- balances:
  - beginning_balance / ending_balance: number or null
  - printed_totals: totals the statement prints, e.g.
    {"deposits_credits": 202100.41, "checks_debits": -195600.04}
- printed_counts: counts the statement prints, e.g.
  {"deposits_credits": 24, "checks_debits": 262}. Omit a key you cannot find;
  never estimate one.

If the document holds no account statement, return {"statements": []}.
Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


_TRANSACTIONS_SYSTEM = """\
You are extracting transactions from PART of one account statement, for use as
trial evidence. Accuracy matters more than completeness: never invent a value.

You are given a slice of pages from a statement whose account and period are
stated below. Extract EVERY transaction ANCHORED on the PRIMARY PAGES named
there, and nothing else. Do not summarise, do not stop early, and do not skip
repetitive rows — a run of forty near-identical card purchases must produce
forty entries.

WHICH PAGE AN ENTRY BELONGS TO. A transaction is **anchored** on the page where
its AMOUNT is printed. An entry runs to several lines — merchant, city, card,
reference, timestamp — and those lines routinely continue past a page break, so
the page an entry starts on is not always the page it is anchored on.

  * Report a transaction whose amount is on a primary page, and give it the
    whole of its description, including any lines that run on to the page after.
  * Do NOT report a transaction whose amount is printed in the CONTINUATION
    CONTEXT at the end of this slice. That page is shown only so you can finish
    an entry anchored on the last primary page. The next slice reports it.
  * If the FIRST lines of the first primary page are continuation lines with no
    amount of their own, skip them. They finish an entry anchored on an earlier
    page, and the slice that owned that page already reported it in full.

Following those three rules exactly is what keeps each entry reported once, and
keeps a description from being cut in half at a page break.

Return ONLY a valid JSON object of the form {"transactions": [ ... ]}, in
printed order. Each transaction has:
  - transaction_date: "YYYY-MM-DD" or null
  - posted_date: "YYYY-MM-DD" or null
  - date_provenance: "printed" if the full date was printed, "derived" if you
    took the year from the statement period, "unknown" otherwise
  - description: the printed description as one line
  - description_lines: the raw printed lines for this entry
  - counterparty: the merchant or payee with card noise removed, or null
  - location: trailing "City ST" if present, else null
  - amount: SIGNED number — see the sign rule below
  - running_balance: number or null, only if printed per line
  - check_number: string or null. Set it whenever a check number is printed for
    the entry, wherever it appears — "ARC CHECK # 2487" in the debit list and a
    row of the checks table are both checks. As printed, without any trailing
    asterisk.
  - physical_page_number: the number from the "<<<PAGE n>>>" marker most
    recently preceding this transaction
  - flags: array of {code, severity, field_path, note} objects

SIGN RULE. Sign each amount by how it moves the balance the institution prints:
  * Deposit, credit, interest earned, dividend  -> POSITIVE
  * Withdrawal, debit, check, ATM, fee          -> NEGATIVE
  * Credit card purchase, cash advance, fee     -> POSITIVE (raises what is owed)
  * Credit card payment or refund               -> NEGATIVE (lowers what is owed)

CHECKS ARE TRANSACTIONS. A table headed "CHECKS IN SERIAL NUMBER ORDER",
"CHECKS PAID", or similar lists real debits — on many statements it is the ONLY
place a check appears, and skipping it loses money that left the account.
Extract every priced row as a transaction:
  - amount NEGATIVE (a check reduces the balance)
  - check_number set to the number as printed, without any trailing asterisk
  - description "Check 2495" when nothing else is printed for it

Two things about that table:
  * It is laid out in COLUMN GROUPS read left to right — typically
    Date/Number/Amount, then Date/Number/Amount again on the same rows. The
    extracted text interleaves them into one long sequence, so read it as
    repeating triples rather than as one column running down the page.
  * A row whose amount is not a printed figure — "-See above-", "-See
    reverse-", blank — is NOT a separate transaction. The statement is telling
    you it is already itemised in the debit list. Skip those rows; extracting
    them would double-count. An asterisk beside a number means the numbers
    before it are missing from the sequence, not that the row is special.

These blocks are NOT transactions. Never turn one into an entry:
  - DAILY ENDING BALANCE, or any table of dates against running balances
  - ACCOUNT SUMMARY / SUMMARY OF ACCOUNTS, INTEREST RATE SUMMARY, OVERDRAFT SUMMARY
  - Checkbook reconciliation worksheets, check images, disclosures, notices
A date column beside an amount column does not make a table a register. Ask
whether the row moved the balance. A check did; a running balance did not.

FLAGS — record every inference. severity "info" for a routine derivation, "warn"
for something a human should look at. Codes: YEAR_INFERRED, LOCATION_INFERRED,
SIGN_ASSUMED, AMOUNT_UNCLEAR, DESCRIPTION_TRUNCATED.

If these pages carry no transactions at all, return {"transactions": []}.
Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


# Above this many pages a document is read in passes rather than in one call.
# Chosen from where the single call actually fails: a 27-page statement with 286
# entries came back with 38 of them, well-formed and 87% short, having used a
# seventh of the output budget it was given. It did not run out of room — it
# stopped. Below this, one call is both reliable and cheaper.
_MAX_SINGLE_PASS_PAGES = 6

# Pages per transaction call. A dense page runs to about fifteen entries, so
# four keeps each answer small enough that finishing is the easy option.
_PAGES_PER_CHUNK = 4

# Pages of the NEXT chunk shown at the end of each slice, read-only.
#
# An entry's lines run past a page break constantly — the merchant is on one
# page and the city, card, and timestamp on the next — so a slice that stopped
# dead at its last page would cut those descriptions in half, and neither slice
# would hold the whole entry. One page of lookahead is enough: a single entry
# never spans more than a break.
_PAGE_LOOKAHEAD = 1


_DATE_AUDIT_SYSTEM = """\
You are re-reading ONLY the dates on part of an account statement. Another pass
already extracted these transactions and got the amounts right; its reading of
the date column is in doubt.

You are given the pages, and the transactions extracted from them as a numbered
list IN THE ORDER THEY ARE PRINTED. Report the date printed for each one.

MATCH BY POSITION, NOT BY DESCRIPTION. The list is in printed order, so line 12
is the twelfth transaction on these pages. Descriptions and amounts repeat
constantly on a statement — three "Transfer from DDA (Sweep)" of $5,000.00 on
the same day is ordinary — so they are there to confirm you are on the right
line, never to identify it. If a description does not seem to match what you
read, trust the position and say so in the note.

Return ONLY a valid JSON object of the form {"dates": [ ... ]}, one entry per
numbered line, using the same numbers, omitting none:
  - index: the line number you were given
  - transaction_date: "YYYY-MM-DD", or null if the statement prints no date
    for that entry
  - posted_date: "YYYY-MM-DD" or null, only when printed separately
  - date_provenance: "printed" if the full date was printed, "derived" if you
    took the year from the statement period, "unknown" otherwise

Most of these statements print only month and day, so the year comes from the
statement period given below. A period that spans a year boundary is the case
to be careful with: December belongs to the earlier year and January to the
later one.

Report what is printed. Do not carry a date down from the row above to fill a
gap, and do not infer one from the rows around it — a missing date is a fact
about the document, and null is the correct answer for it.

Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


# How far outside its period a statement may legitimately date a line.
#
# Not zero: these statements post an interest deposit after the period closes —
# 6/02 on a statement running 5/01 to 5/31 — so a hard bound would flag ordinary
# entries. Ten days tolerates that while still catching a date wrong by a month.
_PERIOD_MARGIN_DAYS = 10


def _strip_fences(text: str) -> str:
    """Strip ```json fences the model adds despite being told not to."""
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", stripped).strip()


def _money(value: Any) -> Optional[Decimal]:
    """
    Convert an extracted figure to exact cents.

    Goes through ``str`` on purpose: ``Decimal(1203.02)`` from a float carries
    the binary rounding error into the record, and these numbers end up in
    exhibits.

    :return: The value quantized to cents, or None when it is not a number.
    :rtype: Optional[Decimal]
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_form_vendor(name: str) -> bool:
    """
    True when a name is the form printer rather than the account's institution.

    The leading words are matched as well as the whole string, because the
    imprint is usually read back with its revision code attached — "CSI REV
    3/12/18" is the same wrong answer as "CSI".

    But a vendor's initials can also begin a real institution's name, and
    "CSI Federal Credit Union" is a bank however it starts. So anything that
    names itself as a financial institution is taken at its word, unless the
    whole string is nothing but the vendor.
    """
    cleaned = re.sub(r"[^a-z0-9& ]", " ", name.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in _FORM_VENDORS:
        return True
    if any(word in cleaned for word in _INSTITUTION_WORDS):
        return False
    words = cleaned.split()
    return any(" ".join(words[:n]) in _FORM_VENDORS for n in range(1, min(4, len(words)) + 1))


def _date_audit_reason(
    lines: list[dict[str, Any]],
    period_start: Optional[date],
    period_end: Optional[date],
) -> Optional[str]:
    """
    Whether a batch's dates should be read again, and why.

    A missing date is not just a missing value — it is evidence that the call
    that produced this batch stopped attending to the date column. Ground truth
    on a 27-page statement bore that out: wrong dates never appeared alone, only
    ever in batches that also had nulls, and in the worst batch every one of its
    four pages came back undated. The amounts in those same batches were
    perfect. So one null condemns every date in the batch, not just itself.

    The other trigger catches a batch with wrong dates and no nulls, which the
    first cannot see: a date outside the statement period.

    Deliberately sensitive rather than precise. A false trigger costs one small
    call; a miss puts a wrong date in an exhibit.

    There is no ordering check, though it was considered. These statements list
    credits and then debits, each section starting again at the beginning of the
    period, so dates legitimately jump backwards mid-batch — 12/26 to 11/28 in
    the first batch of every statement of this shape. It would fire on almost
    every document and catch nothing known.

    :return: The reason to re-read, or None when the dates look sound.
    :rtype: Optional[str]
    """
    if not lines:
        return None

    missing = sum(1 for line in lines if not _parse_date(line.get("transaction_date")))
    if missing:
        return "%d of %d line(s) came back with no date" % (missing, len(lines))

    if period_start and period_end:
        low = period_start - timedelta(days=_PERIOD_MARGIN_DAYS)
        high = period_end + timedelta(days=_PERIOD_MARGIN_DAYS)
        outside = [
            parsed for parsed in (_parse_date(line.get("transaction_date")) for line in lines)
            if parsed and not (low <= parsed <= high)
        ]
        if outside:
            return "%d line(s) dated outside %s to %s" % (len(outside), period_start, period_end)
    return None


def _apply_date_audit(
    lines: list[dict[str, Any]],
    answers: list[dict[str, Any]],
) -> int:
    """
    Take the re-read dates, recording every one that moved.

    The second read wins. It is the narrower question asked of the same pages,
    and the first pass has already shown it was distracted — that is why the
    audit ran at all.

    A change is written onto the line as a flag rather than applied silently.
    Dates do not enter reconciliation, so nothing downstream would ever reveal
    that a date had been revised; without the flag the revision would be
    invisible in a record that ends up in front of a court.

    :param answers: ``[{"index": 1, "transaction_date": ...}, ...]``, matched to
        ``lines`` by position — descriptions repeat on these statements and
        cannot identify a row.
    :return: How many dates changed.
    :rtype: int
    """
    by_index: dict[int, dict[str, Any]] = {}
    for answer in answers:
        try:
            by_index[int(answer.get("index"))] = answer
        except (TypeError, ValueError):
            continue

    changed = 0
    for position, line in enumerate(lines, start=1):
        answer = by_index.get(position)
        if answer is None:
            continue

        before = _parse_date(line.get("transaction_date"))
        after = _parse_date(answer.get("transaction_date"))
        if before == after:
            continue

        line["transaction_date"] = after.isoformat() if after else None
        line["posted_date"] = answer.get("posted_date") or line.get("posted_date")
        if answer.get("date_provenance"):
            line["date_provenance"] = answer["date_provenance"]
        line.setdefault("flags", []).append({
            "code": "DATE_REREAD",
            "severity": "info",
            "field_path": "transaction_date",
            "note": "The date column was re-read after this batch showed missing dates. "
                    "This line changed from %s to %s."
                    % (before.isoformat() if before else "(none)",
                       after.isoformat() if after else "(none)"),
            "from": before.isoformat() if before else None,
            "to": after.isoformat() if after else None,
        })
        changed += 1
    return changed


def _check_number(value: Any) -> Optional[str]:
    """
    A check number as printed, minus the asterisk.

    The asterisk in "2493*" is the statement's own footnote marking a break in
    the serial sequence — "(*) Denotes missing check numbers" — not part of the
    number. Keeping it would make the number fail to match the check itself in
    a discovery request.
    """
    cleaned = str(value or "").strip().rstrip("*").strip()
    return cleaned or None


def _dedupe_checks(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Drop a checks-table row for a check already itemised in the debit list.

    A check can be printed twice: once in the running debits and again in the
    summary table at the back. This statement marks the repeats itself — the
    amount column reads "-See above-" — and the prompt skips those, but that
    convention is one bank's. Banks that repeat the check *with* its amount
    would double-count it, and a doubled debit is worse than a missing one: it
    reconciles to a wrong number rather than an obviously wrong one.

    The debit-list entry wins, because it carries the payee and the fuller
    description; the summary row usually has only a number.

    :return: ``(lines_to_keep, notes)`` — one note per row removed.
    :rtype: tuple[list[dict[str, Any]], list[str]]
    """
    kept: list[dict[str, Any]] = []
    notes: list[str] = []

    for index, line in enumerate(lines):
        number = _check_number(line.get("check_number"))
        if not number:
            kept.append(line)
            continue

        duplicate_of = None
        for other_index, other in enumerate(lines):
            if other_index == index:
                continue
            description = str(other.get("description") or "")
            same_number = _check_number(other.get("check_number")) == number
            # Requiring the word "check" near the digits keeps a merchant's
            # reference number from being read as a check number: "REF#
            # 334900022500" contains 2500 and has nothing to do with a cheque.
            named_in_text = bool(re.search(
                r"(?:check|chk)\W{0,6}%s\b" % re.escape(number), description, re.I,
            ))
            if not (same_number or named_in_text):
                continue
            richer = len(description) > len(str(line.get("description") or ""))
            if richer or (same_number and other_index < index and not richer):
                duplicate_of = description.strip() or "an earlier entry"
                break

        if duplicate_of is None:
            kept.append(line)
        else:
            notes.append("check %s (already listed as \"%s\")" % (number, duplicate_of[:60]))
    return kept, notes


def _provenance(passes: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Who actually did the work, for the statement's ``extraction`` record.

    The profile name says what was *asked for*. It does not say which model
    answered, and on a long statement read in passes it cannot: the chain falls
    through independently on every call, so one document can be part Claude and
    part OpenAI. When a figure from this statement is put in front of a court,
    "which tool produced it" has a real answer, and this is where it lives.

    :param passes: One record per LLM call, in the order they were made.
    :return: The per-pass detail, plus a one-glance summary of the models used
        and whether the preferred one ever had to be passed over.
    :rtype: dict[str, Any]
    """
    labels = []
    for entry in passes:
        label = "%s/%s" % (entry["vendor"], entry["model"])
        if label not in labels:
            labels.append(label)
    return {
        "passes": passes,
        "models_used": labels,
        # True when any pass fell through to a later candidate. A statement
        # read by the second choice is not wrong, but it is worth being able
        # to find later — that is how a vendor regression gets noticed.
        "failed_over": any(entry["attempts"] > 1 for entry in passes),
    }


def _unreadable_pages(raw_text: str) -> list[int]:
    """
    Pages ``pdf_service`` could read no text from at all.

    It leaves a marker in place of the page's text when both the text layer and
    the vision fallback fail, so the loss travels in ``raw_text`` and is still
    visible in the stored record long afterwards.

    :return: Page numbers, in order. Empty when every page was read, and empty
        when the text carries no page markers to attribute a loss to.
    :rtype: list[int]
    """
    if "[[PAGE COULD NOT BE READ" not in raw_text:
        return []
    return sorted(
        page for page, text in bates_service.split_pages(raw_text).items()
        if "[[PAGE COULD NOT BE READ" in text
    )


def _bates_value(stamp: str) -> int:
    """The numeric part of a Bates stamp, for arithmetic on the run."""
    return int("".join(c for c in stamp if c.isdigit()))


def _page_number(value: Any) -> Optional[int]:
    """A page number, or None. Zero and negatives are model noise, not pages."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _flag(code: str, severity: str, note: str, field_path: Optional[str] = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "field_path": field_path, "note": note}


# Fields a person may correct after ingestion, mapped to how they read in the
# audit sentence the change leaves behind. Everything else on a transaction is
# either structural (which statement it belongs to) or derived, and correcting
# it would mean re-ingesting rather than editing.
_CORRECTABLE = {
    "description": "description",
    "transaction_date": "transaction date",
    "posted_date": "posted date",
    "counterparty": "counterparty",
    "location": "location",
    "amount": "amount",
    "running_balance": "running balance",
    "bates_number": "Bates number",
    "check_number": "check number",
    "physical_page_number": "page number",
}


def _coerce_field(field: str, value: Any) -> Any:
    """Bring an incoming value to the type the column holds."""
    if value is None or value == "":
        return None
    if field in ("amount", "running_balance"):
        return _money(value)
    if field in ("transaction_date", "posted_date"):
        return _parse_date(value)
    if field == "physical_page_number":
        return _page_number(value)
    return str(value).strip() or None


def _same_value(before: Any, after: Any) -> bool:
    """True when a correction would change nothing."""
    if isinstance(before, Decimal) and isinstance(after, Decimal):
        return before == after
    return before == after


def _display(field: str, value: Any) -> str:
    """Render a value the way it should read inside the audit sentence."""
    if value is None or value == "":
        return "(blank)"
    if field in ("amount", "running_balance") and isinstance(value, Decimal):
        # -$6.49, not $-6.49 — the sign belongs outside the symbol.
        return ("-$%s" % -value) if value < 0 else ("$%s" % value)
    if isinstance(value, date):
        return value.isoformat()
    return '"%s"' % value


def _serialize(value: Any) -> Any:
    """JSON-safe form of a before/after value, for the structured half of the flag."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value

# How a statement labels its own credit and debit totals varies by bank, so the
# keys are matched by what they contain rather than by an exact name.
_CREDIT_WORDS = ("credit", "deposit")
_DEBIT_WORDS = ("debit", "check", "withdrawal", "payment", "purchase")

# A total is only worth comparing when it is more than rounding apart. Statements
# print to the cent, so anything above this is a real difference.
_TOTAL_TOLERANCE = Decimal("0.01")


def _printed_side(printed: dict[str, Any], words: tuple[str, ...],
                  against: tuple[str, ...]) -> Optional[Decimal]:
    """
    Pull one side's printed figure out of whatever the statement called it.

    :param words: Substrings that mark the side wanted.
    :param against: Substrings that mark the other side, checked first — a key
        like "checks_debits" contains both "check" and "debit", and
        "deposits_credits" contains "deposit"; whichever list matches more
        specifically wins, so the other side is excluded explicitly.
    """
    for key, value in (printed or {}).items():
        name = str(key).lower()
        if any(w in name for w in against):
            continue
        if any(w in name for w in words):
            amount = _money(value)
            if amount is not None:
                return abs(amount)
    return None


def _completeness_findings(
    lines: list[dict[str, Any]],
    printed_totals: dict[str, Any],
    printed_counts: dict[str, Any],
) -> list[str]:
    """
    Compare what was extracted against what the statement says about itself.

    A statement states its own answer in the account summary — "24
    Deposits/Credits 202,100.41", "262 Checks/Debits 195,600.04" — and until now
    that was recorded and never read. Reconciliation alone does catch a short
    extraction, but only as one large unexplained delta; this says which side
    fell short and by how much, which is the difference between "something is
    wrong" and "the debits stopped after fifteen of two hundred sixty-two".

    Reports a side only when the extraction falls SHORT of what the statement
    prints — never when it exceeds it. A statement may break its debits into
    several named buckets and this compares against one of them, so having more
    lines or a larger total than a single bucket is ordinary. Having fewer is
    not.

    :return: One sentence per side that comes up short. Empty when the
        extraction covers what is printed, and empty when the statement printed
        nothing to check against.
    :rtype: list[str]
    """
    amounts = [_money(line.get("amount")) or ZERO for line in lines]
    got_credits = [a for a in amounts if a > 0]
    got_debits = [a for a in amounts if a < 0]

    findings: list[str] = []
    for label, got, total_words, count_words, against in (
        ("credit", got_credits, _CREDIT_WORDS, _CREDIT_WORDS, _DEBIT_WORDS),
        ("debit", got_debits, _DEBIT_WORDS, _DEBIT_WORDS, _CREDIT_WORDS),
    ):
        printed_total = _printed_side(printed_totals, total_words, against)
        printed_count = _printed_side(printed_counts, count_words, against)
        got_total = abs(sum(got, ZERO))

        # Only a SHORTFALL is a finding. Exceeding a printed figure is ordinary:
        # a statement may split its debits across several named buckets and this
        # compares against one of them. Bank of Texas prints "2 Checks &
        # Withdrawals 159.11" and "Service Fees 2.00" as separate lines of its
        # summary, so extracting all three debits — correctly — reads as two
        # too many and two dollars over.
        #
        # The reverse has no innocent explanation: nothing makes a printed
        # bucket larger than the lines that make it up except lines we failed to
        # read. And over-counting, the other way this could go wrong, is caught
        # by reconciliation rather than here — a doubled debit breaks the
        # balance, where a bucketing artifact does not.
        if printed_count is not None and len(got) < int(printed_count):
            findings.append(
                "%d %s line(s) extracted, but the statement prints %d"
                % (len(got), label, int(printed_count))
            )
        if printed_total is not None and (printed_total - got_total) > _TOTAL_TOLERANCE:
            findings.append(
                "%s lines total %s, but the statement prints %s (short by %s)"
                % (label.capitalize(), got_total, printed_total, printed_total - got_total)
            )
    return findings

def _conflict(code: str, blocking: bool, detail: str) -> dict[str, Any]:
    return {"code": code, "blocking": blocking, "detail": detail}


def _account_label(account: Any) -> str:
    tail = " ....%s" % account.account_number_last4 if account.account_number_last4 else ""
    return "%s%s" % (account.institution, tail)


class StatementService:
    """Extracts statements from a PDF and commits them to a matter."""

    # ── Extraction ────────────────────────────────────────────────────────

    def extract(self, raw_text: str) -> dict[str, Any]:
        """
        Pull every statement in a document.

        :param raw_text: Full extracted text of the PDF.
        :type raw_text: str
        :return: ``{"statements": [...]}`` as returned by the model.
        :rtype: dict[str, Any]
        :raises ValueError: If the response is not valid JSON.
        """
        # A long document is read in passes. One call reliably handles a short
        # statement and reliably gives up on a long one, so the length decides.
        pages = bates_service.split_pages(raw_text)
        if len(pages) > _MAX_SINGLE_PASS_PAGES:
            return self._extract_chunked(raw_text, pages)

        body = raw_text[:_MAX_STATEMENT_CHARS]
        if len(raw_text) > _MAX_STATEMENT_CHARS:
            body += "\n\n[DOCUMENT TRUNCATED — report only the statements above]"

        passes: list[dict[str, Any]] = []
        parsed = self._call_json(
            _STATEMENT_SYSTEM, body, "statements", "Could not read the statement",
            passes=passes, label="whole document",
        )
        parsed["extraction"] = _provenance(passes)
        return parsed


    def _extract_chunked(self, raw_text: str, pages: dict[int, str]) -> dict[str, Any]:
        """
        Read a long document in passes: index it, then walk it.

        One call cannot do a long statement. A 27-page statement of 286 entries
        came back with 38 — every credit, then fifteen debits, then a closing
        brace. The JSON was valid and the output used a seventh of its token
        budget, so nothing failed and nothing was truncated: the model simply
        decided it was done. No ceiling can be raised to fix that, because no
        ceiling was reached.

        What does fix it is making each answer small enough that finishing is
        the easy option. One pass indexes the document — accounts, periods,
        balances, page ranges — and then each statement's pages are walked a few
        at a time, asking only for transactions.

        :param raw_text: Page-marked text of the whole document.
        :param pages: ``{page_number: text}`` from the same.
        :return: ``{"statements": [...]}`` in the shape a single call produces.
        :rtype: dict[str, Any]
        :raises ValueError: If the index pass returns nothing usable.
        """
        passes: list[dict[str, Any]] = []
        index = self._call_json(
            _METADATA_SYSTEM, raw_text[:_MAX_STATEMENT_CHARS], "statements",
            "Could not index the document", passes=passes, label="index",
        )
        statements = index.get("statements") or []
        LOGGER.info(
            "statement_service.extract: %d page(s), %d statement(s) indexed; reading in passes",
            len(pages), len(statements),
        )

        last_page = max(pages)
        for statement in statements:
            first = _page_number(statement.pop("first_page", None)) or 1
            last = _page_number(statement.pop("last_page", None)) or last_page
            first, last = max(1, min(first, last_page)), max(1, min(last, last_page))

            context = self._statement_context(statement)
            period = statement.get("period") or {}
            period_start = _parse_date(period.get("start_date"))
            period_end = _parse_date(period.get("end_date"))
            collected: list[dict[str, Any]] = []
            for start in range(first, last + 1, _PAGES_PER_CHUNK):
                stop = min(start + _PAGES_PER_CHUNK - 1, last)
                body = self._slice_body(pages, start, stop, last)
                if not body.strip():
                    continue
                found = self._call_json(
                    _TRANSACTIONS_SYSTEM,
                    "%s\n\nPRIMARY PAGES: %d to %d.\n\n%s" % (context, start, stop, body),
                    "transactions",
                    "Could not read pages %d-%d" % (start, stop),
                    passes=passes, label="pages %d-%d" % (start, stop),
                ).get("transactions") or []
                # A slice can only report what it was given; anything anchored
                # in its lookahead belongs to the next one. Enforced here as
                # well as in the prompt, because a duplicated transaction is a
                # duplicated line in an exhibit.
                found = [
                    line for line in found
                    if (_page_number(line.get("physical_page_number")) or start) <= stop
                ]
                # One missing date condemns every date in this batch, not just
                # itself — see _date_audit_reason. The amounts are left alone:
                # they were right, and re-running the whole batch to fix one
                # column would re-roll the dice on data that already ties.
                reason = _date_audit_reason(found, period_start, period_end)
                if reason:
                    LOGGER.info(
                        "statement_service.extract: pages %d-%d — %s; re-reading the date column",
                        start, stop, reason,
                    )
                    self._audit_dates(found, pages, start, stop, last, context, passes)

                LOGGER.info(
                    "statement_service.extract: pages %d-%d yielded %d transaction(s)",
                    start, stop, len(found),
                )
                collected.extend(found)

            # Numbered here rather than by the model: it only ever saw a slice,
            # so every chunk would otherwise start again at one.
            for index_, line in enumerate(collected, start=1):
                line["line_no"] = index_
            statement["transactions"] = collected

        return {"statements": statements, "extraction": _provenance(passes)}

    def _audit_dates(
        self,
        lines: list[dict[str, Any]],
        pages: dict[int, str],
        start: int,
        stop: int,
        last: int,
        context: str,
        passes: list[dict[str, Any]],
    ) -> None:
        """
        Read one batch's date column again, and take the second answer.

        Modifies ``lines`` in place. Exactly one attempt: if dates are still
        missing afterwards the statement carries a flag and a person dates them
        from the source page, which is a few minutes' work with the page and
        Bates number already on the line. Looping would only spend calls on a
        page that genuinely prints no date.

        A failure here is swallowed on purpose. The batch's amounts are sound
        and already collected; losing the whole statement because a repair pass
        could not run would be a worse outcome than the doubtful dates it was
        meant to fix.
        """
        listing = "\n".join(
            "%d. %s  %s" % (
                position,
                (line.get("description") or "(no description)")[:80],
                line.get("amount"),
            )
            for position, line in enumerate(lines, start=1)
        )
        body = "%s\n\nTRANSACTIONS ON THESE PAGES, IN PRINTED ORDER:\n%s\n\n%s" % (
            context, listing, self._slice_body(pages, start, stop, last),
        )
        try:
            answers = self._call_json(
                _DATE_AUDIT_SYSTEM, body, "dates",
                "Could not re-read dates for pages %d-%d" % (start, stop),
                passes=passes, label="dates, pages %d-%d" % (start, stop),
            ).get("dates") or []
        except ValueError as e:
            LOGGER.warning(
                "statement_service: date re-read failed for pages %d-%d (%s); keeping the first reading",
                start, stop, str(e),
            )
            return

        changed = _apply_date_audit(lines, answers)
        LOGGER.info(
            "statement_service: pages %d-%d — re-read %d date(s), %d changed",
            start, stop, len(answers), changed,
        )

    @staticmethod
    def _slice_body(pages: dict[int, str], start: int, stop: int, last: int) -> str:
        """
        The pages for one transaction pass, plus a page of lookahead.

        The lookahead is what stops a description being cut in half. These
        entries run to several lines — merchant, city, card, reference,
        timestamp — and a page break falls in the middle of one constantly. A
        slice that ended at its last page would lose the tail, and the next
        slice would drop it too, since it belongs to an entry that slice does
        not own.

        The extra page is fenced off in the text so it can be read but not
        reported from.
        """
        body = "\n\n".join(
            "<<<PAGE %d>>>\n%s" % (n, pages[n])
            for n in range(start, stop + 1) if n in pages
        )
        lookahead = [n for n in range(stop + 1, min(stop + _PAGE_LOOKAHEAD, last) + 1) if n in pages]
        if lookahead:
            body += (
                "\n\n=== CONTINUATION CONTEXT ===\n"
                "The page(s) below are shown ONLY so you can finish an entry whose amount is "
                "printed on page %d. Do not report any transaction anchored here.\n\n%s"
                % (stop, "\n\n".join("<<<PAGE %d>>>\n%s" % (n, pages[n]) for n in lookahead))
            )
        return body

    @staticmethod
    def _statement_context(statement: dict[str, Any]) -> str:
        """
        The line of context a transaction pass needs.

        Chiefly the period: a statement prints "12/04" and the year comes from
        the period it covers, so a slice read without it cannot date its own
        rows — and a statement spanning a year boundary would date half of them
        wrongly.
        """
        account = statement.get("account") or {}
        period = statement.get("period") or {}
        return (
            "STATEMENT CONTEXT — these pages belong to:\n"
            "  Institution: %s\n"
            "  Account ending: %s\n"
            "  Account type: %s\n"
            "  Statement period: %s to %s\n"
            "Use the period to supply the year on any line that prints only a "
            "month and day, and flag those YEAR_INFERRED."
            % (
                account.get("institution") or "unknown",
                account.get("account_number_last4") or "unknown",
                account.get("account_type") or "unknown",
                period.get("start_date") or "unknown",
                period.get("end_date") or "unknown",
            )
        )

    def _call_json(
        self,
        system: str,
        body: str,
        expect: str,
        failure: str,
        passes: Optional[list[dict[str, Any]]] = None,
        label: str = "",
    ) -> dict[str, Any]:
        """
        One LLM call that must come back as a JSON object holding ``expect``.

        :param passes: Collected provenance, appended to in place. Which vendor
            and model answered is recorded per call, not per document: a long
            statement is read in several passes and the chain can fall through
            to a different vendor on any of them, so one name for the whole
            statement would be a guess.
        :param label: What this pass was for, e.g. ``"pages 5-8"``.
        :raises ValueError: If the response is not valid JSON, or lacks the key.
        """
        result = llm_service.complete_detailed(system, body, profile="extract_account_statement")
        if passes is not None:
            passes.append({
                "pass": label or expect,
                "vendor": result.vendor,
                "model": result.model,
                # Above 1 means the preferred model did not answer. Worth
                # keeping: it is the difference between "Claude read this" and
                # "Claude was asked and something else ended up reading it".
                "attempts": result.attempts,
            })
        try:
            parsed = json.loads(_strip_fences(result.text))
        except json.JSONDecodeError as e:
            LOGGER.warning("statement_service: %s — parse failure: %s", failure, str(e))
            raise ValueError("%s — the model's response was not valid JSON" % failure) from e
        if not isinstance(parsed, dict) or expect not in parsed:
            raise ValueError("%s — the response had no '%s'" % (failure, expect))
        return parsed

    def resolve_missing_institutions(self, extracted: dict[str, Any], pdf_bytes: bytes) -> int:
        """
        Fill in an institution name the text layer never carried.

        Many statements print the bank's name only inside the letterhead
        graphic. The page is otherwise dense with text, so it clears the
        threshold for native extraction and is never rendered for vision — the
        name exists solely as pixels nobody looks at. The result is an account
        filed under "Unknown institution", and, because institution plus last
        four is the dedup key, the *next* upload of the same account creating a
        second row rather than matching the first.

        So when the name is missing, look at the page. One narrow vision call
        per unnamed statement, asking only that question.

        Modifies ``extracted`` in place.

        :return: How many names were recovered.
        :rtype: int
        """
        from services.pdf_service import pdf_service  # noqa: PLC0415 — avoids an import cycle

        statements = extracted.get("statements") or []

        # Strip the form printer's name before anything else looks at it.
        #
        # The vendor guard used to live only on the vision answer, which missed
        # the commoner case by a mile: "CSI REV 3/12/18" is in the page's text
        # layer, so the *primary* extraction returns "CSI" as the institution,
        # and a statement that already has a name never reaches the fallback at
        # all. Four months of one account filed themselves under the software
        # vendor that printed the form.
        #
        # Blanked rather than corrected here, so the lookup below gets its
        # ordinary chance to read the letterhead.
        for statement in statements:
            account = statement.setdefault("account", {})
            current = (account.get("institution") or "").strip()
            if current and _is_form_vendor(current):
                LOGGER.info("statement_service: extraction named a form vendor; discarding it")
                account["institution"] = None

        # `or ""`, not a get() default: the key is usually present and set to
        # null, which a default never sees. This is the field the model most
        # often returns as null — the name lives in the letterhead graphic, so
        # not reading it is the normal outcome, not the rare one.
        named = {
            ((s.get("account") or {}).get("institution") or "").strip()
            for s in statements
        }
        named.discard("")
        # More than one institution out of a single upload is a signal, not a
        # fact. It happens when the name is unreadable and each statement gets a
        # different guess — which then files them under different accounts and
        # hides them from the duplicate check. Re-read every name from the page
        # rather than trusting any of them. Non-destructive: if the pages really
        # do name two banks, that is what comes back.
        disputed = len({n.lower() for n in named}) > 1
        if disputed:
            LOGGER.info(
                "statement_service: %d institutions reported from one upload; verifying against the pages",
                len(named),
            )

        recovered = 0
        for statement in statements:
            account = statement.setdefault("account", {})
            name = (account.get("institution") or "").strip()
            if name and name.lower() not in _UNKNOWN_INSTITUTION and not disputed:
                continue

            pages = [
                p for p in (
                    _page_number(line.get("physical_page_number"))
                    for line in (statement.get("transactions") or [])
                ) if p
            ]
            answer = pdf_service.ask_page(pdf_bytes, min(pages) if pages else 1, _INSTITUTION_PROMPT)
            if not answer:
                continue
            answer = answer.strip().strip(".").strip()
            if not answer or answer.upper().startswith("UNKNOWN") or len(answer) > 120:
                continue
            if _is_form_vendor(answer):
                # Leaving it unnamed is the better failure. An unnamed account is
                # obvious and fixed in seconds; a confidently wrong name looks
                # settled and quietly opens a second account for a real one.
                LOGGER.info("statement_service: page named a form vendor, not an institution; ignoring")
                continue
            if answer == name:
                continue
            account["institution"] = answer
            recovered += 1
            LOGGER.info("statement_service: read the institution name off the page image")
        return recovered

    # ── Reconciliation ────────────────────────────────────────────────────

    @staticmethod
    def reconcile(
        beginning: Optional[Decimal],
        ending: Optional[Decimal],
        amounts: list[Decimal],
    ) -> tuple[Optional[Decimal], bool, Optional[Decimal]]:
        """
        Check that the transactions account for the change in balance.

        :return: ``(computed_ending, reconciled, delta)``. When either printed
            balance is missing there is nothing to check against, so
            ``reconciled`` is False and ``delta`` is None — unverified, which is
            not the same as wrong.
        :rtype: tuple[Optional[Decimal], bool, Optional[Decimal]]
        """
        if beginning is None:
            return None, False, None
        computed = (beginning + sum(amounts, ZERO)).quantize(Decimal("0.01"))
        if ending is None:
            return computed, False, None
        delta = (ending - computed).quantize(Decimal("0.01"))
        return computed, delta == ZERO, delta

    # ── Commit ────────────────────────────────────────────────────────────

    def commit_document(
        self,
        manager: DatabaseManager,
        matter_id: int,
        staff_id: int,
        extracted: dict[str, Any],
        raw_text: str,
        storage_path: Optional[str] = None,
        source_job_id: Optional[str] = None,
        bates_prefix: Optional[str] = None,
        source_filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Write every statement found in one document.

        A statement that reconciles and raises no blocking flag is accepted
        without review; anything else lands in the exceptions queue. That is
        what makes a production of several hundred statements workable.

        :param source_filename: The name of the uploaded file, kept so a
            statement in the exceptions queue can be tied back to the document
            it came from and to the import log entry.
        :param bates_prefix: An optional stamp prefix the user supplied, used
            to disambiguate an odd production. Detection normally needs no
            help — see :mod:`services.bates_service`.
        :return: A summary: counts plus a per-statement outcome list.
        :rtype: dict[str, Any]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        # Bates numbers are found by pattern over the page-marked text, not
        # taken from the model. One series per document, resolved once here and
        # applied to every line by its page.
        pages = bates_service.split_pages(raw_text)
        series = bates_service.detect(pages, prefix_hint=bates_prefix) if pages else None

        # Page ranges already committed from THIS document. Two statements cannot
        # be printed on the same page, so an overlap means the model split one
        # statement in two — and the duplicate guard cannot catch that, because
        # it is scoped to an account and the split usually invents a second one.
        claimed_pages: list[dict[str, Any]] = []

        results: list[dict[str, Any]] = []
        for raw_statement in (extracted.get("statements") or []):
            try:
                results.append(self._commit_one(
                    account_repo, statement_repo, transaction_repo,
                    matter_id=matter_id,
                    staff_id=staff_id,
                    raw_statement=raw_statement,
                    raw_text=raw_text,
                    storage_path=storage_path,
                    source_job_id=source_job_id,
                    extraction_meta=extracted.get("extraction") or {},
                    series=series,
                    source_filename=source_filename,
                    claimed_pages=claimed_pages,
                ))
            except Exception as e:  # noqa: BLE001 — one bad statement must not lose the rest
                LOGGER.error("statement_service.commit: statement failed: %s", str(e))
                results.append({"status": "error", "error": str(e)})

        accepted = sum(r.get("status") == StatementReviewStatus.auto_accepted.value for r in results)
        review = sum(r.get("status") == StatementReviewStatus.needs_review.value for r in results)
        LOGGER.info(
            "statement_service.commit: matter=%s statements=%d auto_accepted=%d needs_review=%d",
            matter_id, len(results), accepted, review,
        )
        return {
            "statements_found": len(results),
            "auto_accepted": accepted,
            "needs_review": review,
            "results": results,
            "bates": series.summary() if series else None,
        }

    def _commit_one(
        self,
        account_repo: FinancialAccountRepository,
        statement_repo: FinancialAccountStatementRepository,
        transaction_repo: FinancialAccountTransactionRepository,
        matter_id: int,
        staff_id: int,
        raw_statement: dict[str, Any],
        raw_text: str,
        storage_path: Optional[str],
        source_job_id: Optional[str],
        extraction_meta: dict[str, Any],
        series: Optional[bates_service.BatesSeries] = None,
        source_filename: Optional[str] = None,
        claimed_pages: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Write one statement, its account, and its lines."""
        account_block = raw_statement.get("account") or {}
        period = raw_statement.get("period") or {}
        balances = raw_statement.get("balances") or {}
        lines = raw_statement.get("transactions") or []

        period_start = _parse_date(period.get("start_date"))
        period_end = _parse_date(period.get("end_date"))
        if not period_start or not period_end:
            raise ValueError("Statement has no readable period — cannot file it against an account")

        flags: list[dict[str, Any]] = []

        # A check printed both in the running debits and again in the summary
        # table at the back is one payment. Removed before anything counts the
        # lines, so reconciliation and the completeness check both see the true
        # set — a doubled debit reconciles to a wrong number rather than an
        # obviously wrong one, which is the harder error to notice.
        lines, doubled = _dedupe_checks(lines)
        if doubled:
            flags.append(_flag(
                "DUPLICATE_CHECK_ROWS", "info",
                "%d check(s) appeared twice — once in the debit list and again in the summary "
                "table — and the repeat was dropped: %s." % (len(doubled), "; ".join(doubled)),
                "transactions",
            ))

        # 1. Account: match on institution + last four, or create.
        institution = (account_block.get("institution") or "").strip() or "Unknown institution"
        last4 = (account_block.get("account_number_last4") or "").strip() or None
        account = account_repo.find_match(matter_id, institution, last4)
        if account is None:
            # About to open a second account for a number the matter already
            # holds. Say so before it disappears into the inventory as two
            # half-complete histories — that is what an unreadable institution
            # name costs, and it is invisible once the statements pile up.
            twins = account_repo.others_with_last4(matter_id, institution, last4)
            if twins:
                flags.append(_flag(
                    "SAME_LAST4_DIFFERENT_INSTITUTION", "warn",
                    "This opened a new account under \"%s\" ····%s, but the matter already has "
                    "%s ····%s. The account number matches, so these are probably one account "
                    "read under two names. Merge them from the account editor if so."
                    % (institution, last4,
                       " and ".join('"%s"' % t.institution for t in twins), last4),
                    "account.institution",
                ))
            account = account_repo.insert(FinancialAccount(
                matter_id=matter_id,
                institution=institution,
                account_type=self._coerce_account_type(account_block.get("account_type")),
                account_number_last4=last4,
                account_number_masked=account_block.get("account_number_masked"),
                name_on_account=account_block.get("name_on_account"),
            ).model_dump())
            LOGGER.info("statement_service: created account id=%s for matter=%s", account.id, matter_id)
            if not last4:
                flags.append(_flag(
                    "NO_ACCOUNT_MATCH", "warn",
                    "The account number was not readable, so this statement could not be matched to an "
                    "existing account and a new one was created. Merge it if it duplicates.",
                    "account.account_number_last4",
                ))

        # 2. Already have this period? Do not write it twice.
        duplicate = statement_repo.find_period(account.id, period_start, period_end)
        if duplicate is not None:
            LOGGER.info("statement_service: duplicate period for account=%s statement=%s",
                        account.id, duplicate.id)
            return {
                "status": "duplicate",
                "statement_id": duplicate.id,
                "account_id": account.id,
                "period": [period_start.isoformat(), period_end.isoformat()],
            }

        # 2b. Not the same period, but sharing days with one already filed.
        # Consecutive statements do not overlap, so this is nearly always the
        # same statement read twice with its two printed date ranges swapped.
        # Nearly always is not enough to discard evidence on, so it is flagged.
        for other in statement_repo.find_overlapping(account.id, period_start, period_end):
            flags.append(_flag(
                "OVERLAPPING_PERIOD", "warn",
                "This covers %s to %s, which shares days with statement #%s (%s to %s) already on "
                "this account. Statements do not normally overlap — if this is the same one read "
                "twice, reject whichever is less complete."
                % (period_start, period_end, other.id, other.period_start, other.period_end),
                "period",
            ))
            break

        # 3. Reconcile before writing anything.
        amounts = [_money(line.get("amount")) or ZERO for line in lines]
        beginning = _money(balances.get("beginning_balance"))
        ending = _money(balances.get("ending_balance"))
        computed, reconciled, delta = self.reconcile(beginning, ending, amounts)

        if beginning is None or ending is None:
            flags.append(_flag(
                "BALANCE_MISSING", "warn",
                "The statement did not print both a beginning and an ending balance, so the "
                "transaction list could not be verified against it.",
                "balances",
            ))
        elif not reconciled:
            flags.append(_flag(
                "UNRECONCILED", "warn",
                "Transactions do not account for the change in balance. Printed close %s, "
                "computed %s, difference %s. Nothing was added to force it to balance." % (ending, computed, delta),
                "balances.ending_balance",
            ))

        # Does the statement agree that we got all of it? This is a stronger
        # signal than reconciliation, and a far more useful one: an unreconciled
        # statement says only that the arithmetic fails, while this says which
        # side is short and by how much. A 27-page statement whose debits
        # stopped after 15 of 262 lines reads as one enormous delta otherwise.
        printed_counts = raw_statement.get("printed_counts") or {}
        shortfalls = _completeness_findings(lines, balances.get("printed_totals") or {}, printed_counts)
        if shortfalls:
            flags.append(_flag(
                "INCOMPLETE_EXTRACTION", "warn",
                "This statement does not match the totals it prints for itself: %s. Extraction "
                "very likely stopped early — re-ingest before relying on it." % "; ".join(shortfalls),
                "transactions",
            ))

        # A warn-level flag on any line is enough to hold the statement back.
        line_warnings = sum(
            1 for line in lines
            for f in (line.get("flags") or [])
            if (f or {}).get("severity") == "warn"
        )
        if line_warnings:
            flags.append(_flag(
                "LINE_WARNINGS", "warn",
                "%d transaction line(s) carry a warning from extraction." % line_warnings,
            ))
        if not lines:
            flags.append(_flag("NO_TRANSACTIONS", "warn", "No transactions were found in this statement."))

        # An undated line has no effect on reconciliation — the balance check is
        # pure arithmetic over amounts — so nothing else would ever reveal it.
        # It matters anyway: a NULL fails both `>=` and `<=`, so the line drops
        # out of every date-filtered search, and it sorts to the end of the
        # account's history. The statement would tie while an exhibit built by
        # date range quietly omitted the line, and nothing would explain the
        # difference between the two figures.
        undated = sum(1 for line in lines if not _parse_date(line.get("transaction_date")))
        if undated:
            flags.append(_flag(
                "UNDATED_TRANSACTIONS", "warn",
                "%d transaction(s) have no date, after the date column was re-read. They count "
                "toward this statement's balance but are excluded from every date-filtered search, "
                "so an exhibit built by date range will not contain them. Date them from the "
                "source page." % undated,
                "transaction_date",
            ))

        # A page nobody could read is not a blank page.
        #
        # When the text layer is unusable and the vision fallback also fails,
        # pdf_service leaves a marker where the page's text would have been.
        # Without this the loss was total and silent: the page contributed an
        # empty string, the ingest carried on, and no record anywhere said a
        # page was missing from the extraction.
        unreadable = _unreadable_pages(raw_text)
        if unreadable:
            flags.append(_flag(
                "PAGE_UNREADABLE", "warn",
                "Page(s) %s could not be read at all — neither the text layer nor OCR. Any "
                "transaction printed there is absent from this record. Re-ingest, or check the "
                "source PDF for a scan that needs redoing."
                % ", ".join(str(p) for p in unreadable),
                "transactions",
            ))

        # Bates findings, scoped to the pages this statement actually occupies.
        # The document may hold several statements; a hole in another one's
        # page range is not this statement's problem.
        statement_pages = sorted({p for p in (_page_number(l.get("physical_page_number")) for l in lines) if p})
        bates_gaps: list[str] = []
        if series is None:
            guessed = sum(1 for l in lines if (l.get("bates_number") or "").strip())
            if guessed:
                flags.append(_flag(
                    "BATES_UNVERIFIED", "info",
                    "No Bates series was detected in this document, so the %d number(s) recorded came "
                    "from the reader and nothing has confirmed them against the page." % guessed,
                    "bates_number",
                ))
        elif statement_pages:
            unstamped = [p for p in statement_pages if p not in series.by_page]
            if unstamped:
                flags.append(_flag(
                    "BATES_UNSTAMPED", "info",
                    "No Bates stamp was readable on page(s) %s, so those lines carry no citation."
                    % ", ".join(str(p) for p in unstamped),
                    "bates_number",
                ))

            # The gap scan runs over the statement's page SPAN, not over the
            # pages that happened to carry a transaction.
            #
            # Statements are full of pages with no lines on them: a checkbook
            # reconciliation worksheet, a disclosures page, a closing page of
            # running balances. Scanning only transaction-bearing pages made
            # every one of those read as a hole in the run — a First Financial
            # statement with entries on pages 1 and 3 reported its own page 2 as
            # missing from the production, while the page sat right there,
            # stamped, in the file. The flag fired precisely when a statement
            # had transactions on two non-adjacent pages, which says nothing
            # about the production at all.
            span = range(statement_pages[0], statement_pages[-1] + 1)
            held = {_bates_value(series.by_page[p]) for p in span if p in series.by_page}
            # A page inside the span with no readable stamp is still a page we
            # hold, so project what it would carry and do not call it missing.
            anchors = [p for p in span if p in series.by_page]
            projected = {
                _bates_value(series.by_page[min(anchors, key=lambda q: abs(q - p))])
                + (p - min(anchors, key=lambda q: abs(q - p)))
                for p in span if p not in series.by_page
            } if anchors else set()

            if held:
                bates_gaps = [
                    series.format(n) for n in range(min(held), max(held) + 1)
                    if n not in held and n not in projected
                ]
            if bates_gaps:
                flags.append(_flag(
                    "BATES_GAP", "warn",
                    "The Bates run breaks inside this statement — %s missing. Those pages are absent "
                    "from the production, so lines printed on them are not in this record."
                    % ", ".join(bates_gaps),
                    "bates_number",
                ))

        # Does this statement claim pages another one from this document already
        # claimed? Two statements cannot be printed on the same page. The usual
        # cause is a summary table — a daily ending balance list, an account
        # summary — read as a second register, which then invents its own
        # account because the institution on it could not be read either.
        overlap = None
        if statement_pages and claimed_pages is not None:
            first, last = statement_pages[0], statement_pages[-1]
            for earlier in claimed_pages:
                if first <= earlier["last"] and earlier["first"] <= last:
                    overlap = earlier
                    break
        if overlap is not None:
            flags.append(_flag(
                "SUSPECT_SPLIT", "warn",
                "This covers page(s) %d–%d of the upload, which statement #%s (%s, %s to %s) already "
                "covers. Two statements cannot be printed on the same page, so one of them is a "
                "misreading — most often a balance or summary table read as a transaction list. "
                "Compare the two and reject whichever is not the real register."
                % (statement_pages[0], statement_pages[-1], overlap["statement_id"],
                   overlap["institution"], overlap["period_start"], overlap["period_end"]),
                "transactions",
            ))

        blocking = any(f["severity"] == "warn" for f in flags)
        status = StatementReviewStatus.needs_review if blocking else StatementReviewStatus.auto_accepted

        statement = statement_repo.insert(FinancialAccountStatement(
            financial_account_id=account.id,
            matter_id=matter_id,
            period_start=period_start,
            period_end=period_end,
            beginning_balance=beginning,
            ending_balance=ending,
            computed_ending_balance=computed,
            reconciled=reconciled,
            reconciliation_delta=delta,
            printed_totals=balances.get("printed_totals") or {},
            flags=flags,
            review_status=status,
            storage_path=storage_path,
            raw_text=raw_text,
            extraction={
                **extraction_meta,
                "profile": "extract_account_statement",
                # Provenance the exceptions queue reads back: which file this
                # came out of, and where it sits in the production.
                "source_filename": source_filename,
                "printed_counts": printed_counts,
                "bates_first": series.by_page.get(statement_pages[0]) if series and statement_pages else None,
                "bates_last": series.by_page.get(statement_pages[-1]) if series and statement_pages else None,
            },
            source_job_id=source_job_id,
            ingested_by_staff_id=staff_id,
        ).model_dump())

        if statement_pages and claimed_pages is not None:
            claimed_pages.append({
                "statement_id": statement.id,
                "first": statement_pages[0],
                "last": statement_pages[-1],
                "institution": institution,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })

        # 4. Lines, in printed order.
        unverified_bates = 0
        for index, line in enumerate(lines, start=1):
            page = _page_number(line.get("physical_page_number"))
            if series is not None:
                # A series was found, so the pattern owns the field outright —
                # including writing None for a page it found no stamp on. The
                # model's answer is discarded rather than used as a fallback:
                # its failure mode here is producing a plausible number for an
                # unstamped page, which is exactly what a citation must not do.
                bates = series.by_page.get(page) if page else None
            else:
                # Nothing detected. Keep what the model read so an unusual
                # stamp is not lost, but mark it: nothing has confirmed it.
                bates = (line.get("bates_number") or "").strip() or None
                if bates:
                    unverified_bates += 1

            transaction_repo.insert(FinancialAccountTransaction(
                statement_id=statement.id,
                financial_account_id=account.id,
                line_no=int(line.get("line_no") or index),
                transaction_date=_parse_date(line.get("transaction_date")),
                posted_date=_parse_date(line.get("posted_date")),
                date_provenance=self._coerce_provenance(line.get("date_provenance")),
                description=(line.get("description") or "").strip() or "(no description)",
                description_lines=line.get("description_lines") or [],
                counterparty=line.get("counterparty"),
                location=line.get("location"),
                amount=_money(line.get("amount")) or ZERO,
                running_balance=_money(line.get("running_balance")),
                # Extraction never sets category_id. The free-text `category`
                # below is the model's guess and is only ever a hint to whoever
                # categorizes; the FK is set by a human, because an FIS total is
                # only as defensible as the person who can testify to it.
                category=(line.get("category") or None),
                physical_page_number=page,
                bates_number=bates,
                check_number=_check_number(line.get("check_number")),
                flags=line.get("flags") or [],
            ).model_dump())

        return {
            "status": status.value,
            "statement_id": statement.id,
            "account_id": account.id,
            "institution": institution,
            "period": [period_start.isoformat(), period_end.isoformat()],
            "transactions": len(lines),
            "reconciled": reconciled,
            "delta": str(delta) if delta is not None else None,
            "bates_first": series.by_page.get(statement_pages[0]) if series and statement_pages else None,
            "bates_last": series.by_page.get(statement_pages[-1]) if series and statement_pages else None,
            "bates_gaps": bates_gaps,
        }

    # ── Merging two rows that are one account ─────────────────────────────

    def preview_merge(
        self,
        manager: DatabaseManager,
        source_id: int,
        target_id: int,
    ) -> dict[str, Any]:
        """
        Report what merging one account into another would do.

        Accounts split in two for a mundane reason: many statements print the
        institution only in the letterhead graphic, so extraction files the
        first one under "Unknown institution", and correcting that name does
        not retroactively match the next upload. The result is two rows that
        are one account.

        Merging is destructive — it moves evidence and drops a row — so this
        reports first and the caller decides.

        :return: The counts, plus every conflict found. ``blocking`` conflicts
            cannot be forced past.
        :rtype: dict[str, Any]
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        source = account_repo.select_one(condition={"id": source_id})
        target = account_repo.select_one(condition={"id": target_id})
        if source is None or target is None:
            raise ValueError("Account not found")

        conflicts: list[dict[str, Any]] = []
        if source_id == target_id:
            conflicts.append(_conflict("SAME_ACCOUNT", True, "An account cannot be merged into itself."))
        if source.matter_id != target.matter_id:
            conflicts.append(_conflict(
                "DIFFERENT_MATTER", True,
                "These accounts belong to different matters. Moving records between matters is "
                "never a merge.",
            ))

        source_statements = statement_repo.get_by_account(source_id)
        target_statements = statement_repo.get_by_account(target_id)

        # Overlapping periods are blocking, not a warning. A unique index on
        # (account, period_start, period_end) rejects the second one, so the
        # merge would fail part-way with an opaque database error. The fix is
        # to reject the duplicate statement first, which the user has to do.
        for mine in source_statements:
            if mine.review_status == StatementReviewStatus.rejected:
                continue
            for theirs in target_statements:
                if theirs.review_status == StatementReviewStatus.rejected:
                    continue
                if mine.period_start <= theirs.period_end and theirs.period_start <= mine.period_end:
                    conflicts.append(_conflict(
                        "PERIOD_OVERLAP", True,
                        "Both accounts hold a statement covering %s to %s. Reject the duplicate "
                        "before merging — the same period cannot sit twice on one account."
                        % (max(mine.period_start, theirs.period_start),
                           min(mine.period_end, theirs.period_end)),
                    ))
                    break

        # The same Bates numbers on both sides means the same pages were
        # ingested twice. Worth stopping for, but forceable: a production can
        # legitimately restamp, and the attorney can see what they are doing.
        source_bates = {t.bates_number for t in transaction_repo.get_by_account(source_id, include_deleted=True)
                        if t.bates_number}
        target_bates = {t.bates_number for t in transaction_repo.get_by_account(target_id, include_deleted=True)
                        if t.bates_number}
        shared = sorted(source_bates & target_bates)
        if shared:
            conflicts.append(_conflict(
                "BATES_OVERLAP", False,
                "%d page(s) are already on the destination account: %s%s. The same pages appear to "
                "have been ingested twice."
                % (len(shared), ", ".join(shared[:5]), " …" if len(shared) > 5 else ""),
            ))

        if (source.account_number_last4 and target.account_number_last4
                and source.account_number_last4 != target.account_number_last4):
            conflicts.append(_conflict(
                "LAST4_MISMATCH", False,
                "The account numbers end differently (%s vs %s). These may be two real accounts."
                % (source.account_number_last4, target.account_number_last4),
            ))
        if source.account_type != target.account_type:
            conflicts.append(_conflict(
                "TYPE_MISMATCH", False,
                "One is a %s and the other a %s." % (source.account_type.value, target.account_type.value),
            ))

        blocking = [c for c in conflicts if c["blocking"]]
        return {
            "source_account_id": source_id,
            "target_account_id": target_id,
            "source_label": _account_label(source),
            "target_label": _account_label(target),
            "statements_to_move": len(source_statements),
            "transactions_to_move": len(transaction_repo.get_by_account(source_id, include_deleted=True)),
            "conflicts": conflicts,
            "can_merge": not blocking,
            "needs_force": not blocking and bool(conflicts),
        }

    def merge(
        self,
        manager: DatabaseManager,
        source_id: int,
        target_id: int,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Move every statement off one account onto another, then delete it.

        Only the statements are repointed. Their transactions follow
        automatically: ``financial_account_id`` on a transaction is a copy of
        its statement's, and migration 026 made that composite foreign key
        ``ON UPDATE CASCADE`` so the copy cannot fall out of step.

        Statements are moved **before** the source is deleted, and that order
        is not stylistic — the statements table cascades on account delete, so
        dropping the row first would take the evidence with it.

        :param force: Proceed despite non-blocking conflicts. A blocking
            conflict is refused regardless.
        :return: Counts moved, plus the surviving account.
        :rtype: dict[str, Any]
        :raises ValueError: If the merge is unsafe.
        """
        preview = self.preview_merge(manager, source_id, target_id)
        if not preview["can_merge"]:
            raise ValueError("; ".join(c["detail"] for c in preview["conflicts"] if c["blocking"]))
        if preview["needs_force"] and not force:
            raise ValueError("; ".join(c["detail"] for c in preview["conflicts"]))

        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        target = account_repo.select_one(condition={"id": target_id})
        if target is None:
            raise ValueError("Account not found")

        moved = 0
        for statement in statement_repo.get_by_account(source_id):
            statement_repo.update(statement.id, {"financial_account_id": target_id})
            moved += 1

        # Anything naming the source as its predecessor is repointed first.
        # The column is ON DELETE SET NULL, so leaving it would quietly break a
        # succession chain at exactly the moment two halves of one account were
        # finally joined up.
        for account in account_repo.get_by_matter(target.matter_id):
            if account.antecedent_account_id == source_id and account.id != target_id:
                account_repo.update(account.id, {"antecedent_account_id": target_id})
        if target.antecedent_account_id == source_id:
            account_repo.update(target_id, {"antecedent_account_id": None})

        account_repo.delete(source_id)
        target = account_repo.select_one(condition={"id": target_id})

        LOGGER.info(
            "statement_service.merge: source=%s -> target=%s statements=%d transactions=%d forced=%s",
            source_id, target_id, moved, preview["transactions_to_move"], force,
        )
        return {
            "statements_moved": moved,
            "transactions_moved": preview["transactions_to_move"],
            "target": target,
        }

    # ── Correcting a line after ingestion ─────────────────────────────────

    def correct_transaction(
        self,
        manager: DatabaseManager,
        transaction_id: int,
        updates: dict[str, Any],
        staff_id: int,
        staff_name: str,
        reason: Optional[str] = None,
    ) -> tuple[Any, Any]:
        """
        Change a value on an ingested line, and record who changed it.

        Extraction misreads things — a smudged digit, a description running off
        the page — so a line has to be correctable. But the corrected figure
        goes into an exhibit, and the first question on cross is where it came
        from. So nothing is quietly overwritten: every change appends a
        ``MANUAL_CORRECTION`` flag naming the field, the old value, the new
        value, and the person, which makes the original recoverable from the
        record itself.

        Correcting an ``amount`` re-reconciles the statement. That is the point
        of allowing the edit at all — an unreconciled statement is usually one
        misread figure, and fixing it should close the gap rather than leave a
        stale delta sitting on the row.

        :param updates: Fields to change. Only the ones actually different from
            what is stored are applied and recorded.
        :param staff_name: Recorded in the flag so it reads as a sentence years
            later, when a staff id means nothing to anybody.
        :param reason: Optional note on why, e.g. "corrected against page 3".
        :return: ``(transaction, statement)`` after the change. The statement is
            None when nothing touched reconciliation.
        :raises ValueError: If the transaction does not exist, or nothing changed.
        """
        transaction_repo = FinancialAccountTransactionRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)

        record = transaction_repo.select_one(condition={"id": transaction_id})
        if record is None:
            raise ValueError("Transaction not found")

        stamped_at = datetime.now(timezone.utc).isoformat()
        applied: dict[str, Any] = {}
        new_flags: list[dict[str, Any]] = []

        for field, raw_value in updates.items():
            if field not in _CORRECTABLE:
                continue
            before = getattr(record, field)
            after = _coerce_field(field, raw_value)
            if _same_value(before, after):
                continue
            # Stored as the native type. The manager's json_safe already turns
            # Decimal and date into what PostgREST wants; converting here as
            # well leaves a string on the model the caller gets back, which then
            # fails the next arithmetic that touches it.
            applied[field] = after
            new_flags.append({
                "code": "MANUAL_CORRECTION",
                "severity": "info",
                "field_path": field,
                "note": "%s changed the %s from %s to %s." % (
                    staff_name, _CORRECTABLE[field],
                    _display(field, before), _display(field, after),
                ),
                "by_staff_id": staff_id,
                "by": staff_name,
                "at": stamped_at,
                "from": _serialize(before),
                "to": _serialize(after),
                "reason": reason or None,
            })

        if not applied:
            raise ValueError("Nothing changed")

        applied["flags"] = list(record.flags) + new_flags
        updated = transaction_repo.update(transaction_id, applied)
        LOGGER.info(
            "statement_service.correct_transaction: transaction=%s fields=%s staff=%s",
            transaction_id, ",".join(sorted(f for f in applied if f != "flags")), staff_id,
        )

        statement = None
        if "amount" in applied:
            statement = self._rereconcile(statement_repo, transaction_repo, record.statement_id)
        return updated, statement

    @staticmethod
    def _rereconcile(
        statement_repo: FinancialAccountStatementRepository,
        transaction_repo: FinancialAccountTransactionRepository,
        statement_id: int,
    ) -> Any:
        """
        Recompute a statement's balance check after one of its lines changed.

        The stale ``UNRECONCILED`` flag is replaced rather than added to, so a
        statement corrected into balance stops claiming it is out of balance.
        ``review_status`` is deliberately left alone: whether an exception is
        cleared is a decision, not a consequence of arithmetic.
        """
        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            return None

        amounts = [t.amount for t in transaction_repo.get_by_statement(statement_id)]
        computed, reconciled, delta = StatementService.reconcile(
            statement.beginning_balance, statement.ending_balance, amounts,
        )

        flags = [f for f in statement.flags if f.get("code") != "UNRECONCILED"]
        if statement.beginning_balance is not None and statement.ending_balance is not None and not reconciled:
            flags.append(_flag(
                "UNRECONCILED", "warn",
                "Transactions do not account for the change in balance. Printed close %s, "
                "computed %s, difference %s." % (statement.ending_balance, computed, delta),
                "balances.ending_balance",
            ))

        return statement_repo.update(statement_id, {
            "computed_ending_balance": computed,
            "reconciled": reconciled,
            "reconciliation_delta": delta,
            "flags": flags,
        })

    # ── Discarding a bad extraction ───────────────────────────────────────

    def reject_statement(self, manager: DatabaseManager, statement_id: int) -> dict[str, Any]:
        """
        Throw away a bad extraction: the statement, its lines, and — when it is
        left with nothing — the account it was filed under.

        Rejecting used to flip a status and stop there. The statement stayed,
        its transactions stayed, and the account it created stayed, all of them
        filtered out of every view. That is the worst of both worlds: the
        records are invisible, so nobody can act on them, but they are still
        there, so the period stays occupied in spirit and an empty account
        clutters the inventory. A rejected extraction is not evidence held back
        — it is data that was read wrong, and the honest thing is to delete it.

        The source PDF is left in Storage. One upload can back several
        statements, so deleting the file on one rejection would take the others'
        source with it, and the job row still names it either way.

        The account is only removed when nothing of value would go with it: no
        statements left, nothing naming it as a predecessor, and no human
        judgment recorded on it. An account someone has characterized is worth
        more than the import that created it.

        :return: What was removed, and — when the account was kept — why.
        :rtype: dict[str, Any]
        :raises ValueError: If the statement does not exist.
        """
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)
        account_repo = FinancialAccountRepository(manager)

        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            raise ValueError("Statement not found")

        account_id = statement.financial_account_id
        # Counted before the delete: the rows go with the statement, so
        # afterwards there is nothing left to count.
        # Every row goes with the statement, so every row is counted.
        line_count = len(transaction_repo.get_by_statement(statement_id, include_deleted=True))
        statement_repo.delete(statement_id)

        account_deleted = False
        kept_reason: Optional[str] = None
        account = account_repo.select_one(condition={"id": account_id})
        if account is not None:
            kept_reason = self._reason_to_keep(account_repo, statement_repo, account)
            if kept_reason is None:
                account_repo.delete(account_id)
                account_deleted = True

        LOGGER.info(
            "statement_service.reject_statement: statement=%s transactions=%d account=%s deleted=%s",
            statement_id, line_count, account_id, account_deleted,
        )
        return {
            "statement_id": statement_id,
            "financial_account_id": account_id,
            "transactions_deleted": line_count,
            "account_deleted": account_deleted,
            "account_kept_reason": kept_reason,
        }

    @staticmethod
    def _reason_to_keep(
        account_repo: FinancialAccountRepository,
        statement_repo: FinancialAccountStatementRepository,
        account: Any,
    ) -> Optional[str]:
        """
        Why an emptied account should survive its last statement, if it should.

        :return: The reason to keep it, or None when it is safe to remove.
        :rtype: Optional[str]
        """
        if statement_repo.get_by_account(account.id):
            return "it still has other statements"

        # An account can be a meaningful link in a succession chain with no
        # statements of its own — we know the account existed, we just have
        # nothing for it yet. Deleting it would quietly break the chain, since
        # antecedent_account_id is ON DELETE SET NULL.
        for other in account_repo.get_by_matter(account.matter_id):
            if other.id != account.id and other.antecedent_account_id == account.id:
                return "another account is recorded as succeeding it"
        if account.antecedent_account_id is not None:
            return "it is part of an account history"

        # Characterization, ownership, purpose, and notes are attorney work, not
        # extraction output. An account someone has reasoned about outlives the
        # import that happened to create it.
        if account.ownership != AccountOwnership.unknown:
            return "someone has recorded who holds it"
        if account.property_character is not None:
            return "it has been characterized"
        if (account.purpose or "").strip() or (account.notes or "").strip():
            return "it carries notes someone wrote"
        return None

    # ── Dropping a line from a statement ──────────────────────────────────

    def delete_transaction(
        self,
        manager: DatabaseManager,
        transaction_id: int,
        staff_id: int,
        staff_name: str,
        reason: Optional[str] = None,
    ) -> tuple[Any, Any]:
        """
        Drop a line from its statement without destroying it.

        The one legitimate reason to do this is that extraction invented the
        line — a row read twice, a daily-balance entry read as a transaction.
        When that is what happened, the statement reconciles BETTER afterwards,
        because the line was never part of the printed total. If reconciliation
        gets worse, something real was just removed, and the returned statement
        says so.

        Nothing is destroyed, because dropping a line is an assertion about the
        document — "this is not printed there" — and assertions that reach an
        exhibit need an author. The row is hidden everywhere, excluded from
        every total, and swept when the matter closes.

        :return: ``(transaction, statement)`` after the change; the statement is
            re-reconciled without the dropped line.
        :raises ValueError: If the line does not exist or is already dropped.
        """
        transaction_repo = FinancialAccountTransactionRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)

        record = transaction_repo.select_one(condition={"id": transaction_id})
        if record is None:
            raise ValueError("Transaction not found")
        if record.deleted_at is not None:
            raise ValueError("That line has already been removed")

        stamped_at = datetime.now(timezone.utc)
        flag = {
            "code": "MANUAL_DELETION",
            "severity": "info",
            "field_path": None,
            "note": "%s removed this line from the statement.%s" % (
                staff_name, (" %s" % reason) if reason else "",
            ),
            "by_staff_id": staff_id,
            "by": staff_name,
            "at": stamped_at.isoformat(),
            "reason": reason or None,
        }
        updated = transaction_repo.update(transaction_id, {
            "deleted_at": stamped_at,
            "deleted_by_staff_id": staff_id,
            "deletion_reason": reason or None,
            "flags": list(record.flags) + [flag],
        })
        LOGGER.info(
            "statement_service.delete_transaction: transaction=%s statement=%s staff=%s",
            transaction_id, record.statement_id, staff_id,
        )
        statement = self._rereconcile(statement_repo, transaction_repo, record.statement_id)
        return updated, statement

    def restore_transaction(
        self,
        manager: DatabaseManager,
        transaction_id: int,
        staff_id: int,
        staff_name: str,
    ) -> tuple[Any, Any]:
        """
        Put a dropped line back on its statement.

        The undo half of :meth:`delete_transaction`, and the reason the deletion
        is soft at all. Leaves its own flag rather than erasing the deletion's:
        that a line was removed and restored is part of the record, not a
        mistake to tidy away.

        :return: ``(transaction, statement)``, re-reconciled with the line back.
        :raises ValueError: If the line does not exist or was never dropped.
        """
        transaction_repo = FinancialAccountTransactionRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)

        record = transaction_repo.select_one(condition={"id": transaction_id})
        if record is None:
            raise ValueError("Transaction not found")
        if record.deleted_at is None:
            raise ValueError("That line has not been removed")

        flag = {
            "code": "MANUAL_RESTORE",
            "severity": "info",
            "field_path": None,
            "note": "%s put this line back on the statement." % staff_name,
            "by_staff_id": staff_id,
            "by": staff_name,
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": None,
        }
        updated = transaction_repo.update(transaction_id, {
            "deleted_at": None,
            "deleted_by_staff_id": None,
            "deletion_reason": None,
            "flags": list(record.flags) + [flag],
        })
        LOGGER.info("statement_service.restore_transaction: transaction=%s staff=%s",
                    transaction_id, staff_id)
        statement = self._rereconcile(statement_repo, transaction_repo, record.statement_id)
        return updated, statement

    # ── Removing an account and everything under it ───────────────────────

    def preview_account_delete(self, manager: DatabaseManager, account_id: int) -> dict[str, Any]:
        """
        Report what deleting an account would take with it.

        Deleting cascades to every statement and every line, so it says so
        first. The warnings are the same conditions that stop an emptied account
        being cleaned up automatically — but here they only warn, because this
        is somebody deliberately removing an account rather than a side effect
        of rejecting a statement.

        :raises ValueError: If the account does not exist.
        """
        account_repo = FinancialAccountRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        account = account_repo.select_one(condition={"id": account_id})
        if account is None:
            raise ValueError("Account not found")

        statements = statement_repo.get_by_account(account_id)
        transactions = transaction_repo.get_by_account(account_id, include_deleted=True)

        warnings: list[str] = []
        if account.property_character is not None:
            warnings.append("This account has been characterized as %s."
                            % account.property_character.value.replace("_", " "))
        if account.ownership != AccountOwnership.unknown:
            warnings.append("Ownership is recorded as %s." % account.ownership.value.replace("_", " "))
        if (account.purpose or "").strip() or (account.notes or "").strip():
            warnings.append("It carries notes somebody wrote.")
        for other in account_repo.get_by_matter(account.matter_id):
            if other.id != account_id and other.antecedent_account_id == account_id:
                warnings.append(
                    "%s is recorded as succeeding this account; deleting it breaks that history."
                    % _account_label(other))
        tagged = sum(1 for t in transactions if t.deleted_at is None)

        return {
            "account_id": account_id,
            "account_label": _account_label(account),
            "statements": len(statements),
            "transactions": tagged,
            "periods": [
                "%s to %s" % (s.period_start, s.period_end)
                for s in sorted(statements, key=lambda s: s.period_start)
            ],
            "warnings": warnings,
        }

    def delete_account(self, manager: DatabaseManager, account_id: int) -> dict[str, Any]:
        """
        Delete an account, its statements, and their lines.

        Hard, not soft. The Bates-stamped PDFs are still in Storage, so a
        mistake costs a re-import rather than an evidence problem — and an
        account that lingers half-deleted in an inventory is worse than one that
        is gone. The database does the cascade: statements go with the account,
        transactions with the statements.

        The stored PDFs are deliberately left alone. One upload can back several
        statements across different accounts.

        :return: What was removed.
        :raises ValueError: If the account does not exist.
        """
        counts = self.preview_account_delete(manager, account_id)
        account_repo = FinancialAccountRepository(manager)

        account = account_repo.select_one(condition={"id": account_id})
        # Anything naming this account as its predecessor would be silently
        # unlinked by ON DELETE SET NULL. Clear it deliberately instead, so the
        # break is something we did rather than something that happened.
        for other in account_repo.get_by_matter(account.matter_id):
            if other.id != account_id and other.antecedent_account_id == account_id:
                account_repo.update(other.id, {"antecedent_account_id": None})

        account_repo.delete(account_id)
        LOGGER.info(
            "statement_service.delete_account: account=%s statements=%d transactions=%d",
            account_id, counts["statements"], counts["transactions"],
        )
        return counts

    @staticmethod
    def _coerce_account_type(value: Any) -> AccountType:
        try:
            return AccountType(str(value))
        except ValueError:
            return AccountType.other

    @staticmethod
    def _coerce_provenance(value: Any) -> DateProvenance:
        try:
            return DateProvenance(str(value))
        except ValueError:
            return DateProvenance.unknown


statement_service = StatementService()
