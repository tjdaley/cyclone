"""
tests/test_staff_sign_in.py - A staff member signs in whatever case their email was typed in.

The reported failure: a new paralegal was told "No account is awaiting
activation", and granting admin fixed it. Two causes, both pinned here:

  * Correlation matched `user_roles.auth_email` exactly, and Google returns the
    address in lower case while a person types "Jane.Doe@Gmail.com".
  * Correcting the sign-in email on the staff record never reached the role
    rows, so the original role kept waiting for the old address while a role
    granted afterwards (copied from the staff record) worked.

Run:  venv/Scripts/python.exe tests/test_staff_sign_in.py
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import routers.auth_flow as auth_flow  # noqa: E402
import routers.staff as staff_router  # noqa: E402
from db.models.staff import StaffRole  # noqa: E402
from db.models.user_role import UserRoleInDB, UserRoleType  # noqa: E402
from db.repositories.user_role import UserRoleRepository  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from schemas.staff import StaffUpdateRequest  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


ROLES: list[UserRoleInDB] = []
class Record(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


STAFF = {7: Record(id=7, auth_email="Jane.Doe@Gmail.com", role=StaffRole.paralegal,
                   supabase_uid=None)}


class Manager:
    """What the real UserRoleRepository reaches through BaseRepository."""

    def select_many(self, table, *args, **kwargs):
        condition = next((a for a in args if isinstance(a, dict)), kwargs.get("condition", {}))
        found = [r for r in ROLES
                 if all(getattr(r, k) == v for k, v in condition.items())]
        return found, len(found)


class Roles(UserRoleRepository):
    """The real lookup, over an in-memory table; writes go straight to it."""

    def __init__(self, manager=None):
        super().__init__(Manager())

    def update(self, row_id, data):
        row = next(r for r in ROLES if r.id == row_id)
        ROLES[ROLES.index(row)] = row.model_copy(update=data)

    def insert(self, data):
        ROLES.append(UserRoleInDB(id=len(ROLES) + 1, created_at=datetime.now(timezone.utc), **data))


class Staff:
    def __init__(self, manager=None):
        pass

    def select_one(self, condition):
        return STAFF.get(condition["id"])

    def update(self, staff_id, data):
        STAFF[staff_id] = Record(**{**vars(STAFF[staff_id]), **data})
        return STAFF[staff_id]


class Audit:
    def __init__(self, manager=None):
        pass

    def log(self, **kwargs):
        pass


auth_flow.UserRoleRepository = Roles
auth_flow.StaffRepository = Staff
auth_flow.AuditLogger = Audit
staff_router.UserRoleRepository = Roles
staff_router.StaffRepository = Staff
staff_router.StaffResponse = lambda **kwargs: kwargs
staff_router._single_staff_roles = lambda manager, staff_id: []


def sign_in(uid, email):
    request = SimpleNamespace(state=SimpleNamespace(supabase_uid=uid, email=email))
    try:
        return auth_flow.correlate_staff(request, None)["roles"]
    except HTTPException as e:
        return e.status_code


def as_admin():
    return SimpleNamespace(state=SimpleNamespace(role="admin", supabase_uid="admin-uid"))


print("1. an address typed with capitals still signs in")
ROLES.append(UserRoleInDB(id=1, created_at=datetime.now(timezone.utc), role=UserRoleType.paralegal,
                          staff_id=7, auth_email="Jane.Doe@Gmail.com "))
check("paralegal only, mixed case and a stray space", sign_in("uid-1", "jane.doe@gmail.com"),
      ["paralegal"])

print("2. correcting the sign-in email reaches the role rows")
ROLES.clear()
ROLES.append(UserRoleInDB(id=1, created_at=datetime.now(timezone.utc), role=UserRoleType.paralegal,
                          staff_id=7, auth_email="jane.typo@gmail.com"))
STAFF[7].auth_email = "jane.typo@gmail.com"
staff_router.update_staff(7, StaffUpdateRequest(auth_email="Jane.Doe@gmail.com"), as_admin(), None)
check("the role row now carries the new address", ROLES[0].auth_email, "jane.doe@gmail.com")
check("so the paralegal signs in", sign_in("uid-2", "jane.doe@gmail.com"), ["paralegal"])
check("and the old address finds nothing waiting", sign_in("uid-3", "jane.typo@gmail.com"), 404)

print("3. an edit that leaves the email alone touches no role row")
before = ROLES[0]
staff_router.update_staff(7, StaffUpdateRequest(telephone="555-0100"), as_admin(), None)
check("unchanged", ROLES[0], before)
staff_router.update_staff(7, StaffUpdateRequest(auth_email=" JANE.DOE@GMAIL.COM "), as_admin(), None)
check("a respelling of the same address is not a change", ROLES[0], before)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
