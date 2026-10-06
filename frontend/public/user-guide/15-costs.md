# 15. AI costs and prices

[← Where leads go](14-lead-routing.md) · [Index](README.md)

---

**Admin** → **Costs**, and the menu item is visible to administrators only.

Cyclone sends documents to AI vendors — reading a statement, classifying a
discovery request, drafting a lead reply — and every one of those calls is
billed by the vendor. This page says what that costs, and who it was spent on.

It is not paralegal work. Nothing here changes a matter, a statement, or a
figure on an exhibit.

---

## What the numbers are, and what they are not

**Model-API spend only.** Not Supabase, not AWS, not storage, not hosting.
Those are designed and not built, so **this page is not the cost of running
Cyclone** — it is the cost of the AI work inside it.

| | |
| - | - |
| **Counted, never estimated** | Figures are the vendor's own token counts multiplied by the price you entered for that day. Nothing is guessed from the length of a document |
| **Every attempt counts, including failures** | A call that failed over to another vendor, or whose answer was rejected as unreadable, was still billed. One statement import can therefore show several calls to different vendors. That is the bill, not double-counting |
| **Months are UTC** | Vendor invoices are. A call made at 8pm Texas time on 30 September counts in **October** |
| **Budgets do not exist yet** | Nothing here stops spending. It reports |

### Two labels that mean something specific

**"Priced so far"**, where you would expect *Total*, means some calls have no
price entered yet. The figure is then a **floor**: the real number is higher.
An unpriced call counts as *unknown*, never as $0 — see
[Missing prices](#missing-prices-the-to-do-list) below.

**`<$0.01`** means more than nothing and less than a cent. It is written that
way because rounding to cents would print `$0.00`, and free is not what
happened.

---

## Spend

Pick a month range and how to break it down:

| Broken down by | Answers |
| -------------- | ------- |
| **Firm** | Whose spend it is. One row today, because there is one firm |
| **Vendor and model** | Which vendor is costing what |
| **Task** | What the work *was* — reading a statement, classifying discovery, a lead reply |
| **Matter** | Which case. *(no matter)* covers work that is not about one |
| **Staff member** | Who set it off |
| **Where it started** | A background job, a web request, the CRM poller |

Two entries under *Where it started* are worth knowing:

- **`unattributed`** is spend from a path that recorded no owner. It is shown
  rather than hidden, because a gap you can see is a gap somebody can close.
- **Intake preview has no matter, by design** — its whole purpose is to *create*
  the matter. It is attributed to the person and the request instead.

> **Renaming the firm relabels past months too.** The report keys on the firm's
> identity, not the name it had at the time.

---

## Missing prices — the to-do list

Every meter that carried tokens and has no price. While this list is not empty,
the Spend total says *"Priced so far"*.

**Always start here rather than typing a price in by hand.** **Set price** fills
in the vendor, the exact model name, the meter, and a start date equal to the
first day that model was used — so the price covers every call already recorded.
A model name typed from memory (`flash-lite` against `flash_lite`, a trailing
space) produces a price that silently never matches anything.

### A model you have never heard of

A row may name a model that appears nowhere in Cyclone's configuration, with
**"requested as …"** written underneath.

That is not a mistake. Cyclone asks a vendor for a model by name; the vendor may
serve that name from a **specific dated version**, and it says which in its
reply. Prices are matched to the model that **answered**, because that is what
the vendor charged for — and on a large document job that is exactly the figure
a billing dispute turns on.

So: **enter the price under the name in the row**, and use *"requested as"* to
find the right line on the vendor's pricing page.

> **When a vendor repoints one of its aliases at a newer version, the new
> version appears here** instead of being quietly charged at the old price. That
> is the feature working.

**If the vendor no longer lists the model** — retired preview models do this —
take the price from the invoice or your own records, and say so in the note.

---

## Entering a price

**Prices are per 1,000,000 tokens**, which is how every vendor quotes them, and
you want the **Standard** (paid, on-demand) tier. Cyclone does not use Batch,
Flex or Priority pricing.

Which line on the vendor's page goes into which meter — they all word it
differently:

| Meter here | Anthropic | OpenAI | Gemini |
| ---------- | --------- | ------ | ------ |
| **Input** | Base input | Input | Input price |
| **Cached input** | Cache hits & refreshes | Cached input | Context caching price |
| **Cache write** | 5-minute cache writes | *(not billed)* | *(not billed per token)* |
| **Output** | Output | Output | Output price *(including thinking tokens)* |

- **Thinking and reasoning tokens are Output** on every vendor, and are already
  counted there.
- **A meter nothing has used needs no price.** Missing prices only lists meters
  that actually carried tokens.
- **Always fill in the source URL**, and note the tier. The price table is the
  evidence behind every figure on this page; a price with no source is an
  assertion.

*Worked example — Gemini 3.5 Flash-Lite, Standard, 16 September 2026: input
$0.30 → **Input**; output $2.50 → **Output**; context caching $0.03 → **Cached
input**. The "$1.00 per 1M per hour" storage line is not entered — Cyclone does
not use it.*

---

## Changing a price against correcting one

Two buttons, and they are not the same act.

| | **New price** | **Correct** |
| - | ------------- | ----------- |
| Use when | The vendor changed its price from a date | You typed it wrong |
| Dates | Closes the old price the moment the new one starts, in one step | **Cannot move dates** |
| Effect on past spend | None before the new start date | Re-prices every call that row covered |
| Recorded as | `service_rate.set` | `service_rate.corrected`, with before and after |

- **There is no delete.** A wrong price is corrected, never removed.
- **A start date earlier than a price that already begins later is refused**,
  with a sentence saying so. That stops a schedule being rewritten by accident;
  correct the later row instead, or choose a date after it.
- **Past totals move when a price is corrected, and that is deliberate.** Prices
  are applied when the report runs, never stored on the call, because prices get
  entered late and get fixed. A total that changed after an edit is the design,
  not a bug.

---

## When the page and an invoice disagree

1. Compare the **same UTC month** and the **same vendor**.
2. If the total says **"Priced so far"**, clear Missing prices first. Nothing
   else is comparable until it is empty.
3. Expect some legitimate differences:
   - taxes and credits;
   - a key on a free tier — the invoice is $0 while this page shows a cost;
   - charges Cyclone does not model: Gemini's cache *storage* (per hour),
     grounding with Search or Maps, and long-prompt tiers that charge more past
     a certain size;
   - **another application sharing the same API key**;
   - records lost while a process was stopping (below).
4. For one specific call, Cyclone stores the vendor's own reference for the
   response — which is what vendor billing support can look up. Ask a developer
   for it; it is one query.

---

## Known limits — worth knowing before somebody finds them

- **Records can be lost when a service stops.** Anything queued gets five
  seconds to finish writing. That is a deliberate trade: a shutdown must not
  hang waiting on a database.
- **Each server records its own.** If one stops recording it says so only in its
  own log, and nothing yet compares the two.
- **The ledger cannot be edited.** Corrections happen in the price table, which
  is why the price table carries the audit trail.
- **A new model means new missing prices.** Whenever the models Cyclone uses are
  changed, expect rows here the next day — and *"Priced so far"* until they are
  filled in.

---

[← Where leads go](14-lead-routing.md) · [Index](README.md)
