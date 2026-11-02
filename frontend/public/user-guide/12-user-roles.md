# 12. User roles

[← Discovery tracking](11-discovery-tracking.md) · [Index](README.md) · Next: [Assigning staff to a matter →](13-matter-staff.md)

---

## The five roles

| Role | Who holds it |
| ---- | ------------ |
| **attorney** | Lawyers |
| **paralegal** | Paralegals and legal assistants |
| **admin** | Whoever administers Cyclone for the firm |
| **client** | A client of the firm, given a read-only view of their own matters — see [Chapter 16](16-client-portal.md) |
| **financial_professional** | A tracing expert, CPA or similar, given consented access to particular matters — see [Chapter 18](18-financial-professionals.md) |

**A person can hold more than one.** Each ticked role is a separate record, so
someone can be an attorney *and* an admin, and holds the combined access of
both. Access is granted if **any** role held allows the action.

> **Clients and financial professionals are not set up on the Admin page.** You
> give and revoke their access where the work is: on the client's edit form
> ([Chapter 16](16-client-portal.md)) and on the matter's detail page
> ([Chapter 18](18-financial-professionals.md)).
>
> **Neither lands in the staff app.** A client is taken to their own portal, and
> a financial professional to their own screens, each with its own menu and
> wording.
>
> **Somebody who holds `client` *and* a staff role is treated as staff here.**
> That is deliberate: an attorney who is also a client of the firm should not be
> dropped into a client's view of their own divorce. If they need the portal
> view, give them a second account.
>
> `/access-denied` now means what it says — a signed-in account with no role at
> all.

---

## What each role can do

Everything below assumes the person has signed in successfully, and covers the
three staff roles. What a client or a financial professional can do is in their
own chapters.

### Visible in the menu

| Menu item | attorney | paralegal | admin |
| --------- | :------: | :-------: | :---: |
| Dashboard, Billing, Matters, Clients, Discovery, Pleadings, Profile | ✅ | ✅ | ✅ |
| Leads | ✅ | ✅ | ✅ |
| Admin | — | — | ✅ |
| Costs | — | — | ✅ |

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
| Record, change or delete a **payee ruling** — including a firm-wide one | ✅ | ✅ | ✅ |
| Rate overrides on a matter | — | — | ✅ |
| Delete a matter or a client | — | — | ✅ |
| Create staff, grant and revoke roles | — | — | ✅ |
| Read AI spend, and enter or correct a price ([Chapter 15](15-costs.md)) | — | — | ✅ |
| Give or revoke a client's portal access ([Chapter 16](16-client-portal.md)) | ✅ | ✅ | ✅ |
| Let a client tag on a matter, or show or hide a matter in their portal ([Chapter 16](16-client-portal.md)) | ✅ | — | ✅ |
| Offer a tag to clients, and clear or restore a client's tag | ✅ | ✅ | ✅ |
| Give, renew or revoke a financial professional's access ([Chapter 18](18-financial-professionals.md)) | ✅ | ✅ | ✅ |
| Knowledge base articles | — | — | ✅ |
| Read the audit log | — | — | ✅ |

**The whole of Part 3 of this guide is available to paralegals.** That is
deliberate — it is the work.

> **Payee rulings are the one firm-wide setting a paralegal can change**, which
> is deliberate and not an oversight: triaging the creditor queue *is* the work,
> and a ruling somebody cannot fix from where they found it is worse than one
> they can. The safeguard is a confirmation before a firm-wide ruling is changed
> or deleted, not a locked door. See
> [Chapter 11](11-discovery-tracking.md#reviewing-changing-and-undoing-a-ruling).

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

**Staff roles are firm-wide, not per matter.** Anyone holding attorney, paralegal, or
admin can see **every matter in the firm** and every client, statement, and
transaction in it. There is no way to restrict a staff member to a subset of matters. Clients and
financial professionals are the opposite: each sees only their own matters.

Assigning someone to a matter does not grant them access, and removing them does
not take it away. That is [Chapter 13](13-matter-staff.md), and it is worth
reading before you assume otherwise.

If a matter genuinely must be walled off from some staff, Cyclone cannot do it
today. Raise it rather than working around it.

---

[← Discovery tracking](11-discovery-tracking.md) · [Index](README.md) · Next: [Assigning staff to a matter →](13-matter-staff.md)
