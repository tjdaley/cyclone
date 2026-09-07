# 9. Tagging transactions, and creating custom tags

[← Categorizing, and the FIS](08-categorizing-and-fis.md) · [Index](README.md) · Next: [Exporting exhibits and data →](10-exports.md)

---

A tag groups transactions into an exhibit. One line can carry several — that is
exactly what separates a tag from a category, of which a line has one.

Where a category answers *"what kind of spending is this?"*, a tag answers
*"what am I going to argue with this?"*

Typical tags:

- `Waste: Sister's Wedding`
- `Undisclosed transfers`
- `Post-separation spending`
- `Business expenses run through personal`

The same $4,200 payment can be Entertainment (its category), and carry both
`Waste: Sister's Wedding` and `Post-separation spending`.

---

## Two layers of tags

Both live in the same list, and the difference matters when you are tagging.

| | **Matter tag** | **Firm-wide tag** |
| - | -------------- | ----------------- |
| Scope | This case only | Every matter in the firm |
| States | A theory about *this* case | Vocabulary the whole firm shares |
| Who can edit it | You, from inside the matter | Not from inside a matter |
| Shown as | Plain label | Prefixed with **★** in the tag filter |

Renaming a firm-wide tag would silently rewrite the vocabulary of every other
case in the firm, so firm-wide tags are **shown** in the tag manager but not
editable there. That is a firm decision, not a case decision.

---

## Applying tags

1. **Financials** → **Transactions**.
2. Find the lines. Filter by description, date, account, check number, category —
   whatever gets you to the set.
3. Tick the lines. The checkbox in the header row selects every line on the page
   (200 at a time).
4. In the blue bar: **Add tag…** and choose one.

**Remove tag…** in the same bar takes a tag off the selected lines.

**Clear selection** deselects everything. Note that changing a filter or moving
to the next page also clears the selection — so apply the tag before you
navigate.

---

## Finding tagged lines again

In the **Tags** row of the filters, each tag is a chip. Click to filter by it.
Chips show a usage count, so you can see at a glance how much is filed under
each.

Special chips in the same row:

| Chip | What it does |
| ---- | ------------ |
| **Untagged** | Only lines carrying no tag at all |
| **Checks only** | Only lines that are checks |
| **Show removed** | Includes lines somebody has removed (red when active) |

When you have selected more than one tag, a **Must carry all of them** tick box
appears. Off, it finds lines with *any* of the chosen tags; on, only lines
carrying *every* one.

---

## Creating a custom tag

1. **Financials** → **Transactions**.
2. Click **Manage tags** at the top right of the filter panel.
3. Under **Add a tag to this matter**:

   | Field | Notes |
   | ----- | ----- |
   | **Label** | e.g. `Waste: Sister's Wedding` |
   | **Description** | What this tag means, for whoever tags next |
   | **Colour** | red, amber, blue, purple, green, gray |

4. Click **Add tag**.

> **Write the description.** Six months on, "Waste" means nothing without one —
> and the person tagging next may not be you. This is the single most-skipped
> field in the application and the one most worth filling in.

Colour is presentation only, but it is stored on the tag, so the same claim
reads the same colour everywhere it appears. Use it consistently.

---

## Editing and retiring a tag

In the tag manager, each of **this matter's tags** shows its label,
description, and how many lines carry it, with **Edit** and **Delete**.

**Edit** opens in place — the list does not close underneath you, because tags
get corrected mid-way through classifying a production and losing your place is
maddening. You can change the label, description, colour, and whether the tag
is **active**.

**Retiring** a tag (unticking active) keeps it on the lines that carry it but
takes it out of circulation for new tagging. Prefer this to deleting when a tag
has been used — the classification stays intact and the exhibit still works.

**Delete** removes the tag entirely.

---

## How tags become exhibits

Tags drive Rule 1006 summaries. The workflow is:

1. Tag the lines that make the point.
2. On the **Transactions** tab, filter to that tag — and nothing else, or the
   exhibit is a subset of what you tagged.
3. Check the matching count above the results.
4. Name the exhibit and export it — [Chapter 10](10-exports.md).

The exhibit states the filters that produced it, so a reader who did not run the
query can see exactly what the table is a summary *of*.

---

[← Categorizing, and the FIS](08-categorizing-and-fis.md) · [Index](README.md) · Next: [Exporting exhibits and data →](10-exports.md)
