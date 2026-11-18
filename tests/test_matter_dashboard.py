"""
tests/test_matter_dashboard.py - The opening page's recent list, the billing id, and who may read a matter.

  * Recent matters are this staff member's own, newest first, one entry per matter.
  * A blank billing id is stored as no billing id, so the unique index never
    sees two empty strings.
  * No staff route keyed by an id in the path admits the `client` role. Staff
    routes are not scoped to a client's own matters, so `client` there let a
    signed-in client read any matter, bill or discovery document by number.

Run:  venv/Scripts/python.exe tests/test_matter_dashboard.py
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import db.repositories.matter as matter_repos  # noqa: E402
import routers.matters as matters  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from schemas.matter import MatterCreateRequest, MatterUpdateRequest  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


VISITS: list[dict] = []
NOW = datetime.now(timezone.utc)


class Manager:
    """Stands in for SupabaseManager under the real StaffRecentMatterRepository."""

    def upsert(self, table, data, model, on_conflict):
        assert on_conflict == "staff_id,matter_id"
        VISITS[:] = [v for v in VISITS
                     if (v["staff_id"], v["matter_id"]) != (data["staff_id"], data["matter_id"])]
        VISITS.append(dict(data))
        return None

    def select_many(self, table, model, condition, sort_by=None, sort_direction="asc", *a, **k):
        rows = [SimpleNamespace(**v) for v in VISITS
                if all(v[key] == value for key, value in condition.items())]
        rows.sort(key=lambda r: r.viewed_at, reverse=sort_direction == "desc")
        return rows, len(rows)


MATTERS = {n: SimpleNamespace(id=n, model_dump=lambda n=n: {"id": n}) for n in (1, 2, 3)}


class Matters:
    def __init__(self, manager):
        pass

    def select_one(self, condition):
        return MATTERS.get(condition["id"])

    def select_many(self, condition):
        return [MATTERS[i] for i in condition["id"] if i in MATTERS], 0


STAFF_BY_UID = {"uid-amy": SimpleNamespace(id=10), "uid-bob": SimpleNamespace(id=20)}
matters.StaffRepository = lambda manager: SimpleNamespace(get_by_supabase_uid=STAFF_BY_UID.get)
matters.MatterRepository = Matters
matters.MatterResponse = lambda **kwargs: kwargs["id"]


def request(uid):
    return SimpleNamespace(state=SimpleNamespace(supabase_uid=uid))


tick = iter(range(1000))
real_touch = matter_repos.StaffRecentMatterRepository.touch


def touch(self, staff_id, matter_id):
    # A distinct, increasing timestamp per visit, so the order is the visit order.
    self.upsert({"staff_id": staff_id, "matter_id": matter_id,
                 "viewed_at": (NOW + timedelta(seconds=next(tick))).isoformat()},
                on_conflict="staff_id,matter_id")


matter_repos.StaffRecentMatterRepository.touch = touch
M = Manager()

print("1. recent matters")
check("nobody has opened anything yet", matters.recent_matters(request("uid-amy"), 8, M), [])
for matter_id in (1, 2, 3, 1):
    matters.matter_viewed(matter_id, request("uid-amy"), M)
matters.matter_viewed(2, request("uid-bob"), M)
check("newest first, and a revisit moves a matter to the top",
      matters.recent_matters(request("uid-amy"), 8, M), [1, 3, 2])
check("one entry per matter", len([v for v in VISITS if v["staff_id"] == 10]), 3)
check("each person has their own list", matters.recent_matters(request("uid-bob"), 8, M), [2])
check("the limit is honoured", matters.recent_matters(request("uid-amy"), 2, M), [1, 3])
del MATTERS[3]
check("a deleted matter drops out rather than failing",
      matters.recent_matters(request("uid-amy"), 8, M), [1, 2])
try:
    matters.matter_viewed(99, request("uid-amy"), M)
    check("an unknown matter is refused", "recorded", "refused")
except HTTPException as e:
    check("an unknown matter is refused", e.status_code, 404)
matter_repos.StaffRecentMatterRepository.touch = real_touch

print("2. a blank billing id is no billing id")
body = MatterUpdateRequest(billing_id="   ")
matters._clean_billing_id(body)
check("blank clears it", body.model_dump(exclude_unset=True), {"billing_id": None})
body = MatterUpdateRequest(billing_id="  00123 ")
matters._clean_billing_id(body)
check("leading zeros are kept, spaces are not", body.billing_id, "00123")
body = MatterUpdateRequest(notes="x")
matters._clean_billing_id(body)
check("left alone when not sent", "billing_id" in body.model_dump(exclude_unset=True), False)
body = MatterCreateRequest(client_id=1, matter_name="M", matter_type="divorce", county="Collin",
                           billing_id="")
matters._clean_billing_id(body)
check("blank on create too", body.billing_id, None)

print("3. no staff route admits a client")
routers = os.path.join(ROOT, "app", "routers")
admitting = []
for filename in sorted(os.listdir(routers)):
    if not filename.endswith(".py"):
        continue
    text = open(os.path.join(routers, filename), encoding="utf-8").read()
    for roles in re.findall(r"require_role\(\[([^\]]*)\]\)", text):
        if re.search(r"""['"]client['"]""", roles):
            admitting.append(filename)
check("the client portal has its own router and its own dependency", sorted(set(admitting)), [])

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
