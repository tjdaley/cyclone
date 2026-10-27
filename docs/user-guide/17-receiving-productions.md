# 17. Receiving and splitting a production

[← Creating a matter](03-matters.md) · [Index](README.md) · Next: [Importing statements →](04-importing-statements.md)

---

A production does not arrive as statements. It arrives as whatever opposing
counsel, the client, or a subpoena witness happened to send: one enormous PDF,
a stack of scans, three years of statements in a single file, a bag of
everything.

This chapter is what happens before [Chapter 4](04-importing-statements.md).
If somebody hands you tidy monthly statement PDFs, skip it and drop them on
**Financials → Import**.

---

## Getting there

**Matters** → click the matter → **Received documents →** (top right).

---

## What this page is for

Every file is kept **exactly as it arrived**. It is never OCR'd, never split,
never re-saved. Working copies are made *from* it and the original stays
untouched.

That matters more than it sounds. If anybody ever says *"that is not what we
produced"*, the answer is a file nobody has edited, with a checksum taken the
moment it arrived. A photograph's capture time and GPS, a PDF's internal
metadata, the exact bytes served on us — all of it is the sort of thing that
turns out to matter long after the day it was received.

**Files are identified by their contents, not their names.** Production
filenames are unreliable to the point of uselessness, and the same documents are
routinely re-served under new ones. So if you upload a file this matter already
holds, nothing is stored twice and nothing is uploaded at all — you are told
immediately.

---

## Uploading

Drop PDFs on the page, or click to browse. Drop as many as you like.

The upload goes **straight from your browser to storage**, not through Cyclone,
which is why a large production does not time out. You will see it move through
three stages per file:

| Stage | |
| ----- | - |
| **Reading** | Computing the file's fingerprint, so a duplicate costs no upload at all |
| **Uploading** | The bytes going to storage |
| **Recording** | Writing it down |

> ### ⛔ There is a size limit, and it is not ours alone
>
> Files over the limit are refused **before anything is sent**, so you are told
> in an instant rather than after waiting out an upload.
>
> **Split a file that is too large at a boundary you can see** — one year per
> file, or one account per file. **Do not split by size.** A split made by size
> lands in the middle of a document, and the two halves then sit in separate
> uploads where nothing can put them back together.
>
> The message tells you how many files to split it into, and names which limit
> refused it. If it says the **remote file storage** refused it, that is a
> separate cap set outside Cyclone and an administrator can raise it.

---

## Reading, and what the verdict means

A file is read in the background once it is stored. While that happens the list
shows **reading…** and the split link reads **not ready**. Nothing is waiting on
your browser; you can close the page.

When it finishes you get a verdict with the numbers behind it. **Read the
numbers, not just the verdict** — they are what tells you when the routing is
wrong.

| | |
| - | - |
| **pages** | How long the file is |
| **readable text** | The share of pages Cyclone can read without OCR |
| **self-paginating** | The share of pages inside a complete "Page 1 of 4" run |
| **documents at least** | How many documents paginate themselves. A lower bound: a receipt prints no pagination and is not counted |

Three verdicts:

| Verdict | What it means | What to do |
| ------- | ------------- | ---------- |
| **automatic** | The file describes its own structure well enough to split mechanically | Split it on the bench for now; the automatic split is not built yet |
| **bench** | Too long, or it does not say which document each page belongs to | Split it by hand — see below |
| **ocr_first** | The pages are images with no readable text | It needs a text layer before anything can read it. That step is not built yet, so OCR it elsewhere and upload the result, or split it on the bench where the thumbnails are all you need |

**Why "self-paginating" is the number that decides it.** A run of bank
statements prints "Page 1 of 4" and the three pages that follow agree. A stack
of receipts, letters and handwritten notes cannot. Measured on two real
productions: one at 74%, which splits cleanly on its own; one at 8%, which has
no good automatic answer at all — **and both had a perfectly good text layer.**
Text quality is not the test.

---

## The splitting bench

Click **split** to open it. The bench does one job: you say which pages group,
and each group becomes a document.

**It does not name anything, classify anything, or read anything.** That is
deliberate. Naming is what makes splitting in Acrobat slow, and it is only
necessary there because the filename is the only place the information can
live. Here a document is a record with its pages, its Bates range and its type,
and its description comes from reading it later. So the files are called things
like `20261020-1021-001.pdf` and that is fine.

### How it works

1. **Click pages that belong together.** They highlight. Click again to release.
2. **Non-adjacent pages are ordinary.** A document interrupted mid-way by
   unrelated pages and resumed later is one selection, not two.
3. **Press DONE.** Those pages become a document and disappear from the grid.
4. **Repeat** until the grid is empty.
5. **Press Finish.**

The grid emptying *is* the progress bar. Keyboard works too: arrows move, space
toggles, Enter is DONE, Ctrl+Z unbundles the last one.

### What the badges under each thumbnail mean

They are **facts printed on the page**, never a guess about where a document
starts:

| Badge | |
| ----- | - |
| `Page 1 of 4` | The pagination the page prints |
| A date | A date printed in the page's header |
| `···9260` | A number the header labels as an account |
| **blank** | Nothing on the page |
| **no readable text** | Something on the page, but not readable text |

Nothing is pre-selected, on purpose. A file reaches the bench precisely because
the automatic signals are weak on it, so a machine guess would be something you
have to undo rather than something that helps.

### Documents split from this file

Every document you have made is listed under the grid with its page ranges, and
the list survives closing the page. Each row offers:

- **unbundle** — puts those pages back on the grid.
- **read as statement** — see below.

### Things the bench will not let you do

| | |
| - | - |
| **Finish with pages still selected** | Finish is not DONE. It refuses and says so, because it would otherwise sweep your selection into the leftovers |
| **Unbundle a document a statement was read from** | That would orphan the statement. Reject the statement in Financials first |

> ### ⚠ Showing pages already bundled
>
> Bundled pages leave the grid. The **Show pages already bundled** checkbox
> brings them back, and it is there for one real case: a combined statement
> where a single sheet carries the close of one account and the opening of the
> next. That page genuinely belongs to two documents.
>
> If you select pages that are already in a document, you are warned before
> bundling — because doing it by accident makes a duplicate.

### Finishing

**Finish** gathers whatever is left into one document, so no produced page is
lost. It tells you how many it swept, and asks first.

If those leftover pages are all blank, the document is labelled
**blank or separator**. If they are not, it carries no label and waits for
review — because calling a stack of real pages "separators" would be a false
statement about them.

**Leaving without finishing is fine.** Press **Leave for now** and the file is
kept, unsplit, in the Received documents list. It was served on us whether or
not anybody has got to it yet.

---

## Reading a split document as a statement

Once a document is a bank, card, brokerage, policy or annuity statement, press
**read as statement** on its row.

That sends it to the same extraction described in
[Chapter 4](04-importing-statements.md), and the result appears in
**Financials → Accounts** — or in the exceptions queue on **Financials →
Import** if it did not clear on its own
([Chapter 5](05-import-problems.md)).

The bundle row then shows what the statement *is* — the institution, the
account and the period — and links to it.

### Three possible results

| | |
| - | - |
| **One statement** | Linked to the document. Done |
| **Several statements** | Linked to none of them, and the document says so. A combined package is one document with several accounts in it, and picking one of them would make every report cite that one account's period for all of them |
| **No statement found** | The document is flagged and comes back for review. Either it is not a statement, or nothing on it could be read |

---

## What is not built yet

Worth knowing so you are not waiting for it:

- **The automatic split.** A file whose verdict is *automatic* still has to be
  split on the bench.
- **Adding a text layer.** A file whose verdict is *ocr_first* has to be OCR'd
  outside Cyclone, or split on the bench by eye.
- **Classification.** Nothing reads a document and decides what kind it is, so
  **read as statement** is a choice you make.

---

[← Creating a matter](03-matters.md) · [Index](README.md) · Next: [Importing statements →](04-importing-statements.md)
