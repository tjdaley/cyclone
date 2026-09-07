# Cyclone — User Guide

For the people who do the work: paralegals, legal assistants, and the
attorneys who look over their shoulder.

Each chapter is a task. Read the one you need; you do not need the others
first, except that everything in Part 3 assumes a matter exists.

## Part 1 — Getting in

| | |
| - | - |
| [1. Getting started](01-getting-started.md) | Being given an account, first sign-in, what to do when it does not work |

## Part 2 — Setting up work

| | |
| - | - |
| [2. Creating a client](02-clients.md) | The client record, the conflict check, and the three other ways a client gets created |
| [3. Creating a matter](03-matters.md) | The matter, its caption, and the fields that other screens depend on |

## Part 3 — Financial statement discovery

This is the largest part of the guide, and the order below is the order the
work happens in.

| | |
| - | - |
| [4. Importing statements](04-importing-statements.md) | Dropping PDFs, what happens to them, and how to read the result |
| [5. When an import has a problem](05-import-problems.md) | The exceptions queue, every flag you can see, and what to do about each one |
| [6. Merging accounts](06-merging-accounts.md) | When one real account has been filed twice |
| [7. Viewing statements and transactions](07-viewing-statements.md) | Reaching the source page, correcting a line, removing one |
| [8. Categorizing, and the FIS](08-categorizing-and-fis.md) | Filing transactions so the Financial Information Statement survives cross-examination |
| [9. Tagging transactions](09-tagging.md) | Tags, custom tags, and how they differ from categories |
| [10. Exporting exhibits and data](10-exports.md) | CSV, Markdown, Word, PDF — and which one to use when |
| [11. Discovery tracking](11-discovery-tracking.md) | The compliance matrix, look-back dates, and who produces what |

## Part 4 — Administration

| | |
| - | - |
| [12. User roles](12-user-roles.md) | The four roles, what each can do, and how to change them |
| [13. Assigning staff to a matter](13-matter-staff.md) | What assignment does — and what it does not |

---

## Before you start: why the answers can be trusted

Everything in Part 3 rests on four rules. Knowing them is the difference
between using this software well and clicking buttons until the warnings go
away.

**1. Every statement checks itself.**
Opening balance, plus every transaction, should equal the closing balance the
bank printed. If it does, the statement is marked *reconciled* and you can rely
on the transaction list being complete. If it does not, Cyclone says so and
tells you the exact difference. **It never invents a transaction to make the
numbers balance** — a made-up line is precisely what gets taken apart on
cross-examination.

> **The firm's rule follows directly from this: a statement that does not
> reconcile is never accepted without attorney approval.** Either the import
> failed — usually a PDF holding too many pages, fixed by splitting it and
> re-importing — or the document itself is defective, and needs replacing or
> re-ordering. Both have a repair, and accepting is not one of them.
> [Chapter 5](05-import-problems.md#an-unreconciled-statement-what-to-do) has
> the procedure.

**2. A guess is always labelled.**
Where the software had to infer something — a year that was not printed on the
line, a bank name it could not read, a date it re-read — it marks the record.
You will see these as flags and as small dagger symbols (†). An unlabelled
figure was read off the page.

**3. Judgment is never automated.**
Who owns an account, whether property is community or separate, whether a
statement is the first one an account ever had, whether a payee is a creditor —
none of these are extracted. A person decides them, and the record shows who.

**4. What is missing matters as much as what is there.**
The compliance matrix, the Bates gap report, and the undisclosed-accounts
report all exist to show holes. A blank cell is not an error in the software;
it is usually the point.

## A note on money

Amounts are signed by **how they moved the balance the bank printed**. A deposit
is positive. A withdrawal is negative. On a credit card, a purchase is positive
(it raises what is owed) and a payment is negative. This is why
`opening + everything = closing` works for every kind of account, and it is
worth knowing before a credit-card statement looks upside down to you.
