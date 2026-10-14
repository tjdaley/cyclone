"""
tests/test_client_portal.py - What a client may see, and the one thing they may do.

THE CLIENT IS THE FIRST CALLER WHO IS NOT ON THE FIRM'S SIDE OF THE TABLE.
Every access decision before this one was made between staff, all of whom are
entitled to a matter's records; a wrong filter there showed a paralegal
something they could have opened anyway. Here it crosses a party boundary, and
`SupabaseManager` holds the service-role key and bypasses RLS, so the database
will not catch the mistake.

So the scope rules are pinned here, and so is the asymmetry that makes tagging
workable: a client tags freely, staff clear what is wrong, and **a cleared tag
can never be re-applied by the client** — only by staff. That is why clearing is
a suppression rather than a delete (migration 044): the row holds the
`(transaction, tag)` pair, so the client's next click lands on it and is refused
instead of creating a fresh link.

Run:  venv/Scripts/python.exe tests/test_client_portal.py
"""
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.client_portal_service as mod  # noqa: E402
from db.models.financial import TagSource  # noqa: E402
from services.client_portal_service import (  # noqa: E402
    PortalDenied, TaggingClosed, client_portal_service,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def denied(name, call, expect=PortalDenied):
    try:
        call()
    except expect as e:
        print("  PASS %s (%s)" % (name, str(e)[:60]))
        return str(e)
    except Exception as e:  # noqa: BLE001
        print("  FAIL %s — raised %s instead" % (name, type(e).__name__))
        FAILURES.append(name)
        return ""
    print("  FAIL %s — allowed" % name)
    FAILURES.append(name)
    return ""


class Row(dict):
    __getattr__ = dict.get


# ── The world: two clients, two matters, one shared firm tag list ────────
OURS, THEIRS = 1, 2          # client ids
MY_MATTER, OTHER_MATTER = 10, 20

MATTERS = [
    Row(id=MY_MATTER, client_id=OURS, matter_name="Salmons", matter_number="DF-24-01234",
        client_tagging_enabled=True),
    Row(id=OTHER_MATTER, client_id=THEIRS, matter_name="Harrison", matter_number="DF-24-09999",
        client_tagging_enabled=True),
]
ACCOUNTS = [
    Row(id=100, matter_id=MY_MATTER, institution="Chase", account_type="checking",
        account_number_last4="4855", is_closed=False, name_on_account="M SEGALINI"),
    Row(id=200, matter_id=OTHER_MATTER, institution="First Financial", account_type="savings",
        account_number_last4="9260", is_closed=False, name_on_account=None),
]
TRANSACTIONS = [
    Row(id=1000, financial_account_id=100, statement_id=1, transaction_date=date(2024, 3, 2),
        description="TARGET.COM", amount=Decimal("135.26")),
    Row(id=2000, financial_account_id=200, statement_id=2, transaction_date=date(2024, 3, 5),
        description="NOT YOURS", amount=Decimal("-50.00")),
]
TAGS = [
    Row(id=7, matter_id=None, label="Client: possible waste", description=None, color="rose",
        is_active=True, client_visible=True),
    Row(id=8, matter_id=None, label="Reconstitute — paramour", description=None, color="navy",
        is_active=True, client_visible=False),
]


class FakeMatters:
    def select_many(self, condition, sort_by=None):
        return [m for m in MATTERS if m["client_id"] == condition["client_id"]], 0


class FakeAccounts:
    def get_by_matter(self, matter_id):
        return [a for a in ACCOUNTS if a["matter_id"] == matter_id]

    def select_one(self, condition):
        return next((a for a in ACCOUNTS if a["id"] == condition["id"]), None)


class FakeTransactions:
    def select_one(self, condition):
        return next((t for t in TRANSACTIONS if t["id"] == condition["id"]), None)

    def select_many(self, condition, sort_by=None):
        wanted = condition["id"]
        return [t for t in TRANSACTIONS if t["id"] in wanted], 0

    def get_by_statement(self, statement_id, include_deleted=False):
        return [t for t in TRANSACTIONS if t["statement_id"] == statement_id]


class FakeTags:
    def available_for_matter(self, matter_id, include_inactive=False):
        return [t for t in TAGS if t["matter_id"] in (None, matter_id)]

    def select_one(self, condition):
        return next((t for t in TAGS if t["id"] == condition["id"]), None)


class FakeLinks:
    def __init__(self):
        self.rows: list[Row] = []

    def find_link(self, transaction_id, tag_id):
        return next((r for r in self.rows
                     if r["transaction_id"] == transaction_id and r["tag_id"] == tag_id), None)

    def insert(self, data):
        row = Row(data)
        row["id"] = len(self.rows) + 1
        row.setdefault("cleared_at", None)
        row["created_at"] = datetime.now(timezone.utc)
        self.rows.append(row)
        return row

    def update(self, link_id, patch):
        row = next(r for r in self.rows if r["id"] == link_id)
        row.update(patch)
        return row

    def delete(self, link_id):
        self.rows = [r for r in self.rows if r["id"] != link_id]

    def select_one(self, condition):
        return next((r for r in self.rows if r["id"] == condition["id"]), None)

    def select_many(self, condition, sort_by=None):
        rows = self.rows
        if "source" in condition:
            rows = [r for r in rows if r["source"] == condition["source"]]
        if "cleared_at" in condition and condition["cleared_at"] is None:
            rows = [r for r in rows if not r.get("cleared_at")]
        return rows, len(rows)

    def for_transactions(self, ids):
        out: dict[int, list[int]] = {}
        for r in self.rows:
            if r["transaction_id"] in ids and not r.get("cleared_at"):
                out.setdefault(r["transaction_id"], []).append(r["tag_id"])
        return out

    def cleared_for_transactions(self, ids):
        out: dict[int, list[int]] = {}
        for r in self.rows:
            if r["transaction_id"] in ids and r.get("cleared_at"):
                out.setdefault(r["transaction_id"], []).append(r["tag_id"])
        return out


links = FakeLinks()
mod.MatterRepository = lambda m: FakeMatters()
mod.FinancialAccountRepository = lambda m: FakeAccounts()
mod.TransactionTagRepository = lambda m: FakeTags()
mod.TransactionTagLinkRepository = lambda m: links

import db.repositories.financial as repos_mod  # noqa: E402
repos_mod.FinancialAccountTransactionRepository = lambda m: FakeTransactions()


# ── 1. Scope: a client reaches their own matters and nothing else ────────
print("1. the party boundary")
check("their matters", [m["id"] for m in client_portal_service.matters(None, OURS)], [MY_MATTER])
check("and only theirs", client_portal_service.matter_ids(None, OURS), {MY_MATTER})
check("their accounts", [a["id"] for a in
                         client_portal_service.accounts(None, OURS, MY_MATTER)], [100])
message = denied("another client's matter is refused",
                 lambda: client_portal_service.accounts(None, OURS, OTHER_MATTER))
# A 403 would confirm the matter exists, which is an inventory of the firm's
# cases available to anyone with an account and a loop.
check("without saying whether it exists", "not available" in message, True)
denied("another client's account is refused",
       lambda: client_portal_service.statements(None, OURS, 200))
denied("another client's transaction is refused",
       lambda: client_portal_service.require_transaction(None, OURS, 2000))
check("their own transaction resolves to their matter",
      client_portal_service.require_transaction(None, OURS, 1000)[1], MY_MATTER)

# ── 2. Only tags the firm published ──────────────────────────────────────
print("2. the tag list is opt-in")
check("the client-visible one is offered",
      [t["id"] for t in client_portal_service.tags(None, OURS, MY_MATTER)], [7])
denied("a strategy tag cannot be applied even by id",
       lambda: client_portal_service.apply_tag(None, OURS, 1000, 8))
check("and nothing was written", len(links.rows), 0)

# ── 3. Tagging, and taking it back ───────────────────────────────────────
print("3. the one write")
check("applied", client_portal_service.apply_tag(None, OURS, 1000, 7), "applied")
check("recorded as the client's", links.rows[0]["source"], TagSource.client)
check("attributed to them", links.rows[0]["tagged_by_client_id"], OURS)
check("and never to a staff member", links.rows[0].get("tagged_by_staff_id"), None)
check("applying twice is not an error",
      client_portal_service.apply_tag(None, OURS, 1000, 7), "already")
check("still one link", len(links.rows), 1)
check("they can take back their own",
      client_portal_service.remove_tag(None, OURS, 1000, 7), "removed")
check("which deletes it, leaving them free to change their mind", len(links.rows), 0)

# ── 4. THE ASYMMETRY: a cleared tag stays cleared, for the client ────────
print("4. cleared means cleared")
client_portal_service.apply_tag(None, OURS, 1000, 7)
link_id = links.rows[0]["id"]
client_portal_service.clear_tag(None, link_id, staff_id=3)
check("the row survives — it holds the pair", len(links.rows), 1)
check("marked cleared", bool(links.rows[0]["cleared_at"]), True)
check("by a named person", links.rows[0]["cleared_by_staff_id"], 3)
check("and it counts as no tag anywhere",
      links.for_transactions([1000]).get(1000, []), [])
check("while the portal can still show it greyed",
      links.cleared_for_transactions([1000])[1000], [7])

message = denied("the client cannot put it back",
                 lambda: client_portal_service.apply_tag(None, OURS, 1000, 7),
                 expect=TaggingClosed)
check("and is told why, in words they can read",
      "reviewed this one and removed" in message, True)
check("removing a cleared tag is a no-op, not an error",
      client_portal_service.remove_tag(None, OURS, 1000, 7), "absent")

print("   staff can")
client_portal_service.restore_tag(None, link_id, staff_id=3)
check("restored", bool(links.rows[0]["cleared_at"]), False)
check("as a STAFF tag, because applying it is now their judgment",
      links.rows[0]["source"], TagSource.staff.value)
check("so the client cannot remove it either",
      denied("", lambda: client_portal_service.remove_tag(None, OURS, 1000, 7)) != "", True)

# ── 5. The hearing-prep lock ─────────────────────────────────────────────
print("5. tagging paused for hearing prep")
links.rows = []
MATTERS[0]["client_tagging_enabled"] = False
message = denied("applying is refused", lambda: client_portal_service.apply_tag(None, OURS, 1000, 7),
                 expect=TaggingClosed)
check("with a sentence that does not read as a fault",
      "paused" in message and "safe" in message, True)
denied("and so is removing", lambda: client_portal_service.remove_tag(None, OURS, 1000, 7),
       expect=TaggingClosed)
check("nothing written while locked", len(links.rows), 0)
MATTERS[0]["client_tagging_enabled"] = True
check("and it works again when staff turn it back on",
      client_portal_service.apply_tag(None, OURS, 1000, 7), "applied")

# ── 6. What staff see of it ──────────────────────────────────────────────
print("6. the review screen")
review = client_portal_service.review_list(None, MY_MATTER)
check("one row to review", len(review), 1)
check("naming the line and the tag",
      (review[0]["transaction_id"], review[0]["tag_label"]),
      (1000, "Client: possible waste"))
check("scoped to the matter's accounts — not every case's client tags",
      client_portal_service.review_list(None, OTHER_MATTER), [])
counts = client_portal_service.client_tag_counts(None, OURS)
check("counted", counts["applied"], 1)
check("with the coaching signal: how many staff removed", counts["cleared"], 0)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
