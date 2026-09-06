"""
app/services/category_rule_service.py - Filing transactions by keyword.

Most of a production is ordinary. The same grocer, the same mortgage servicer,
the same bank fee, month after month, and a paralegal filing each one by hand is
work a rule can do — leaving the genuinely ambiguous lines, which is where the
judgment actually is.

Three rules govern this, and they are what make an automatic assignment
defensible rather than merely convenient:

**A rule never overwrites a person.** It fills a line nobody has categorized, or
replaces a value the machine itself set. A paralegal's judgment is not reversed
by a keyword.

**Every assignment says it was automatic, and which rule did it.** That makes
the review queue possible ("filed by rule, nobody has checked it"), makes a bad
rule reversible in one query, and answers the question that matters on the
stand — *"why is this Household Supplies?"* — in one sentence.

**Matching is punctuation- and case-blind.** WALMART has to find "WAL-MART
#1234", "WAL MART SUPERCENTER" and "WALMART.COM", because that is what "Walmart
and its derivatives" means to the person writing the rule.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from db.models.financial import CategorySource
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountTransactionRepository,
    TransactionCategoryRuleRepository,
)
from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

ZERO = Decimal("0.00")

# Rows per page when re-running rules over an existing matter.
_PAGE = 1000

_ALPHANUMERIC = re.compile(r"[A-Z0-9]+")


def normalize(text: Optional[str]) -> str:
    """
    Reduce a description to what a keyword should match against.

    Uppercase, letters and digits only. This is the whole reason WALMART finds
    "WAL-MART #1234": both sides lose their punctuation, so the pattern the firm
    typed and the string the bank printed meet in the middle.

    Applied to the pattern too, so a rule typed as "Wal-Mart" behaves the same as
    one typed "WALMART" and nobody has to know which form to use.
    """
    return "".join(_ALPHANUMERIC.findall((text or "").upper()))


def prepare(text: Optional[str]) -> tuple[str, frozenset[int]]:
    """
    The searchable form of a description, and where its words begin and end.

    Dropping the separators is what lets WALMART find WAL-MART, but it also
    erases every word boundary — and a plain substring test on the result is
    wrong in a way nobody would catch:

        TARGET  found inside  S-TARGET-TER LLC
        ROSS    found inside  C-ROSS-ROADS MARKET
        MARTS   found inside  WAL MART S-UPERCENTER

    Each of those is a confident, silent mis-filing of evidence. So the offsets
    survive the flattening: a match must begin where a word begins and end where
    one ends. Every intended match still works, because "WAL-MART" flattens to
    WAL|MART and a pattern spanning whole words lands exactly on those seams.

    :return: The flattened text, and every offset at which a word starts or ends.
    :rtype: tuple[str, frozenset[int]]
    """
    boundaries = {0}
    position = 0
    tokens = _ALPHANUMERIC.findall((text or "").upper())
    for token in tokens:
        position += len(token)
        boundaries.add(position)
    return "".join(tokens), frozenset(boundaries)


def _contains(haystack: str, boundaries: frozenset[int], needle: str) -> bool:
    """
    Whether the needle appears, aligned to word boundaries.

    Every occurrence is checked, not just the first: MART sits inside SMART at
    one offset and starts a real word at another, and only the second counts.
    """
    if not needle:
        return False
    start = haystack.find(needle)
    while start != -1:
        if start in boundaries and (start + len(needle)) in boundaries:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _sign_allows(rule: Any, amount: Decimal) -> bool:
    """PAYROLL arriving is income; PAYROLL leaving is an expense."""
    if rule.applies_to == "credit":
        return amount >= ZERO
    if rule.applies_to == "debit":
        return amount < ZERO
    return True


class CategoryRuleService:
    """Applies the firm's keyword rules to transactions."""

    def rules_for(self, manager: DatabaseManager, matter_id: int) -> list[Any]:
        """
        The rules in force on a matter, in the order they should be tried.

        Firm-wide rules and this matter's, sorted by priority then by pattern
        length descending. The length tiebreak matters: two rules at the same
        priority where one pattern contains the other — WALMART and WALMART
        PHARMACY — must try the longer first, or the general one wins every time
        and the specific rule may as well not exist.
        """
        rules = TransactionCategoryRuleRepository(manager).active_for_matter(matter_id)
        return sorted(rules, key=lambda r: (r.priority, -len(normalize(r.pattern)), r.id))

    def match(self, rules: list[Any], description: Optional[str],
              counterparty: Optional[str], amount: Optional[Decimal]) -> Optional[Any]:
        """
        The first rule that claims this transaction, or None.

        Both the description and the counterparty are searched. The counterparty
        is the merchant with card noise already stripped — "FSP*PILOT POINT FEED
        S PILOT POINT TX" becomes "Pilot Point Feed" — so it is the cleaner
        target whenever extraction managed to fill it.
        """
        fields = (prepare(description), prepare(counterparty))
        value = amount if amount is not None else ZERO
        for rule in rules:
            if not _sign_allows(rule, value):
                continue
            needle = normalize(rule.pattern)
            # The two fields are searched separately rather than joined, so a
            # match cannot straddle the seam between them and invent a
            # merchant nobody paid.
            if any(_contains(text, bounds, needle) for text, bounds in fields):
                return rule
        return None

    def assignment(self, rule: Optional[Any]) -> dict[str, Any]:
        """
        The fields a matched rule writes onto a transaction.

        Returned as a dict so the ingest path can fold it into the row it is
        already building rather than following the insert with an update.
        """
        if rule is None:
            return {}
        return {
            "category_id": rule.category_id,
            "category_source": CategorySource.rule.value,
            "category_rule_id": rule.id,
            "category_set_at": datetime.now(timezone.utc),
        }

    def apply_to_matter(
        self,
        manager: DatabaseManager,
        matter_id: int,
        include_reviewed: bool = False,
    ) -> dict[str, Any]:
        """
        Re-run the rules across a matter's transactions.

        This is what makes "we will keep adding keywords" worth doing: a rule
        written today reaches the statements ingested last month. Without it the
        list only ever improves the next import.

        **Only lines a machine filed, or nobody filed, are touched.** A human
        assignment is left exactly as it is — that is the difference between a
        tool that saves work and one that quietly undoes it.

        :param include_reviewed: Also re-file automatic assignments a person has
            already confirmed. Off by default: confirming one is a decision, and
            a later rule change should not silently reverse it.
        :return: How many were examined, changed, and left alone.
        :rtype: dict[str, Any]
        """
        rules = self.rules_for(manager, matter_id)
        if not rules:
            return {"examined": 0, "filed": 0, "refiled": 0, "unmatched": 0, "rules": 0}

        accounts = FinancialAccountRepository(manager).get_by_matter(matter_id)
        if not accounts:
            return {"examined": 0, "filed": 0, "refiled": 0, "unmatched": 0,
                    "rules": len(rules)}

        transaction_repo = FinancialAccountTransactionRepository(manager)
        scope = sorted(a.id for a in accounts)

        examined = filed = refiled = unmatched = 0
        offset = 0
        while True:
            rows, total = transaction_repo.search(
                account_ids=scope, limit=_PAGE, offset=offset,
            )
            if not rows:
                break
            for row in rows:
                source = getattr(row, "category_source", None)
                if source == CategorySource.human.value:
                    continue
                if row.category_id is not None and source is None:
                    # Filed before provenance existed. Treat it as a person's
                    # work: it almost certainly was, and guessing wrong in that
                    # direction destroys real judgment.
                    continue
                if (not include_reviewed
                        and getattr(row, "category_reviewed_at", None) is not None):
                    continue

                examined += 1
                rule = self.match(rules, row.description, row.counterparty, row.amount)
                if rule is None:
                    unmatched += 1
                    continue
                if row.category_id == rule.category_id and getattr(
                        row, "category_rule_id", None) == rule.id:
                    continue

                transaction_repo.update(row.id, self.assignment(rule))
                if row.category_id is None:
                    filed += 1
                else:
                    refiled += 1

            offset += len(rows)
            if offset >= total:
                break

        LOGGER.info(
            "category_rule_service.apply_to_matter: matter=%s rules=%d examined=%d "
            "filed=%d refiled=%d unmatched=%d",
            matter_id, len(rules), examined, filed, refiled, unmatched,
        )
        return {
            "examined": examined, "filed": filed, "refiled": refiled,
            "unmatched": unmatched, "rules": len(rules),
        }


category_rule_service = CategoryRuleService()
