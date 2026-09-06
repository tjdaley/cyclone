"""
tests/test_category_rules.py - Filing transactions by keyword.

The three properties that make an automatic assignment defensible rather than
merely convenient, each with its own section below:

  * a rule never overwrites a person
  * every assignment records that it was automatic, and which rule did it
  * matching is punctuation- and case-blind, so WALMART finds WAL-MART #1234

The descriptions are real shapes from the Salmons and Harrison productions.

Run:  venv/Scripts/python.exe tests/test_category_rules.py
"""
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import CategorySource  # noqa: E402
from services.category_rule_service import CategoryRuleService, normalize  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


class FakeRule:
    def __init__(self, id, pattern, category_id, priority=100, applies_to="any",
                 matter_id=None):
        self.id = id
        self.pattern = pattern
        self.category_id = category_id
        self.priority = priority
        self.applies_to = applies_to
        self.matter_id = matter_id
        self.is_active = True


class FakeTransaction:
    def __init__(self, id, description, amount, counterparty=None, category_id=None,
                 category_source=None, category_rule_id=None, category_reviewed_at=None):
        self.id = id
        self.financial_account_id = 1
        self.description = description
        self.counterparty = counterparty
        self.amount = Decimal(amount)
        self.category_id = category_id
        self.category_source = category_source
        self.category_rule_id = category_rule_id
        self.category_reviewed_at = category_reviewed_at


SERVICE = CategoryRuleService()

HOUSEHOLD, MEDICAL, INCOME, PAYROLL_EXPENSE, CASH = 23, 91, 48, 70, 65


# -- Normalisation ------------------------------------------------------------

print("\nWALMART and its derivatives")

for raw in ("WAL-MART #1234", "WAL MART SUPERCENTER", "WALMART.COM",
            "PURCHASE WAL*MART 4471", "walmart neighborhood market"):
    rule = FakeRule(1, "Walmart", HOUSEHOLD)
    check("matches %r" % raw, SERVICE.match([rule], raw, None, Decimal("-50.00")), rule)

check("and does not match something else",
      SERVICE.match([FakeRule(1, "Walmart", HOUSEHOLD)], "TARGET T-1234", None,
                    Decimal("-50.00")), None)

print("\nThe pattern is normalised too, so how it was typed does not matter")
for typed in ("WALMART", "Wal-Mart", "wal mart", "  Walmart  "):
    rule = FakeRule(1, typed, HOUSEHOLD)
    check_true("typed as %r" % typed,
               SERVICE.match([rule], "WAL-MART #1234", None, Decimal("-50.00")) is rule)

print("\nThe counterparty is searched as well as the description")
# Extraction strips card noise into counterparty, so it is the cleaner target.
rule = FakeRule(1, "Pilot Point Feed", HOUSEHOLD)
check_true("matched on counterparty alone",
           SERVICE.match([rule], "FSP*PILOT POINT FEED S PILOT POINT TX",
                         "Pilot Point Feed", Decimal("-88.00")) is rule)

print("")
print("A substring match, but aligned to word boundaries")

# Tom's case: the keyword sits in the middle of a longer description.
check_true("a keyword anywhere in the description matches",
           SERVICE.match([FakeRule(1, "ATM WITHDRAWAL", CASH)],
                         "IN PERSON ATM WITHDRAWAL AT 5700 W PLANO PKY",
                         None, Decimal("-200.00")) is not None)

# But flattening the separators erases the word boundaries, and a plain
# substring test on the result is wrong in a way nobody would ever notice.
for pattern, description in (
    ("TARGET", "STARGETTER LLC"),
    ("ROSS",   "CROSSROADS MARKET"),
    ("MARTS",  "WAL MART SUPERCENTER"),
):
    check("%r does not match %r" % (pattern, description),
          SERVICE.match([FakeRule(1, pattern, HOUSEHOLD)], description, None,
                        Decimal("-50.00")), None)

check_true("while the real merchant still does",
           SERVICE.match([FakeRule(1, "TARGET", HOUSEHOLD)], "TARGET T-1234", None,
                         Decimal("-50.00")) is not None)

# The first occurrence may sit inside a word while a later one starts a real one.
check_true("every occurrence is checked, not just the first",
           SERVICE.match([FakeRule(1, "MART", HOUSEHOLD)], "SMART WAL MART", None,
                         Decimal("-50.00")) is not None)


print("\nA match cannot straddle the two fields")
# description ends "PILOT", counterparty starts "POINT" — joined naively that
# spells a merchant nobody paid.
check("no false match across the boundary",
      SERVICE.match([FakeRule(1, "PILOTPOINT", HOUSEHOLD)], "PILOT", "POINT",
                    Decimal("-10.00")), None)


# -- Priority and specificity -------------------------------------------------

print("\nThe specific rule beats the general one")

general = FakeRule(1, "WALMART", HOUSEHOLD, priority=100)
specific = FakeRule(2, "WALMART PHARMACY", MEDICAL, priority=50)
rules = sorted([general, specific],
               key=lambda r: (r.priority, -len(normalize(r.pattern)), r.id))
check("pharmacy goes to medical",
      SERVICE.match(rules, "WALMART PHARMACY 0123", None, Decimal("-30.00")).category_id,
      MEDICAL)
check("and a plain purchase still goes to household",
      SERVICE.match(rules, "WAL-MART #1234", None, Decimal("-30.00")).category_id,
      HOUSEHOLD)

print("At equal priority, the longer pattern is tried first")
# Without the length tiebreak the general rule wins every time and the specific
# one may as well not exist.
tied = sorted([FakeRule(1, "WALMART", HOUSEHOLD), FakeRule(2, "WALMART PHARMACY", MEDICAL)],
              key=lambda r: (r.priority, -len(normalize(r.pattern)), r.id))
check("pharmacy still wins", SERVICE.match(tied, "WALMART PHARMACY", None,
                                           Decimal("-30.00")).category_id, MEDICAL)


# -- The sign constraint ------------------------------------------------------

print("\nPAYROLL arriving is income; PAYROLL leaving is an expense")

incoming = FakeRule(1, "PAYROLL", INCOME, applies_to="credit")
outgoing = FakeRule(2, "PAYROLL", PAYROLL_EXPENSE, applies_to="debit")
both = [incoming, outgoing]
check("a deposit", SERVICE.match(both, "PAYROLL ACH CREDIT", None,
                                 Decimal("13595.25")).category_id, INCOME)
check("a withdrawal", SERVICE.match(both, "PAYROLL ACH DEBIT", None,
                                    Decimal("-13595.25")).category_id, PAYROLL_EXPENSE)

print("A credit-only rule ignores a debit entirely")
check("no match", SERVICE.match([incoming], "PAYROLL", None, Decimal("-100.00")), None)
check("zero counts as a credit, not a debit",
      SERVICE.match([incoming], "PAYROLL", None, Decimal("0.00")), incoming)


# -- What an assignment records -----------------------------------------------

print("\nAn assignment says it was automatic, and which rule did it")

assignment = SERVICE.assignment(FakeRule(7, "WALMART", HOUSEHOLD))
check("the category", assignment["category_id"], HOUSEHOLD)
check("that a rule filed it", assignment["category_source"], CategorySource.rule.value)
check("which rule, so a bad one is reversible", assignment["category_rule_id"], 7)
check_true("and when", isinstance(assignment["category_set_at"], datetime))
check("no rule, nothing written", SERVICE.assignment(None), {})


# -- Re-running over a matter -------------------------------------------------

class FakeAccountRepo:
    def get_by_matter(self, matter_id):
        class A:
            id = 1
        return [A()]


class FakeTransactionRepo:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple[int, dict]] = []

    def search(self, account_ids, limit=200, offset=0, **kwargs):
        return self.rows[offset:offset + limit], len(self.rows)

    def update(self, row_id, payload):
        self.updates.append((row_id, payload))


class FakeRuleRepo:
    def __init__(self, rules):
        self._rules = rules

    def active_for_matter(self, matter_id):
        return self._rules


def rerun(rows, rules, include_reviewed=False):
    import services.category_rule_service as mod

    repo = FakeTransactionRepo(rows)
    original = (mod.FinancialAccountRepository, mod.FinancialAccountTransactionRepository,
                mod.TransactionCategoryRuleRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo()
    mod.FinancialAccountTransactionRepository = lambda m: repo
    mod.TransactionCategoryRuleRepository = lambda m: FakeRuleRepo(rules)
    try:
        return CategoryRuleService().apply_to_matter(
            object(), 1, include_reviewed=include_reviewed), repo
    finally:
        (mod.FinancialAccountRepository, mod.FinancialAccountTransactionRepository,
         mod.TransactionCategoryRuleRepository) = original


RULES = [FakeRule(1, "WALMART", HOUSEHOLD)]

print("\nA rule fills what nobody has filed")

result, repo = rerun([FakeTransaction(1, "WAL-MART #1234", "-50.00")], RULES)
check("one filed", result["filed"], 1)
check("and written once", len(repo.updates), 1)
check("with the rule's category", repo.updates[0][1]["category_id"], HOUSEHOLD)

print("\nBut never what a person filed")

human = FakeTransaction(2, "WAL-MART #1234", "-50.00",
                        category_id=MEDICAL, category_source="human")
result, repo = rerun([human], RULES)
check("not even examined", result["examined"], 0)
check("and untouched", repo.updates, [])

print("\nNor what was filed before provenance existed")

# A category with no source is almost certainly a paralegal's work from before
# these columns were added. Guessing wrong in that direction destroys judgment.
legacy = FakeTransaction(3, "WAL-MART #1234", "-50.00", category_id=MEDICAL)
result, repo = rerun([legacy], RULES)
check("left alone", repo.updates, [])

print("\nA machine's own assignment can be corrected by a better rule")

stale = FakeTransaction(4, "WALMART PHARMACY", "-30.00",
                        category_id=HOUSEHOLD, category_source="rule", category_rule_id=1)
better = sorted([FakeRule(1, "WALMART", HOUSEHOLD),
                 FakeRule(2, "WALMART PHARMACY", MEDICAL, priority=50)],
                key=lambda r: r.priority)
result, repo = rerun([stale], better)
check("re-filed", result["refiled"], 1)
check("to medical", repo.updates[0][1]["category_id"], MEDICAL)
check("and the new rule is recorded", repo.updates[0][1]["category_rule_id"], 2)

print("\nA confirmed assignment is left alone unless asked for")

confirmed = FakeTransaction(5, "WALMART PHARMACY", "-30.00", category_id=HOUSEHOLD,
                            category_source="rule", category_rule_id=1,
                            category_reviewed_at=datetime.now(timezone.utc))
result, repo = rerun([confirmed], better)
check("skipped by default", repo.updates, [])
result, repo = rerun([confirmed], better, include_reviewed=True)
check("but re-filed when asked", len(repo.updates), 1)

print("\nAn unchanged assignment is not rewritten")

settled = FakeTransaction(6, "WAL-MART #1234", "-50.00", category_id=HOUSEHOLD,
                          category_source="rule", category_rule_id=1)
result, repo = rerun([settled], RULES)
check("no write", repo.updates, [])
check("but it was examined", result["examined"], 1)

print("\nWhat no rule claims is the work that remains")

result, _ = rerun([FakeTransaction(7, "SOME OBSCURE MERCHANT", "-12.00")], RULES)
check("counted", result["unmatched"], 1)
check("and nothing filed", result["filed"], 0)

print("\nNo rules at all is a no-op, not an error")
result, repo = rerun([FakeTransaction(8, "WAL-MART", "-50.00")], [])
check("nothing examined", result["examined"], 0)
check("nothing written", repo.updates, [])

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all category-rule checks passed")
