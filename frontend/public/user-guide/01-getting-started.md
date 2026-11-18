# 1. Getting started

[← Index](README.md) · Next: [Creating a client →](02-clients.md)

---

There is no sign-up page. **You cannot create your own account**, and neither
can anyone else create one for themselves. Access is granted by an
administrator inside Cyclone before you ever visit the site.

This makes getting a new person started a two-stage job with two people in it:
an administrator does the first half, and the new user does the second.

---

## Stage 1 — The administrator creates the account

Everything here happens on **Admin** in the left-hand menu. The Admin item is
only visible to someone holding the `admin` role.

### Step 1: Create the staff record

1. Go to **Admin**.
2. Click **+ New staff member**.
3. Fill in the form. The starred fields are required:

   | Field | Notes |
   | ----- | ----- |
   | **First** and **Last** | The person's legal name. Title, middle name and suffix are optional. |
   | **Sign-in email** | **The critical field.** This must be the exact email address the person will use to sign in with Google. It is what links the login to this record. |
   | **Work email** | Their firm address, for display and correspondence. May be the same as the sign-in email. |
   | **Telephone** | |
   | **Role** | Governs billing and display. It is *not* what controls access — see Step 2. |
   | **Slug** | A short handle, e.g. `jdoe`. **For an attorney who takes leads from the marketing site, this must match the slug that site sends, exactly** — it is what routes their leads to them. See [Chapter 14](14-lead-routing.md). |
   | **Office ID** | |
   | **Default rate ($/hr)** | Optional. The fallback billing rate when a matter has no override. |
   | **Bar admissions** | State and bar number, for attorneys. |

4. Save.

> **Get the sign-in email exactly right.** If it does not match the Google
> account the person signs in with, character for character, they will be
> refused at first login and you will have to come back and correct it. This is
> by far the most common reason a new user cannot get in.

### Step 2: Grant auth roles

Creating the staff record does **not** grant access. It records a person; it
does not let them in.

1. Still on **Admin**, scroll to the **User roles** panel.
2. Click the row for the new person. A role editor opens beneath it.
3. Tick every role they should hold — `attorney`, `paralegal`, `admin`. A person
   can hold more than one; someone can be both an attorney and an admin.
4. Click **Save roles**.

A person with no roles shows **no auth roles** in amber. They can sign in to
Google, but Cyclone will turn them away.

See [Chapter 12](12-user-roles.md) for what each role can actually do.

### Step 3: If this person is involved with leads

Two more pieces of setup live outside this form, and neither happens on its own:

- **An attorney who receives leads** needs responders configured, or nobody is
  told when one of their leads needs attention. Admin → **Lead responders**.
- **Anyone who works leads** needs access to the relevant slugs. A new staff
  member is granted the firm's general inbox (`www`) and nothing else — not even
  an attorney's own slug.

[Chapter 14](14-lead-routing.md) is the whole of it, including what to check
when a lead arrives and nobody hears about it.

---

## Stage 2 — The new user signs in

1. Go to the Cyclone address your firm uses and click **Log in**.
2. Click **Continue with Google**, and use the firm Google account matching the
   sign-in email the administrator entered.
3. Cyclone links the login to the waiting staff record automatically. You will
   briefly see a page saying so, and then land on the dashboard.

That is all. There is no password to set, no invitation email to find, and no
activation link. The linking happens on the first sign-in and never needs to be
repeated.

Clients and financial professionals sign in the same way and land on their own
screens instead of the dashboard: [Chapter 16](16-client-portal.md) and
[Chapter 18](18-financial-professionals.md).

---

## When sign-in does not work

| What you see | What it means | What to do |
| ------------ | ------------- | ---------- |
| **"No account found" page** | Nothing is waiting for the Google account you just used. | Staff: ask the administrator to check the sign-in email on your staff record against the Google address you actually signed in with. A typo, or a personal Gmail used instead of the firm address, is almost always the cause. A client or a financial professional sees this too, usually because access has not been given yet or went to a different address ([Chapter 16](16-client-portal.md), [Chapter 18](18-financial-professionals.md)). |
| **Access denied** page | You signed in successfully, but your account holds no role that opens any part of Cyclone. | Ask the administrator to check your roles ([Chapter 12](12-user-roles.md)). |
| You reach the dashboard but menu items are missing | You hold a role, but not one that includes those areas. | See [Chapter 12](12-user-roles.md). **Leads** needs attorney, paralegal or admin; **Admin** needs admin. |

---

## Finding your way around

**Work on a case starts from the matter, not the menu.** Find the matter, open
its dashboard, and every part of the case is a tile from there.

### Finding a matter

The **Dashboard** opens with two things:

| | |
| - | - |
| **Find a matter** | Type any part of the client's name, the matter's name, the cause number or the billing ID. Every word you type has to match, so `smith 2024` narrows faster than `smith` |
| **Recently opened** | The matters **you** opened most recently, newest first. It follows you between computers |

Clicking a matter opens its **matter dashboard**. The **Matters** list in the
menu opens the same dashboards.

### The matter dashboard

The top of the page names the matter: its status, cause number, billing ID,
court, county, type and the client. The same header sits at the top of every
page you reach from here, with **← Matter dashboard** to come back.

Below it are tiles, grouped the way the work is:

| Group | Tiles |
| ----- | ----- |
| **Matter** | **Edit matter** (name, status, court, numbers, dates, retainer, rate overrides) and **Matter details** (parties, staff, children, counsel, claims, caption, discovery scope, client portal, financial professionals). See [Chapter 3](03-matters.md) |
| **Pleadings** | **Pleadings**: ingest one, review its claims, track which is live |
| **Discovery** | **Discovery requests**, and **Statements held**, the compliance grid ([Chapter 11](11-discovery-tracking.md)) |
| **Financials** | **Received documents**, **Import statements**, **Accounts**, **Transactions**, **Inventory** and the **Financial Information Statement**. That is Part 3 of this guide |

### The menu

| Menu item | What it is | Who sees it |
| --------- | ---------- | ----------- |
| **Dashboard** | Find a matter, and the ones you opened recently | Everyone |
| **Billing** | Time entry, including plain-English entry | Everyone |
| **Matters** | Every matter, with filters, and **+ New matter** | Everyone |
| **Clients** | The client list and the conflict check | Everyone |
| **Leads** | Prospective clients | Attorney, paralegal, admin |
| **Profile** | Your own details | Everyone |
| **Admin** | Staff, roles, knowledge base | Admin only |
| **Costs** | What the AI work costs ([Chapter 15](15-costs.md)) | Admin only |

Discovery and Pleadings are no longer in the menu: both belong to one matter,
so they open from its dashboard.

---

[← Index](README.md) · Next: [Creating a client →](02-clients.md)
