# 8. Categorizing, and the Financial Information Statement

[← Viewing statements](07-viewing-statements.md) · [Index](README.md) · Next: [Tagging transactions →](09-tagging.md)

---

The Financial Information Statement is a sworn document. Every figure on it is
an average of transactions somebody filed under a category, so the FIS is only
as good as the categorizing beneath it — and the categorizing is where the
cross-examination lands.

This chapter covers both, because in Cyclone they are the same screen.

---

## Category or tag?

Get this distinction right before you start.

| | **Category** | **Tag** |
| - | ------------ | ------- |
| How many per line | Exactly **one** | As many as you like |
| Who defines them | The firm, centrally | The firm, or you, per matter |
| What it drives | The **FIS** | **Rule 1006 summaries** and exhibits |
| Why | A line in two categories double-counts on a sworn statement | One line is routinely evidence in several exhibits at once |

If you want a transaction to appear in an exhibit about the client's sister's
wedding *and* still count as Entertainment on the FIS, that is a category plus
a tag — not two categories. Tags are [Chapter 9](09-tagging.md).

---

## Filing transactions

There are two places to do it. They write the same thing.

### From the Transactions tab — for bulk work

1. **Financials** → **Transactions**.
2. Narrow the list. The filters are:

   | Filter | Notes |
   | ------ | ----- |
   | **Description contains** | `pilot point`, `wedding`, `transfer`… |
   | **Check #** | An exact check number |
   | **From** / **To** | A date window |
   | **Category** | Including **Uncategorized**, and a tick box for whether subcategories are included |
   | **Accounts** | Click account chips to include them |
   | **Tags** | Click tag chips; **Untagged**, **Checks only**, and **Show removed** are chips too |

3. Tick the lines you want. The header checkbox selects every line on the page
   (200 at a time).
4. In the blue bar that appears, choose **File under…** and pick a category.

**— clear category —** in the same dropdown removes a category.

### From the FIS panel — for reviewing and correcting

1. **Financials** → **FIS**.
2. Click any line of the statement. Its transactions open on the right.
3. Tick the ones that are wrong, choose **Re-file as…**, pick the right
   category.
4. **The statement recomputes immediately.** Classification and the document you
   get from it are one activity, not two screens apart.

This is the better way to *review*: you are looking at a figure and the
transactions that produced it side by side.

---

## Some categories are filed automatically

Your firm can define keyword rules — "anything matching WALMART goes to
Groceries" — which run as statements are imported. When they do:

- A rule **never overwrites a category a person set.** It fills lines nobody
  has filed, and replaces ones the machine itself set.
- Every automatic assignment records **what** filed it and **which rule**, so a
  bad rule is reversible and *"why is this Household Supplies?"* is answerable
  in one sentence.
- Matching ignores case and punctuation, so `WALMART` finds `WAL-MART #1234`,
  but it respects word boundaries, so `TARGET` does **not** match
  `STARGETTER LLC`.

**There is no screen for editing these rules yet.** Ask a developer if a rule
needs adding or changing.

> The AI that reads statements **never** sets a category. It fills a free-text
> hint that nothing relies on. A guess that varies run to run is not something
> anyone can defend on the stand; a firm-authored keyword rule is.

---

## Building the FIS

**Financials** → **FIS**.

### 1. Set the window

Pick **From** and **Through** as month and year. Whole months only — there is
no way to ask for a part-month, because *"average monthly"* over
three-and-a-bit months cannot be explained on the stand.

The default is the twelve whole months ending with last month.

### 2. Choose accounts

By default, all of them. Click account chips to narrow.

### 3. Read the statement

Categories down the left, an average monthly amount on the right, and
**NET CASH FLOW PER MONTH** at the bottom.

**Compressed** hides empty lines. A heading survives if it has a figure of its
own or a surviving line under it.

---

## The four things to check before anybody swears to it

### 1. The warnings panel

If an amber **Before relying on these figures** panel is showing, read every
line of it. These warnings travel onto the exported exhibit too — they are not
just a screen nicety.

### 2. Statement coverage

At the bottom: every account, how many months of statements are actually held
out of the months in the window, and which months are missing.

> **Every figure divides by the number of months in the window.** Dividing by
> eight months asserts eight months of statements. If an account is missing
> three of them, every line drawn from it understates by roughly a third — and
> the statement will not look wrong. This panel is the check.

### 3. Transactions not yet filed

An amber bar: *"84 transactions not yet filed — $12,480.19 in the window, in no
line above. Click to file them."*

Click it. This is real money appearing in **no** category while the net still
looks authoritative. Filing these is the single highest-value thing you can do
to an FIS.

### 4. Excluded from this statement

Below the form: money that moved without being income or expense — transfers
between the client's own accounts, most often. It is **listed rather than
hidden**, so it reads as set aside rather than missing.

If something here is genuinely income or an expense, re-file it into a category
that counts.

---

## Payment schedules: the trap that quietly changes the sworn figure

A payment made quarterly or annually covers months beyond the one it falls in.
$3,600 of property tax paid in January averages to $1,800/month over a
January–February window and $1,200/month over January–March. **Same facts,
different sworn figure, depending on when the report was run.**

Cyclone handles this — but it has to be told how often the category is paid.

1. Click the category line in the FIS.
2. Above the transactions, read the explanation of how the figure was computed.
3. Click **Set schedule** (or **Change schedule**).
4. Choose **Paid**: Monthly, Twice monthly, Every two weeks, Weekly, Quarterly,
   Twice yearly, Annually, or As incurred.
5. Optionally give an **Annual amount** — the attorney's own figure, used when
   the transactions show none — and a **Note** (*"escrowed with the mortgage"*).
6. **Save schedule**.

The line then tells you which of three ways its figure was reached:

| Basis | Meaning |
| ----- | ------- |
| **Totalled over the window and divided by N months** | The ordinary case, for monthly and more frequent categories |
| **Totalled over the twelve months ending with the window and divided by 12** | Used for quarterly, twice-yearly and annual categories, because a payment this infrequent covers months outside the window. It also finds a payment made *outside* the window, where window-only arithmetic prints a blank line |
| **Entered by hand as an annual figure, divided by 12** | You gave a stated amount; the transactions are not used |

> **Schedules belong to the person, not the matter.** A schedule is a fact about
> someone's finances, and the same client may have matters in several counties
> from successive marriages. A category with no person-specific setting uses the
> firm-wide default, and the line says so.

---

## Why credit-card spending is not double-counted

Everywhere else in Cyclone, an amount is signed by how it moved the bank's
printed balance — so a card purchase is **positive**.

The FIS asks a different question: what did this household earn and spend. So
for credit cards and loans it **flips the sign**. Without that, $500 of
groceries on debit and $500 on credit would cancel to nothing, and a month
lived on plastic would report as income.

The stored value is untouched — reconciliation still needs the printed sign,
and the transaction exhibit still shows what the statement shows.

A card *payment* would then invert to positive, which is only correct because
the payment must be excluded anyway: it is the same money as the withdrawal
from checking. That is what the `include_in_fis` setting on a category is for,
and it is why interaccount transfers appear under **Excluded**.

---

## The detail schedule

Two tabs sit at the top of the FIS panel: **Statement** and **Transactions by
category**. The second is the schedule behind the form — every transaction,
grouped by category, with its provenance.

Both are built from the **same** window and accounts, deliberately — a schedule
computed over a different period would not back the document it claims to back,
and nobody would notice until it mattered.

Export either with the buttons — see [Chapter 10](10-exports.md).

---

## Checklist before an FIS goes out

- [ ] Every account's ownership is recorded (no amber count on the **Accounts** tab)
- [ ] The exceptions queue is empty — and any unreconciled statement still in it was accepted **by the attorney**, not by whoever was clearing the queue
- [ ] **Transactions not yet filed** is zero, or the remainder is understood
- [ ] **Statement coverage** is complete, or the gaps are known and disclosed
- [ ] Quarterly and annual categories have a payment schedule set
- [ ] The warnings panel has been read
- [ ] The exhibit caption is filled in ([Chapter 3](03-matters.md))

---

[← Viewing statements](07-viewing-statements.md) · [Index](README.md) · Next: [Tagging transactions →](09-tagging.md)
