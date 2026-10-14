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
from services import account_number_service, bates_service
from services.category_rule_service import category_rule_service
from services.llm_service import llm_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# Statements are dense and repetitive; a long one can run past this, so the
# extractor is told to report truncation rather than silently stop.
_MAX_STATEMENT_CHARS = 60_000

# Pages to re-show at the start of the next index window. A statement whose
# header sits on a window boundary would otherwise be described by neither side
# as a whole: the window before it sees the header with no body, the one after
# sees the body with no header. Two pages covers both halves of a seam on every
# document shape seen so far, and costs two pages of prompt per window.
_INDEX_WINDOW_OVERLAP = 2

# A page that restarts its own pagination is a statement's FIRST page, and the
# word boundary is the whole pattern: without it "PAGE 1" matches inside
# "PAGE 10" and every long statement reads as a dozen first pages. The same
# expression pdf_service surveys an upload with, applied here to the extracted
# text rather than to the PDF.
_PAGE_ONE = re.compile(r"\bpage\s+1\b(?!\d)", re.I)

# Below this, a page carries no statement content worth claiming -- a separator
# sheet, a mail insert, a page whose OCR produced nothing. They are excluded
# from the unindexed-page warning, which is about CONTENT no statement
# accounted for; an unreadable page is a different fact with its own flag.
_MIN_INDEXABLE_CHARS = 200

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


# NOT "brokerages" -- accounts whose value moves by FLOWS PLUS MARKET RESULTS,
# which is what the five behaviours behind this set are actually about: skip
# trades, balance is total account value, change in value is a printed line, the
# check is the statement's own summary block, and the omission is counted.
#
# A cash-value life policy or an annuity satisfies every one of them. 038 added
# the types; before that such a statement landed on `other`, got the BANK-shaped
# completeness check, and a RiverSource VUL that reconciled to the cent was
# flagged INCOMPLETE_EXTRACTION because `premium_payments` matched as the
# printed DEBIT total. A premium payment is money IN on a policy and money OUT
# on a bank statement -- see `_printed_side`.
#
# Declared ABOVE the prompts because the prompts are built from it. See
# _ACCOUNT_TYPES / _CASH_FLOW_NAMES.
_CASH_FLOW_ACCOUNTS = frozenset({
    AccountType.brokerage, AccountType.retirement,
    AccountType.life_insurance, AccountType.annuity,
})

# THE PROMPTS ARE BUILT FROM THE ENUM, NOT TYPED BESIDE IT.
#
# They used to spell both lists out in English, and both drifted. The metadata
# prompts offered the 036-era set, so `crypto` was never an answer the model
# could give -- for two migrations -- and `life_insurance` and `annuity` were
# not either. Worse, the transactions prompt gated its cash-flow rule on the
# literal words "brokerage" or "retirement", so adding a type to
# `_CASH_FLOW_ACCOUNTS` switched the PYTHON checks to the cash-flow path while
# leaving the MODEL on the bank rule. The result was a policy statement read
# with bank rules, checked with brokerage rules, and extracted as zero
# transactions.
#
# Substituted with `str.replace` on sentinels rather than %-formatting or
# `.format`: these prompts contain literal braces (JSON examples) and percent
# signs, and both would have to be escaped throughout.
_ACCOUNT_TYPES = " | ".join('"%s"' % t.value for t in AccountType)
_CASH_FLOW_NAMES = " or ".join(
    '"%s"' % t.value for t in sorted(_CASH_FLOW_ACCOUNTS, key=lambda t: t.value))


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

BROKERAGE AND RETIREMENT ACCOUNTS — A DIFFERENT RULE ABOUT WHAT COUNTS. On
those account types, extract money that CROSSED THE ACCOUNT BOUNDARY and nothing
else. A purchase of stock exchanges cash for shares inside one account and
leaves it worth what it was; so does a sale, a redemption, a maturity, an
exchange, a reinvestment, and a split.

  EXTRACT: deposits, transfers and wires in; securities transferred IN in kind
  at the stated value; dividends and capital gain distributions, cash or
  reinvested; interest and sweep income; withdrawals, transfers, wires, ACH,
  checks and cards out; securities transferred OUT in kind; margin interest,
  advisory, management and account fees; and CHANGE IN MARKET VALUE where the
  statement prints it as a figure of its own.

  DO NOT EXTRACT: buys, sells, redemptions, maturities, exchanges, reinvestment
  purchases, splits, spin-offs, CUSIP changes, journal entries between the
  account's own cash and margin balances, or the per-holding cost basis and
  unrealized gain columns of a positions table. Count what you passed over and
  report it as securities_transactions_omitted on the statement.

  CHANGE IN MARKET VALUE IS NEVER ARITHMETIC YOU PERFORM. Report it only if the
  statement prints such a figure. NEVER compute it as closing value minus
  opening value minus everything else — that number always balances, so it would
  conceal a wire nobody extracted instead of revealing it. If it is not printed,
  leave it out and let the statement fail to reconcile. Description exactly
  "Change in Market Value"; a gain POSITIVE, a loss NEGATIVE.

  BALANCES ON THESE ACCOUNTS ARE THE TOTAL ACCOUNT VALUE — cash plus securities,
  from the portfolio or account-value summary — never the cash or sweep balance.
  The cash balance cannot reconcile against this extraction, because the
  purchases that turned cash into securities are deliberately absent from it.

These blocks are NOT transaction registers. Never turn one into transactions,
and never let one become a statement of its own:
  - DAILY ENDING BALANCE, or any table of dates against running balances. These
    are end-of-day snapshots of one figure, not money moving.
  - ACCOUNT SUMMARY / SUMMARY OF ACCOUNTS — totals and counts, not entries.
  - INTEREST RATE SUMMARY, rate tables, yield tables.
  - Checkbook reconciliation worksheets, and any blank form the customer fills in.
  - Check images, disclosures, error-resolution notices, marketing inserts.
  - REWARDS or POINTS activity — "SHOP WITH POINTS ACTIVITY", "REWARDS
    ACTIVITY", "POINTS ACTIVITY", "MEMBERSHIP REWARDS", "CASH BACK ACTIVITY",
    and any continuation of one headed "(CONTINUED)". A table carrying a
    REWARDS or POINTS column beside its dollar column is this block whatever it
    is titled, and that column is the surest way to recognise it.
    These are the SAME purchases already listed in the account activity, shown
    again to report the points earned or redeemed on each. Extracting them
    charges the card twice for one purchase.
    THE SUB-HEADINGS INSIDE THIS BLOCK ARE THE EXACT WORDS A REAL REGISTER
    USES — "PURCHASES AND REDEMPTIONS", "RETURNS AND OTHER CREDITS" — so the
    heading on a row cannot tell you where you are. What the block sits under
    can.
A date column next to an amount column does not make a table a register. Ask
whether each row is a payment, deposit, or charge that moved the balance. A
check did; a balance, a total, or a rate did not, and neither did a purchase
being counted a second time for its points.

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
  - account_type: one of <<ACCOUNT_TYPES>>
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
- portfolio_summary: BROKERAGE AND RETIREMENT ONLY, else null. The statement's
  own summary of how the account value moved, printed under a heading such as
  "Change in Account Value" or "Portfolio Summary". Copy what it prints and omit
  what it does not; NEVER derive one figure from the others:
    {"opening_value": 412300.11, "additions": 10000.00, "withdrawals": -1000.00,
     "income": 200.00, "change_in_market_value": 8140.22,
     "closing_value": 429640.33}
  This is the independent check on an extraction that deliberately omits trades,
  so a figure you calculated rather than read defeats the check entirely.
- securities_transactions_omitted: integer, how many buy/sell/redemption rows
  you deliberately passed over on this statement. 0 for a non-brokerage account.
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
On a brokerage that identity holds against the TOTAL ACCOUNT VALUE, with the
change in market value as one of the amounts — never against the cash balance.
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

A DOCUMENT MAY PACK SEVERAL ACCOUNTS ONTO ONE PAGE. Some banks print a combined
statement — every account the customer holds, one register after another, with
no page break between them. A page can therefore end one account and begin the
next, and a single page can carry the close of one register, the whole of a
short one, and the opening of a third. Do not assume a new account starts at the
top of a page, and do not merge two accounts because they share a page.

Return ONLY a valid JSON object of the form {"statements": [ ... ]}. Each has:

- header_text: the exact line of text, copied character for character, that
  introduces this statement — normally the account name and number as printed,
  e.g. "Amandas Checking Account - 36325262322". Copy it verbatim from the text
  above; it is searched for literally, so a paraphrase or a corrected spelling
  will not be found. This is what marks where one register ends and the next
  begins when they share a page.
- first_page / last_page: integers, the page range this statement occupies,
  from its first header to the last page belonging to it. Include its balance
  tables, check images, and disclosure pages — everything before the next
  statement starts. Two statements MAY name the same page when one ends and the
  next begins on it.
- account:
  - institution: the bank, brokerage, or card issuer that HOLDS the account, as
    printed in the letterhead at the top of the page. Statements also name the
    company that PRINTED the form, in small type at the foot of the page beside
    a revision date or form number ("CSI REV 3/12/18", "3380-STMT", "Fiserv").
    That is a software vendor; never report it. If the only name you can find is
    in that footer, or the letterhead is a graphic you cannot read, return null.
  - account_type: one of <<ACCOUNT_TYPES>>
  - account_number_last4: string of the last four digits, or null if redacted
  - account_number_masked: the masked form exactly as printed
  - name_on_account: string or null
- period:
  - start_date / end_date: "YYYY-MM-DD"
- balances:
  - beginning_balance / ending_balance: number or null. On a BROKERAGE or
    RETIREMENT account these are the TOTAL ACCOUNT VALUE — cash plus securities
    — taken from the portfolio or account-value summary, NOT the cash or sweep
    balance.
  - printed_totals: totals the statement prints, e.g.
    {"deposits_credits": 202100.41, "checks_debits": -195600.04}
- printed_counts: counts the statement prints, e.g.
  {"deposits_credits": 24, "checks_debits": 262}. Omit a key you cannot find;
  never estimate one.
- portfolio_summary: BROKERAGE AND RETIREMENT ACCOUNTS ONLY, else null. The
  statement's own summary of how the account value moved over the period —
  printed under a heading like "Change in Account Value", "Portfolio Summary",
  or "Account Summary". Copy the figures it prints; NEVER derive one from the
  others, and omit any key it does not print:
    {"opening_value": 412300.11, "additions": 10000.00,
     "withdrawals": -1000.00, "income": 200.00,
     "change_in_market_value": 8140.22, "closing_value": 429640.33}
  This block is what the transaction extraction is checked against, so a value
  you calculated rather than read would defeat the check entirely.

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

ONE ACCOUNT ONLY. Some banks print every account the customer holds one after
another with no page break, so a page can end one register and begin the next.
The slice is cut at the account headers, but if a line introducing a DIFFERENT
account number than the one named below still appears, stop there: report
nothing at or after it. Rows belonging to another account are that account's,
and filing them here puts one person's money in another account's history.

Return ONLY a valid JSON object of the form
{"transactions": [ ... ], "securities_transactions_omitted": 0}, in printed
order. The count is for brokerage accounts and is 0 everywhere else. Each
transaction has:
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

BROKERAGE, RETIREMENT, INSURANCE AND ANNUITY ACCOUNTS — A DIFFERENT RULE ABOUT
WHAT COUNTS. This applies ONLY when the account type stated above is one of
<<CASH_FLOW_TYPES>>. On those, extract money that CROSSED THE ACCOUNT BOUNDARY
and nothing else.

A purchase of stock is not money moving. It exchanges cash for shares inside
one account, and the account is worth what it was a second earlier. So is a
sale, a redemption, a maturity, an exchange, a reinvestment, and a split.

  EXTRACT — value entered or left the account:
    * Cash in: deposit, transfer in, wire in, ACH credit, contribution, rollover
    * Securities transferred IN in kind, at the value the statement states
    * Dividends and capital gain distributions, whether paid in cash or
      reinvested. A reinvested dividend still ADDED value; the purchase that
      spent it is the part that nets to nothing.
    * Interest and sweep income
    * Cash out: withdrawal, transfer out, wire, ACH debit, check, EFT, card
    * Securities transferred OUT in kind, at the value the statement states
    * Margin interest charged, advisory, management and account fees
    * CHANGE IN MARKET VALUE, and ONLY when the statement prints it as a
      figure of its own — see the paragraph below.

  DO NOT EXTRACT — value only changed form:
    * Buy, sell, redeem, mature, exchange, conversion, reinvestment purchase
    * Stock split, spin-off, name change, CUSIP change
    * Journal entries between the account's own cash and margin balances
    * Cost basis, unrealized gain/loss shown PER HOLDING in a positions table

  Instead, count them. Report securities_transactions_omitted: how many buy,
  sell and similar rows you passed over on these pages. That number is how a
  reader knows the omission was deliberate. Report 0 if there were none.

CHANGE IN MARKET VALUE IS NOT ARITHMETIC YOU PERFORM. Report it only if the
statement PRINTS such a figure — "Change in Market Value", "Market
Appreciation/Depreciation", "Unrealized Gain/Loss for the Period" in a summary
of how the account value moved. Copy that number.

  * NEVER compute it as closing value minus opening value minus everything
    else. That number always makes the statement balance, which means it would
    hide a wire nobody extracted instead of revealing it.
  * If no such figure is printed, omit the line. A statement that does not
    reconcile is a statement a person will look at, which is the correct
    outcome.
  * Its description must be exactly "Change in Market Value". Sign it the way
    it moved the account: a gain POSITIVE, a loss NEGATIVE.

BALANCES ON A BROKERAGE STATEMENT ARE THE TOTAL ACCOUNT VALUE — cash plus
securities — not the cash or sweep balance. Take the opening and closing figures
from the portfolio or account-value summary. The cash balance alone cannot
reconcile against this extraction, because the purchases that moved cash into
securities are deliberately not in it.

A POLICY OR ANNUITY STATEMENT HAS NO REGISTER, AND ITS SUMMARY IS THE ACTIVITY.
Cash-value life insurance and annuities print no transaction list at all. The
"Value of your policy" / "Value of your account" block IS the activity, and is
the only place the statement says what moved:

    Beginning value                           $63,991.33
    Additions
      Premium payments                         $1,500.00
    Withdrawals
      Monthly deductions                        -$523.17
    Change in value
      Premium expense charges                    -$75.00
      Fixed and variable account results         $478.44
    Total change in value                        $403.44
    Ending value                              $65,371.60

  * Extract the LEAF lines and only those. Above, that is FOUR entries: premium
    payments, monthly deductions, premium expense charges, and the investment
    results.
  * NEVER extract a SUBTOTAL or a total. "Total change in value", "Net deposits
    & withdrawals", "Change in value" given as one figure — each restates lines
    you have already taken, and taking it as well double-counts.
  * Beginning value and ending value are BALANCES, not transactions.
  * The investment-results line IS the change in market value. Describe it
    "Change in Market Value" whatever the statement called it — "Fixed and
    variable account results", "Investment results", "Net investment gain".
  * Deductions, expense charges and cost of insurance are FEES: real money out.
    Extract them.
  * The "these blocks are NOT transactions" rule immediately below is about a
    summary that RESTATES a register printed elsewhere. On these statements
    there is no register, so it does not apply to this block.

These blocks are NOT transactions. Never turn one into an entry:
  - DAILY ENDING BALANCE, or any table of dates against running balances
  - ACCOUNT SUMMARY / SUMMARY OF ACCOUNTS, INTEREST RATE SUMMARY, OVERDRAFT SUMMARY
  - Checkbook reconciliation worksheets, check images, disclosures, notices
  - REWARDS or POINTS activity — "SHOP WITH POINTS ACTIVITY", "REWARDS
    ACTIVITY", "POINTS ACTIVITY", "MEMBERSHIP REWARDS", "CASH BACK ACTIVITY",
    and any page continuing one under "(CONTINUED)". A table with a REWARDS or
    POINTS column beside its dollar column is this block whatever it is called.
    It restates purchases already listed in the account activity to report the
    points on them, so extracting it charges the card twice for one purchase.
    Its sub-headings are the words a real register uses — "PURCHASES AND
    REDEMPTIONS", "RETURNS AND OTHER CREDITS" — so judge the block, not the row.
    THESE PAGES MAY BE THE ONLY THING ON THIS CHUNK. A chunk holding nothing but
    a rewards block correctly returns no transactions at all.
A date column beside an amount column does not make a table a register. Ask
whether the row moved the balance. A check did; a running balance did not, and
neither did a purchase counted again for its points.

FLAGS — record every inference. severity "info" for a routine derivation, "warn"
for something a human should look at. Codes: YEAR_INFERRED, LOCATION_INFERRED,
SIGN_ASSUMED, AMOUNT_UNCLEAR, DESCRIPTION_TRUNCATED.

If these pages carry no transactions at all, return
{"transactions": [], "securities_transactions_omitted": 0}.
Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


# Above this many pages a document is read in passes rather than in one call.
# Chosen from where the single call actually fails: a 27-page statement with 286
# entries came back with 38 of them, well-formed and 87% short, having used a
# seventh of the output budget it was given. It did not run out of room — it
# stopped. Below this, one call is both reliable and cheaper.
# Past this, a PDF is more likely to be a production than a single statement.
# Only a warning: a combined statement legitimately runs long, and one holding
# five accounts is ordinary input.
# ── Fill the prompt sentinels from the enums ────────────────────────────────
#
# Done once, here, rather than per call: the values cannot change at runtime and
# a prompt rebuilt per request is a prompt that can differ between two requests.
# `tests/test_statement_prompts.py` asserts no sentinel survives and that every
# AccountType reaches the model.
_STATEMENT_SYSTEM = _STATEMENT_SYSTEM.replace("<<ACCOUNT_TYPES>>", _ACCOUNT_TYPES)
_METADATA_SYSTEM = _METADATA_SYSTEM.replace("<<ACCOUNT_TYPES>>", _ACCOUNT_TYPES)
_TRANSACTIONS_SYSTEM = (
    _TRANSACTIONS_SYSTEM
    .replace("<<ACCOUNT_TYPES>>", _ACCOUNT_TYPES)
    .replace("<<CASH_FLOW_TYPES>>", _CASH_FLOW_NAMES)
)

_MANY_PAGES = 30

# ── THE HARD LIMITS. Past these an upload is REFUSED, not warned about ───────
#
# The warning below was not enough, and the way it failed is worth recording: a
# 700-page PDF holding 122 statements was uploaded, the survey said so — "48
# first pages in the first 250" — and the import went ahead anyway because the
# survey is advice. It was a total loss, and an expensive one: hours of model
# calls, a mess to unpick, and the statements that did import were not
# trustworthy enough to keep.
#
# A warning is the right answer to a document that is PROBABLY wrong. It is the
# wrong answer to one that cannot work: at this size the index is windowed
# across hundreds of pages, every window costs a call, the walk costs more, and
# a single bad seam mis-files months of transactions against the wrong account.
# Nothing downstream can recover from an input nobody should have accepted.
#
# So the limits are a gate, and they are deliberately well below what the
# pipeline can technically survive. Splitting is not a workaround here — a stack
# of monthly files is read SEVERAL AT A TIME by the worker pool, where one
# combined PDF is read in sequence, so the split version finishes sooner as well
# as more accurately.
#
# Both are module constants rather than settings, like `_MANY_PAGES`: a limit
# somebody can raise under pressure is a limit that gets raised at the moment it
# is doing its job.
_MAX_UPLOAD_PAGES = 50
_MAX_UPLOAD_STATEMENTS = 8

_PAGE_MARKER_PREFIX = "<<<PAGE "
_PAGE_MARKER_AT = re.compile(r"<<<PAGE (\d+)>>>")


def _squash_institution(name: Optional[str]) -> str:
    """A bank name reduced to letters, for asking whether two spellings agree."""
    squashed = re.sub(r"[^A-Z0-9]+", "", (name or "").upper())
    # "Unknown institution" is the absence of an answer, not an answer, and two
    # statements that both failed to read the letterhead have not agreed.
    return "" if squashed.startswith("UNKNOWN") else squashed


def _page_of_offset(raw_text: str, offset: int) -> Optional[int]:
    """
    Which page a character offset falls on, by the last marker before it.

    Needed because a statement can begin partway down a page: its text then
    carries no marker of its own, and everything up to the next one would be
    attributed to the following page — off by one, precisely at the seam where
    two accounts meet.
    """
    found = None
    for mark in _PAGE_MARKER_AT.finditer(raw_text, 0, offset + 1):
        found = int(mark.group(1))
    return found


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


class UnparseableResponse(ValueError):
    """
    A model answer that would not parse, and which kind of not-parsing it was.

    The two kinds want opposite responses, which is the whole reason this
    carries a flag rather than being a bare ValueError:

    * **Truncated** — the JSON ends mid-structure, so the parser ran out of
      input. Asking the same question again produces an answer the same length
      and it truncates again. The answer is to ask for less.
    * **Malformed** — a bad escape, a stray character, a duplicated key. The
      answer arrived whole and one character of it is wrong, which a second
      sample usually fixes.

    Told apart by position: a decode error whose offset is the end of the string
    means the parser consumed a complete value and then found nothing where a
    delimiter had to be. That cannot happen to a complete answer.
    """

    def __init__(self, message: str, truncated: bool):
        super().__init__(message)
        self.truncated = truncated


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


def _count(value: Any) -> int:
    """A non-negative integer from a model's answer, or zero.

    Used for the number of securities trades a brokerage pass was told to skip.
    A junk value must read as "none reported" rather than raise: the count is
    reassurance for a reader, and losing it should never cost the extraction.
    """
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _page_number(value: Any) -> Optional[int]:
    """A page number, or None. Zero and negatives are model noise, not pages."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _page_ranges(numbers: list[int]) -> str:
    """``[3, 4, 5, 9]`` as ``"3-5, 9"`` — how a person reads a page list."""
    if not numbers:
        return ""
    spans: list[tuple[int, int]] = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], number)
        else:
            spans.append((number, number))
    return ", ".join(
        str(first) if first == last else "%d-%d" % (first, last) for first, last in spans
    )


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

# ── A LIABILITY ACCOUNT'S SUMMARY WORDS MEAN THE OPPOSITE SIDE ───────────────
#
# Amounts are signed by their effect on the printed balance, so on a credit card
# a PURCHASE is positive (it raises what is owed) and a PAYMENT is negative.
# The word lists above are a bank's vocabulary, where both are the other way
# round -- and `_DEBIT_WORDS` carries "purchase" and "payment" for exactly that
# reason.
#
# Applied to a card, that read Chase's printed "Purchases $244.11" as the DEBIT
# total, compared it against the extracted negative lines (a card with no
# payments has none), and reported INCOMPLETE_EXTRACTION "short by 244.11" on a
# statement whose three purchases sum to 244.11 and which reconciled to the
# cent. It fired on essentially every card statement carrying purchases, which
# is the shape of warning that teaches people to ignore the next real one.
#
# Same failure as the RiverSource `premium_payments` case in `_printed_side`,
# one layer up: there the sign settles it where the document signs its figures,
# but a card prints its summary as magnitudes, so the words are all there is.
# What decides is the ACCOUNT TYPE, which the caller knows. The FIS makes the
# same inversion for the same reason and nowhere else (`household_amount()`).
_LIABILITY_ACCOUNTS = frozenset({AccountType.credit_card, AccountType.loan})

#: Words marking what raises a liability balance. "charge" catches both
#: "fees_charged" and "interest_charged"; "advance" catches a cash advance.
_LIABILITY_UP_WORDS = ("purchase", "charge", "advance", "fee", "interest", "debit")
#: Words marking what reduces it. A payment and a credit are the same side here.
_LIABILITY_DOWN_WORDS = ("payment", "credit", "refund")

# A total is only worth comparing when it is more than rounding apart. Statements
# print to the cent, so anything above this is a real difference.
_TOTAL_TOLERANCE = Decimal("0.01")


def _printed_side(printed: dict[str, Any], words: tuple[str, ...],
                  against: tuple[str, ...],
                  want_sign: int = 0) -> Optional[Decimal]:
    """
    Pull one side's printed figure out of whatever the statement called it.

    :param words: Substrings that mark the side wanted.
    :param against: Substrings that mark the other side, checked first — a key
        like "checks_debits" contains both "check" and "debit", and
        "deposits_credits" contains "deposit"; whichever list matches more
        specifically wins, so the other side is excluded explicitly.
    :param want_sign: ``-1`` for the debit side, ``1`` for the credit side, or
        ``0`` to match on the words alone.

    **THE WORDS ALONE GET THE SIDE WRONG, BECAUSE "PAYMENT" CHANGES DIRECTION
    BETWEEN DOCUMENTS.** On a bank statement a payment is money leaving — a card
    payment, a bill payment — which is why it sits in ``_DEBIT_WORDS``. On an
    insurance or annuity statement a *premium payment* is money arriving, and it
    is usually the largest credit on the page. An Ameriprise/RiverSource policy
    printing ``premium_payments: 1500`` therefore matched as the printed DEBIT
    total, was compared against $598.17 of real deductions, and reported a
    $901.83 shortfall on a statement that reconciled to the cent. A warning that
    fires on every correct import is the kind nobody reads, so the next real
    ``INCOMPLETE_EXTRACTION`` would have been dismissed with it.

    **The statement's own sign settles it where the statement bothered to give
    one.** A bank prints its summary buckets as magnitudes — "262 Checks/Debits
    195,600.04" — so nothing there is negative and the words are all there is.
    A policy prints ``monthly_deductions: -523.17``: signed, and the sign is the
    document saying which side it means. So signedness is detected per dict
    rather than assumed, and only a dict that actually carries a negative
    switches to reading signs. That keeps every bank statement on exactly the
    behaviour it has today.
    """
    entries = [(str(key).lower(), _money(value))
               for key, value in (printed or {}).items()]
    # Does this document sign its own totals? One negative is proof; a bank's
    # all-positive magnitudes are not evidence either way, so they fall through
    # to the word matching unchanged.
    signed = any(amount is not None and amount < ZERO for _name, amount in entries)

    for name, amount in entries:
        if amount is None:
            continue
        if signed and want_sign and (amount * want_sign) < ZERO:
            # Its own sign says it belongs to the other side, whatever it is
            # called. This is what stops `premium_payments` being read as a
            # debit total.
            continue
        if any(w in name for w in against):
            continue
        if any(w in name for w in words):
            return abs(amount)
    return None


def _completeness_findings(
    lines: list[dict[str, Any]],
    printed_totals: dict[str, Any],
    printed_counts: dict[str, Any],
    account_type: Any = None,
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

    **THE WORDS THAT NAME A SIDE DEPEND ON THE ACCOUNT TYPE.** On a deposit
    account a deposit is positive and a purchase negative; on a credit card or a
    loan both are reversed, because an amount is signed by its effect on the
    balance the institution prints. Matching a card's printed "Purchases" with
    the bank vocabulary reported a shortfall equal to the whole statement, on
    every card import. See ``_LIABILITY_UP_WORDS``.

    :param account_type: The account's type. ``None`` reads as a deposit
        account, which is what every caller before liability accounts were
        handled meant.
    :return: One sentence per side that comes up short. Empty when the
        extraction covers what is printed, and empty when the statement printed
        nothing to check against.
    :rtype: list[str]
    """
    amounts = [_money(line.get("amount")) or ZERO for line in lines]
    got_positive = [a for a in amounts if a > 0]
    got_negative = [a for a in amounts if a < 0]

    if account_type in _LIABILITY_ACCOUNTS:
        # "Charge" and "payment" rather than "credit" and "debit": the sentence
        # has to read true of the document somebody is holding.
        sides = (
            ("charge", got_positive, _LIABILITY_UP_WORDS, _LIABILITY_DOWN_WORDS, 1),
            ("payment", got_negative, _LIABILITY_DOWN_WORDS, _LIABILITY_UP_WORDS, -1),
        )
    else:
        sides = (
            ("credit", got_positive, _CREDIT_WORDS, _DEBIT_WORDS, 1),
            ("debit", got_negative, _DEBIT_WORDS, _CREDIT_WORDS, -1),
        )

    findings: list[str] = []
    for label, got, total_words, against, want_sign in sides:
        count_words = total_words
        printed_total = _printed_side(printed_totals, total_words, against, want_sign)
        # Counts are populations, never signed, so they match on words alone.
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


# The prompt asks for the description "Change in Market Value", but a model
# sensibly sometimes copies the heading the statement actually printed, and
# brokerages print all of these for the same figure.
_MARKET_CHANGE = (
    "change in market value",
    "unrealized gain",
    "unrealized loss",
    "market appreciation",
    "market depreciation",
    # Insurance and annuity wording. RiverSource prints "Fixed and variable
    # account results"; others print "Investment results" or "Change in value".
    #
    # UNDER-matching is the safe direction and over-matching is not. A line
    # classified as market change is EXCLUDED from the flow totals compared
    # against the statement's own summary, so a wrong match weakens the check
    # that finds a missing withdrawal. A missed match only inflates the
    # extracted flows, and that comparison reports shortfalls only.
    "account results",
    "investment results",
    "change in value",
)


def _is_market_change(line: dict[str, Any]) -> bool:
    """
    Whether an extracted line is the printed change-in-value figure.

    Used to keep it out of the flow totals compared against the statement's own
    summary: it is not money that crossed the account boundary, and letting it
    count as one would paper over the missing withdrawal that check exists to
    find.
    """
    text = (line.get("description") or "").strip().lower()
    return any(phrase in text for phrase in _MARKET_CHANGE)


def _portfolio_findings(
    lines: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[str]:
    """
    Check a brokerage extraction against the statement's own value summary.

    **This is the only real check a brokerage statement gets, and the reason it
    is not optional.** The ordinary one — beginning + sum == ending — closes as
    soon as the change-in-market-value line is present, whatever that line says.
    A model that computed the figure instead of reading it would produce a
    statement that reconciles perfectly and conceals a missing $50,000 wire.

    So the flows are compared against what the statement prints for itself, the
    same two-source argument ``_completeness_findings`` makes on a bank
    statement: additions against the money we found coming in, withdrawals
    against the money we found going out.

    Reported only where the extraction falls SHORT of the printed figure, never
    where it exceeds it. A statement may split its additions across named
    buckets, so extracting all of them correctly can read as too many against
    one; a shortfall has no innocent explanation.
    """
    if not summary:
        return []

    findings: list[str] = []
    flows = [line for line in lines if not _is_market_change(line)]
    for key, want_sign, label in (
        ("additions", 1, "additions"),
        ("withdrawals", -1, "withdrawals"),
    ):
        printed = _money(summary.get(key))
        if printed is None:
            continue
        printed = abs(printed)
        found = sum(
            (abs(_money(line.get("amount")) or ZERO) for line in flows
             if (_money(line.get("amount")) or ZERO) * want_sign > 0),
            start=ZERO,
        )
        if found < printed - _TOTAL_TOLERANCE:
            findings.append(
                "the summary prints %s of %s and only %s was extracted"
                % (label, printed, found)
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
        statements = self._index_document(pages, passes)
        LOGGER.info(
            "statement_service.extract: %d page(s), %d statement(s) indexed; reading in passes",
            len(pages), len(statements),
        )

        last_page = max(pages)
        for position, statement in enumerate(statements):
            first = _page_number(statement.pop("first_page", None)) or 1
            last = _page_number(statement.pop("last_page", None)) or last_page
            first, last = max(1, min(first, last_page)), max(1, min(last, last_page))

            # Cut this statement out at its printed header, so a page shared
            # with the account before or after carries only this account's rows
            # into the pass. On a document where every account starts on a fresh
            # page this changes nothing; on a combined statement it is the whole
            # difference between filing a transaction correctly and filing it
            # against the neighbouring account.
            own = self._statement_pages(raw_text, pages, statements, position)
            if own is not None:
                # Header slicing knows the statement's real extent: it read it
                # out of the text rather than being told. So it also OVERRIDES
                # the declared range — an index that under-reports last_page
                # would otherwise leave a page nobody ever asks for, and lines
                # printed on it would be missing with nothing to say so.
                first, last = min(own), max(own)
                own_last = last
            else:
                # No findable header. Fall back to the declared page range, and
                # cap the lookahead there too — otherwise a slice reads into a
                # document this statement has no claim on.
                own, own_last = pages, last
                if len(statements) > 1:
                    LOGGER.warning(
                        "statement_service.extract: statement %d of %d gave no findable "
                        "header; falling back to its page range, which cannot separate "
                        "two accounts sharing a page",
                        position + 1, len(statements),
                    )
                    statement["header_not_found"] = True
            statement.pop("header_text", None)
            # What this statement actually accounts for, which is what makes a
            # page nobody claimed detectable at the end of the walk.
            statement["_pages_read"] = sorted(own)

            context = self._statement_context(statement)
            period = statement.get("period") or {}
            period_start = _parse_date(period.get("start_date"))
            period_end = _parse_date(period.get("end_date"))
            collected: list[dict[str, Any]] = []
            unread: list[int] = []
            skipped = 0
            for start in range(first, last + 1, _PAGES_PER_CHUNK):
                stop = min(start + _PAGES_PER_CHUNK - 1, last)
                # ONE UNREADABLE PASS MUST NOT COST THE WHOLE DOCUMENT.
                #
                # This used to propagate: a pass whose answer would not parse
                # failed the job, and an upload holding five accounts — four of
                # them read perfectly — committed nothing at all. The wasted
                # work is not the point. The point is that the person re-uploads
                # the same file having been given no reason to expect a
                # different outcome.
                #
                # `_read_pages` splits a too-long answer and, failing that,
                # hands back the pages it could not read. Those are flagged on
                # the statement, which is then held and will not reconcile —
                # three independent signals that it is short. The same trade
                # pdf_service makes with its unreadable marker: a visible
                # partial loss beats a silent total one.
                found, lost, passed_over = self._read_pages(
                    context, own, start, stop, own_last, passes)
                unread.extend(lost)
                skipped += passed_over
                if not found:
                    continue
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
                    self._audit_dates(found, own, start, stop, own_last, context, passes)

                LOGGER.info(
                    "statement_service.extract: pages %d-%d yielded %d transaction(s)",
                    start, stop, len(found),
                )
                collected.extend(found)

            # Carried on the statement so commit_document can flag it. Recorded
            # rather than raised: the caller decides what an incomplete read
            # means, and for a statement it means "hold this for a person".
            if unread:
                statement["unread_pages"] = sorted(set(unread))

            # Numbered here rather than by the model: it only ever saw a slice,
            # so every chunk would otherwise start again at one.
            for index_, line in enumerate(collected, start=1):
                line["line_no"] = index_
            statement["transactions"] = collected
            # A brokerage register that comes back with four lines where the
            # statement shows fifty rows looks exactly like a failed import.
            # This is what says the silence was deliberate.
            if skipped:
                statement["securities_transactions_omitted"] = skipped

        result: dict[str, Any] = {
            "statements": statements,
            "extraction": _provenance(passes),
        }
        # PAGES NOBODY CLAIMED ARE THE FAILURE THIS FILE MOST NEEDED TO REPORT.
        #
        # The index used to be handed the first 60,000 characters of the
        # document and nothing else, with no truncation notice: on a scanned
        # multi-month production that is roughly eight statements, and
        # everything past it was invisible. The import then reported success for
        # the statements it had found while months 9 onward were either absent
        # or swallowed by whichever statement's page range ran to the end of the
        # document. Windowing the index (`_index_document`) is the fix; this is
        # the check that says so when it still happens, for any reason.
        #
        # `SUSPECT_SPLIT` already catches statements that OVERLAP. This is the
        # opposite direction and needs its own answer: a gap.
        unclaimed = sorted(set(
            self._unclaimed_pages(pages, statements)
            + self._unindexed_statement_starts(pages, statements)
        ))
        for statement in statements:
            statement.pop("_pages_read", None)
        if unclaimed:
            result["unindexed_pages"] = unclaimed
            LOGGER.warning(
                "statement_service.extract: %d page(s) begin a statement that was not read, "
                "or belong to none that was: %s. Transactions printed there are NOT imported.",
                len(unclaimed), _page_ranges(unclaimed),
            )
        return result

    @staticmethod
    def _unindexed_statement_starts(
        pages: dict[int, str], statements: list[dict[str, Any]],
    ) -> list[int]:
        """
        Pages that begin a statement nobody indexed.

        **The unclaimed-page check cannot see the failure that actually
        happened.** When the index stopped early, the pages past it were not
        left unclaimed — the last indexed statement's slice ran to the next
        indexed header, which was nowhere, so it **absorbed** them. Every page
        had an owner and the gap check would have reported nothing, while months
        nine onward were being filed as part of month eight.

        What survives that is the document's own pagination. Almost every
        statement prints "Page 1 of N" and restarts it, so a page that restarts
        pagination and is not the first page of any indexed statement is a
        statement nobody read. Deterministic, free, and computed from the text
        rather than asked of a model — the same argument as Bates detection and
        account numbers.

        Page 1 of the document is excluded: it restarts by definition.
        """
        starts = {min(s.get("_pages_read") or [0]) for s in statements}
        return [
            number for number, text in sorted(pages.items())
            if number > 1 and number not in starts and _PAGE_ONE.search(text or "")
        ]

    @staticmethod
    def _unclaimed_pages(
        pages: dict[int, str], statements: list[dict[str, Any]],
    ) -> list[int]:
        """
        Pages of the document no indexed statement accounts for.

        A page carrying no readable text is **not** reported: a blank separator
        sheet or an unreadable scan is a different fact, already flagged by
        ``pdf_service``'s marker and by ``PAGE_UNREADABLE``, and reporting it
        here would make the warning fire on documents where nothing is missing.
        The marker is matched by its text, the way ``_unreadable_pages`` does,
        rather than importing it — ``pdf_service`` owns the string.
        """
        claimed: set[int] = set()
        for statement in statements:
            span = statement.get("_pages_read") or ()
            claimed.update(span)
        return [
            number for number, text in sorted(pages.items())
            if number not in claimed
            and len((text or "").strip()) >= _MIN_INDEXABLE_CHARS
            and "[[PAGE COULD NOT BE READ" not in (text or "")
        ]

    def _index_document(
        self, pages: dict[int, str], passes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Index a document in windows, so its length cannot hide the back of it.

        **One call used to index any document, clipped to
        ``_MAX_STATEMENT_CHARS``.** A scanned statement page yields two to four
        thousand characters, so 60,000 is fifteen to thirty pages — about eight
        monthly statements — and a two-year production was indexed as far as
        month eight and then stopped. Nothing said so: unlike the single-pass
        path, this one did not even tell the model it had been truncated, so
        there was no "[DOCUMENT TRUNCATED]" line in the prompt and no flag on
        the result. The import reported success for what it found. Reported from
        the field as *"eight months ingest, the rest has numerous errors"*, and
        the errors are the tail being absorbed by the last indexed statement.

        So the document is indexed in page windows that each fit the budget,
        and the statement lists are stitched. The windows **overlap** by
        ``_INDEX_WINDOW_OVERLAP`` pages, because a statement whose header sits
        at a boundary would otherwise be described by neither side as a whole.

        A single-window document takes exactly the call it always took, under
        the same ``index`` label, so nothing changes for the short documents
        this path already handled.

        :param pages: ``{page_number: text}`` for the whole document.
        :param passes: Provenance accumulator; one entry per call.
        :return: Stitched statement descriptions, ordered by first page.
        :rtype: list[dict[str, Any]]
        """
        windows = self._index_windows(pages)
        stitched: list[dict[str, Any]] = []
        for first, last in windows:
            body = "\n\n".join(
                "<<<PAGE %d>>>\n%s" % (number, pages[number])
                for number in range(first, last + 1) if number in pages
            )
            # A single page longer than the whole budget is the only way this
            # still clips. Say so in the prompt, which the old code did not.
            if len(body) > _MAX_STATEMENT_CHARS:
                body = body[:_MAX_STATEMENT_CHARS] + (
                    "\n\n[DOCUMENT TRUNCATED — report only the statements above]"
                )
            label = "index" if len(windows) == 1 else "index pages %d-%d" % (first, last)
            found = self._call_json(
                _METADATA_SYSTEM, body, "statements",
                "Could not index the document", passes=passes, label=label,
            ).get("statements") or []
            for statement in found:
                self._stitch_statement(stitched, statement, window=(first, last))

        stitched.sort(key=lambda s: _page_number(s.get("first_page")) or 0)
        if len(windows) > 1:
            LOGGER.info(
                "statement_service.extract: indexed %d page(s) in %d window(s), "
                "%d statement(s) after stitching",
                len(pages), len(windows), len(stitched),
            )
        return stitched

    @staticmethod
    def _index_windows(pages: dict[int, str]) -> list[tuple[int, int]]:
        """
        Page ranges to index, each inside the character budget.

        Built by accumulating pages until the next one would not fit, which
        keeps a window as large as it can be — fewer windows means fewer seams
        to stitch, and a statement described whole is described better than one
        described in halves.
        """
        numbers = sorted(pages)
        if not numbers:
            return []
        # Measured the way the prompt will see it, markers included.
        def cost(number: int) -> int:
            return len(pages[number]) + len("<<<PAGE %d>>>\n\n\n" % number)

        windows: list[tuple[int, int]] = []
        start = numbers[0]
        used = 0
        for number in numbers:
            size = cost(number)
            if used and used + size > _MAX_STATEMENT_CHARS:
                windows.append((start, number - 1))
                # Step back a little so a statement straddling the seam is seen
                # whole by this window, not split across two descriptions.
                start = max(start, number - _INDEX_WINDOW_OVERLAP)
                used = sum(cost(n) for n in range(start, number) if n in pages)
            used += size
        windows.append((start, numbers[-1]))
        return windows

    @staticmethod
    def _statement_identity(statement: dict[str, Any]) -> tuple[tuple[str, str], tuple[Any, Any]]:
        """A statement's account identity and its period, as the stitch compares them."""
        account = statement.get("account") or {}
        period = statement.get("period") or {}
        return (
            (
                _squash_institution(account.get("institution")) or "",
                (account.get("account_number_last4") or "").strip(),
            ),
            (period.get("start_date"), period.get("end_date")),
        )

    @classmethod
    def _stitch_statement(
        cls,
        stitched: list[dict[str, Any]],
        statement: dict[str, Any],
        window: tuple[int, int],
    ) -> None:
        """
        Add a window's statement to the list, merging it with its other half.

        Overlapping windows mean the same statement is often described twice,
        and the two descriptions are not equally good: the window that saw the
        header knows the institution and the opening balance, the one that saw
        the end knows the closing balance and the last page. **Merging keeps the
        first non-null answer for every field and the widest page range**, which
        is better than either description alone and better than picking one and
        throwing the other away.

        Three ways two descriptions are judged to be the same statement, in
        decreasing order of how much they prove:

        1. **Same account and same period.** A statement seen twice.
        2. **Same period, and the page ranges touch.** One window could not read
           the account number; the period plus a shared page is as far as the
           evidence goes.
        3. **A FRAGMENT that touches.** A description carrying neither an
           account nor a whole period is not a statement in its own right — it
           is the middle of one, seen by a window that started after its header.
           A statement longer than the overlap produces exactly this, and
           leaving it alone would commit it as a second account with no
           institution, which is worse than attaching it to the statement whose
           pages it continues.

        Nothing else is merged. Two real statements fused into one would lose a
        month, and that is the failure this whole path exists to prevent.
        """
        first = _page_number(statement.get("first_page"))
        last = _page_number(statement.get("last_page"))
        identity, span = cls._statement_identity(statement)
        is_fragment = not any(identity) and not all(span)

        for existing in stitched:
            other_identity, other_span = cls._statement_identity(existing)
            other_first = _page_number(existing.get("first_page"))
            other_last = _page_number(existing.get("last_page"))
            touching = (
                None not in (first, last, other_first, other_last)
                and first <= other_last and other_first <= last  # type: ignore[operator]
            )
            same_account = any(identity) and identity == other_identity
            same_period = all(span) and span == other_span
            other_is_fragment = not any(other_identity) and not all(other_span)

            if not (
                (same_account and same_period)
                or (same_period and touching)
                or (is_fragment and touching)
                or (other_is_fragment and touching and any(identity))
            ):
                continue

            for key, value in statement.items():
                if value in (None, "", [], {}):
                    continue
                if existing.get(key) in (None, "", [], {}):
                    existing[key] = value
            if first is not None:
                existing["first_page"] = min(first, other_first if other_first is not None else first)
            if last is not None:
                existing["last_page"] = max(last, other_last if other_last is not None else last)
            return

        statement.setdefault("first_page", window[0])
        stitched.append(statement)

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

    def _read_pages(
        self,
        context: str,
        own: dict[int, str],
        start: int,
        stop: int,
        own_last: int,
        passes: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[int], int]:
        """
        The transactions on a page range, splitting the range if the answer
        will not fit in one.

        A statement's density is not knowable in advance. `_PAGES_PER_CHUNK` is
        a guess that holds for most documents and fails on a dense one: two
        pages of a Capital One 360 register produced about forty entries and
        seventeen thousand characters of JSON, which came back cut off
        mid-array. The budget was nowhere near spent — the answer simply
        stopped.

        `_call_json` asks again first, and on the measured document that was
        enough both times it happened. Splitting is what remains when it is not:
        halve the range and ask each half, down to a single page. That is the
        rule the codebase already follows when an extraction comes back short —
        **split the work instead** — and it is a last resort rather than a first
        one because it costs a call per half and the cheaper fix usually works.

        A single page that still will not read cannot be divided any further,
        and is returned as unread rather than raising: when a range splits, one
        half failing must not discard the half that succeeded. The caller flags
        whatever comes back unread, so the loss is reported page by page instead
        of chunk by chunk.

        The third return value is how many securities trades the pass was told
        to skip. On a brokerage account the extraction deliberately omits buys
        and sells, and a register that comes back with four lines where the
        statement shows fifty rows looks exactly like a failed import. The count
        is what says the silence was on purpose.

        :return: ``(transactions, unread_pages, securities_omitted)``.
        :rtype: tuple[list[dict[str, Any]], list[int], int]
        """
        body = self._slice_body(own, start, stop, own_last)
        if not body.strip():
            return [], [], 0
        try:
            answer = self._call_json(
                _TRANSACTIONS_SYSTEM,
                "%s\n\nPRIMARY PAGES: %d to %d.\n\n%s" % (context, start, stop, body),
                "transactions",
                "Could not read pages %d-%d" % (start, stop),
                passes=passes, label="pages %d-%d" % (start, stop),
            )
            found = answer.get("transactions") or []
            omitted = _count(answer.get("securities_transactions_omitted"))
        except UnparseableResponse as e:
            if stop <= start:
                LOGGER.warning(
                    "statement_service.extract: page %d could not be read (%s); it is the "
                    "smallest slice there is, so its lines are missing from this statement",
                    start, str(e),
                )
                return [], [start], 0
            if not e.truncated:
                # Malformed rather than too long, and `_call_json` already asked
                # again. Splitting would not help; report the range.
                LOGGER.warning(
                    "statement_service.extract: pages %d-%d could not be read (%s)",
                    start, stop, str(e),
                )
                return [], list(range(start, stop + 1)), 0
            middle = start + (stop - start) // 2
            LOGGER.info(
                "statement_service.extract: pages %d-%d answered past the end of the reply; "
                "splitting into %d-%d and %d-%d",
                start, stop, start, middle, middle + 1, stop,
            )
            left, left_unread, left_skipped = self._read_pages(
                context, own, start, middle, own_last, passes)
            right, right_unread, right_skipped = self._read_pages(
                context, own, middle + 1, stop, own_last, passes)
            return (left + right, left_unread + right_unread, left_skipped + right_skipped)

        # A slice can only report what it was given; anything anchored in its
        # lookahead belongs to the next one. Applied per sub-range as well as
        # per chunk, or a split half would claim its sibling's rows.
        return [
            line for line in found
            if (_page_number(line.get("physical_page_number")) or start) <= stop
        ], [], omitted

    @staticmethod
    def _statement_pages(
        raw_text: str,
        pages: dict[int, str],
        statements: list[dict[str, Any]],
        index: int,
    ) -> Optional[dict[int, str]]:
        """
        One statement's own text, cut at the account headers around it.

        **The page is not always the unit of division.** A combined statement —
        Capital One 360 prints one — runs each account's register straight into
        the next with no page break, so a single page can close one account,
        contain the whole of a short one, and open a third. Slicing by page then
        hands the pass reading account A a page that ends with account B's
        opening balance, under a context line that says "these pages belong to"
        account A. It is a false statement and the model reasonably believes it:
        one production filed B's first transactions against A, on every account
        after the first.

        Two statements sharing a page is not an edge case for that format, it is
        every page. So the boundary is the printed header, found literally in
        Python — the same argument as Bates numbers and account numbers. A header
        is a line the bank prints; locating it is exact, free, and auditable,
        where asking a model to judge where one register stops is neither.

        :param index: Which statement in ``statements`` to cut out.
        :return: ``{page_number: text}`` holding only this statement's text, with
            page markers preserved so a line still reports the page it was
            printed on. None when the header cannot be found verbatim, which
            leaves the caller on the page-range behaviour and flags it.
        :rtype: Optional[dict[int, str]]
        """
        header = (statements[index].get("header_text") or "").strip()
        if not header:
            return None
        start = raw_text.find(header)
        if start == -1:
            return None

        # Where the NEXT statement's header begins, searched from after this one
        # so a repeated header on a later page cannot cut the body short.
        stop = len(raw_text)
        for other in statements[index + 1:]:
            candidate = (other.get("header_text") or "").strip()
            if not candidate:
                continue
            at = raw_text.find(candidate, start + len(header))
            if at != -1:
                stop = min(stop, at)

        body = raw_text[start:stop]
        # The body starts at the header, which may sit halfway down a page, so
        # it usually has no page marker of its own. Without one the first lines
        # would be attributed to whatever page the marker search finds next —
        # off by one, on the page where the mis-attribution began.
        opening = _page_of_offset(raw_text, start)
        if opening is not None and not body.lstrip().startswith(_PAGE_MARKER_PREFIX):
            body = "<<<PAGE %d>>>\n%s" % (opening, body)

        own = bates_service.split_pages(body)
        return {n: text for n, text in own.items() if n in pages} or None

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

        **A malformed answer is asked again once.** `llm_service` fails a
        candidate over on an *exception* — a network fault, a refusal, an empty
        body — and deliberately never retries the same candidate. A response
        that arrived intact and is simply not valid JSON is a different event:
        nothing failed, the vendor answered, and one character of the answer is
        wrong. That is what a second sample fixes, and on a real ingest a single
        unparseable chunk out of seven was enough to lose a five-account
        document entirely. The retry lives here rather than in `llm_service`
        because only the caller knows the answer had to parse.
        """
        attempt_note = ""
        for attempt in (1, 2):
            result = llm_service.complete_detailed(
                system, body + attempt_note, profile="extract_account_statement",
            )
            if passes is not None:
                passes.append({
                    "pass": ("%s (retry)" % (label or expect)) if attempt > 1 else (label or expect),
                    "vendor": result.vendor,
                    "model": result.model,
                    # Above 1 means the preferred model did not answer. Worth
                    # keeping: it is the difference between "Claude read this"
                    # and "Claude was asked and something else ended up reading
                    # it".
                    "attempts": result.attempts,
                })
            try:
                parsed = json.loads(_strip_fences(result.text))
            except json.JSONDecodeError as e:
                # The response itself is never logged: it is a register of
                # transactions, and §6 forbids amounts and payee names in logs.
                # The position and length are enough to tell a truncation from a
                # bad escape, and the full text is recoverable from the model
                # provenance if it ever matters.
                body_text = _strip_fences(result.text or "")
                truncated = getattr(e, "pos", -1) >= len(body_text)
                LOGGER.warning(
                    "statement_service: %s — attempt %d %s at char %s of %d: %s",
                    failure, attempt,
                    "response truncated" if truncated else "parse failure",
                    getattr(e, "pos", "?"), len(body_text), e.msg,
                )
                if attempt == 2:
                    raise UnparseableResponse(
                        "%s — the model's response was not valid JSON" % failure,
                        truncated=truncated,
                    ) from e
                # BOTH KINDS GET A SECOND ASK, AND IT IS NOT A RE-ROLL.
                #
                # The reasoning that says otherwise — "a truncated answer asked
                # again comes back the same length" — sounds right and is
                # wrong in practice, twice out of two on the document this was
                # written for. The reason is that the note below CHANGES THE
                # QUESTION: this profile runs at temperature 0.0, so a second
                # call on an identical prompt really would return the identical
                # reply, and the only thing making the retry worth anything is
                # that the prompt is no longer identical.
                #
                # So the note has to name the failure it is answering. Telling a
                # model that ran out of room to escape its quotes more carefully
                # addresses nothing.
                attempt_note = (
                    (
                        "\n\nYOUR PREVIOUS ANSWER WAS CUT OFF before it finished and was "
                        "discarded — it ended in the middle of the JSON. Return the same "
                        "transactions again, complete and closed, keeping every description "
                        "to what is printed and omitting nothing."
                    ) if truncated else (
                        "\n\nYOUR PREVIOUS ANSWER WAS NOT VALID JSON and was discarded. Return "
                        "the same data again as a single valid JSON object. Escape every quote "
                        "and backslash inside a string value, and emit no text outside the "
                        "object."
                    )
                )
                continue
            if not isinstance(parsed, dict) or expect not in parsed:
                if attempt == 1:
                    attempt_note = (
                        "\n\nYOUR PREVIOUS ANSWER did not contain the required \"%s\" key. "
                        "Return a single JSON object with it." % expect
                    )
                    continue
                raise ValueError("%s — the response had no '%s'" % (failure, expect))
            return parsed
        raise ValueError(failure)  # unreachable; the loop returns or raises

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
        from services.pdf_service import pdf_service  # noqa: PLC0415


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
        # Whether this document holds exactly one statement. It decides whether
        # the whole document may be searched for an account number when the
        # statement's own page span is unknown — on a two-statement PDF that
        # would find the OTHER account's number and file this statement against
        # it, which is worse than leaving it unmatched.
        only_statement = len(extracted.get("statements") or []) == 1
        # Loaded once for the document: the table is small but the round trip is
        # not free, and the rules cannot change part-way through an import.
        #
        # A failure here must never fail the ingest. The statement is the
        # evidence; filing it by keyword is a convenience laid on top, and the
        # table may not even exist yet on a node where 032 has not been applied.
        # Logged rather than swallowed, because rules that quietly stop firing
        # would look exactly like rules nobody wrote.
        try:
            rules = category_rule_service.rules_for(manager, matter_id)
        except Exception as e:  # noqa: BLE001 — convenience must not break evidence
            LOGGER.warning(
                "statement_service.commit: category rules unavailable (%s); "
                "transactions will be ingested unfiled", str(e),
            )
            rules = []
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
                    only_statement=only_statement,
                    rules=rules,
                    # A document-level fact, carried onto every statement it
                    # came with: the import is short, and which statement you
                    # happen to open should not decide whether you are told.
                    unindexed_pages=extracted.get("unindexed_pages") or None,
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
        only_statement: bool = True,
        rules: Optional[list[Any]] = None,
        unindexed_pages: Optional[list[int]] = None,
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

        # Which pages this statement was printed on. Needed here as well as
        # further down, because the account number is looked for on the pages
        # of THIS statement — a document holding two accounts would otherwise
        # have no number on every page and give up for no reason.
        statement_pages = sorted({p for p in (_page_number(l.get("physical_page_number")) for l in lines) if p})

        # 1. Account: match on institution + last four, or create.
        institution = (account_block.get("institution") or "").strip() or "Unknown institution"
        last4 = (account_block.get("account_number_last4") or "").strip() or None

        # The account number is a pattern — the one long digit run printed on
        # every page — and the extraction reads it only about half the time on
        # some bank forms. Institution plus last four is the dedup key, so a
        # coin flip there opens a fresh account per statement. See
        # account_number_service for the measurements behind this.
        document_pages = bates_service.split_pages(raw_text)

        # **A statement's transaction pages are not its extent.** Every Chase
        # transaction prints on page 1 and page 2 carries only disclosures, so
        # scoping to transaction pages showed the detector a single page — and a
        # single page cannot demonstrate repetition, which is the entire signal.
        # It then fell through to the lookup, which saw one page containing both
        # the account number and the barcode and chose the barcode, twelve times.
        # When the document holds one statement, the document IS the statement.
        if only_statement:
            scope: Optional[list[int]] = None
        elif statement_pages:
            scope = statement_pages
        else:
            # Several statements in one document and no page numbers on the
            # lines. Searching the whole document would find another statement's
            # account number and file this one against it.
            scope = []

        found = None
        if scope != []:
            found = account_number_service.detect(
                document_pages, only=scope, expected_last4=last4,
            )
            if last4 is None and (found is None or not found.unambiguous):
                # Repetition could not settle it and the extraction did not read
                # it. Put the single question to a fast model — the full prompt
                # asks for twenty fields and this one gets a line of its
                # attention. The answer is checked against the printed text, and
                # against how it behaves across pages, before it is believed.
                asked = account_number_service.ask(document_pages, only=scope)
                if asked is not None:
                    found = asked

        last4, number_flag = account_number_service.reconcile(last4, found)
        if number_flag:
            flags.append(number_flag)

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
                # Two Chase runs stored the literal string "Account Number:"
                # here — the model reached the label, found the value missing,
                # and returned the caption. A masked form always carries digits.
                account_number_masked=(
                    account_block.get("account_number_masked")
                    if account_number_service.looks_like_a_number(
                        account_block.get("account_number_masked"))
                    else None
                ),
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
                # THE ACCOUNT'S NAME, NOT THE EXTRACTION'S. This row said
                # "Unidentified institution" in the import log, which is the one
                # thing it certainly is not: the statement was identified well
                # enough to match an existing account, and that account is where
                # the authoritative name lives — read off the page, repaired by
                # `resolve_missing_institutions`, or corrected by a person. The
                # name this particular read produced may be null or a misread,
                # and reporting that instead told the reader nothing about which
                # of their accounts the upload duplicated.
                "institution": account.institution,
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
        cash_flow_only = account.account_type in _CASH_FLOW_ACCOUNTS
        summary = raw_statement.get("portfolio_summary") or {}
        # Read unconditionally: it is kept in the extraction record for every
        # statement, and only the CHECK below is account-type specific.
        printed_counts = raw_statement.get("printed_counts") or {}
        if cash_flow_only:
            # THE BANK-SHAPED CHECK CANNOT BE APPLIED HERE, and applying it
            # would be worse than not checking. Its counts and totals describe
            # every row the statement prints, including the fifty securities
            # trades this extraction deliberately leaves out — so it would
            # report a shortfall on every brokerage statement, and a warning
            # that is always on is a warning nobody reads.
            shortfalls = _portfolio_findings(lines, summary)
        else:
            shortfalls = _completeness_findings(
                lines, balances.get("printed_totals") or {}, printed_counts,
                # A card's summary words mean the opposite side. Without this,
                # "Purchases $244.11" read as the debit total and every card
                # import reported a shortfall equal to its own purchases.
                account_type=account.account_type)
        if shortfalls:
            flags.append(_flag(
                "INCOMPLETE_EXTRACTION", "warn",
                "This statement does not match the totals it prints for itself: %s. Extraction "
                "very likely stopped early — re-ingest before relying on it." % "; ".join(shortfalls),
                "transactions",
            ))

        omitted = _count(raw_statement.get("securities_transactions_omitted"))
        # ONLY WHEN SOMETHING WAS ACTUALLY OMITTED. The flag exists to explain
        # rows that are missing from the register -- "four lines here where the
        # statement shows fifty" looks exactly like a failed import, and the
        # count is what says the silence was deliberate. With nothing omitted
        # there is no silence to explain, and raising it anyway asserts an
        # omission that did not happen.
        #
        # It fired on every cash-value policy, which is where this showed:
        # a variable policy CAN hold sub-account investments -- that is what
        # "variable" means -- but one sitting 100% in its fixed account trades
        # nothing, so the count is zero and the register is complete.
        if cash_flow_only and omitted:
            flags.append(_flag(
                "SECURITIES_NOT_EXTRACTED", "info",
                "%d securities transaction(s) were present and deliberately not extracted. "
                "Buying, selling and redeeming move value between cash and securities "
                "inside one account without changing what it is worth, so they are not cash "
                "flow and are not recorded. What is recorded is money that crossed the account "
                "boundary, plus dividends, interest, fees, and the change in market value the "
                "statement prints." % omitted,
                "transactions",
            ))
        # ONLY WHEN THE STATEMENT ACTUALLY FAILS TO TIE. This flag's whole
        # claim is that a movement in value is unaccounted for, and it says
        # so in as many words -- "the balances will not tie". On a statement
        # that reconciles that sentence is simply FALSE, and a flag that
        # contradicts the row it sits on is worse than no flag: it teaches
        # the reader that the flags do not mean anything.
        #
        # A reconciled statement with no line we RECOGNISE as market change
        # is one of two harmless things: the figure was extracted under a
        # name `_MARKET_CHANGE` does not list, or the holdings did not move.
        # Either way nothing is missing, which is what reconciliation just
        # proved.
        if (cash_flow_only and not reconciled
                and not any(_is_market_change(line) for line in lines)):
            flags.append(_flag(
                "MARKET_CHANGE_NOT_PRINTED", "info",
                "No change-in-market-value figure was found on this statement, so any "
                "movement in the value of the holdings is not accounted for and the "
                "balances will not tie. The figure is never calculated here: one derived "
                "by subtraction would make every statement balance and would hide a "
                "withdrawal nobody extracted.",
                "balances",
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
        # A DEGRADED READ AND A PERIOD SUMMARY ARE NOT THE SAME FACT, and the
        # remedy separates them: "date them from the source page" is impossible
        # when the source page prints no dates.
        #
        # A register whose date column went wrong yields SOME dated lines and
        # some not -- that mixture is the degraded call this flag was built for.
        # EVERY line undated on a cash-flow account is the other shape: a policy
        # or annuity statement has no register at all, and its value-of-policy
        # block states period totals. Those figures never had dates; the
        # statement period is what bounds them.
        #
        # So the consequence is still reported -- an exhibit built by date range
        # will not contain them, which is worth knowing either way -- but it is
        # `info` rather than a `warn` that holds the statement in the exceptions
        # queue awaiting a correction nobody can make.
        summary_derived = cash_flow_only and lines and undated == len(lines)
        if undated and summary_derived:
            flags.append(_flag(
                "UNDATED_TRANSACTIONS", "info",
                "All %d figure(s) are period totals from the statement's own value summary, "
                "which prints no dates \u2014 this kind of statement has no transaction register. "
                "They count toward the balance and are bounded by the statement period, but a "
                "NULL date is excluded from every date-filtered search, so an exhibit built by "
                "date range will not contain them." % undated,
                "transaction_date",
            ))
        elif undated:
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
        # A pass whose answer would not parse, twice. The lines printed on those
        # pages are simply absent from this record, so it is said outright: the
        # statement is held, and reconciliation will disagree by whatever was on
        # them. Silence here would leave a short register looking complete.
        unread_pages = [p for p in (raw_statement.get("unread_pages") or []) if p]
        if unread_pages:
            flags.append(_flag(
                "PASS_UNREADABLE", "warn",
                "Page(s) %s were read but the extraction of them could not be parsed, twice. "
                "Any transaction printed there is missing from this statement, and the balances "
                "below will be short by that amount. Re-import the document; if it fails the "
                "same way again, those pages need reading by hand."
                % ", ".join(str(p) for p in unread_pages),
                "transactions",
            ))

        # The document could not be cut at this statement's printed header, so
        # its page range was used instead. Harmless on a document where every
        # account starts on a fresh page; on a combined statement it means the
        # neighbouring account's rows may have been swept in.
        if raw_statement.get("header_not_found"):
            flags.append(_flag(
                "STATEMENT_BOUNDARY_UNCERTAIN", "warn",
                "This statement's header could not be located in the document text, so it was "
                "separated from the others by page number alone. Where a bank prints several "
                "accounts on one page, lines belonging to the account before or after this one "
                "may have been filed here. Check the first and last few lines against the page.",
                "transactions",
            ))

        if unindexed_pages:
            # NOT this statement's defect -- the DOCUMENT's. Raised on every
            # statement from it because the alternative is an import that looks
            # complete: eight months accepted, sixteen never read, and nothing
            # on screen saying which. Warn-level, so the whole document is held
            # for review rather than auto-accepted.
            flags.append(_flag(
                "PAGES_NOT_INDEXED", "warn",
                "Page(s) %s of the source document begin a statement that was not read, or "
                "belong to none that was. Anything printed there is NOT in this record, and "
                "where such a page falls inside another statement's range its lines may have "
                "been filed against that statement instead. This usually means the document "
                "holds more statements than were found: check those pages, and split the PDF "
                "into one statement per file if they hold months of their own."
                % _page_ranges(unindexed_pages),
                "transactions",
            ))

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
        # page range is not this statement's problem. `statement_pages` was
        # computed before the account lookup, which needs the same span.
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
        # claimed? The usual cause is a summary table — a daily ending balance
        # list, an account summary — read as a second register, which then
        # invents its own account because the institution on it could not be
        # read either.
        #
        # **But a shared page is not by itself wrong.** The flag first shipped
        # saying "two statements cannot be printed on the same page", and that
        # is false for a combined statement: Capital One 360 runs each account's
        # register straight into the next, so one page routinely closes one
        # account and opens another, and page 5 of an eight-page document
        # carried three. Flagging that put every account on the upload into the
        # exceptions queue, which is how a useful warning stops being read.
        #
        # Two things separate a seam from a phantom, and both are required.
        #
        # HOW MUCH IS SHARED. A seam touches exactly one page — the last of one
        # register, the first of the next. A phantom re-covers its parent's
        # range, so it shares more.
        #
        # WHOSE BANK IT IS. A combined statement is ONE bank printing several of
        # its own accounts, so every account on it names the same institution. A
        # phantom arises where the letterhead could not be read, and invents an
        # account whose institution differs or is unknown — which is the same
        # signal `resolve_missing_institutions` already acts on, that
        # disagreement about the institution within one upload means at least
        # one answer is a guess. Requiring agreement is what keeps the guard
        # working on the case it was built for: a register on page 1 and a
        # balance table on page 3, read as "Unknown institution" beside the real
        # bank, still flags on one shared page.
        overlap = None
        if statement_pages and claimed_pages is not None:
            first, last = statement_pages[0], statement_pages[-1]
            for earlier in claimed_pages:
                if first > earlier["last"] or earlier["first"] > last:
                    continue
                shared = min(last, earlier["last"]) - max(first, earlier["first"]) + 1
                mine = _squash_institution(institution)
                theirs = _squash_institution(earlier.get("institution"))
                seam = (
                    shared == 1
                    and last4 and earlier.get("last4") and last4 != earlier.get("last4")
                    and mine and theirs and mine == theirs
                )
                if seam:
                    LOGGER.info(
                        "statement_service.commit: statement shares page %d with #%s — "
                        "a combined statement's seam, not a split",
                        max(first, earlier["first"]), earlier["statement_id"],
                    )
                    continue
                overlap = earlier
                break
        if overlap is not None:
            flags.append(_flag(
                "SUSPECT_SPLIT", "warn",
                "This covers page(s) %d–%d of the upload, which statement #%s (%s, %s to %s) already "
                "covers. More than a single shared boundary page means one of them is probably a "
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
            portfolio_summary=summary or None,
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
                # Carried so the next statement can tell a combined statement's
                # seam from a phantom register: a seam is one shared page
                # between two DIFFERENT accounts.
                "last4": last4,
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
                # Extraction still never sets category_id. The free-text
                # `category` below is the model's guess and remains a hint only:
                # it is unexplainable and varies run to run, which is no basis
                # for a figure on a sworn document.
                #
                # A keyword RULE is a different thing and may set it, applied
                # just below — written by the firm, deterministic, and
                # answerable in a sentence ("the description contains WALMART").
                # What keeps that defensible is the provenance travelling with
                # it: category_source says a rule filed the line, and
                # category_rule_id says which.
                category=(line.get("category") or None),
                physical_page_number=page,
                bates_number=bates,
                check_number=_check_number(line.get("check_number")),
                flags=line.get("flags") or [],
            ).model_dump() | category_rule_service.assignment(
                category_rule_service.match(
                    rules or [],
                    line.get("description"),
                    line.get("counterparty"),
                    _money(line.get("amount")) or ZERO,
                )
            ))

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

    def add_transaction(
        self,
        manager: DatabaseManager,
        statement_id: int,
        values: dict[str, Any],
        staff_id: int,
        staff_name: str,
        reason: Optional[str] = None,
    ) -> tuple[Any, Any]:
        """
        Add a line extraction did not produce, and say on the line that a person
        added it.

        **The reason this exists is that the alternative was an INSERT by hand.**
        A line the extraction missed entirely — a page the model stopped
        reading, a figure printed somewhere no pass looked — left the statement
        permanently out of balance, and the way it was being fixed was a row
        typed straight into the database at zero and then corrected through the
        app so the arithmetic closed. That works, and it leaves no record of
        itself anywhere a person reading the exhibit can see.

        **So the flag is the feature, not the insert.** Every added line carries
        ``MANUAL_ENTRY`` naming the person, the time, and their reason, which is
        the same promise ``correct_transaction`` makes: the figure that reaches
        an exhibit can say where it came from. An exhibit carries the Rule 1006
        notice asserting the underlying records are available for examination,
        and a line nobody can find on the production is precisely what that
        notice must not cover silently.

        **It is for a line that IS on the document and was missed.** Nothing
        here checks that — it cannot — which is why the flag reads as an
        assertion by a named person rather than as a note. The sign rule is
        unchanged (CLAUDE.md §11): an amount is signed by how it moves the
        balance the institution printed.

        ``line_no`` goes after the statement's last line rather than being
        inserted by date. ``line_no`` records the printed sequence, and a line
        that was not read off the page has no place in it; appending says so,
        where renumbering would quietly assert a position on the document.

        :param values: ``description`` and ``amount`` are required; every other
            key in ``_CORRECTABLE`` is accepted.
        :param reason: Why the line is being added — "printed on page 7, missed
            by the import". Recorded on the line with the person's name.
        :return: ``(transaction, statement)``, the statement re-reconciled. A
            manual line always moves the arithmetic, so it is never None.
        :raises ValueError: If the statement does not exist, or the required
            fields are missing.
        """
        transaction_repo = FinancialAccountTransactionRepository(manager)
        statement_repo = FinancialAccountStatementRepository(manager)

        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            raise ValueError("Statement not found")

        record: dict[str, Any] = {}
        for field in _CORRECTABLE:
            if field in values:
                record[field] = _coerce_field(field, values[field])

        description = (record.get("description") or "").strip()
        if not description:
            raise ValueError("A description is required: say what the line is.")
        if record.get("amount") is None:
            raise ValueError(
                "An amount is required. Sign it the way the statement moves the "
                "balance: money out is negative, money in is positive."
            )
        record["description"] = description

        # Existing lines include dropped ones: line numbers are positions, and
        # reusing one a soft-deleted line still holds would collide if it were
        # ever restored.
        existing = transaction_repo.get_by_statement(statement_id, include_deleted=True)
        record.update({
            "statement_id": statement_id,
            "financial_account_id": statement.financial_account_id,
            "line_no": max((t.line_no or 0 for t in existing), default=0) + 1,
            "flags": [{
                "code": "MANUAL_ENTRY",
                "severity": "info",
                "field_path": None,
                "note": "%s added this line by hand; it was not produced by the import.%s" % (
                    staff_name, (" Reason: %s" % reason) if reason else "",
                ),
                "by_staff_id": staff_id,
                "by": staff_name,
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason or None,
            }],
        })
        # date_provenance is left at its default: it distinguishes a year read
        # off the line from one inferred from the period, and neither describes
        # a figure somebody typed. MANUAL_ENTRY is what says where this came
        # from.
        created = transaction_repo.insert(record)
        LOGGER.info(
            "statement_service.add_transaction: statement=%s transaction=%s staff=%s",
            statement_id, created.id, staff_id,
        )
        # Always, not only when it closes the gap: a new line changes the sum,
        # so a stale delta on the statement would now be wrong in both
        # directions.
        return created, self._rereconcile(statement_repo, transaction_repo, statement_id)

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

    def correct_balances(
        self,
        manager: DatabaseManager,
        statement_id: int,
        beginning: Optional[Decimal],
        ending: Optional[Decimal],
        staff_id: int,
        staff_name: str,
        reason: Optional[str] = None,
    ) -> Any:
        """
        Correct a statement's opening or closing balance, with a name on it.

        **A MISREAD BALANCE HAS NO OTHER REPAIR, AND THE ALTERNATIVES ARE ALL
        WRONG.** The figure is written once, at ingest, so until now the only
        routes were a Retry -- which re-reads the same PDF through a
        ``temperature: 0.0`` profile and returns the same wrong number -- or an
        ``UPDATE`` typed into the database, which leaves nothing a reader of the
        exhibit can see. The third temptation is worse than either: editing
        TRANSACTIONS until the arithmetic closes falsifies lines to fix a
        header figure, and the statement then claims to reconcile while the
        balance printed on the page disagrees with the record.

        So the flag is the feature, exactly as with ``add_transaction``. A
        balance anchors the whole reconciliation and reaches an exhibit through
        the inventory's as-of figures, so each changed end carries
        ``BALANCE_MANUALLY_ENTERED`` with both values, the person, the time and
        their reason. Nothing checks that the new figure is the one printed on
        the page, because nothing can -- which is precisely why the record
        names somebody.

        ``review_status`` is deliberately untouched, like ``_rereconcile``:
        whether an exception clears is a decision, not a consequence of
        arithmetic.

        :param beginning: New opening balance, or None to leave it alone.
        :param ending: New closing balance, or None to leave it alone.
        :raises ValueError: Unknown statement, or nothing actually changed.
        :return: The statement, re-reconciled.
        """
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)
        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            raise ValueError("Statement %d not found" % statement_id)

        changes: dict[str, Any] = {}
        flags = list(statement.flags)
        for field, new in (("beginning_balance", beginning), ("ending_balance", ending)):
            if new is None:
                continue
            old = getattr(statement, field)
            # Decimal("100.00") == Decimal("100.0"), so an unchanged figure
            # typed in a different shape is correctly not a change.
            if old is not None and old == new:
                continue
            changes[field] = new
            flags.append(_flag(
                "BALANCE_MANUALLY_ENTERED", "info",
                "%s set to %s by %s%s. Extraction read %s." % (
                    field.replace("_", " "), new, staff_name,
                    " (%s)" % reason if reason else "",
                    "nothing" if old is None else old),
                "balances.%s" % field,
            ))
            flags[-1].update({"field": field, "from": None if old is None else str(old),
                              "to": str(new), "by": staff_name, "by_staff_id": staff_id})

        if not changes:
            raise ValueError("Nothing to change: both balances already hold those figures")

        changes["flags"] = flags
        statement_repo.update(statement_id, changes)
        # Re-read and re-reconcile rather than computing here: the balances have
        # moved, and _rereconcile is the one place that decides what UNRECONCILED
        # should say afterwards.
        return self._rereconcile(statement_repo, transaction_repo, statement_id)

    # ── Discarding a bad extraction ───────────────────────────────────────

    def set_bates(
        self,
        manager: DatabaseManager,
        statement_id: int,
        first: str,
        last: Optional[str],
        staff_id: int,
        staff_name: str,
    ) -> tuple[Any, int]:
        """
        Number a statement's pages by hand, when detection found no series.

        Rare, and it happens: a production stamped in a font the text layer
        renders as something else, or a scan where the stamp fell outside the
        page. Retrying does not help — the same document reads the same way —
        so the numbers are typed once and applied here.

        **Sequential across the PAGES WE HOLD, not across page numbers.** A
        partial production arrives as physical pages 1, 2 and 4, and its stamps
        run consecutively over those three sheets: the missing sheet took its
        number with it. So the run is assigned in page order over the pages that
        carry transactions, which is the only page list this system can see.

        **Both ends are asked for, and the arithmetic is the check.** If the
        range does not span exactly the pages in hand, nothing is written and
        the refusal says both counts. Getting this wrong by one silently
        mis-cites every line after the first — a figure traced to a page it is
        not printed on is worse than a figure with no citation at all.

        Every line and the statement are marked ``BATES_MANUALLY_ENTERED`` with
        the person and the range, because the audit trail's whole value is that
        a number in an exhibit can say where it came from. A detected stamp was
        read off the document; a typed one is somebody's assertion about it, and
        the record has to tell them apart.

        :param first: The stamp on the statement's first page, as printed.
        :param last: The stamp on its last page. Optional for a single page.
        :return: ``(statement, pages_numbered)``.
        :raises ValueError: Unknown statement, unreadable stamp, no pages to
            number, or a range that does not match the pages in hand.
        """
        statement_repo = FinancialAccountStatementRepository(manager)
        transaction_repo = FinancialAccountTransactionRepository(manager)

        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            raise ValueError("Statement not found")

        parsed = bates_service.parse_stamp(first)
        if parsed is None:
            raise ValueError(
                "That does not look like a Bates number. Copy it exactly as it is stamped, "
                "for example KF 000101 or 000101.")
        prefix, separator, digits, start = parsed

        lines = transaction_repo.get_by_statement(statement_id)
        pages = sorted({t.physical_page_number for t in lines
                        if t.physical_page_number is not None})
        if not pages:
            raise ValueError(
                "No page numbers are recorded for this statement's transactions, so there is "
                "nothing to attach a stamp to.")

        if last:
            ending = bates_service.parse_stamp(last)
            if ending is None:
                raise ValueError(
                    "The last Bates number does not look like a stamp. Copy it exactly as "
                    "printed, or leave it blank.")
            span = ending[3] - start + 1
            if span != len(pages):
                raise ValueError(
                    "That range covers %d page(s), but this statement has transactions on "
                    "%d. Check the range against the document: a run that is off by one "
                    "mis-cites every line after the first."
                    % (span, len(pages)))

        stamped_at = datetime.now(timezone.utc).isoformat()
        by_page = {page: "%s%s%0*d" % (prefix, separator, digits, start + offset)
                   for offset, page in enumerate(pages)}

        for line in lines:
            number = by_page.get(line.physical_page_number)
            if number is None or line.bates_number == number:
                continue
            transaction_repo.update(line.id, {
                "bates_number": number,
                "flags": list(line.flags) + [{
                    "code": "BATES_MANUALLY_ENTERED",
                    "severity": "info",
                    "field_path": "bates_number",
                    "note": "%s entered this statement's Bates numbers by hand; detection "
                            "found none on the document." % staff_name,
                    "by_staff_id": staff_id,
                    "by": staff_name,
                    "at": stamped_at,
                    "to": number,
                }],
            })

        extraction = dict(statement.extraction or {})
        extraction["bates_first"] = by_page[pages[0]]
        extraction["bates_last"] = by_page[pages[-1]]
        extraction["bates_manual"] = {
            "by_staff_id": staff_id, "by": staff_name, "at": stamped_at,
            "first": by_page[pages[0]], "last": by_page[pages[-1]], "pages": len(pages),
        }
        flags = [f for f in statement.flags
                 if f.get("code") not in ("BATES_UNSTAMPED", "BATES_UNVERIFIED",
                                          "BATES_MANUALLY_ENTERED")]
        flags.append(_flag(
            "BATES_MANUALLY_ENTERED", "info",
            "%s entered Bates numbers %s to %s by hand across %d page(s); the document "
            "carried none this system could read. A number here is somebody's reading of "
            "the page, not a stamp detected on it."
            % (staff_name, by_page[pages[0]], by_page[pages[-1]], len(pages)),
            "bates",
        ))
        updated = statement_repo.update(statement_id, {
            "extraction": extraction, "flags": flags})

        LOGGER.info("statement_service.set_bates: statement=%s pages=%d staff=%s",
                    statement_id, len(pages), staff_id)
        return updated, len(pages)

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

    def refuse_oversized_upload(self, pdf_bytes: bytes) -> Optional[str]:
        """
        Say why this PDF must be split, or None if it may be ingested.

        **Called BEFORE the document is stored or a job is queued**, which is
        the whole point: a refused upload leaves nothing behind — no object in
        storage, no job row, no half-imported statements for somebody to unpick.
        The ordering is the feature, not an optimization.

        Two independent limits, each sufficient on its own:

        * **Pages.** ``_MAX_UPLOAD_PAGES``, counted exactly — the page count is
          free from the PDF header.
        * **Statements.** ``_MAX_UPLOAD_STATEMENTS``, counted by restarted
          pagination. This is a **lower bound**: the survey reads at most
          ``pdf_service._SURVEY_CAP`` pages, so a 700-page document reporting 48
          first pages in its first 250 has at least 48 and probably far more.
          A lower bound is all a refusal needs — exceeding the limit is already
          proved.

        **A PDF that cannot be surveyed is NOT refused.** A gate that fires when
        it cannot see would reject a document for being unreadable by the one
        check that never reads it properly, and the ingest reports a corrupt
        file far better than this could. Logged, and allowed through.

        :return: The sentence to show the person who uploaded it, or None.
        :rtype: Optional[str]
        """
        from services.pdf_service import pdf_service  # noqa: PLC0415

        try:
            survey = pdf_service.survey(pdf_bytes)
        except Exception as e:  # noqa: BLE001 — see the docstring
            LOGGER.warning(
                "statement_service.refuse_oversized_upload: could not survey the PDF "
                "(%s); allowing it through", str(e))
            return None

        pages = survey.get("pages") or 0
        scanned = survey.get("scanned") or pages
        statements = survey.get("first_pages") or 0

        if statements > _MAX_UPLOAD_STATEMENTS:
            LOGGER.warning(
                "statement_service: refused an upload of %d page(s) holding at least %d "
                "statement(s); limit is %d", pages, statements, _MAX_UPLOAD_STATEMENTS)
            return (
                "This PDF holds at least %d separate statements%s, and %d is the most that "
                "can be imported at once. Split it into one file per statement and upload "
                "them together — they are read several at a time, so a stack of monthly "
                "files finishes sooner than the same months combined. Nothing was saved."
                % (statements,
                   " in its first %d pages" % scanned if scanned < pages else "",
                   _MAX_UPLOAD_STATEMENTS)
            )

        if pages > _MAX_UPLOAD_PAGES:
            LOGGER.warning("statement_service: refused a %d-page upload; limit is %d",
                           pages, _MAX_UPLOAD_PAGES)
            return (
                "This PDF is %d pages, and %d is the most that can be imported at once. "
                "A single statement is rarely longer than that, so this is probably several "
                "of them in one file: split it into one file per statement and upload them "
                "together. Nothing was saved."
                % (pages, _MAX_UPLOAD_PAGES)
            )
        return None

    def survey_upload(self, pdf_bytes: bytes) -> list[str]:
        """
        Look at a PDF before queueing it, and say what is likely to go wrong.

        Warnings only — the ingest goes ahead either way. A document holding
        several statements is perfectly legal input: a combined statement is one
        upload with five accounts in it, and blocking that to catch a
        twenty-four-month production would trade a real workflow for a rare one.
        What the reader gets instead is the sentence they will wish they had
        read: *split it and retry*.

        This is worth doing because the failure it names is expensive and slow
        to discover. A 142-page PDF of twenty-four months ran long enough to time
        the browser out while continuing on the server, imported some statements,
        made a mess of the last, and had to be rejected wholesale and split by
        hand. Every part of that is recoverable; none of it is quick.

        Two independent signals, deliberately not combined into one score:

        * **Length.** Past `_MANY_PAGES` a statement is more likely to be a
          production than a month.
        * **Restarted pagination.** Statements print their own page numbers, so
          a document holding twenty-four of them says "Page 1" twenty-four
          times. That is a much stronger signal than length and it names a
          number, which is what makes the warning actionable.

        :return: Warnings in the order they should be read, or empty.
        :rtype: list[str]
        """
        # Resolved at call time, like every other use of pdf_service here: the
        # suites that exercise this module replace the singleton on its own
        # module, and a name bound at import would not see the replacement.
        from services.pdf_service import pdf_service  # noqa: PLC0415

        try:
            survey = pdf_service.survey(pdf_bytes)
        except Exception as e:  # noqa: BLE001 — a warning must never block an upload
            LOGGER.warning("statement_service.survey_upload: could not survey the PDF: %s", str(e))
            return []

        pages = survey.get("pages") or 0
        first_pages = survey.get("first_pages") or 0
        warnings: list[str] = []

        # Said first, because it carries a count and the reader can check it.
        if first_pages > 1:
            warnings.append(
                "This PDF looks like it holds about %d separate statements — its pagination "
                "restarts at \"Page 1\" %d times%s. Reading a document this size takes a long "
                "time and the accounts can be filed against the wrong statement. If the import "
                "comes out wrong, split the PDF into one file per statement and upload them "
                "together: they are read several at a time, so a stack of monthly files "
                "finishes sooner than the same months combined."
                % (first_pages, first_pages,
                   " in the first %d pages" % survey["scanned"]
                   if survey.get("scanned", pages) < pages else "")
            )
        elif pages > _MANY_PAGES:
            # Only when the stronger signal found nothing — otherwise the reader
            # gets two warnings saying the same thing in different words.
            warnings.append(
                "This PDF is %d pages, which is longer than a single statement usually runs. "
                "It may hold more than one. If the import comes out wrong, split it into one "
                "file per statement and upload them together — they are read several at a "
                "time." % pages
            )

        if warnings:
            LOGGER.info(
                "statement_service.survey_upload: %d page(s), %d first-page marker(s); warning",
                pages, first_pages,
            )
        return warnings

    def retry_statement(
        self, manager: DatabaseManager, statement_id: int, staff_id: int,
    ) -> dict[str, Any]:
        """
        Throw away what one upload produced and read the PDF again.

        **The unit is the document, not the statement**, and that is the whole
        of the design. A re-import re-reads the file, and the file may hold five
        accounts — a combined statement does. Rejecting only the statement the
        user clicked and requeueing would produce all five again, leaving the
        other four duplicated: two copies of an account's history, each half
        complete, which is the failure mode `find_match` and the merge tooling
        exist to clean up after. So every statement from the same ingest job
        goes, and the document is read once more as a whole.

        **Nothing is deleted until the PDF is known to be retrievable.** The
        obvious ordering — reject, then requeue — loses the evidence outright
        when the source has been purged from storage, and the person clicking
        "Retry" is asking to re-read a document, never to destroy one. So the
        bytes are fetched first and the deletes only happen once they are in
        hand.

        The bytes are re-uploaded under the new job's own id rather than the new
        job pointing at the old path. It costs a duplicate object in storage and
        buys independence: two jobs sharing one file means purging either one's
        source removes the other's.

        :return: What was discarded and the id of the job now reading it again.
        :rtype: dict[str, Any]
        :raises ValueError: If the statement is unknown, or its source PDF
            cannot be retrieved — in which case nothing has been deleted.
        """
        statement_repo = FinancialAccountStatementRepository(manager)
        statement = statement_repo.select_one(condition={"id": statement_id})
        if statement is None:
            raise ValueError("Statement not found")
        if not statement.storage_path:
            raise ValueError(
                "No source PDF was stored for this statement, so there is nothing to read "
                "again. Nothing has been deleted."
            )

        # Imported here, not at module scope: job_service reaches back into this
        # module to run an ingest, so a module-level import is a cycle. The
        # worker resolves the same pair the same way.
        from services.job_service import job_service  # noqa: PLC0415

        pdf_bytes = StorageService(manager).download(statement.storage_path)
        if not pdf_bytes:
            raise ValueError(
                "The source PDF could not be retrieved from storage, so it cannot be read "
                "again. Nothing has been deleted — upload the document afresh instead."
            )

        # THE SAME GATE AS AN UPLOAD, and checked here too because a document
        # accepted before the limit existed is still in storage. Re-reading one
        # would run exactly the import that cannot work — and it would do it
        # AFTER discarding what is already held, which is worse than refusing.
        # Checked before anything is deleted, like the download above.
        too_big = self.refuse_oversized_upload(pdf_bytes)
        if too_big:
            raise ValueError(
                "%s Nothing has been deleted, and the statements already imported from it "
                "are untouched." % too_big.replace(" Nothing was saved.", "")
            )

        # Everything this upload produced. Falls back to the one statement when
        # the ingest predates source_job_id, which is the conservative answer:
        # better to leave a sibling in place than to delete a statement this
        # re-import will not recreate.
        siblings = (
            statement_repo.get_by_source_job(statement.source_job_id)
            if statement.source_job_id else [statement]
        ) or [statement]

        discarded = [self.reject_statement(manager, s.id) for s in siblings]

        extraction = statement.extraction or {}
        job = job_service.enqueue_statement_ingest(
            manager, staff_id, statement.matter_id, pdf_bytes,
            bates_prefix=extraction.get("bates_prefix"),
            source_filename=extraction.get("source_filename"),
        )
        LOGGER.info(
            "statement_service.retry_statement: statement=%s discarded %d statement(s) from "
            "job=%s; requeued as job=%s",
            statement_id, len(discarded), statement.source_job_id, job.id,
        )
        return {
            "job_id": job.id,
            "statements_discarded": len(discarded),
            "transactions_discarded": sum(d["transactions_deleted"] for d in discarded),
            "accounts_deleted": sum(1 for d in discarded if d["account_deleted"]),
            "source_filename": extraction.get("source_filename"),
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
