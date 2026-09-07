# 5. When an import has a problem

[← Importing statements](04-importing-statements.md) · [Index](README.md) · Next: [Merging accounts →](06-merging-accounts.md)

---

Statements that did not clear on their own wait in **Needs review** on the
**Import** tab, under an amber bar. The tab itself carries the count.

Nothing here is an error in the ordinary sense. It is the software telling you
what it could not be sure of, so that a person can decide.

> **The one hard rule in this chapter:** a statement that does not reconcile is
> **never** accepted without attorney approval. Everything else on this page is
> judgment; that one is policy. See
> [An unreconciled statement: what to do](#an-unreconciled-statement-what-to-do).

---

## What a queued statement shows you

```text
Wells Fargo ····4448   01/05/2024 – 02/04/2024   off by $1,203.02   jan-2024.pdf
[Show the source statement]   KF-000101–KF-000112    Accept  Retry  Reject and delete

opened $12,400.11    printed close $9,872.55    computed $11,075.57

UNRECONCILED · balances.ending_balance — Transactions do not account for the
change in balance. Printed close 9872.55, computed 11075.57, difference 1203.02.
Nothing was added to force it to balance.
```

The three figures are the whole diagnosis:

| | |
| - | - |
| **opened** | The opening balance the bank printed |
| **printed close** | The closing balance the bank printed |
| **computed** | Opening balance plus every transaction Cyclone extracted |

If **computed** and **printed close** disagree, something is missing, doubled,
or wrong — and the difference is usually the size of the missing item, which is
often enough to find it.

Beneath the figures are the flags: the code in monospace, then plain English.
Amber flags are warnings; grey ones are informational.

---

## Your three choices

### Show the source statement

**Do this first, nearly every time.** It opens the original PDF at the page the
statement's first transaction was printed on. You cannot decide whether a
balance is really what the bank printed by looking at the numbers Cyclone
extracted from it.

If the PDF cannot be shown you get one of three specific answers — never
stored, purged, or storage unavailable — because each calls for a different
next move.

### Accept

Keeps the statement exactly as it is. Use it when you have looked at the source
and the extraction is right, or when the problem is understood and harmless.

> **🚫 NEVER accept an unreconciled statement without attorney approval.**
>
> If the statement shows **off by $…** rather than **reconciled**, a paralegal
> does not accept it. Not after checking the source, not because the difference
> looks small, not to clear the badge. **Take it to the attorney.**
>
> An unreconciled statement means either the import failed or the document
> itself is defective — and both have a repair. Accepting is not a repair; it
> is a decision to proceed on records known to be incomplete, and that decision
> belongs to the attorney. See [the next section](#an-unreconciled-statement-what-to-do).

**Accepting does not fix anything.** It records that a person looked. If the
statement is genuinely short a hundred transactions, accepting it puts a hundred
missing transactions into every exhibit built afterwards, silently.

### Retry

Discards and re-reads the document. Use it when the extraction is plainly wrong
in a way a second reading might fix — a short read, an unreadable page, a
truncated pass.

> **Retry discards the whole upload, not just this statement.** One PDF can
> hold five accounts; re-reading it recreates all five, so all five have to go
> first or you would end up with duplicates. The confirmation says exactly what
> will be discarded. If the source PDF can no longer be retrieved, **nothing is
> deleted** — Cyclone checks it can get the file back before destroying
> anything.

### Reject and delete

Deletes the statement and its transactions permanently. If that leaves the
account with nothing, the account goes too — unless somebody has already
recorded ownership, characterization, purpose, or notes on it, in which case
the account is kept and you are told why.

The source PDF stays in storage, so a rejected statement can always be uploaded
again. Use this for a genuine mess: pages scanned out of order, a summary table
read as a transaction register, a statement imported onto the wrong matter.

---

## An unreconciled statement: what to do

**`opening + every transaction` did not equal the closing balance the bank
printed.** Cyclone tells you the exact difference and has added nothing to
force it to balance.

There are exactly **two** reasons this happens, and they have different repairs.
Work out which one you have before doing anything else.

### Cause 1 — The import failed *(much the most common)*

Nothing is wrong with the document. The reading of it is short.

Almost always this is a **PDF with too many pages**: someone produced a whole
year, or a whole production, as one file, and the reader got lost partway
through. The signs:

- The difference is large, or the line count looks far too low for the period.
- **INCOMPLETE_EXTRACTION** is also flagged — the statement's own printed totals
  disagree with what was extracted.
- The source PDF is long, or covers several statements.
- The upload warned you that the file looked like it held several statements.

**The repair:**

1. **Reject and delete** the statement.
2. **Split the PDF into one statement per file.**
3. Upload the split files **together** — they are read several at a time, so a
   stack of monthly PDFs finishes sooner than the one big file did.

Splitting is the fix, not retrying the same enormous file. **Retry** is worth
one attempt on a document that is already a single statement; on a file holding
a year, it will get lost the same way.

### Cause 2 — The document itself is defective

The reading is faithful. The statement is the problem: a **missing page**, or a
multi-statement scan with **missing or badly out-of-order pages**. The signs:

- **BATES_GAP** is also flagged — pages are missing from the production.
- Page numbers on the source jump, repeat, or run out of order.
- The running balance visibly breaks between two lines on the page itself.
- The difference matches a page's worth of activity.

**The repair is to fix the document, not the record:**

- **Get a better copy** — from the client, from opposing counsel, or from the
  bank.
- If the pages are merely **out of order**, reorder them, then reject the
  statement and re-import the corrected PDF.

A missing page is also a **discovery finding**. Tell the attorney regardless of
whether you manage to get a replacement.

### The rare third outcome — the attorney decides to accept

Sometimes no better statement can be had. In that case the attorney may decide
it is worth accepting the unreconciled statement to capture what we can from it.

**That is the attorney's call, and only the attorney's.** When it is made:

- Accept it, so the queue reflects a decision rather than a backlog.
- The **UNRECONCILED** flag stays on the record permanently, which is correct —
  the statement will still show *off by $…* everywhere it appears, and every
  exhibit drawn from it carries the notice that entries were extracted by
  automated means.
- Make a note of who decided and why.

### The decision in short

| What you found | What to do |
| -------------- | ---------- |
| The read is short — long PDF, low line count, INCOMPLETE_EXTRACTION | **Reject**, split the PDF into one statement per file, re-import |
| A single-statement PDF read short | **Retry** once; if it fails the same way, treat as above |
| One misread figure, and you can see the right one on the page | **Correct the line** ([Chapter 7](07-viewing-statements.md)) — the statement re-reconciles by itself |
| Pages missing, or badly out of order | Get a better copy, or reorder and re-import. **Tell the attorney** |
| No better copy exists | **Attorney decides.** Do not accept on your own judgment |

---

## Flag reference

Every flag you can meet, what it means, and what to do about it.

### The balance did not work out

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **UNRECONCILED** | Opening + transactions does not equal the printed closing balance. The exact difference is given. Nothing was invented to close the gap. | **See [An unreconciled statement: what to do](#an-unreconciled-statement-what-to-do).** Either the import failed (split the PDF and re-import) or the document is defective (get a better copy). **Never accept it without attorney approval.** |
| **BALANCE_MISSING** | The statement did not print both a beginning and an ending balance, so there was nothing to check against. | Look at the source. Some statement formats genuinely do not print both — but first make sure this is not a page that failed to read. A statement with no self-check has an **unverified** transaction list, so treat accepting it the same way as an unreconciled one: raise it with the attorney rather than deciding alone. |

### The extraction may be incomplete — the serious ones

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **INCOMPLETE_EXTRACTION** | The statement prints its own totals ("262 Checks/Debits 195,600.04") and the extraction falls short of them. This is the strongest signal that a read stopped early, and it usually accompanies **UNRECONCILED**. | If the source PDF is long or holds several statements, **reject, split it into one statement per file, and re-import** — retrying the same big file will lose its place the same way. On a PDF that is already a single statement, **Retry** once. Never accept. |
| **PASS_UNREADABLE** | Named pages were read but the answer could not be understood, twice. Transactions printed there are missing, and the balances will be short by that amount. | **Retry.** If it fails the same way again, those pages need reading and entering by hand. |
| **PAGE_UNREADABLE** | Named pages could not be read at all — neither the text layer nor OCR. | Look at those pages in the source. Usually a bad scan. Get a better copy if you can; otherwise re-scan, or enter those pages by hand. |
| **NO_TRANSACTIONS** | No transactions were found at all. | Almost always a page that is not a transaction register — a cover page, a disclosure, a summary. Look at the source, then reject if that is what it is. |

### One statement may have been read as two

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **SUSPECT_SPLIT** | Two statements from the same document claim more than one shared page. Usually a summary table — a daily-balance list, an account summary — read as a second transaction register, which then invents its own account. | Open the source and compare the two. **Reject whichever is not the real register**, then delete the phantom account if one was created. |
| **OVERLAPPING_PERIOD** | This statement's dates overlap another already on the account. Consecutive statements do not overlap. | Usually the same statement read twice with its date range read differently. Compare the two and reject the less complete one. |
| **STATEMENT_BOUNDARY_UNCERTAIN** | The statement's printed header could not be found, so it was separated from its neighbours by page number alone. On a combined statement, lines belonging to the account before or after may have been filed here. | Check the first and last few lines against the source page. If they belong to another account, reject and split the PDF into one file per statement. |

### The account may be wrong

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **NO_ACCOUNT_MATCH** | The account number could not be read, so the statement could not be matched to an existing account, and a new one was created. | Go to the **Accounts** tab and check whether this duplicates an account already there. If it does, [merge them](06-merging-accounts.md). |
| **SAME_LAST4_DIFFERENT_INSTITUTION** | A new account was opened whose last four digits match an account the matter already holds, under a different bank name. | Two banks really can share a last four, so this reports rather than merging. Check the source. If it is one account read under two names, [merge them](06-merging-accounts.md). |
| **ACCOUNT_NUMBER_CONFLICT** | The account number read from the statement text and the number that appears on most pages disagree. The extracted one was kept. | Open the source and see which is right. Correct the **Last four** field on the account if needed. |
| **ACCOUNT_NUMBER_AMBIGUOUS** | Several numbers appear on as many pages as each other, so repetition could not settle which is the account number. | Check the source and set the account's **Last four** by hand. |
| **ACCOUNT_NUMBER_DERIVED** *(grey)* | The number was not in the statement text, so it was taken from the digit run printed on every page. | Informational. Spot-check it against the source once per account; it is right far more often than the extraction is. |

### Bates numbering

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **BATES_GAP** | The Bates run breaks *inside* this statement — named numbers are missing. | This is a finding about the production, not a software problem. Those pages are absent, so lines printed on them are not in the record. **Tell the attorney** — it is usually worth a letter. |
| **BATES_UNSTAMPED** *(grey)* | No readable stamp on named pages, so those lines carry no citation. | Informational. Nothing is fabricated — a number is never filled in from neighbouring pages. |
| **BATES_UNVERIFIED** *(grey)* | No Bates series was detected in the document, so any numbers recorded came from the reader and nothing has confirmed them. | Informational. Do not cite these numbers without checking the page. |

### Dates and individual lines

| Flag | What it means | What to do |
| ---- | ------------- | ---------- |
| **UNDATED_TRANSACTIONS** | Some lines have no date even after the date column was re-read. They count toward the statement's balance but **are excluded from every date-filtered search** — so an exhibit built by date range will not contain them. | Open the source and date them by hand ([Chapter 7](07-viewing-statements.md)). Do not leave these: they are invisible in exactly the place they matter most. |
| **LINE_WARNINGS** | Some transaction lines carry warnings of their own. | Open the statement's transactions and look at the flags on individual lines (below). |
| **DUPLICATE_CHECK_ROWS** *(grey)* | Checks appeared both in the debit list and again in the checks summary table; the repeats were dropped. | Informational — this is Cyclone preventing a double count. |

### Flags on an individual transaction line

These appear beneath a line in the transaction list rather than on the
statement.

| Flag | What it means |
| ---- | ------------- |
| **YEAR_INFERRED** | The line printed only month and day; the year came from the statement period. Shown in the list as a small **†** beside the date. |
| **DATE_REREAD** | The batch's date column was re-read because dates were missing, and this line's date changed. Both values are recorded. |
| **SIGN_ASSUMED** | The direction of the money was not explicit and was inferred. Worth checking. |
| **AMOUNT_UNCLEAR** | The figure was smudged, cut off, or ambiguous. **Check this one against the source.** |
| **LOCATION_INFERRED** | A city or state was split off the description. |
| **DESCRIPTION_TRUNCATED** | The description ran off the page. |
| **MANUAL_CORRECTION** | Somebody edited this line. The field, both values, who, and when are all recorded. |
| **MANUAL_DELETION** / **MANUAL_RESTORE** | Somebody removed or restored this line. |

---

## A rule of thumb

| Situation | Do this |
| --------- | ------- |
| It reconciles, and the flag is explaining something informational | **Accept** |
| **It does not reconcile** | **Do not accept.** [Work out which of the two causes it is](#an-unreconciled-statement-what-to-do), repair it, and escalate if it cannot be repaired |
| The read is short on a single-statement PDF | **Retry**, once |
| The read is short on a long or multi-statement PDF | **Reject**, split into one statement per file, upload them together |
| It is not a real statement, or it duplicates one | **Reject and delete** |
| Retry failed the same way twice | Split the PDF and upload again — do not keep retrying |
| Missing Bates pages, missing statement pages, or unproduced accounts | Tell the attorney — it is a discovery finding, not a bug |

**When in doubt, open the source statement.** Almost every question on this page
is answered in ten seconds by looking at the page.

---

[← Importing statements](04-importing-statements.md) · [Index](README.md) · Next: [Merging accounts →](06-merging-accounts.md)
