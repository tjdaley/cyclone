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
