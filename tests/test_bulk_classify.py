"""
tests/test_bulk_classify.py - A bulk tag or category change is a few requests, not one per line.

Filing 100 selected lines used to send 100 UPDATEs, and tagging them a lookup
plus an insert each, so the screen sat still for seconds. The count of requests
is what is pinned here, against a fake PostgREST, along with every rule the
line-at-a-time loop carried: a client's tag is CLEARED rather than deleted, a
staff tag is deleted, a cleared tag put back becomes the firm's, and re-applying
counts as nothing.

Run:  venv/Scripts/python.exe tests/test_bulk_classify.py
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.guest_access_service as guest_mod  # noqa: E402
import services.transaction_search_service as mod  # noqa: E402
from db.repositories.financial import (  # noqa: E402
    FinancialAccountTransactionRepository, TransactionTagLinkRepository,
)
from services.transaction_search_service import transaction_search_service as svc  # noqa: E402

FAILURES: list[str] = []
LINKS = "financial_account_transaction_tags"
TRANSACTIONS = "financial_account_transactions"


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Query:
    """One PostgREST call: records itself and applies to the in-memory table."""

    def __init__(self, db, table, kind, payload=None, **options):
        self.db, self.table, self.kind, self.payload, self.options = db, table, kind, payload, options
        self.ids = []

    def in_(self, column, ids):
        assert column == "id"
        self.ids = list(ids)
        return self

    def execute(self):
        self.db.writes.append((self.kind, self.table))
        rows = self.db.tables[self.table]
        if self.kind == "update":
            for row in rows:
                if row["id"] in self.ids:
                    row.update(self.payload)
        elif self.kind == "delete":
            self.db.tables[self.table] = [r for r in rows if r["id"] not in self.ids]
        else:
            self.db.upsert_options.append(self.options)
            for data in self.payload:
                self.db.next_id += 1
                rows.append({"id": self.db.next_id, **data})
        return SimpleNamespace(data=[])


class Table:
    def __init__(self, db, table):
        self.db, self.table = db, table

    def update(self, payload, **options):
        return Query(self.db, self.table, "update", payload, **options)

    def delete(self, **options):
        return Query(self.db, self.table, "delete", **options)

    def upsert(self, payload, **options):
        return Query(self.db, self.table, "upsert", payload, **options)


class Db:
    """Stands in for the manager: what the base repository and raw client reach."""

    def __init__(self, line_count):
        self.tables = {
            TRANSACTIONS: [{"id": i, "financial_account_id": 1, "category_id": None,
                            "statement_id": 1, "deleted_at": None}
                           for i in range(1, line_count + 1)],
            LINKS: [],
        }
        self.reads = 0
        self.writes = []
        self.upsert_options = []
        self.next_id = 1000
        self.client = SimpleNamespace(table=lambda table: Table(self, table))

    def select_many(self, table, *args, **kwargs):
        self.reads += 1
        condition = next((a for a in args if isinstance(a, dict)), kwargs.get("condition", {}))
        found = []
        for row in self.tables[table]:
            if all(row.get(k) in v if isinstance(v, list) else row.get(k) == v
                   for k, v in condition.items()):
                found.append(SimpleNamespace(**row))
        return found, len(found)

    def requests(self):
        return self.reads + len(self.writes)

    def link(self, transaction_id, tag_id=5):
        return next((r for r in self.tables[LINKS]
                     if r["transaction_id"] == transaction_id and r["tag_id"] == tag_id), None)

    def add_link(self, transaction_id, source, tag_id=5, **extra):
        self.next_id += 1
        self.tables[LINKS].append({
            "id": self.next_id, "transaction_id": transaction_id, "tag_id": tag_id,
            "source": source, "cleared_at": None, "tagged_by_staff_id": None,
            "tagged_by_client_id": None, "tagged_by_guest_id": None,
            "cleared_by_staff_id": None, **extra})


class FakeOne:
    def __init__(self, row):
        self.row = row

    def select_one(self, condition):
        return self.row


mod.TransactionTagRepository = lambda m: FakeOne(SimpleNamespace(id=5, matter_id=None, guest_id=None))
mod.TransactionCategoryRepository = lambda m: FakeOne(SimpleNamespace(id=9))
mod.FinancialAccountRepository = lambda m: SimpleNamespace(
    get_by_matter=lambda matter_id: [SimpleNamespace(id=1)])
ids = list(range(1, 101))


print("1. a hundred lines is a handful of requests")
db = Db(100)
check("categorizing reports every line", svc.set_category(db, 1, ids, 9), 100)
check("every line is filed", all(r["category_id"] == 9 for r in db.tables[TRANSACTIONS]), True)
check("one write, not a hundred", len(db.writes), 1)
db = Db(100)
check("a clear reaches every line too", svc.set_category(db, 1, ids, None), 100)
db = Db(100)
check("mark reviewed reports every line", svc.mark_reviewed(db, 1, ids, staff_id=2), 100)
check("marks them", all(r.get("category_reviewed_by_staff_id") == 2
                        for r in db.tables[TRANSACTIONS]), True)
check("one write again", len(db.writes), 1)
db = Db(100)
check("tagging counts what it added", svc.apply_tag(db, 1, ids, 5, staff_id=2), 100)
check("every line carries it", len(db.tables[LINKS]), 100)
check("named as the staff member's", db.link(7)["tagged_by_staff_id"], 2)
check("one write", len(db.writes), 1)
check("two reads: verify the lines, find the links", db.reads, 2)
check("a double click cannot raise a duplicate",
      (db.upsert_options[0]["on_conflict"], db.upsert_options[0]["ignore_duplicates"]),
      ("transaction_id,tag_id", True))
check("re-applying changes nothing and writes nothing",
      (svc.apply_tag(db, 1, ids, 5, staff_id=2), len(db.writes)), (0, 1))

print("2. more than a URL can carry still reaches every line")
db = Db(450)
big = list(range(1, 451))
svc.set_category(db, 1, big, 9)
check("every line", all(r["category_id"] == 9 for r in db.tables[TRANSACTIONS]), True)
check("in chunks", len(db.writes), 3)

print("3. the rules the loop carried")
db = Db(4)
db.add_link(1, "client", tagged_by_client_id=3)
db.add_link(2, "staff", tagged_by_staff_id=8)
db.add_link(3, "client", tagged_by_client_id=3, cleared_at="2026-01-01T00:00:00+00:00",
            cleared_by_staff_id=8)
check("removing counts the two live ones", svc.apply_tag(db, 1, [1, 2, 3, 4], 5, 2, remove=True), 2)
check("a client's tag is cleared, not deleted, naming who",
      (db.link(1)["cleared_by_staff_id"], db.link(1)["cleared_at"] is not None), (2, True))
check("a staff tag is deleted", db.link(2), None)
check("one already cleared is left alone", db.link(3)["cleared_by_staff_id"], 8)
check("a line with no tag stays that way", db.link(4), None)

db = Db(3)
db.add_link(1, "client", tagged_by_client_id=3, cleared_at="2026-01-01T00:00:00+00:00",
            cleared_by_staff_id=8)
db.add_link(2, "client", tagged_by_client_id=3)
check("applying counts the restored and the new, not the live",
      svc.apply_tag(db, 1, [1, 2, 3], 5, staff_id=2), 2)
restored = db.link(1)
check("a restored tag becomes the firm's",
      (restored["source"], restored["tagged_by_staff_id"], restored["tagged_by_client_id"],
       restored["cleared_at"], restored["cleared_by_staff_id"]),
      ("staff", 2, None, None, None))
check("a client's live tag stays theirs", db.link(2)["source"], "client")
check("the new one is staff's", db.link(3)["source"], "staff")

print("4. a line on another matter stops the whole change")
db = Db(2)
db.tables[TRANSACTIONS][1]["financial_account_id"] = 99
try:
    svc.set_category(db, 1, [1, 2], 9)
    check("refused", False, True)
except ValueError:
    check("refused", True, True)
check("and nothing was written", db.writes, [])

print("5. a financial professional's bulk tagging")
guest_mod.TransactionTagLinkRepository = TransactionTagLinkRepository
guest_svc = guest_mod.guest_access_service
guest_svc._own_tag = lambda manager, guest_id, tag_id: SimpleNamespace(id=tag_id, matter_id=1)
db = Db(100)
check("adds every line", guest_svc.apply_tag(db, 4, 1, ids, 5), 100)
check("as theirs", (db.link(1)["source"], db.link(1)["tagged_by_guest_id"]), ("guest", 4))
check("in one write", len(db.writes), 1)
check("removes every line", guest_svc.apply_tag(db, 4, 1, ids, 5, remove=True), 100)
check("in one more", (len(db.tables[LINKS]), len(db.writes)), (0, 2))

print("6. the repositories ask only for what they need back")
db = Db(1)
FinancialAccountTransactionRepository(db).update_many([1], {"category_id": 9})
check("the update is a real one", db.tables[TRANSACTIONS][0]["category_id"], 9)

print("7. a chip counts what clicking it finds")
mod.FinancialAccountStatementRepository = lambda m: SimpleNamespace(rejected_ids=lambda matter_id: [7])
db = Db(6)
db.add_link(1, "staff")
db.add_link(2, "client")
db.add_link(3, "client", cleared_at="2026-01-01T00:00:00+00:00")
db.add_link(4, "staff")
db.tables[TRANSACTIONS][3]["deleted_at"] = "2026-01-01T00:00:00+00:00"
db.add_link(5, "staff")
db.tables[TRANSACTIONS][4]["statement_id"] = 7
db.add_link(6, "client")
db.tables[TRANSACTIONS][5]["financial_account_id"] = 99
db.add_link(1, "staff", tag_id=6)
check("live marks on this matter only, per tag", svc.tag_usage(db, 1, [5, 6, 8]), {5: 2, 6: 1})
check("no tags, no question", svc.tag_usage(db, 1, []), {})

print("8. an exhibit's description carries the payee and location")
describe = mod._exhibit_description
check("both", describe({"description": "Check 2072", "counterparty": "Blue Bee Construction",
                        "location": "McKinney, TX"}),
      "Check 2072 — Blue Bee Construction; McKinney, TX")
check("payee only", describe({"description": "Check 2072", "counterparty": "Blue Bee"}),
      "Check 2072 — Blue Bee")
check("location only", describe({"description": "Check 2072", "location": "Plano, TX"}),
      "Check 2072 — Plano, TX")
check("neither", describe({"description": "Check 2072"}), "Check 2072")
check("a payee the description names is not repeated",
      describe({"description": "TARGET T-1234", "counterparty": "Target"}), "TARGET T-1234")

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
