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
filled cell. The type headings are Checking, Savings, Credit cards, Loans,
Brokerage, **Crypto**, Retirement, **Life insurance**, **Annuities**, HSA and
Other.

> ⚠ **Crypto accounts printed as "Other" on every compliance exhibit produced
> before this release.** The account was always typed correctly; only the
> heading on the exhibit was wrong. **Re-export any exhibit you have already
> handed over or filed** if the matter holds a crypto account — the grid itself
> was right, so nothing needs re-importing.

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

### Text for a motion to compel

Beneath the grid: **Text for a motion to compel**. It writes the paragraph and
the numbered list for you — each account, and the periods nobody produced, in
whole dates — ready to paste. Then **Copy**.

- Accounts with nothing missing are left out.
- If nothing is missing at all, it says so rather than producing an empty
  heading.

---

## Accounts referenced but never produced

A separate report, on the **Accounts** tab: **Referenced but not produced**.

A production names the accounts it does not contain. Money moves between
accounts, and the statement you *do* have prints the number of the one you do
not — `Transfer from XXX4070`, `INTERNET XFER FROM CHKG 8098386837`. Matching
those against the accounts on the matter leaves the ones nobody produced.

It reports four kinds of evidence, each in its own section — plus a fifth block
of rows it has [set aside](#the-probably-already-produced-group):

| Section | What it found |
| ------- | ------------- |
| **Referenced but not produced** | An account number named in a transfer or payment, matching no account on the matter |
| **Institutions named by wires, with no account produced** | A bank named by an incoming or outgoing **wire**. A wire names the sending *institution*, never the sending account, so there is no number to key on — which is why these are listed separately |
| **Creditors paid, with no account produced** | A payee that a payment says money is owed to |
| **Platforms holding value, with no account produced** | Venmo, PayPal and the like. The figure shown is money that **left** the produced accounts through the platform and did not come back — deliberately **not** called a balance: it may still be there, it may have been spent from the platform, or it may have moved on to another unproduced account. A negative figure is the opposite finding — the platform funded the produced accounts from somewhere these records do not contain |

### Reading the platforms section

Each platform row shows how many lines matched, the money **sent to** it, the
money that **came back**, and the difference — labelled **Not returned**, never
*balance*.

Beside the platform's name are amber chips: **the kinds of account to ask for.**
One platform commonly holds several — PayPal is a balance, a card and a PayPal
Credit line; Robinhood is a brokerage, crypto, an IRA and a margin loan — and **which of them this household actually has cannot be read off a
transaction description**. So the chips are what the platform is *capable* of
holding, and the exhibit says so in a footnote. Put the whole list in the
request for production and let the other side answer it.

Where the matter already holds an account at that platform, the row says so —
*(1 already produced)* — and the chips shrink to what is still unaccounted for.
**A platform disappears from the report only when every kind has been
produced**, because a produced PayPal *balance* answers the deposit half and
says nothing whatever about PayPal Credit.

### The "Probably already produced" group

A fifth block appears when an institution name closely resembles an account the
matter already holds. `BofA` and `Bank of America, N.A.` are the same bank, and
comparing the names as raw text reported one as undisclosed. Names are now
compared **as words**, with the furniture — *Bank*, *N.A.*, *Credit Union*,
*Financial* — set aside, so what is left is what identifies the institution.

| | |
| - | - |
| What it holds | Rows that are probably about an account already on the matter, each named with the account it probably is |
| In the exhibit? | **No.** The Selection block states how many were set aside, so a filed document never reads as the complete list when it is not |
| What to do | **Check them.** If one really is a separate account, it belongs back in the exhibit, and the two account names need reconciling — [merging](06-merging-accounts.md) is how |

**A close match demotes a row; it never deletes one.** Reporting an account that
turns out to have been produced is a visible nuisance. Hiding one that was never
produced is silent, and far worse.

> **An account on the matter whose number could not be read** used to make every
> reference to it look undisclosed, because there was nothing to match on. Those
> land in this group too.

### Add the account, to get a row off the list

**Add to the account list…** on a row, or **Add an account** on the Accounts tab.

**This list is rebuilt from the transactions every time it runs, so there is
nothing to tick off.** A $5 repayment to a relative stays on it forever.
**Adding the account is what resolves the row**, and the **character** you
record says why:

| Character | What follows |
| --------- | ------------ |
| **No marital property interest** | A third party's account the production named in passing. It stays on the matter so the references stop being reported, and it is **left out of the compliance grid and the inventory** — there is nothing to compel and nothing to divide |
| **Anything else** | The account joins the compliance grid with **nothing produced against it**, which is exactly what a motion to compel is built from |

This replaces a list of "don't worry about that one" accounts kept in somebody's
head or a spreadsheet. It is also how an account from something unprocessable —
a receipt found in a vehicle — gets onto the matter at all. **A duplicate is
refused**, and the message names the account that already exists.

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

---

## Rulings about payees

### The three verdicts

| Verdict | Where you record it | Scope by default | What it does |
| ------- | ------------------- | ---------------- | ------------ |
| **Creditor** | The **Creditor** button on a candidate row | **Firm-wide** | The payee becomes a finding and reaches the exhibit. A creditor is nearly always a national brand, so ruling once means it is never triaged again |
| **Not a creditor** | The **Vendor** button on a candidate row | **This matter** | Removes the payee from the report. Suppressing one across every case in the firm on one person's judgment is how a real account goes missing silently |
| **Holds value** | The rulings screen below (there is no button on a candidate row) | Whichever you choose | Marks a counterparty that holds a balance, a portfolio or crypto — traffic in *either* direction then proves the account exists. It must say **what to ask for** |

> **A payee can be "holds value" *and* "not a creditor" at the same time, and
> Venmo is exactly that** — not a lender, and holding money. The two are matched
> against separate lists on purpose: otherwise the true statement *"Venmo is not
> a creditor"* would silently delete the finding that it holds a balance.

### Rulings that are already in place

The firm ships with a body of rulings, so the queue starts short:

- **Thirty-odd platforms** ruled as *holds value* — wallets (Venmo, PayPal, Cash
  App, Apple Cash), crypto exchanges (Coinbase, Kraken, Crypto.com), brokerages
  and app-first investment platforms (Robinhood, Fidelity, Vanguard, Schwab,
  E\*TRADE, Betterment), and the neobanks (SoFi, Chime, Varo, Revolut).
- **The buy-now-pay-later lenders** ruled as *creditors* — Affirm, Klarna,
  Afterpay, Sezzle, Zip, Uplift, Katapult, Perpay and Splitit. This is the debt
  least likely to be disclosed: it is rarely on a credit report, the client does
  not experience it as borrowing, and on a statement it looks like a
  subscription.

**Nobody should have to answer the same question every week.** Venmo holds a
balance on every matter that will ever be opened, so it is answered once, for
the firm. Only the long tail — a credit union's app, a fintech from last year —
reaches the triage queue.

> **Some real platforms are deliberately *not* seeded**, because their names are
> also ordinary English: Current, Dave, Albert, Empower, Public, Gemini. A
> firm-wide pattern that is also a common word mis-files evidence on every
> matter at once, so those are left for a **matter** ruling, made by somebody
> who has looked at the line that prompted it.

### Reviewing, changing, and undoing a ruling

At the foot of the **Referenced but not produced** panel:
**▸ Standing rulings about payees**.

Two lists, shown separately rather than merged — **This matter** first, then
**Firm-wide** — so nobody changes the firm's answer while believing they are
working on one case.

| Control | What it does |
| ------- | ------------ |
| **Edit** | Change the pattern, the verdict, the name used in a motion, the kind of debt, the kinds to ask for, the note, or retire it by unticking **Active** |
| **Delete** | Removes the ruling. The payee returns to the candidate queue on the next scan |
| **Add a ruling for this matter** | A pattern of three characters or more, a verdict, and a name for a motion. A *holds value* ruling will not save until you tick at least one **Ask for** kind — that set **is** the request for production |

**Deleting is safe in a way most deletes in Cyclone are not.** A ruling is an
opinion about a payee, not evidence. Nothing is re-filed and nothing is lost:
the report is recomputed from the transactions every time it is read, so the
next reading simply reflects the new answer.

> **Editing a firm-wide ruling changes every open case**, and Cyclone asks you
> to confirm before it does. Usually the better fix is the one the screen
> suggests: leave the firm ruling alone and **add a matter ruling for the same
> payee**, which overrides the firm's answer for this case only.

**Write the note.** It is the field that tells whoever inherits the matter why
somebody decided a payee was a vendor.

### If you click twice

Ruling on a payee triggers a full rescan of the matter, which takes a few
seconds. The row is removed from the queue **immediately**, and the buttons are
replaced by *Saving…* while the write is in flight. If you do click twice, the
second click is not an error — the ruling was stored the first time, and Cyclone
treats it as the success it is.

---

## Checklist

- [ ] Both look-back dates set on the matter
- [ ] Every account has **Who produces** set (the banner counts the ones that do not)
- [ ] First and last statements marked on every account you can vouch for
- [ ] Run the report for **both** sides
- [ ] Take the coverage lines — the day-level gaps — to the attorney, not the grid
- [ ] Work the candidate queue down on **Referenced but not produced**, so the
      exhibit carries every creditor and no unruled payee
- [ ] Check **Standing rulings about payees** before assuming a payee is absent
      because nothing was found — it may have been ruled away

---

[← Exporting exhibits and data](10-exports.md) · [Index](README.md) · Next: [User roles →](12-user-roles.md)
