# 3. Creating a matter

[← Creating a client](02-clients.md) · [Index](README.md) · Next: [Importing statements →](04-importing-statements.md)

---

A matter is one engagement: a divorce, a modification, an enforcement. It
belongs to exactly one client, and everything else in Cyclone hangs off it —
billing, discovery, pleadings, and all of the financial work in Part 3.

---

## Creating it

1. **Matters** → **+ New matter**.
2. Fill in the form:

   | Field | Required | Notes |
   | ----- | -------- | ----- |
   | **Client** | Yes | Chosen from existing clients. Create the client first ([Chapter 2](02-clients.md)). |
   | **Matter type** | Yes | `divorce`, `child custody`, `modification`, `enforcement`, `CPS`, `probate`, `estate planning`, `civil`, `other`. Drives which fee agreement template applies. |
   | **Short name** | No | What you will see in lists. Left blank, it is generated from client + type + year. |
   | **Matter name** | Yes | e.g. *Smith v. Smith — Divorce*. |
   | **State** | Yes | |
   | **County** | Yes | e.g. *Dallas*. Appears in the exhibit caption. |
   | **Court name** | No | e.g. *401st District Court*. Appears in the exhibit caption. |
   | **Matter number** | No | The court-assigned cause number. Appears in the exhibit caption. |
   | **Pro bono** | No | A tick box, and it is not cosmetic — see the warning below. |
   | **Notes** | No | |

3. Click **Create matter**.

> **Pro bono zeroes every rate on the matter.** Ticking it forces every billing
> entry to a rate of zero and an amount of zero, regardless of the staff
> member's default rate or any override. This is enforced in three separate
> places, so it cannot be worked around by editing an entry. Tick it only when
> you mean it.

---

## Opening a matter

Click any row in the matter list. That opens the matter detail page, which is
where nearly everything about the case is set. The sections, in the order they
appear:

| Section | What it holds |
| ------- | ------------- |
| **Exhibit caption** | Case style and which side we represent. See below. |
| **Discovery scope** | How far back each side must produce. See [Chapter 11](11-discovery-tracking.md). |
| **Opposing parties** | The other side, by name. |
| **Staff** | Who is on the case, and origination credit. See [Chapter 13](13-matter-staff.md). |
| **Children** | Names and dates of birth. |
| **Opposing counsel** | Their lawyers, deduplicated by bar number. |
| **Claims** | What is being asked for, grouped by the pleading that asked. |
| **Pleadings** | Filed documents on this matter. |

The link to the financial work is at the **top right**: **Financials →**.

---

## Fill in the exhibit caption early

Every exhibit Cyclone generates — the FIS, transaction summaries, the
compliance matrix — is headed by a court caption built from this matter. Two
of the fields it needs live nowhere else, and they are on the matter detail
page under **Exhibit caption**:

| Field | Notes |
| ----- | ----- |
| **Case style** | As it is written on a filing: `IN THE MATTER OF THE MARRIAGE OF JANE DOE AND JOHN DOE`. Not the short name. |
| **We represent the** | Petitioner, Respondent, Counter-Petitioner, Counter-Respondent, Intervenor, Plaintiff, Defendant, Applicant, or Movant. This titles the exhibit — *"Petitioner's Financial Summary"*. |

The cause number, court and county come from the matter fields above.

A live preview of the caption sits below the fields, showing exactly what will
be printed. Underscored blanks are fields nothing has filled.

> **A missing caption field does not stop an export.** It prints as a blank
> rule (`__________`), and the download tells you which fields were blank.
> That is deliberate — attorneys want the numbers long before a cause number
> exists — but a blank that reaches a filing unnoticed is a real problem, so
> fill these in as soon as you can.

Click **Save caption** when done.

---

## Rate overrides

On the matter list, expand a matter and open **Rate overrides** to set a
per-staff, per-matter hourly rate. This beats the matter's rate card and the
staff member's default rate.

The full order Cyclone uses to decide a rate:

1. **Pro bono** — if the matter is pro bono, the rate is zero. Nothing below is
   consulted.
2. **Rate override** for that staff member on that matter.
3. The **matter's rate card** (a rate for attorneys, a rate for paralegals).
4. The staff member's **default billing rate**.

---

## Other matter fields

Expand a matter in the list and click **Edit** to reach:

**Status** — `intake`, `conflict review`, `active`, `closed`, `archived`.
**Retainer**, **Fee agreement signed**, **Opened date**, **Closed date**, and
**Notes**.

---

[← Creating a client](02-clients.md) · [Index](README.md) · Next: [Importing statements →](04-importing-statements.md)
