"""
tests/test_portal_access.py - A client signs in only after somebody gives them access.

Pins the bug this exists for: a login email on a client record created no role
row, so correlation always answered "No account is awaiting activation". The
journey is run through the real correlation route and the real `require_client`,
over an in-memory `user_roles`.

Also pinned, each a way this could go quietly wrong:
  * a login email alone grants nothing;
  * an address held by anyone else is refused, because `require_client` would
    otherwise serve one client another client's matters;
  * a login email cannot change underneath existing access;
  * revoking takes effect on the very next request, and access can be given back.

Run:  venv/Scripts/python.exe tests/test_portal_access.py
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import dependencies as deps  # noqa: E402
import routers.auth_flow as auth_flow  # noqa: E402
import services.portal_access_service as mod  # noqa: E402
from db.models.user_role import UserRoleInDB, UserRoleType  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from services.portal_access_service import (  # noqa: E402
    PortalAccessConflict, portal_access_service as svc,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def raises(name, call, expect, contains=""):
    try:
        call()
    except expect as e:
        text = str(getattr(e, "detail", e))
        check(name, contains.lower() in text.lower(), True)
        return e
    except Exception as e:  # noqa: BLE001
        check(name + " (raised %s)" % type(e).__name__, False, True)
        return None
    check(name + " (allowed)", False, True)
    return None


CLIENTS = {
    13: SimpleNamespace(id=13, auth_email=" FamLawyerz@Gmail.com "),
    14: SimpleNamespace(id=14, auth_email="famlawyerz@gmail.com"),
    15: SimpleNamespace(id=15, auth_email="para@firm.test"),
    16: SimpleNamespace(id=16, auth_email="expert@cpa.test"),
    17: SimpleNamespace(id=17, auth_email="not an address"),
}
ROWS: list[UserRoleInDB] = []
AUDIT: list[dict] = []


class FakeClients:
    def __init__(self, manager):
        pass

    def select_one(self, condition):
        return CLIENTS.get(condition["id"])


class FakeRoles:
    def __init__(self, manager):
        pass

    def get_by_client(self, client_id):
        return [r for r in ROWS if r.client_id == client_id]

    def get_by_uid(self, uid):
        return [r for r in ROWS if r.supabase_uid == uid]

    def get_unlinked_by_auth_email(self, email):
        return [r for r in ROWS if r.auth_email == email and r.supabase_uid is None]

    def select_many(self, condition):
        return list(ROWS), len(ROWS)

    def insert(self, data):
        row = UserRoleInDB(id=len(AUDIT) + len(ROWS) + 100,
                           created_at=datetime.now(timezone.utc), **data)
        ROWS.append(row)
        return row

    def update(self, row_id, data):
        row = next(r for r in ROWS if r.id == row_id)
        ROWS[ROWS.index(row)] = row.model_copy(update=data)

    def delete(self, row_id):
        ROWS[:] = [r for r in ROWS if r.id != row_id]


class FakeAudit:
    def __init__(self, manager):
        pass

    def log(self, **kwargs):
        AUDIT.append(kwargs)


mod.ClientRepository = FakeClients
mod.UserRoleRepository = FakeRoles
mod.AuditLogger = FakeAudit
auth_flow.UserRoleRepository = FakeRoles
auth_flow.StaffRepository = lambda manager: None
auth_flow.AuditLogger = FakeAudit
deps.UserRoleRepository = FakeRoles


def signed_in(uid, email):
    return SimpleNamespace(state=SimpleNamespace(supabase_uid=uid, email=email))


def seed():
    ROWS.clear()
    AUDIT.clear()
    FakeRoles(None).insert({"auth_email": "para@firm.test", "role": UserRoleType.paralegal,
                            "staff_id": 7, "supabase_uid": "staff-uid"})
    FakeRoles(None).insert({"auth_email": "EXPERT@cpa.test",
                            "role": UserRoleType.financial_professional, "guest_id": 3})


UID, GOOGLE = "ef4a6acd", "famlawyerz@gmail.com"


print("1. The reported bug: a login email alone grants nothing")
seed()
check("status reads none", svc.status(None, 13)["status"], "none")
raises("sign-in is refused", lambda: auth_flow.correlate_staff(signed_in(UID, GOOGLE), None),
       HTTPException, "awaiting activation")

print("2. Give access, then the whole first sign-in")
given = svc.give(None, 13, "actor")
check("awaiting first sign-in", given["status"], "awaiting_sign_in")
check("address normalized to what correlation compares", given["auth_email"], GOOGLE)
check("audited with the actor", (AUDIT[-1]["action"], AUDIT[-1]["supabase_uid"]),
      ("client_portal.access_given", "actor"))
profile = auth_flow.correlate_staff(signed_in(UID, GOOGLE), None)
check("correlation links the client", (profile["roles"], profile["client_id"]), (["client"], 13))
check("/me now answers", auth_flow.get_me(signed_in(UID, GOOGLE), None)["client_id"], 13)
check("the portal serves this client", deps.require_client(signed_in(UID, GOOGLE), None), 13)
check("status reads active", svc.status(None, 13)["status"], "active")

print("3. Giving twice leaves one row")
svc.give(None, 13, "actor")
check("still one row", len(FakeRoles(None).get_by_client(13)), 1)

print("4. One address, one person")
raises("another client's address", lambda: svc.give(None, 14, "a"), PortalAccessConflict, "another client")
raises("a staff address", lambda: svc.give(None, 15, "a"), PortalAccessConflict, "staff")
raises("a guest address, ignoring case", lambda: svc.give(None, 16, "a"),
       PortalAccessConflict, "financial professional")
raises("not an address", lambda: svc.give(None, 17, "a"), ValueError, "google address")
raises("no such client", lambda: svc.give(None, 99, "a"), LookupError, "not found")
check("none of those created a row", len(ROWS), 3)

print("5. The login email cannot move underneath access")
raises("a different address is refused",
       lambda: svc.check_login_email_change(None, 13, "new@gmail.com"), PortalAccessConflict, "revoke")
svc.check_login_email_change(None, 13, " FAMLAWYERZ@gmail.com ")
print("  PASS the same address in other spelling is fine")
svc.check_login_email_change(None, 14, "anything@gmail.com")
print("  PASS a client with no access may change it freely")

print("6. Revoke takes effect on the next request")
check("has access", svc.has_access(None, 13), True)
check("revoked", svc.revoke(None, 13, "actor2")["status"], "none")
check("audited with the actor", (AUDIT[-1]["action"], AUDIT[-1]["supabase_uid"]),
      ("client_portal.access_revoked", "actor2"))
raises("portal refuses at once", lambda: deps.require_client(signed_in(UID, GOOGLE), None),
       HTTPException, "no role")
raises("/me is 404 again", lambda: auth_flow.get_me(signed_in(UID, GOOGLE), None), HTTPException, "no role")
check("revoking twice is harmless", svc.revoke(None, 13, "actor2")["status"], "none")
check("nobody else's row went with it", len(ROWS), 2)

print("7. Given back, the same Google account links again")
svc.give(None, 13, "actor")
check("relinks", auth_flow.correlate_staff(signed_in(UID, GOOGLE), None)["client_id"], 13)

print()
if FAILURES:
    print("FAILED: %d" % len(FAILURES))
    for failure in FAILURES:
        print(" -", failure)
    sys.exit(1)
print("All checks passed.")
