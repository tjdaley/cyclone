"""
tests/test_account_discovery.py - Reading account numbers out of transfer lines.

Every description in the parser cases below is a real line from a produced
statement in the Salmons matter (First Financial x9260, Bank of Texas), plus
the two shapes Tom described from other productions. The noise cases matter as
much as the hits: a confirmation number and a card timestamp are digit runs
sitting inside a transfer description, and reading either one as an account
invents an undisclosed account out of nothing.

Run:  .venv/Scripts/python.exe tests/test_account_discovery.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import AccountType  # noqa: E402
import services.account_discovery_service as ads  # noqa: E402
from services.account_discovery_service import (  # noqa: E402
    AccountDiscoveryService, _aba_is_valid, _last4, _payee_key, _references,
    _same_party, _wire_details,
)

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeAccount:
    def __init__(self, id, institution, last4, ownership="unknown",
                 account_type=AccountType.checking):
        self.id = id
        self.institution = institution
        self.account_number_last4 = last4
        self.ownership = ownership
        self.account_type = account_type


class FakeTransaction:
    def __init__(self, id, account_id, description, amount, when=None, category_id=None):
        self.id = id
        self.financial_account_id = account_id
        self.description = description
        self.amount = Decimal(amount)
        self.transaction_date = when
        self.category_id = category_id


class FakeRuling:
    def __init__(self, id, pattern, classification, name=None, kind=None):
        self.id = id
        self.pattern = pattern
        self.classification = classification
        self.creditor_name = name
        self.creditor_type = kind


class FakeCategoryRepo:
    def __init__(self, liability_ids=()):
        self._ids = list(liability_ids)

    def liability_ids(self):
        return self._ids


class FakeClassificationRepo:
    def __init__(self, rulings=()):
        self._rulings = list(rulings)

    def available_for_matter(self, matter_id, include_inactive=False):
        return self._rulings


def guard_fakes() -> None:
    """
    Every attribute a fake invents must exist on the real model.

    This check exists because its absence cost a session: fakes carrying a
    ``.name`` the real model spells ``description`` agreed perfectly with code
    that read ``.name``, so the tests passed and production 500'd. A fake that
    is free to invent fields tests the fake.
    """
    from db.models.financial import (
        FinancialAccount, FinancialAccountTransaction, PayeeClassification,
    )
    pairs = (
        ("FakeAccount", FakeAccount(1, "B", "1234"), FinancialAccount,
         {"id"}),
        ("FakeTransaction", FakeTransaction(1, 1, "d", "0.00"), FinancialAccountTransaction,
         {"id"}),
        ("FakeRuling", FakeRuling(1, "P", "creditor"), PayeeClassification,
         {"id"}),
    )
    renamed = {"kind": "creditor_type", "name": "creditor_name"}
    for label, fake, model, exempt in pairs:
        for attribute in vars(fake):
            field = renamed.get(attribute, attribute)
            if field in exempt or field in model.model_fields:
                continue
            check("%s.%s exists on %s" % (label, attribute, model.__name__), field, "a real field")


class FakeAccountRepo:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_by_matter(self, matter_id):
        return self._accounts


class FakeStatementRepo:
    def __init__(self, rejected=None):
        self._rejected = rejected or []

    def rejected_ids(self, matter_id):
        return self._rejected


class FakeTransactionRepo:
    """Applies the same substring filter PostgREST would, so paging is exercised."""

    def __init__(self, rows, page_cap=1000):
        self._rows = rows
        self._page_cap = page_cap
        self.calls = 0

    def search(self, account_ids, exclude_statement_ids=None, text=None,
               category_ids=None, limit=200, offset=0, **kwargs):
        self.calls += 1
        matches = [
            r for r in self._rows
            if r.financial_account_id in account_ids
            and (text is None or text.lower() in (r.description or "").lower())
            and (category_ids is None or getattr(r, "category_id", None) in category_ids)
        ]
        size = min(limit, self._page_cap)
        return matches[offset:offset + size], len(matches)


def run(accounts, rows, page_cap=1000):
    """Drive the service against fakes and return (results, transaction_repo)."""
    import services.account_discovery_service as mod

    account_repo = FakeAccountRepo(accounts)
    statement_repo = FakeStatementRepo()
    transaction_repo = FakeTransactionRepo(rows, page_cap=page_cap)

    original = (mod.FinancialAccountRepository,
                mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository)
    mod.FinancialAccountRepository = lambda m: account_repo
    mod.FinancialAccountStatementRepository = lambda m: statement_repo
    mod.FinancialAccountTransactionRepository = lambda m: transaction_repo
    try:
        return AccountDiscoveryService().undisclosed(object(), 1), transaction_repo
    finally:
        (mod.FinancialAccountRepository,
         mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository) = original


def run_creditors(accounts, rows, liability_ids=(), rulings=()):
    """Drive the creditor scan against fakes. Returns (creditors, candidates)."""
    import services.account_discovery_service as mod

    account_repo = FakeAccountRepo(accounts)
    statement_repo = FakeStatementRepo()
    transaction_repo = FakeTransactionRepo(rows)
    category_repo = FakeCategoryRepo(liability_ids)
    classification_repo = FakeClassificationRepo(rulings)

    original = (mod.FinancialAccountRepository,
                mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository,
                mod.TransactionCategoryRepository,
                mod.PayeeClassificationRepository)
    mod.FinancialAccountRepository = lambda m: account_repo
    mod.FinancialAccountStatementRepository = lambda m: statement_repo
    mod.FinancialAccountTransactionRepository = lambda m: transaction_repo
    mod.TransactionCategoryRepository = lambda m: category_repo
    mod.PayeeClassificationRepository = lambda m: classification_repo
    try:
        return AccountDiscoveryService().creditors(object(), 1)
    finally:
        (mod.FinancialAccountRepository,
         mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository,
         mod.TransactionCategoryRepository,
         mod.PayeeClassificationRepository) = original


# ── Parser ───────────────────────────────────────────────────────────────────

print("\nReading references out of a description")

# Real First Financial x9260 lines.
check("Acct No. — full number, no institution named",
      _references("Transfer from DDA (Sweep) Acct No. 81110044625"),
      [("81110044625", None)])

check("Acct No. with a suffix — the suffix is not part of the number",
      _references("Transfer to DDA        Acct No.  86110018909-D"),
      [("86110018909", None)])

check("masked pair — both sides come back, caller drops its own",
      _references("Transfer from XXX4070 to XXX9260: Conf #:19842192"),
      [("4070", None), ("9260", None)])

check("Savings sweep",
      _references("Transfer to Savings (Sweep) Acct No. 81120014527"),
      [("81120014527", None)])

# Bank of Texas.
check("labelled with an account TYPE — CHKG is not an institution",
      _references("INTERNET XFER FROM CHKG 8098386837"),
      [("8098386837", None)])

# The shape Tom described from other productions.
check("labelled with an institution NAME — kept",
      _references("TRANSFER FROM CHASE 4321"),
      [("4321", "CHASE")])

print("\nBanks disagree about the mask character")

# Chase masks with dots and leaves no space before the digits, so none of the
# three patterns reached it: not the XX mask, not "Acct", and not "to <word>
# <space> <digits>". x9323 sat in a produced statement, referenced by name, and
# never appeared on the undisclosed list.
check("Chase dot mask, the line that found this",
      _references("12/15 Online Transfer To Chk ...9323 Transaction#: 19012059496"),
      [("9323", None)])
check("and the other direction",
      _references("Online Transfer From Chk ...4448 Transaction#: 22385628138"),
      [("4448", None)])
check("asterisks", _references("Transfer to ****1234"), [("1234", None)])
check("hashes", _references("XFER TO ####5678"), [("5678", None)])
check("middle dots", _references("Transfer to ••••4321"), [("4321", None)])

print("\nA single mask character is not a mask")

# Two or more is the rule, and it has to be: a lone dot is the one in "Acct No."
# and in every decimal amount printed on the statement, and a lone hyphen is in
# every date.
check("a decimal amount is not an account",
      _references("Transfer of 1,234.56 to savings"), [])
check("a date is not an account",
      _references("Transfer on 2024-11-15 completed"), [])
check("the dot in 'Acct No.' still resolves through the Acct pattern",
      _references("Transfer from DDA (Sweep) Acct No. 81110044625"),
      [("81110044625", None)])

print("\nTransaction numbers are stripped, like conf and ref numbers")

check("a transaction number alone yields nothing",
      _references("Transfer completed Transaction#: 19012059496"), [])
check("trace number", _references("XFER TO ...4321 Trace# 998877665"), [("4321", None)])
check("auth number", _references("Transfer to ...4321 Auth: 55512345"), [("4321", None)])
check("'Transfer' is not mistaken for 'transaction' and stripped",
      _references("Transfer to XXX4070"), [("4070", None)])


print("")
print("Wires name a bank, never an account")

# Verbatim from the Harrison production. Seven of these arrived at the Chase
# checking account in 2024, totalling $198,101.18, and the transfer scan saw
# none of them: the description contains neither "transfer" nor "xfer", and no
# UBS account number appears anywhere in it.
WIRE = ("Fedwire Credit Via: UBS Ag Stamford Branch/026007993 B/O: Kimberly Harrison "
        "United States 75056 Ref: Chase Nyc/Ctr/Bnf=Kimberly A Harrison Lewisville TX "
        "75056-5760 US/Ac-0 00000018227 Rfb=Fa35304Dom240605 BB I=/Ocmt/USD31721,34/ "
        "Imad: 0605B6B7Ik2C001185 Trn: 0839331157Ff")

check("the transfer scan is blind to it", _references(WIRE), [])

details = _wire_details(WIRE)
check("the sending bank is read off the wire", details["institution"], "UBS Ag Stamford Branch")
check("with its routing number", details["aba"], "026007993")
check_true("the originator is captured", details["originator"].startswith("Kimberly Harrison"))
check_true("so is the beneficiary", details["beneficiary"].startswith("Kimberly A Harrison"))

# The finding. Not "money arrived from UBS" but "she wired it to herself".
check("sender and receiver are the same person", details["same_party"], True)

print("")
print("The routing number is checksummed, which is why it is safe to key on")
check("a real ABA validates", _aba_is_valid("026007993"), True)
check("Chase", _aba_is_valid("021000021"), True)
check("a made-up nine-digit run does not", _aba_is_valid("123456789"), False)
check("nor does a short one", _aba_is_valid("02600799"), False)
check("nor a non-numeric one", _aba_is_valid("02600799X"), False)

print("")
print("A nine-digit run that is not a routing number is not an institution")
# The danger of loosening the digit patterns: reporting "UBS ....7993" as an
# undisclosed ACCOUNT, when 026007993 is the bank's routing number.
check("the routing number never reaches the account list",
      [ref for ref, _ in _references(WIRE) if "7993" in ref], [])
check("a wire whose bank number fails the checksum yields no institution",
      _wire_details("Fedwire Credit Via: Nowhere Bank/123456789 B/O: A Person")["institution"],
      None)

print("")
print("What is not a wire")
check("a phone bill from a carrier with WIRELESS in the name",
      _wire_details("VERIZON WIRELESS PMT 8005551212"), None)
check("a grocery run", _wire_details("WAL-MART SUPERCENTER #1234"), None)
check("an ordinary transfer", _wire_details("Transfer from XXX4070 to XXX9260"), None)

print("")
print("A third-party wire is not the same finding")
third = _wire_details("Fedwire Credit Via: UBS Ag Stamford Branch/026007993 "
                      "B/O: Acme Holdings LLC Ref: Bnf=Kimberly A Harrison Lewisville TX")
check("still names the institution", third["institution"], "UBS Ag Stamford Branch")
check("but the parties differ", third["same_party"], False)

print("")
print("Shared address words alone are not a shared identity")
# Both party lines carry "United States" and a Texas town. Two shared NAME words
# are required, which is the rule intake_service uses for matching people.
check("geography does not make two people one",
      _same_party("Acme Corp United States Texas", "Jane Doe United States Texas"), False)
check("a surname alone is not enough either",
      _same_party("Harrison Enterprises", "Kimberly A Harrison"), False)


print("\nNumbers that are not accounts")

check("confirmation number alone is not an account",
      _references("Transfer XXX9260: Conf #:19842192"),
      [("9260", None)])

check("merchant instant transfer — no account number at all",
      _references("INST XFER PAYPAL WEB LULULEMONUS TYPE S"),
      [])

check("brokerage transfer with an alphanumeric reference, not a number",
      _references("Transfer Acorns Invest WEB 9Z920C1 TYPE S"),
      [])

check("card timestamp is stripped",
      _references("XFER TO XXX5150 231212 103658"),
      [("5150", None)])

check("a description that is not a transfer is never parsed",
      _references("CHECK CARD PURCHASE 4820 HEB GROCERY 1234567"),
      [])

check("empty description",
      _references(""),
      [])

print("\nLast four")
check("full number", _last4("81110044625"), "4625")
check("already four", _last4("4070"), "4070")
check("too short to identify an account", _last4("123"), None)


# ── The workflow ─────────────────────────────────────────────────────────────

print("\nAn account the matter does not hold")

held = [FakeAccount(1, "First Financial Bank", "9260"),
        FakeAccount(2, "First Financial Bank", "4527")]
rows = [
    # Its own number and one the matter holds — neither is a discovery.
    FakeTransaction(1, 1, "Transfer to Savings (Sweep) Acct No. 81120014527", "-2500.00", date(2023, 3, 4)),
    # An outside account, twice, in both directions.
    FakeTransaction(2, 1, "Transfer from XXX4070 to XXX9260: Conf #:19842192", "10000.00", date(2023, 3, 6)),
    FakeTransaction(3, 1, "Transfer from XXX9260 to XXX4070: Conf #:19842200", "-4000.00", date(2023, 5, 9)),
]
results, repo = run(held, rows)

check("only the outside account is reported", [r["last4"] for r in results], ["4070"])
entry = results[0]
check("mentions counted", entry["mentions"], 2)
check("money in — the sign says it came from them", entry["money_in"], Decimal("10000.00"))
check("money out — the sign says it went to them", entry["money_out"], Decimal("4000.00"))
check("net", entry["net"], Decimal("6000.00"))
check("first seen", entry["first_seen"], date(2023, 3, 6))
check("last seen", entry["last_seen"], date(2023, 5, 9))
check("institution inferred from the statement it sat on",
      entry["institution"], "First Financial Bank")
check("and flagged as inferred", entry["institution_inferred"], True)
check("named the account it was seen on", entry["seen_on"], ["First Financial Bank ····9260"])
# One query per gate term — transfer, xfer, payment, pmt, autopay — and not one
# page of the matter's whole transaction table.
check("the search was pushed to the database, not paged over everything",
      repo.calls, len(ads._TRANSFER_TERMS) + len(ads._PAYMENT_TERMS))

print("\nAn institution the description names outright")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [FakeTransaction(1, 1, "TRANSFER FROM CHASE 4321", "7500.00", date(2023, 4, 1))]
results, _ = run(held, rows)
check("reported", [r["last4"] for r in results], ["4321"])
check("institution read off the page", results[0]["institution"], "CHASE")
check("not inferred — so no dagger", results[0]["institution_inferred"], False)

print("\nA later mention that names the bank beats an earlier inference")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to XXX4321", "-100.00", date(2023, 4, 1)),
    FakeTransaction(2, 1, "TRANSFER FROM CHASE 4321", "200.00", date(2023, 4, 2)),
]
results, _ = run(held, rows)
check("one account, not two", len(results), 1)
check("upgraded to the named institution", results[0]["institution"], "CHASE")
check("no longer inferred", results[0]["institution_inferred"], False)
check("both mentions kept", results[0]["mentions"], 2)

print("\nThe same account written two ways")

# 86110018909 and XXX8909 are the same account. Deduping on last four is what
# merges them; deduping on the printed string would report two.
held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to DDA        Acct No.  86110018909-D", "-1000.00", date(2023, 6, 1)),
    FakeTransaction(2, 1, "Transfer from XXX9260 to XXX8909", "-500.00", date(2023, 6, 8)),
]
results, _ = run(held, rows)
check("merged into one account", len(results), 1)
check("mentions across both spellings", results[0]["mentions"], 2)
check("quotes the longest form seen", results[0]["reference"], "86110018909")
check("money out summed", results[0]["money_out"], Decimal("1500.00"))

print("\nA line matching both search terms is counted once")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [FakeTransaction(1, 1, "TRANSFER / XFER TO XXX7777", "-300.00", date(2023, 7, 1))]
results, _ = run(held, rows)
check("one mention, not two", results[0]["mentions"], 1)
check("counted once", results[0]["money_out"], Decimal("300.00"))

print("\nPaging")

# 250 transfer lines against a 100-row cap: three pages per term. A scan that
# does not page reports only what fits in the first page and looks complete.
held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(i, 1, "Transfer to XXX4070", "-10.00", date(2023, 1, 1))
    for i in range(1, 251)
]
results, repo = run(held, rows, page_cap=100)
check("every line reached the tally", results[0]["mentions"], 250)
check("money out complete", results[0]["money_out"], Decimal("2500.00"))

print("\nEdge cases")

results, _ = run([], [FakeTransaction(1, 1, "Transfer to XXX4070", "-10.00")])
check("a matter with no accounts returns nothing", results, [])

held = [FakeAccount(1, "First Financial Bank", "9260")]
results, _ = run(held, [])
check("no transfers at all", results, [])

# A null date must not become a sort key or a comparison against None.
rows = [FakeTransaction(1, 1, "Transfer to XXX4070", "-10.00", None)]
results, _ = run(held, rows)
check("a dateless line still counts", results[0]["mentions"], 1)
check("and leaves the window empty", results[0]["first_seen"], None)

# A null amount is what an unreconciled extraction can leave behind.
rows = [FakeTransaction(1, 1, "Transfer to XXX4070", "0.00", date(2023, 1, 1))]
rows[0].amount = None
results, _ = run(held, rows)
check("a null amount does not crash the tally", results[0]["net"], Decimal("0.00"))

print("\nOrdering")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to XXX1111", "-5.00", date(2023, 1, 1)),
    FakeTransaction(2, 1, "Transfer to XXX2222", "-5.00", date(2023, 1, 2)),
    FakeTransaction(3, 1, "Transfer to XXX2222", "-5.00", date(2023, 1, 3)),
]
results, _ = run(held, rows)
check("busiest account first", [r["last4"] for r in results], ["2222", "1111"])

# ── Payments that name an account ────────────────────────────────────────────

print("\nA payment that prints the account it paid")

check("Chase spells a mask in English, and names the issuer",
      _references("12/21 Payment To Chase Card Ending IN 9547"),
      [("9547", "Chase")])

check("without the word 'in', an account-type word carries it",
      _references("Payment To Chase Card ending 1269"),
      [("1269", "Chase")])

check("a masked account inside a payment still reads",
      _references("Hudson Payment for Liukin - Withdrawal to Main Checking Account XXXXXXX3009"),
      [("3009", None)])

# The reason ``_LABELLED`` is transfer-only. Without that split this line reads
# as an undisclosed account belonging to Kathy Gunn, whose "account number" is
# the Zelle confirmation.
check("a trailing confirmation number on a payment is not an account",
      _references("Zelle Payment To Kathy  Gunn 20928990159"), [])

check("but the same shape on a transfer still is",
      _references("TRANSFER FROM CHASE 4321"), [("4321", "CHASE")])

check("a statement period is not an account",
      _references("Payment for the period ending 2024"), [])

check("a payment naming nobody's number yields nothing",
      _references("ACH PMT AMEX EPAYMENT 0005000008 03/09/26 ID #-M3630 "
                  "TRACE #-091000014282361"), [])


# ── Reading a payee out of a payment ─────────────────────────────────────────

print("\nReducing a payment description to who was paid")

check("ACH pull, with the ids and dates that differ every month",
      _payee_key("ACH PMT AMEX EPAYMENT 0005000008 03/09/26 ID #-M3630 "
                 "TRACE #-091000014282361"), "AMEX")
check("the same creditor, worded differently by the same bank",
      _payee_key("Withdrawal from AMEX EPAYMENT ACH PMT"), "AMEX")
check("bank bill-pay, with its confirmation number",
      _payee_key("10/16 Online Payment 22398267106 To City of Lewisville"),
      "CITY OF LEWISVILLE")
check("the account number comes off the payee, it is not part of the name",
      _payee_key("12/21 Payment To Chase Card Ending IN 9547"), "CHASE CARD")
check("a card issuer written two ways lands in one place",
      _payee_key("APPLECARD GSBANK  PAYMENT     17643109"), "APPLECARD GSBANK")
check("and again from the other statement's phrasing",
      _payee_key("Withdrawal from APPLECARD GSBANK PAYMENT"), "APPLECARD GSBANK")
# A Zelle to a named person can be a private loan being repaid, which is a debt
# somebody has to disclose. The rail is furniture; the name is the payee.
check("the person survives, the payment rail does not",
      _payee_key("Zelle Payment To Kathy  Gunn 20928990159"), "KATHY GUNN")
check("a bare rail with no name groups as the rail",
      _payee_key("Venmo Payment 1032673429529 Web ID: 3264681992"), "VENMO")


# ── Creditors ────────────────────────────────────────────────────────────────

print("\nCreditors, and the payees nobody has ruled on")

guard_fakes()

CHECKING = [FakeAccount(1, "Chase", "4448", account_type=AccountType.checking)]
PAYMENTS = [
    # Filed by a person under a liability category — a finding.
    FakeTransaction(1, 1, "ACH PMT AMEX EPAYMENT 0005000008 03/09/26 ID #-M3630",
                    "-5000.00", date(2023, 3, 9), category_id=69),
    FakeTransaction(2, 1, "ACH PMT AMEX EPAYMENT 0005000008 04/09/26 ID #-M3631",
                    "-4000.00", date(2023, 4, 9), category_id=69),
    # Nobody has ruled on these two.
    FakeTransaction(3, 1, "Withdrawal from LOWES PAYMENT", "-300.00", date(2023, 3, 12)),
    FakeTransaction(4, 1, "10/16 Online Payment 22398267106 To City of Lewisville",
                    "-120.00", date(2023, 3, 16)),
]

creditors, candidates = run_creditors(CHECKING, PAYMENTS, liability_ids=[69])
check("the categorized payee is a finding", [c["payee"] for c in creditors], ["AMEX"])
check("and says why, so a reader can weigh it", creditors[0]["reason"], "liability_category")
check("twelve payments, one creditor", creditors[0]["payments"], 2)
check("ranked on what it costs to service", creditors[0]["money_out"], Decimal("9000.00"))
check("the window it was paid over",
      (creditors[0]["first_seen"], creditors[0]["last_seen"]),
      (date(2023, 3, 9), date(2023, 4, 9)))
check("everything else is a question, not a finding",
      [c["payee"] for c in candidates], ["LOWES", "CITY OF LEWISVILLE"])
check("candidates are ranked by money too", candidates[0]["money_out"], Decimal("300.00"))
check("and are marked as unruled", candidates[0]["reason"], "unreviewed")

creditors, candidates = run_creditors(
    CHECKING, PAYMENTS, liability_ids=[69],
    rulings=[FakeRuling(7, "LOWES", "creditor", "Lowe's (Synchrony)", "credit_card")],
)
check("a ruling promotes a payee out of the queue",
      sorted(c["payee"] for c in creditors), ["AMEX", "LOWES"])
check("and it carries the name a person would put on a motion",
      [c["creditor_name"] for c in creditors if c["payee"] == "LOWES"], ["Lowe's (Synchrony)"])
check("which changes what you request",
      [c["creditor_type"] for c in creditors if c["payee"] == "LOWES"], ["credit_card"])
check("the queue is shorter by exactly one",
      [c["payee"] for c in candidates], ["CITY OF LEWISVILLE"])

creditors, candidates = run_creditors(
    CHECKING, PAYMENTS, liability_ids=[69],
    rulings=[FakeRuling(8, "CITY OF LEWISVILLE", "not_creditor")],
)
check("a vendor ruling removes it for good", [c["payee"] for c in candidates], ["LOWES"])
check("and does not touch the findings", [c["payee"] for c in creditors], ["AMEX"])

# The matter layer overriding the firm's answer: last match wins.
creditors, candidates = run_creditors(
    CHECKING, PAYMENTS, liability_ids=[],
    rulings=[FakeRuling(9, "LOWES", "not_creditor"),
             FakeRuling(10, "LOWES", "creditor", "Lowe's", "credit_card")],
)
check("the matter's own ruling overrides the firm's",
      [c["payee"] for c in creditors], ["LOWES"])

print("\nWhat the creditor scan refuses to report")

# Producing the card explains every payment to it. Reporting it anyway is how a
# report that is right most of the time stops being read.
held_card = CHECKING + [FakeAccount(2, "AMEX", "1005", account_type=AccountType.credit_card)]
creditors, candidates = run_creditors(held_card, PAYMENTS, liability_ids=[69])
check("a produced credit account is not a finding", [c["payee"] for c in creditors], [])

# A produced CHECKING account at the same bank explains nothing about a card.
held_bank = CHECKING + [FakeAccount(2, "AMEX", "1005", account_type=AccountType.checking)]
creditors, _ = run_creditors(held_bank, PAYMENTS, liability_ids=[69])
check("but a produced deposit account at the same bank does not",
      [c["payee"] for c in creditors], ["AMEX"])

# Money arriving from a creditor is a refund or a cash advance. Neither says an
# account went unproduced, and both would inflate the total that ranks the list.
inbound = [FakeTransaction(1, 1, "ACH PMT AMEX EPAYMENT REFUND", "500.00", date(2023, 3, 9))]
creditors, candidates = run_creditors(CHECKING, inbound)
check("money coming back from a creditor is not a payment to one",
      (creditors, candidates), ([], []))

# A payment landing ON a card describes the account that funded it, which is a
# different question — and "Payment Thank You" is not the name of a creditor.
card_only = [FakeAccount(1, "Chase", "9547", account_type=AccountType.credit_card)]
on_card = [FakeTransaction(1, 1, "Payment Thank You - Web", "-2000.00", date(2023, 3, 9))]
check("a payment arriving on a card is out of scope",
      run_creditors(card_only, on_card), ([], []))

print("\nWhen a payment names the number as well as the payee")

named = [FakeTransaction(1, 1, "12/21 Payment To Chase Card Ending IN 9547",
                         "-2500.00", date(2023, 3, 9), category_id=69)]
creditors, _ = run_creditors(CHECKING, named, liability_ids=[69])
check("the digits ride along with the creditor", creditors[0]["last4"], ["9547"])
check("so the row is the creditor and the account at once",
      creditors[0]["payee"], "CHASE CARD")

print("\nDegrading when 033 has not been applied")


class ExplodingClassificationRepo:
    def available_for_matter(self, matter_id, include_inactive=False):
        raise RuntimeError('relation "transaction_payee_classifications" does not exist')


def run_without_table():
    import services.account_discovery_service as mod
    original = mod.PayeeClassificationRepository
    mod.PayeeClassificationRepository = lambda m: ExplodingClassificationRepo()
    try:
        return run_creditors(CHECKING, PAYMENTS, liability_ids=[69])
    finally:
        mod.PayeeClassificationRepository = original


creditors, candidates = run_without_table()
check("a missing rulings table degrades to everything-is-a-candidate",
      len(creditors) + len(candidates), 3)
check("and the categorized finding still stands",
      [c["payee"] for c in creditors], ["AMEX"])

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all account-discovery checks passed")
