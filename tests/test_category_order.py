"""
tests/test_category_order.py - Reading order for the chart of accounts.

`display_order` was documented as a sort key across the whole tree, and that
cannot express nesting. In the firm's real chart Utilities and Lawn/Landscaping
both sit at 115 while Utilities' own children start at 120, so a flat sort put
Electricity, Gas and Telephone *after* Pool and Other Staff — indented under a
branch they do not belong to. On the FIS and in every category picker that reads
as no hierarchy at all.

The rows below are taken from the deployed chart, unedited, collisions included.

Run:  venv/Scripts/python.exe tests/test_category_order.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.repositories.financial import TransactionCategoryRepository  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


class FakeCategory:
    def __init__(self, id, description, parent_id, display_order, is_active=True):
        self.id = id
        self.description = description
        self.parent_id = parent_id
        self.display_order = display_order
        self.is_active = is_active


# Straight from the deployed chart. Note 5/26/77 all at 115 and 27/78 both at
# 116 — the collisions that broke the flat sort.
CHART = [FakeCategory(*row) for row in [
    (47, "Income", None, 1),
    (48, "Salary & Wages (W-2)", 47, 2),
    (56, "Other Income", 47, 98),
    (1, "Housing", None, 99),
    (2, "Rent", 1, 100),
    (4, "Property Taxes", 1, 110),
    (5, "Utilities", 1, 115),
    (26, "Lawn/Landscaping", 1, 115),
    (77, "Security - On-Premesis", 1, 115),
    (27, "Pool", 1, 116),
    (78, "Pest Control", 1, 116),
    (28, "Other Staff", 1, 117),
    (6, "Electricity", 5, 120),
    (7, "Gas", 5, 125),
    (18, "Telephone", 5, 132),
    (67, "Municipal Utilities", 5, 134),
    (9, "Transportation", None, 200),
    (11, "Auto Insurance", 9, 210),
]]


def ordered(chart=None, include_inactive=False):
    repo = TransactionCategoryRepository.__new__(TransactionCategoryRepository)
    rows = CHART if chart is None else chart
    repo.get_all = lambda include_inactive=False: [
        c for c in rows if include_inactive or c.is_active
    ]
    return [c.description for c in repo.get_ordered(include_inactive=include_inactive)]


print("\nChildren follow their parent, whatever the numbers collide on")

names = ordered()
check("Utilities' children sit directly under it", names[names.index("Utilities"):names.index("Utilities") + 5],
      ["Utilities", "Electricity", "Gas", "Telephone", "Municipal Utilities"])
check("and Housing's other children come after them",
      names[names.index("Municipal Utilities") + 1], "Lawn/Landscaping")

# The bug in one assertion: Electricity used to land after Other Staff.
check("Electricity is no longer stranded past Other Staff",
      names.index("Electricity") < names.index("Other Staff"), True)

print("\nTop-level sections stay in display order")
check("Income, Housing, Transportation",
      [n for n in names if n in ("Income", "Housing", "Transportation")],
      ["Income", "Housing", "Transportation"])

print("\nSiblings tied on display_order fall back to id, deterministically")
# 5, 26 and 77 all sit at 115.
check("tie broken by id", names[names.index("Municipal Utilities") + 1:][:2],
      ["Lawn/Landscaping", "Security - On-Premesis"])
check("stable across runs", ordered(), names)

print("\nEvery category appears exactly once")
check("no duplicates", len(names), len(set(names)))
check("none dropped", len(names), len(CHART))


print("\nA child whose parent is filtered out is still listed")

# Retiring a parent must not silently take its children off the picker — they
# may still have transactions filed under them.
hidden_parent = [FakeCategory(*row) for row in [
    (1, "Housing", None, 99, True),
    (5, "Utilities", 1, 115, False),
    (6, "Electricity", 5, 120, True),
]]
names = ordered(hidden_parent)
check("the active child survives", "Electricity" in names, True)
check("the retired parent is gone", "Utilities" in names, False)
check("nothing is lost", sorted(names), ["Electricity", "Housing"])

print("  and with inactive included, the tree reassembles")
names = ordered(hidden_parent, include_inactive=True)
check("in order", names, ["Housing", "Utilities", "Electricity"])


print("\nA cycle from a bad edit terminates")

cyclic = [FakeCategory(*row) for row in [
    (1, "A", 2, 10),
    (2, "B", 1, 20),
    (3, "Root", None, 5),
]]
names = ordered(cyclic)
check("the reachable root is first", names[0], "Root")
check("and the cycle's members still appear once each", sorted(names), ["A", "B", "Root"])

print("\nAn empty chart")
check("no rows, no error", ordered([]), [])

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all category-order checks passed")
