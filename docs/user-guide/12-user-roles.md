# 12. User roles

[← Discovery tracking](11-discovery-tracking.md) · [Index](README.md) · Next: [Assigning staff to a matter →](13-matter-staff.md)

---

## The four roles

| Role | Who holds it |
| ---- | ------------ |
| **attorney** | Lawyers |
| **paralegal** | Paralegals and legal assistants |
| **admin** | Whoever administers Cyclone for the firm |
| **client** | Reserved for the client portal, **which is not built yet** |

**A person can hold more than one.** Each ticked role is a separate record, so
someone can be an attorney *and* an admin, and holds the combined access of
both. Access is granted if **any** role held allows the action.

> **`client` does not currently work.** A user holding only the client role can
> sign in and will be sent to an "access denied" page. The client portal is
> planned, not built.

---

## What each role can do

Everything below assumes the person has signed in successfully.

### Visible in the menu

| Menu item | attorney | paralegal | admin |
| --------- | :------: | :-------: | :---: |
| Dashboard, Billing, Matters, Clients, Discovery, Pleadings, Profile | ✅ | ✅ | ✅ |
| Leads | ✅ | ✅ | ✅ |
| Admin | — | — | ✅ |

### Day-to-day work

| Action | attorney | paralegal | admin |
| ------ | :------: | :-------: | :---: |
| View clients and matters | ✅ | ✅ | ✅ |
| Create and edit a client | ✅ | ✅ | ✅ |
| Run a conflict check | ✅ | ✅ | ✅ |
| Create and edit a matter | ✅ | — | ✅ |
| Assign staff to a matter | ✅ | — | ✅ |
| Create and edit billing entries | ✅ | ✅ | ✅ |
| Delete a billing entry | ✅ | — | ✅ |
| Open or close a billing cycle | ✅ | — | ✅ |
| Discovery and pleadings | ✅ | ✅ | ✅ |
| **All financial statement work** — import, review, merge, correct, categorize, tag, export, compliance, FIS | ✅ | ✅ | ✅ |
| Promote a lead to a client | ✅ | — | ✅ |

### Firm-wide settings

| Action | attorney | paralegal | admin |
| ------ | :------: | :-------: | :---: |
| Create or edit a **transaction category** (the FIS chart of accounts) | ✅ | — | ✅ |
| Create a **firm-wide** transaction tag | ✅ | — | ✅ |
| Create a **matter** tag | ✅ | ✅ | ✅ |
| Rate overrides on a matter | — | — | ✅ |
| Delete a matter or a client | — | — | ✅ |
| Create staff, grant and revoke roles | — | — | ✅ |
| Knowledge base articles | — | — | ✅ |
| Read the audit log | — | — | ✅ |

**The whole of Part 3 of this guide is available to paralegals.** That is
deliberate — it is the work.

---

## Changing someone's roles

**Admin** → **User roles** panel → click the person's row → tick or untick →
**Save roles**.

The change takes effect on their next request. They do not need to sign out and
back in.

Every role change is written to the audit log — who changed it, when, from what,
to what.

Someone with **no auth roles** shows an amber label. They can sign in to Google
but Cyclone will refuse them.

---

## Two different "roles" — do not confuse them

| | Where it is set | What it does |
| - | --------------- | ------------ |
| **Auth role** | Admin → **User roles** panel | **Controls access.** This is the one that matters. |
| **Staff role** | Admin → staff record → **Role** field | Display and billing only. Does **not** grant or restrict anything. |

Setting the staff **Role** field to "attorney" grants nobody anything. Only the
User roles panel does that.

---

## What roles do *not* do

**Roles are firm-wide, not per matter.** Anyone holding attorney, paralegal, or
admin can see **every matter in the firm** and every client, statement, and
transaction in it. There is no way to restrict a person to a subset of matters.

Assigning someone to a matter does not grant them access, and removing them does
not take it away. That is [Chapter 13](13-matter-staff.md), and it is worth
reading before you assume otherwise.

If a matter genuinely must be walled off from some staff, Cyclone cannot do it
today. Raise it rather than working around it.

---

[← Discovery tracking](11-discovery-tracking.md) · [Index](README.md) · Next: [Assigning staff to a matter →](13-matter-staff.md)
