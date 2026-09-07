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
   | **Slug** | A short handle, e.g. `jdoe`. |
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

---

## When sign-in does not work

| What you see | What it means | What to do |
| ------------ | ------------- | ---------- |
| **"No account is awaiting activation for this email address."** | No staff record has a **Sign-in email** matching the Google account you just used. | Ask the administrator to check the sign-in email on your staff record against the Google address you actually signed in with. A typo, or a personal Gmail used instead of the firm address, is almost always the cause. |
| **Access denied** page | You signed in successfully and Cyclone knows who you are, but you hold the `client` role. | The client portal is not built yet. A `client` role currently cannot use the application. |
| You reach the dashboard but menu items are missing | You hold a role, but not one that includes those areas. | See [Chapter 12](12-user-roles.md). **Leads** needs attorney, paralegal or admin; **Admin** needs admin. |

---

## Finding your way around

The left-hand menu is the whole application:

| Menu item | What it is | Who sees it |
| --------- | ---------- | ----------- |
| **Dashboard** | Landing page | Everyone |
| **Billing** | Time entry, including plain-English entry | Everyone |
| **Matters** | The matter list — and the way into everything about a case | Everyone |
| **Clients** | The client list and the conflict check | Everyone |
| **Discovery** | Written discovery requests served on us | Everyone |
| **Pleadings** | Filed documents, with parties and claims pulled out | Everyone |
| **Leads** | Prospective clients | Attorney, paralegal, admin |
| **Profile** | Your own details | Everyone |
| **Admin** | Staff, roles, knowledge base | Admin only |

**Financial statement work is not in this menu.** It lives inside a matter:
**Matters** → click the matter → **Financials →** at the top right. That is
Part 3 of this guide.

---

[← Index](README.md) · Next: [Creating a client →](02-clients.md)
