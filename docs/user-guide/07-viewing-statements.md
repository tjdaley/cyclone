# 7. Viewing statements and transactions

[← Merging accounts](06-merging-accounts.md) · [Index](README.md) · Next: [Categorizing, and the FIS →](08-categorizing-and-fis.md)

---

## Drilling down

On the **Accounts** tab, the structure is three levels deep:

**Account** → click the account name → **its statements** → click a period →
**that statement's transactions**.

To see every line an account ever printed, use **All transactions** on the
account row instead. That is what a waste exhibit is usually built from.

---

## Reaching the source PDF

There is a PDF button on every statement row, and on every row in the
exceptions queue.

It opens the original document **at the page the statement's first transaction
was printed on** — approximate, because one upload can hold a whole
production, but far better than page 1 of a 142-page file.

If it cannot be shown, you get one of three answers, because each means
something different:

| Message | Meaning |
| ------- | ------- |
| Never stored | No source PDF was kept for this statement. |
| Purged | It was stored and has since been removed. |
| Storage unavailable | A temporary fault — try again shortly. |

> **The PDF is the evidence; Cyclone holds a reading of it.** When they
> disagree, the PDF wins. Get into the habit of opening it.

---

## The normalized statement: our reading in the bank's shape

Beside the PDF button on every statement row there is a **Normalized** link. It
downloads **one statement redrawn as a statement**: a header, an account
summary, then the transactions in order with a running balance.

Choose **PDF**, **Word**, **Markdown** or **CSV**.

**It exists for one job: to be laid beside the original and read down.** Cyclone
holds a reading of a document; this is that reading, in the same shape as the
document, so the two can be compared line by line.

### ⛔ It is not evidence, and it is built so nobody can mistake it for evidence

| | |
| - | - |
| **No court caption** | Every exhibit Cyclone produces carries one. This carries none — a pro-forma reconstruction wearing a case style invites exactly the mistake it exists to prevent |
| **It says so on its face** | The header ends with **PRO-FORMA — NOT THE ORIGINAL STATEMENT** |
| **No Rule 1006 notice** | That notice promises the underlying records are available for examination. This document is not a summary offered in their place, so the notice is **replaced** by a disclaimer saying what it is: a working copy for comparison, and not an evidentiary substitute |

**Do not produce it, attach it, or hand it to anybody outside the office.** If
you need the statement itself, produce the PDF. If you need a summary to file,
build an exhibit ([Chapter 10](10-exports.md)).

### What is on it

| Part | |
| ---- | - |
| **Header** | The institution, the account, the period in full dates, and the pro-forma line |
| **Account summary** | Beginning balance, the money in and out, **ending balance (as printed)** and **ending balance (computed)** |
| **Transactions** | Date, check number, description, amount, and **Balance (computed)** |
| **Footer** | The client and matter name on every page, so a loose sheet can be identified |

**The balance column is computed, not copied**, and the heading says so. It is
the beginning balance plus each amount in turn — which is the whole point:
reading down it beside the original shows **the exact line where the two part
company**.

> **A check column appears only when the statement has checks on it**, so a card
> statement is not printed with an empty column down the page.
>
> **Removed lines are left out and counted.** The count is stated on the
> document, because a hand-check that silently dropped two rows would send
> somebody hunting an extraction bug that is really a deletion a colleague made
> on purpose ([below](#removing-a-line-and-putting-it-back)).

---

## What a statement row tells you

```text
01/05/2024 – 02/04/2024  [PDF]  cleared  reconciled  KF-000101–KF-000112
                                  [neither end ▾]  $12,400.11 → $9,872.55  Delete
```

| Chip | Meaning |
| ---- | ------- |
| **cleared** | Accepted automatically — nothing was flagged |
| **accepted** | A person reviewed it and accepted it |
| **needs review** | Waiting in the exceptions queue |
| **reconciled** | Opening + transactions = printed close |
| **off by $1,203.02** | It does not balance, by that amount |
| **not checked** | The statement did not print both balances |

The dropdown reading **neither end** is the statement boundary marker —
see [Chapter 11](11-discovery-tracking.md). The Bates range and the opening →
closing balances follow.

**Delete** removes the statement and its transactions. If that empties the
account, the account goes too — unless someone has recorded ownership,
characterization, purpose, or notes on it. The source PDF stays in storage, so
it can be uploaded again.

---

## Typing in Bates numbers

Where detection found no stamp, the statement row offers **Add Bates numbers**.

1. Open the source PDF beside you.
2. Copy the stamp from the **first** page, exactly as printed.
3. Copy the stamp from the **last** page.
4. Click **Save**.

The numbers in between are filled across the pages held.

| | |
| - | - |
| **A partial production still runs consecutively** | Physical pages 1, 2 and 4 carry three consecutive stamps. The missing sheet took its number with it |
| **Both ends are required, and the span is checked** | If the range does not match the pages held, **nothing is saved** and the message gives both counts. Off by one would mis-cite every line after the first |

**Every line is marked as numbered by hand, and so is the statement.** A
detected stamp was read off the document; a typed one is somebody's reading of
it, and an exhibit should be able to tell them apart.

---

## The transaction list

| Column | Notes |
| ------ | ----- |
| **Date** | A small **†** means the year was taken from the statement period, not printed on the line |
| **Description** | With flags and a **✎** if the line has been corrected by hand |
| **Counterparty** | Who the money went to or came from, where the statement names them |
| **Amount** | Green is money in, plain is money out |
| **Balance** | The running balance the statement printed |

> **Amounts are signed by their effect on the printed balance.** On a credit
> card that means a purchase is **positive** and a payment **negative** — the
> opposite of what you might expect, and the reason
> `opening + everything = closing` works for every account type. The FIS flips
> this for you when it computes household spending; see
> [Chapter 8](08-categorizing-and-fis.md).

---

## Brokerage, retirement and insurance accounts read differently

Four account types are read this way: **brokerage**, **retirement**, **life
insurance** and **annuity**.

**The register Cyclone builds is much shorter than the one the statement prints,
and that is deliberate.** A statement showing fifty lines may produce five here.

Buying, selling, redeeming, maturing, exchanging, reinvesting and splitting all
move value between cash and securities **inside one account**. An $8,500 stock
purchase leaves the account worth exactly what it was a second earlier: nothing
entered the marital estate and nothing left it. This project traces where money
came from and where it went, so those lines are not recorded at all.

What **is** recorded is money that crossed the account boundary, plus the income
the account threw off:

| Kept | Why |
| ---- | --- |
| Transfers, wires and ACH in or out | Money crossing the boundary |
| Securities transferred in or out in kind | Value crossing the boundary, even though no cash moved |
| Dividends and capital-gain distributions | Income — **including reinvested ones**, because the dividend added value; the purchase that spent it is the half that nets to nothing |
| Interest | Income |
| Margin interest, advisory and account fees | Money leaving |
| **Change in market value** | Entered as a **line**, taken from the figure the statement prints |

**The statement says how many lines it skipped.** A row reading
*"41 securities transaction(s) were present and deliberately not extracted"*
([Chapter 5](05-import-problems.md#brokerage-retirement-life-insurance-and-annuity-statements))
is what tells you the short register was a decision rather than a failed import.
It appears only when something really was skipped, so a policy that trades
nothing does not carry it.

### A policy or annuity has no transaction register

A cash-value life-insurance policy prints no list of transactions anywhere. Its
**"value of your policy"** block is the only place it says what moved, so that
block *is* the activity, and those are the lines you will see: premiums paid in,
cost-of-insurance and administrative deductions taken out, and the investment
results for the period.

Two consequences follow, and both are normal:

- **Those figures are period totals and carry no dates**, because the statement
  never printed any. The statement says so in grey
  ([Chapter 5](05-import-problems.md#dates-and-individual-lines)) rather than
  asking you to date them. **A date-range search or exhibit will not include
  them** — that is the part worth remembering.
- **A handful of lines is the whole statement**, not a short read.

> **Read the leaf lines, never the subtotal.** Where a statement prints *"Total
> change in value"* over the two lines that make it up, only the two lines are
> imported. The subtotal would count the same money twice.

### Two things that surprise people

**The balances are the total account value, not the cash balance.** Cash plus
securities, taken from the portfolio summary. The cash or sweep balance cannot
be used here, because the purchases that turned cash into securities are
deliberately missing from the register — so nothing would tie.

On a policy that means **the total policy value**, which is not the same figure
as the cash value and is never the death benefit — those two must never be added
together, and only the cash value is divisible property.

**Change in market value is copied off the statement and never calculated.**
Insurers print it under their own names — *"Investment results"*, *"Fixed and
variable account results"*, *"Change in value"* — and any of those is read as
this line. Working it out by subtraction would make *every* such statement balance
perfectly, which sounds desirable and is the opposite: a $50,000 wire nobody
extracted would be silently absorbed into "market change" and the statement
would be stamped reconciled. Where the statement prints no such figure, the line
is omitted, the statement does not tie, and it says so — **a statement that will
not reconcile is a statement a person looks at, which is the right outcome.**

---

## The Inventory tab

**Financials** → **Inventory**. Every account on the matter with the closing
balance of its most recent statement, in two tables — **Assets** and
**Liabilities** — each totalled, with a **Net** beneath.

Columns: institution, account, held by, as of, balance, and Bates. **The Bates
cell opens the statement the figure came from.**

Four things to know before you rely on it:

| | |
| - | - |
| **Liabilities show as negative** | The card statement says you owe $1,045.45; the inventory shows −$1,045.45, because it reduces the estate. Both are right, and they answer different questions |
| **Every row has its own "as of" date** | There is no live balance here. Each figure is the closing balance of a real statement, which is what makes it citable — so two accounts usually speak as of different days |
| **"no statement" is not zero** | An account with nothing on file is listed with no balance and left out of the total. The count sits beneath the table. **Those rows are the ones to chase**, and the [Compliance tab](11-discovery-tracking.md) says which months are missing |
| **Held by is P, R or J** | Taken from the account's ownership and the matter's client alignment. If the column reads **C and O**, the matter has no alignment recorded — set it on the matter detail page ([Chapter 3](03-matters.md)) |

> ⚠ **This is not the Inventory and Appraisement.** The I&A is a sworn form, and
> it is not built. This tab is a schedule of account balances — useful for a
> settlement conference or a temporary-orders hearing, and not the thing you
> file.

---

## Correcting a line

Extraction misreads things — a smudged digit, a description running off the
page. Click **Edit** on any transaction row.

You can change: description, transaction date, posted date, amount, running
balance, counterparty, location, Bates number, check number, and page number.

There is also a **Why (optional)** box — *"Corrected against the source page"*.
**Fill it in.** Six months later it is the difference between a defensible
correction and an unexplained one.

Click **Save correction**.

### What happens when you save

- Only fields you actually changed are recorded.
- Each change appends a **MANUAL_CORRECTION** flag naming the field, the old
  value, the new value, your name, the time, and your reason.
- The original value stays recoverable from the record.
- An **audit log** entry is written.
- Previous corrections to the same line are listed in the dialog under
  **Earlier corrections**.

Nothing is quietly overwritten. The first question on cross-examination is
where a figure came from, and this is the answer.

### Correcting an amount re-reconciles the statement

This is usually the point of the edit — an unreconciled statement is most often
one misread figure. When you change an amount:

- The statement's closing balance is recomputed.
- The stale **UNRECONCILED** flag is *replaced*, not stacked, so a statement
  corrected into balance stops claiming it is out of balance.
- The review status is left alone. Clearing an exception is a decision, not a
  consequence of arithmetic — go and accept it deliberately, **once the chip
  reads *reconciled***. If it still says *off by $…*, it is not yours to accept;
  see [Chapter 5](05-import-problems.md#an-unreconciled-statement-what-to-do).

### Structural fields cannot be edited

Which statement a line belongs to, and which account, are not editable.
Changing those is a re-import, not an edit.

---

## Adding a line the import missed

A line that is printed on the statement but absent from the import leaves the
balance permanently short. **Add it by hand**, from the statement's transaction
list:

1. Open the statement.
2. Click **Add a line**.
3. Type the amount. Sign it the way the statement prints it — negative for money
   out, positive for money in.
4. Type the date, the description, and anything else the page shows: the
   counterparty, the check number, the page number, the Bates number.
5. Type your reason for the addition.
6. Click **Save**.

Cyclone re-checks the balance at once, so a line that was missing takes the
statement back into balance as soon as you save it.

**The new line carries a `MANUAL_ENTRY` flag with your name, the time, and your
reason.** That is the point of the feature rather than a side effect of it:
every exhibit carries a notice promising the underlying records are available
for examination, and a figure nobody can find on the production is exactly what
that promise must not cover in silence.

> **Use it only for a line that is printed on the document.** Nothing can check
> that, which is why the record names a person rather than merely noting an
> edit.
>
> The date may be left empty. A policy statement's figures are period totals and
> never had dates ([above](#a-policy-or-annuity-has-no-transaction-register)) —
> but an **empty** date is the only acceptable blank. Never guess a month: an
> undated line is excluded from date-filtered searches, which is recoverable,
> and a wrongly dated one is not.

**What this replaces is a row typed straight into the database** — entered at
zero, then corrected through the app until the arithmetic closed. That works,
and it leaves nothing on the page a reader can see.

---

## Correcting a balance the import read wrong

The opening and closing balances on a statement row are **clickable**, shown
with a dotted underline. Click either one to correct both.

1. Open the source PDF and read the balance off the page.
2. Click the balance on the statement row.
3. Type the figure as printed, into **Opening balance** or **Closing balance**.
4. Type your reason.
5. Click **Save**.

The statement re-reconciles at once.

**A misread balance has no other repair, which is why this exists.** A **Retry**
re-reads the same PDF and returns the same number. Everything else leaves the
statement permanently out of balance, or invites somebody to adjust a
*transaction* to close a gap the header caused — and then the arithmetic ties
while the page disagrees with the record.

### The dialog offers the neighbouring figures

A statement's opening balance should equal the previous statement's closing
balance. Where Cyclone holds the neighbour, the dialog says what that figure is
and offers **use it**.

**It is a hint, never a default.** Nothing is filled in for you: the number on
the page is the evidence, and the chain is only a way of telling you which page
to look at.

> **The correction is recorded as `BALANCE_MANUALLY_ENTERED`** — both figures,
> your name, the time and your reason — for the same reason a corrected line
> and an added line are. A balance anchors the whole reconciliation, so a figure
> that reached an exhibit should be able to say where it came from.
>
> **It does not clear the exceptions queue.** Accepting a statement stays a
> decision somebody makes, not a consequence of the arithmetic working out.

---

## Removing a line, and putting it back

In the same dialog: **Remove this line**, then confirm.

Removing **hides** the line and takes it out of every total, but keeps it —
with your name and your reason.

> **The test of whether a removal was right: the statement should reconcile
> *better* without the line.** If extraction invented it — a row read twice, a
> daily-balance entry read as a transaction — removing it brings the balance
> into line. **If reconciliation gets worse, you removed something real.** The
> returned statement tells you which happened.

To find removed lines again, go to the **Transactions** tab and switch on
**Show removed** (the red chip). Open the line and click **Put this line
back**.

Removed lines are excluded from search results, totals, and exhibits by
default. That is the intent — but it also means they are easy to forget, so use
**Show removed** before you conclude that a line was never there.

---

## Why statements are deleted outright, and lines are not

A statement or an account is **deleted permanently**. A single transaction line
is **hidden and kept**.

The reason is that nothing in Cyclone is the original record — the Bates-stamped
PDF in storage is. Deleting a statement costs a re-import. But dropping a
*line* asserts something about the document ("this is not printed there") and
changes whether the statement reconciles, so it is flagged, hidden, attributed,
and re-reconciled rather than destroyed.

---

[← Merging accounts](06-merging-accounts.md) · [Index](README.md) · Next: [Categorizing, and the FIS →](08-categorizing-and-fis.md)
