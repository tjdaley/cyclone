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
from datetime import date, datetime, timezone
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

These blocks are NOT transaction registers. Never turn one into transactions,
and never let one become a statement of its own:
  - DAILY ENDING BALANCE, or any table of dates against running balances. These
    are end-of-day snapshots of one figure, not money moving.
  - ACCOUNT SUMMARY / SUMMARY OF ACCOUNTS — totals and counts, not entries.
  - INTEREST RATE SUMMARY, rate tables, yield tables.
  - Checkbook reconciliation worksheets, and any blank form the customer fills in.
  - Check images, disclosures, error-resolution notices, marketing inserts.
A date column next to an amount column does not make a table a register. Ask
whether each row is a payment, deposit, or charge that moved the balance. If it
is a balance, a total, or a rate, leave it out.

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
    {"payments": -6.49, "purchases": 1271.39, "fees": 0.00, "interest": 0.00}
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
        body = raw_text[:_MAX_STATEMENT_CHARS]
        if len(raw_text) > _MAX_STATEMENT_CHARS:
            body += "\n\n[DOCUMENT TRUNCATED — report only the statements above]"

        response = llm_service.complete(_STATEMENT_SYSTEM, body, profile="extract_account_statement")
        try:
            parsed = json.loads(_strip_fences(response))
        except json.JSONDecodeError as e:
            LOGGER.warning("statement_service.extract: parse failure: %s", str(e))
            raise ValueError("Could not read the statement — the model's response was not valid JSON") from e
        if not isinstance(parsed, dict) or "statements" not in parsed:
            raise ValueError("Extraction did not return a statements list")
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
        source_bates = {t.bates_number for t in transaction_repo.get_by_account(source_id) if t.bates_number}
        target_bates = {t.bates_number for t in transaction_repo.get_by_account(target_id) if t.bates_number}
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
            "transactions_to_move": len(transaction_repo.get_by_account(source_id)),
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
        line_count = len(transaction_repo.get_by_statement(statement_id))
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
