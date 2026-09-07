# 4. Importing statements

[← Creating a matter](03-matters.md) · [Index](README.md) · Next: [When an import has a problem →](05-import-problems.md)

---

## Getting there

**Matters** → click the matter → **Financials →** (top right).

The Financials page has five tabs, and they are the shape of the whole job:

| Tab | What it is for |
| --- | -------------- |
| **Import** | Dropping PDFs, and reviewing what did not clear |
| **Accounts** | The accounts the imports created, and your judgments about them |
| **Compliance** | What has and has not been produced |
| **Transactions** | Searching, categorizing, tagging, exporting |
| **FIS** | The Financial Information Statement |

Two of the tabs carry an amber count when something needs you:

- **Import** — statements that did not clear on their own.
- **Accounts** — accounts with no owner recorded yet.

---

## Dropping the files

Drag statement PDFs onto the drop zone, or click it to browse. Bank,
brokerage, and credit card statements are all handled.

**Drop the whole stack at once.** Several files are read at the same time, so
twelve monthly PDFs dropped together finish far sooner than the same twelve
months combined into one large file — and much sooner than dropping them one at
a time.

> **Do not merge a year of statements into one PDF to "make it easier".** It is
> slower, and it is the single most common cause of a failed import: on a very
> long document the reader loses its place partway through, and the statement
> comes out short and unreconciled. Individual monthly files are the best input
> this software can be given — and if a client or opposing counsel produces one
> enormous file, splitting it before you upload will save you doing it
> afterwards.

A single file holding several statements still works — a combined statement
where one bank prints every account you hold is ordinary input — and each
statement inside it is filed separately.

### What you will see, in order

1. **`Uploading 12 files…`** with a running count, on the same instant you drop.
   Sending a dozen files takes ten to fifteen seconds.

   > **Do not drop them again.** If the screen looks busy, it is. Dropping a
   > second time is refused with a message rather than silently ignored, but
   > waiting is what you want.

2. **`Reading the statements…`** with a seconds counter and a done-of-total
   count. This is the slow part — minutes, not seconds, for a large scanned
   document. **You can leave this page.** The work runs on the server and
   carries on without the browser open.

3. **The result summary.**

### If you are told the PDF looks like several statements

An amber panel headed **Before you rely on this import** may appear seconds
after the upload lands. It is advice, not a failure, and the import continues:

> *"This PDF looks like it holds about 24 separate statements — its pagination
> restarts at 'Page 1' 24 times. Reading a document this size takes a long time
> and the accounts can be filed against the wrong statement. If the import
> comes out wrong, split the PDF into one file per statement and upload them
> together…"*

Read the result carefully when you see this. If it comes out wrong, split the
file and re-drop.

---

## Bates stamp options

Under the drop zone is a collapsed **Bates stamp options** section with a
**Prefix** box.

**Usually leave it empty.** Cyclone finds the Bates stamp by its pattern — the
one number on the page that advances by exactly one per page, which nothing
else on a bank statement does. That is more reliable than anything you could
type.

Set a prefix only when a document carries two competing series (one production
stamped over another), or when the result shows the wrong series was picked.

---

## Reading the result

### The Bates panel

At the top of the summary:

- **`KF-000101 → KF-000148 · 48 pages stamped`** — the range found.
- **`uncertain — check the prefix`** in amber — the series was detected weakly.
  Look at the actual PDF before relying on the numbers.
- **`No readable stamp on page 3, 7`** — those pages carry no citation. A number
  is **never** filled in from the pages around it. A citation to a number that
  is not printed on the page is worse than no citation at all.
- **`Missing from the run: KF-000112, KF-000113`** in red — those pages are
  absent from the production. This is a finding about the *other side*, not
  about the software.
- **`No Bates series found`** — this is not a stamped production copy. Common
  and fine for a client's own downloaded statements.

### The per-statement lines

One line per statement found, each starting with a coloured status:

| Status | Meaning |
| ------ | ------- |
| **cleared** (green) | Reconciled, nothing flagged. Nothing to do. |
| **needs review** (amber) | Something did not add up or was flagged. It is waiting in the exceptions queue below. |
| **duplicate** (grey) | This statement period is already on file for that account. Nothing was imported. |
| **error** (red) | The statement could not be read at all. The reason follows on the line. |

After the status: the institution, the period, the number of lines extracted,
and — if it did not reconcile — **`off by $1,203.02`** in red.

**`Unidentified institution`** where the bank name should be is common and
expected: many banks print their name only inside the letterhead *graphic*,
which is a picture, not text. Cyclone re-reads the page to find it, but not
always successfully. It matters because the bank name plus the last four digits
is how a statement is matched to an existing account — see
[Chapter 6](06-merging-accounts.md).

### Two links at the bottom

**Review accounts →** and **Search transactions →**. After an import, reviewing
the accounts is genuinely the next thing to do.

---

## What happens to each statement

Understanding this makes every warning in the next chapter make sense.

1. **The PDF is read.** Pages with a text layer are read directly; pages that
   are images are read by a vision model.
2. **The bank, account number, period, and balances are identified.**
3. **Every transaction is extracted**, with its date, description, amount,
   running balance, page number, and Bates number.
4. **The account is matched.** Bank name **plus last four digits** is the key.
   A match files the statement against the existing account; no match creates a
   new one.
5. **The statement checks itself:** opening balance + every transaction should
   equal the closing balance the bank printed.
6. **The result is graded.** No warnings → **cleared**, and it is accepted
   without anyone looking at it. Any warning → **needs review**, and it waits
   for you.

That last step is what makes a production of several hundred statements
workable: the clean ones go through, and your attention goes only to the ones
that need it.

---

## After the import

1. **Work the exceptions queue** — [Chapter 5](05-import-problems.md).
   Anything showing **off by $…** does not get accepted by a paralegal: it is
   either an import to redo or a document to replace, and if it is neither, the
   attorney decides.
2. **Go to the Accounts tab** and set ownership on every account showing
   **Not yet determined** in amber (the tab count tells you how many).
3. **Check for duplicate accounts** — [Chapter 6](06-merging-accounts.md).
4. Then move on to categorizing ([Chapter 8](08-categorizing-and-fis.md)).

---

[← Creating a matter](03-matters.md) · [Index](README.md) · Next: [When an import has a problem →](05-import-problems.md)
