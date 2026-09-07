# 11. Discovery tracking for financial accounts

[← Exporting exhibits and data](10-exports.md) · [Index](README.md) · Next: [User roles →](12-user-roles.md)

---

This is the report that replaces the workbook a firm otherwise builds by hand:
accounts down, months across, a Bates number in every filled cell.

**Its value is entirely in the blanks.** A filled cell says a statement was
produced; an empty one is a question, and the whole point of the exercise is to
turn the empty ones into either a motion to compel or a fact that nothing is
missing.

Setting it up takes three steps in three places, and it will not tell you
anything useful until all three are done.

---

## Step 1 — Record the look-back dates (once per matter)

**Matters** → the matter → **Discovery scope**.

| Field | What date goes here |
| ----- | ------------------- |
| **Our client produces from** | The look-back in the request **opposing counsel served on us** |
| **The other side produces from** | The look-back in the request **we served on them** |

Click **Save scope**.

> **Note which is which.** The column is named for **who produces**, not who
> asked. The date opposing counsel propounded is the one that binds *our*
> client.

Each report runs from its date through today.

### Why this matters more than it looks

Without a look-back date, the report can only say what falls **between** the
statements produced. The holes at either end can only be described as *"anything
before 4 December 2019"* — which is not something a request for production can
ask for.

With a look-back date, every gap has two ends, a gap wholly before the date
stops being a gap at all, and an account with **nothing** produced reports the
whole window. That last finding is one an unbounded report structurally cannot
make.

---

## Step 2 — Say who must produce each account

**Financials** → **Accounts** → **Edit** an account → **Who produces**.

| Option | Meaning |
| ------ | ------- |
| **Not yet decided** | The default. **The account appears on neither report.** |
| **We produce it** | Appears on the "We produce" report |
| **They produce it** | Appears on the "They produce" report |
| **Both sides ordered to** | Appears on **both**, bounded differently on each — two obligations over the same documents |

> **This is deliberately separate from "Held by".** They usually agree and
> sometimes do not — a joint account both sides were ordered to produce, or an
> account the other party holds whose statements we have and they do not.
> Ownership decides how an asset divides; this decides whose motion to compel it
> is. Neither is derived from the other, because that would be a legal
> conclusion drawn by a database.

Accounts left as **Not yet decided** are **counted and reported** at the top of
the compliance report — they are not silently dropped, because an unmarked
account would otherwise appear on neither report and never be chased.

---

## Step 3 — Mark the edges of each account's life

**Financials** → **Accounts** → open an account → the dropdown on each
statement row.

| Setting | Meaning |
| ------- | ------- |
| **neither end** | The default |
| **first statement †** | The first this account ever had — nothing is missing before it |
| **last statement ‡** | The last — nothing is missing after it |

### Why nobody can do this for you

An account opened in March 2021 has no January statement and never will. But a
statement does not say that it is the first, and an opening balance of zero is
not proof — accounts are swept to zero routinely.

So it is a judgment, like ownership and characterization, and it defaults to the
cautious answer. Until somebody marks it, the report assumes statements exist on
both sides.

**Get this wrong in the cautious direction and you chase documents that do not
exist. Get it wrong the other way and you stop chasing documents that do.**

> **A marker only suppresses the holes at the outer edges.** A gap *between* two
> produced statements is a gap whatever the edges say — the account demonstrably
> existed on both sides of it.

---

## Reading the report

**Financials** → **Compliance**.

### Choose a side first

Three buttons at the top:

| Button | What it shows |
| ------ | ------------- |
| **We produce** | Accounts we are responsible for, against the look-back **they** propounded |
| **They produce** | Accounts they are responsible for, against the look-back **we** propounded |
| **All accounts** | Everything, unbounded |

**These are two different reports, and they are not interchangeable.** A report
run for the wrong side reads as proof that nothing is missing at all.

### The banner

Above the grid:

- *"Measured against everything the other side must produce from 1 January 2019
  through 6 September 2026."* — bounded, and usable.
- An amber line if no look-back date is recorded for that side.
- An amber line counting accounts with nobody marked responsible.

### The grid

Accounts grouped by type, years down, months across, a Bates number in each
filled cell.

| Cell | Meaning |
| ---- | ------- |
| A Bates number | A statement closing in that month, and where it is in the production |
| **✓** on screen (**X** in the exported exhibit) | A statement was produced but carries no Bates stamp |
| **blank**, tinted amber | **No statement produced for that month** |
| **—**, tinted grey | Outside the account's life, from the † / ‡ markers. Not a gap |
| Tinted green / red | The month holds the account's first (†) or last (‡) statement |

Each filled cell carries a small PDF button that opens that statement's source
document. Hovering the Bates number shows the statement's exact period.

> **A cell is placed by the month its statement *closes* in.** A period running
> 4 March to 5 April is the April statement by every convention a bank uses.

### The coverage line — the part that goes in a motion

Beneath each account's grid:

- **"Complete — every day from the opening statement to the closing one is
  accounted for."**
- Or **"Not accounted for:"** with a list: *anything before 4 December 2019*,
  *5 April 2021 – 3 May 2021 (28 days)*, *anything after 6 August 2024*.
- Or **"No statements produced for this account at all."**

> **A filled month is not a covered month.** Statement periods run 4 March to
> 5 April, so a cell can be filled while days at either end of the month are
> missing. **The grid answers "which months have a statement"; the coverage line
> answers "which days does nothing account for" — and only the second belongs in
> a motion.**

### Exporting

Use the export bar ([Chapter 10](10-exports.md)). CSV is the flat grid for a
spreadsheet; MD, DOCX and PDF are landscape exhibits with the caption, the gap
list, and the verification notice.

**Blank rows are never trimmed.** Prosecuting a motion, the blanks are the
argument. A report that hid them to look tidier would be arguing the other
side's case.

---

## Accounts referenced but never produced

A separate report, on the **Accounts** tab: **Referenced but not produced**.

A production names the accounts it does not contain. Money moves between
accounts, and the statement you *do* have prints the number of the one you do
not — `Transfer from XXX4070`, `INTERNET XFER FROM CHKG 8098386837`. Matching
those against the accounts on the matter leaves the ones nobody produced.

It reports four kinds of evidence, each in its own section:

| Section | What it found |
| ------- | ------------- |
| **Referenced but not produced** | An account number named in a transfer or payment, matching no account on the matter |
| **Institutions named by wires, with no account produced** | A bank named by an incoming or outgoing **wire**. A wire names the sending *institution*, never the sending account, so there is no number to key on — which is why these are listed separately |
| **Creditors paid, with no account produced** | A payee that a payment says money is owed to |
| **Platforms holding value, with no account produced** | Venmo, PayPal and the like. The figure shown is money that **left** the produced accounts through the platform and did not come back — deliberately **not** called a balance: it may still be there, it may have been spent from the platform, or it may have moved on to another unproduced account. A negative figure is the opposite finding — the platform funded the produced accounts from somewhere these records do not contain |

### Reading it carefully

**A dagger means the institution was inferred, not read off the page.** Where
the description names no bank, the transfer is assumed to stay inside the bank
whose statement it was printed on. That is an inference and it says so.

**Creditors have to be ruled on, and candidates never reach an exhibit.**
Nothing in a payment description can tell you whether a payee is a creditor —
*"Online Payment To Mr. Cooper"* (a mortgage servicer) and *"Online Payment To
Frontier"* (an ISP) are the same sentence. Two things answer it:

- The payee's transactions are filed under a category marked as a debt — a
  paralegal filing a line under "Credit Card Payments" **is** that assertion,
  made as ordinary work; or
- Somebody records a ruling on that payee.

Everything unruled is a **candidate**, shown in its own list, ranked by money,
and **excluded from the exhibit entirely**. Putting an unclassified payee into a
document filed with a court asserts of a water utility exactly what it asserts
of American Express.

Rule a payee as **not a creditor** when it plainly is not. That is stored,
attributed, and reversible — without it, forty utilities come back on every
matter and the list stops being read.

**The two verdicts are scoped differently, on purpose:**

- **Creditor** goes **firm-wide** by default. A creditor is nearly always a
  national brand, so ruling once means it never has to be triaged again.
- **Not a creditor** is scoped to **this matter** unless you say otherwise.
  Suppressing a payee across every case in the firm on one person's judgment is
  how a real account goes missing silently — which is the failure this whole
  report exists to prevent.

> There is not yet a screen for reviewing or reversing a **firm-wide** ruling.
> Ask a developer if one needs undoing.

---

## Checklist

- [ ] Both look-back dates set on the matter
- [ ] Every account has **Who produces** set (the banner counts the ones that do not)
- [ ] First and last statements marked on every account you can vouch for
- [ ] Run the report for **both** sides
- [ ] Take the coverage lines — the day-level gaps — to the attorney, not the grid

---

[← Exporting exhibits and data](10-exports.md) · [Index](README.md) · Next: [User roles →](12-user-roles.md)
