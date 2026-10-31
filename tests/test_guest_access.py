"""
tests/test_guest_access.py - A financial professional sees their matters and nothing else (052).

The rules this suite holds, each a way the feature could be quietly wrong:

  * **The guest role is in no staff role list.** Staff routes are not
    matter-scoped; one appearance opens every matter in the firm. Checked by
    reading the routers, so a new route cannot slip it in.
  * **Scope is a live grant on THIS matter**: pending, revoked, expired, a
    deactivated guest and another matter all answer the same "no access".
  * **Only their own tags**: filters narrowed, chips filtered, "untagged"
    refused, removed lines never included, another guest's tag refused.
  * **The ledger outlives a deleted statement**, and records what appeared.
  * **One login cannot be staff and guest at once.**

Run:  venv/Scripts/python.exe tests/test_guest_access.py
"""
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.guest_access_service as mod  # noqa: E402
from db.models.guest import ConsentMethod, GuestDesignation  # noqa: E402
from services.guest_access_service import (  # noqa: E402
    GuestConflict, GuestDenied, effective_status, guest_access_service as svc,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def raises(name, exc, fn):
    try:
        fn()
    except exc:
        check(name, "raised", "raised")
        return
    except Exception as e:  # noqa: BLE001
        check(name, type(e).__name__, exc.__name__)
        return
    check(name, "returned", "raised")


# ── 1. The role is in no staff list ──────────────────────────────────────
print("1. financial_professional appears in no staff role list")
routers = os.path.join(ROOT, "app", "routers")
offenders = []
for filename in os.listdir(routers):
    if not filename.endswith(".py") or filename == "guest.py":
        continue
    text = open(os.path.join(routers, filename), encoding="utf-8").read()
    if "financial_professional" in text:
        offenders.append(filename)
check("no router outside guest.py names the role", offenders, [])
guest_router = open(os.path.join(routers, "guest.py"), encoding="utf-8").read()
check("guest.py's staff list does not include it",
      "financial_professional" in re.search(r"_STAFF_ROLES = \[.*?\]", guest_router).group(0), False)
check_true("every guest endpoint takes require_guest",
           all("require_guest" in block for block in
               re.split(r"\n@router\.", guest_router)[1:]))
check_true("and every staff endpoint takes the staff roles",
           all("require_role(_STAFF_ROLES)" in block for block in
               re.split(r"\n@staff_router\.", guest_router)[1:]))
shell = open(os.path.join(ROOT, "frontend", "src", "components", "ProtectedRoute.tsx"),
             encoding="utf-8").read()
check_true("the staff app admits staff roles only", "STAFF_ROLES" in shell)


# ── Fakes ────────────────────────────────────────────────────────────────
class Row(dict):
    __getattr__ = dict.get

    def model_dump(self, **_):
        return dict(self)


def _val(x):
    return getattr(x, "value", x)


def _matches(row, condition):
    for key, want in condition.items():
        have = _val(row.get(key))
        if isinstance(want, list):
            if have not in [_val(w) for w in want]:
                return False
        elif type(want).__name__ == "NotNull":
            if have is None:
                return False
        elif have != _val(want):
            return False
    return True


TABLES: dict[str, list] = {}


class Fake:
    def __init__(self, table):
        self.table = table
        TABLES.setdefault(table, [])

    @property
    def rows(self):
        return TABLES[self.table]

    def select_one(self, condition):
        return next((r for r in self.rows if _matches(r, condition)), None)

    def select_many(self, condition, sort_by=None, **_):
        found = [r for r in self.rows if _matches(r, condition)]
        if sort_by:
            found.sort(key=lambda r: (r.get(sort_by) is None, str(r.get(sort_by))))
        return found, len(found)

    def insert(self, data):
        row = Row({k: _val(v) for k, v in data.items()})
        row["id"] = max([r["id"] for r in self.rows] or [0]) + 1
        row["created_at"] = datetime.now(timezone.utc)
        self.rows.append(row)
        return row

    def update(self, id, data):
        row = self.select_one({"id": id})
        row.update({k: _val(v) for k, v in data.items()})
        return row

    def delete(self, id):
        TABLES[self.table] = [r for r in self.rows if r["id"] != id]
        return True

    # the named helpers the service calls
    def by_email(self, email):
        return next((r for r in self.rows if r["email"].lower() == email.lower()), None)

    def for_guest(self, guest_id):
        return self.select_many({"guest_id": guest_id})[0]

    def for_matter(self, matter_id):
        return self.select_many({"matter_id": matter_id})[0]

    def for_grants(self, ids):
        return [r for r in self.rows if r["grant_id"] in ids]

    def for_grant(self, grant_id):
        return self.select_many({"grant_id": grant_id})[0]

    def latest(self, guest_id):
        found = self.for_guest(guest_id)
        return found[-1] if found else None

    def get_by_matter(self, matter_id):
        return self.select_many({"matter_id": matter_id})[0]

    def get_by_account(self, account_id):
        return self.select_many({"financial_account_id": account_id})[0]

    def get_by_statement(self, statement_id):
        return [r for r in self.rows if r["statement_id"] == statement_id
                and r.get("deleted_at") is None]

    def find_link(self, transaction_id, tag_id):
        return self.select_one({"transaction_id": transaction_id, "tag_id": tag_id})


for attr, table in (
    ("GuestProfessionalRepository", "guest_professionals"),
    ("MatterGuestGrantRepository", "matter_guest_grants"),
    ("GuestConsentRepository", "guest_consents"),
    ("GuestDocumentLedgerRepository", "guest_document_ledger"),
    ("GuestSignInRepository", "guest_sign_ins"),
    ("MatterRepository", "matters"),
    ("UserRoleRepository", "user_roles"),
    ("FinancialAccountRepository", "financial_accounts"),
    ("FinancialAccountStatementRepository", "statements"),
    ("FinancialAccountTransactionRepository", "transactions"),
    ("TransactionTagRepository", "transaction_tags"),
    ("TransactionTagLinkRepository", "tag_links"),
):
    setattr(mod, attr, (lambda t: (lambda m: Fake(t)))(table))

SEARCH_CALLS = []


class FakeSearch:
    def search(self, manager, matter_id, **filters):
        SEARCH_CALLS.append(filters)
        items = [Row(**t, tag_ids=[l["tag_id"] for l in TABLES["tag_links"]
                                   if l["transaction_id"] == t["id"]],
                     client_tag_ids=[99])
                 for t in TABLES["transactions"]
                 if any(a["id"] == t["financial_account_id"] and a["matter_id"] == matter_id
                        for a in TABLES["financial_accounts"])]
        return {"total": len(items), "items": items, "sum_amount": "0.00"}

    @staticmethod
    def _verify_on_matter(manager, matter_id, ids):
        allowed = {a["id"] for a in TABLES["financial_accounts"] if a["matter_id"] == matter_id}
        rows = [t for t in TABLES["transactions"] if t["id"] in ids]
        if len(rows) != len(ids) or any(t["financial_account_id"] not in allowed for t in rows):
            raise ValueError("not on matter")
        return [t["id"] for t in rows]


mod.transaction_search_service = FakeSearch()

M = None  # the manager is never used by the fakes
now = datetime.now(timezone.utc)
Fake("matters").insert({"matter_name": "Salmons", "short_name": "Salmons", "client_id": 1})
Fake("matters").insert({"matter_name": "Other case", "short_name": "Other", "client_id": 2})
Fake("financial_accounts").insert({"matter_id": 1, "institution": "Chase",
                                   "account_number_last4": "1234"})
Fake("financial_accounts").insert({"matter_id": 2, "institution": "Elsewhere",
                                   "account_number_last4": "9999"})
Fake("statements").insert({"financial_account_id": 1, "matter_id": 1, "review_status": "auto_accepted",
                           "period_start": date(2026, 1, 1), "period_end": date(2026, 1, 31),
                           "extraction": {"source_filename": "chase-jan.pdf", "bates_first": "KF1",
                                          "bates_last": "KF4"}})
Fake("statements").insert({"financial_account_id": 2, "matter_id": 2, "review_status": "auto_accepted",
                           "period_start": date(2026, 1, 1), "period_end": date(2026, 1, 31),
                           "extraction": {}})
Fake("transactions").insert({"statement_id": 1, "financial_account_id": 1, "amount": Decimal("-5")})
Fake("transactions").insert({"statement_id": 2, "financial_account_id": 2, "amount": Decimal("-7")})
Fake("user_roles").insert({"auth_email": "paralegal@firm.com", "role": "paralegal", "staff_id": 3})


# ── 2. Inviting ──────────────────────────────────────────────────────────
print("2. inviting a professional opens a grant awaiting consent")
grant = svc.invite(M, 1, "Pat Expert", "pat@tracing.com", GuestDesignation.testifying_expert, 3, "Tom")
check("pending until consent", grant.status, "pending_consent")
check("a role row to sign in with", [r["role"] for r in TABLES["user_roles"]
                                     if r["auth_email"] == "pat@tracing.com"],
      ["financial_professional"])
check_true("it expires", grant.expires_at > now + timedelta(days=179))
raises("a second live grant on the same matter is refused", GuestConflict,
       lambda: svc.invite(M, 1, "Pat Expert", "PAT@tracing.com", GuestDesignation.non_expert, 3, "Tom"))
raises("a staff address cannot be made a guest", ValueError,
       lambda: svc.invite(M, 1, "Para", "paralegal@firm.com", GuestDesignation.non_expert, 3, "Tom"))
guest_id = grant.guest_id

print("3. nothing is visible before consent")
raises("pending grant opens nothing", GuestDenied, lambda: svc.accounts(M, guest_id, 1))
check("and lists no matter", svc.matters(M, guest_id), [])

print("4. attesting consent opens it, and seeds the ledger")
grant, email = svc.attest_consent(M, 1, grant.id, ConsentMethod.phone, date.today(), 3, "Tom",
                                  note="Client agreed on a call")
check("active", grant.status, "active")
check("consent names the staff member", TABLES["guest_consents"][0]["attested_by"], "Tom")
check_true("the email carries the purpose reminder", "solely for the benefit of" in email["body"])
check("ledger seeded with the matter's statement only",
      [e["statement_id"] for e in TABLES["guest_document_ledger"]], [1])
raises("a second attestation is refused", GuestConflict,
       lambda: svc.attest_consent(M, 1, grant.id, ConsentMethod.phone, date.today(), 3, "Tom"))
raises("consent cannot be dated in the future", Exception,
       lambda: svc.attest_consent(M, 1, svc.invite(M, 2, "Pat Expert", "pat@tracing.com",
                                                   GuestDesignation.non_expert, 3, "Tom").id,
                                  ConsentMethod.phone, date.today() + timedelta(days=1), 3, "Tom"))

# ── 5. Scope ─────────────────────────────────────────────────────────────
print("5. scope is a live grant on THIS matter")
check("their matter's accounts", [a["id"] for a in svc.accounts(M, guest_id, 1)], [1])
raises("another matter's accounts", GuestDenied, lambda: svc.accounts(M, guest_id, 2))
raises("another matter's account by id", GuestDenied, lambda: svc.statements(M, guest_id, 2))
raises("another matter's statement by id", GuestDenied,
       lambda: svc.statement_transactions(M, guest_id, 2))
check("their matter's statement", [t["id"] for t in svc.statement_transactions(M, guest_id, 1)], [1])

g = TABLES["matter_guest_grants"][0]
g["expires_at"] = now - timedelta(seconds=1)
check("past its date it reads expired", effective_status(Row(g)), "expired")
raises("and opens nothing", GuestDenied, lambda: svc.accounts(M, guest_id, 1))
g["expires_at"] = now + timedelta(days=30)
TABLES["guest_professionals"][0]["is_active"] = False
raises("a deactivated guest opens nothing", GuestDenied, lambda: svc.accounts(M, guest_id, 1))
TABLES["guest_professionals"][0]["is_active"] = True

# ── 6. Tags ──────────────────────────────────────────────────────────────
print("6. only their own tags")
Fake("transaction_tags").insert({"matter_id": 1, "label": "Staff theory", "guest_id": None,
                                 "is_active": True})
Fake("tag_links").insert({"transaction_id": 1, "tag_id": 1, "source": "staff"})
mine = svc.create_tag(M, guest_id, 1, "Commingling")
check("their tag belongs to them", mine.guest_id, guest_id)
check("their list holds only theirs", [t["label"] for t in svc.own_tags(M, guest_id, 1)],
      ["Commingling"])
check("applying it", svc.apply_tag(M, guest_id, 1, [1], mine.id), 1)
check("the link says a guest made it", TABLES["tag_links"][-1]["source"], "guest")
raises("a staff tag cannot be applied", GuestDenied, lambda: svc.apply_tag(M, guest_id, 1, [1], 1))
raises("nor a line on another matter", GuestDenied,
       lambda: svc.apply_tag(M, guest_id, 1, [2], mine.id))

result = svc.search(M, guest_id, 1, {"tag_ids": [1, mine.id], "untagged": True,
                                     "include_deleted": True})
filters = SEARCH_CALLS[-1]
check("a staff tag id is dropped from the filter", filters["tag_ids"], [mine.id])
check("untagged is refused", filters["untagged"], False)
check("removed lines never included", filters["include_deleted"], False)
check("the chips carry only their tag", result["items"][0]["tag_ids"], [mine.id])
check("and no client tags", "client_tag_ids" in result["items"][0], False)
svc.search(M, guest_id, 1, {"tag_ids": [1]})
check("asking only for someone else's tag gets nothing, not everything",
      SEARCH_CALLS[-1]["tag_ids"], [-1])

print("7. a second guest on the same matter never sees the first one's tags")
other = svc.invite(M, 1, "Sam Consultant", "sam@cpa.com", GuestDesignation.consulting_expert, 3, "Tom")
svc.attest_consent(M, 1, other.id, ConsentMethod.email, date.today(), 3, "Tom")
check("their list is empty", svc.own_tags(M, other.guest_id, 1), [])
raises("and the first guest's tag is not theirs to apply", GuestDenied,
       lambda: svc.apply_tag(M, other.guest_id, 1, [1], mine.id))
check("their search shows none of the first guest's tags",
      svc.search(M, other.guest_id, 1, {})["items"][0]["tag_ids"], [])

# ── 8. The ledger ────────────────────────────────────────────────────────
print("8. the ledger records what appeared, and outlives a deletion")
Fake("statements").insert({"financial_account_id": 1, "matter_id": 1, "review_status": "auto_accepted",
                           "period_start": date(2026, 2, 1), "period_end": date(2026, 2, 28),
                           "extraction": {"source_filename": "chase-feb.pdf"}})
svc.accounts(M, guest_id, 1)
ledger = Fake("guest_document_ledger").for_grant(grant.id)
check("a statement imported later is added", sorted(e["statement_id"] for e in ledger), [1, 3])
Fake("statements").delete(3)   # rejected -> hard-deleted
svc.accounts(M, guest_id, 1)
feb = next(e for e in Fake("guest_document_ledger").for_grant(grant.id) if e["statement_id"] == 3)
check_true("its row survives, stamped removed", feb["removed_at"] is not None)
check("with its filename intact", feb["source_filename"], "chase-feb.pdf")
check("the label names the account", feb["account_label"], "Chase ····1234")

# ── 9. Revocation ────────────────────────────────────────────────────────
print("9. revocation is immediate, and can reach every matter")
svc.revoke(M, 1, grant.id, 3, "Tom", "Engagement ended")
raises("revoked: no access on the next request", GuestDenied, lambda: svc.accounts(M, guest_id, 1))
raises("revoked access cannot be renewed", GuestConflict, lambda: svc.renew(M, 1, grant.id, 30))
second = svc.invite(M, 1, "Pat Expert", "pat@tracing.com", GuestDesignation.testifying_expert, 3, "Tom")
check("a new grant may follow a revoked one", second.status, "pending_consent")
check("revoking everywhere counts every live grant",
      svc.revoke_everywhere(M, guest_id, 3, "Tom"), 2)

print("10. the inventory hides who excluded what")


class FakeInventory:
    def inventory(self, manager, matter_id, matter=None):
        return {"assets": [{"excluded_by": "Thomas J. Daley", "excluded_at": now}],
                "liabilities": [], "rates": [{"entered_by": "Tom"}]}


mod.inventory_service = FakeInventory()
report = svc.inventory(M, other.guest_id, 1)
check("no name on the exclusion", report["assets"][0]["excluded_by"], None)
check("no name on the rate", report["rates"][0]["entered_by"], None)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
