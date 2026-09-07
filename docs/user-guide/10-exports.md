# 10. Exporting exhibits and data

[← Tagging transactions](09-tagging.md) · [Index](README.md) · Next: [Discovery tracking →](11-discovery-tracking.md)

---

Every report in Cyclone exports the same four ways, from the same control. Once
you know it, you know it everywhere.

## Where the export bar appears

| Report | Where |
| ------ | ----- |
| **Transaction summary** | Transactions tab, below the result count |
| **Financial Information Statement** | FIS tab |
| **FIS detail schedule** | FIS tab, in Detail view |
| **Compliance matrix** | Compliance tab |
| **Accounts referenced but not produced** | Accounts tab, in that panel |

---

## Using it

```text
Export as [ Financial Summary        ]  CSV  MD  DOCX  PDF   412 rows
```

1. **Name the exhibit.** The name titles the document and names the file. Make
   it what you would want a judge to read: *"Petitioner's Summary of
   Undisclosed Transfers"*, not *"export3"*.
2. **Check the row count** beside the buttons, so you do not export an empty or
   half-filtered document.
3. **Click a format.** The file downloads.

---

## Which format to use

| Format | What it is | Use it for |
| ------ | ---------- | ---------- |
| **CSV** | Header row, data rows, **nothing else** | Opening in Excel, further analysis, handing to an expert. Deliberately not an exhibit — no caption, no notice, so there is no preamble to strip. |
| **MD** | Markdown | Pasting into an email, a memo, or another document. |
| **DOCX** | Word | **The one to use when you will edit before filing.** Full exhibit with caption and notice, and Word can search it. |
| **PDF** | Print-ready | Handing over as printed. The one output that takes page breaks seriously. |

**CSV is the odd one out on purpose.** It is data. Everything else is a court
exhibit.

---

## What is in an exhibit (MD, DOCX, PDF)

### 1. The caption

Built from the matter:

```text
                            Cause No: DF-24-01234
              In the 401st District Court of Dallas County, TX
       IN THE MATTER OF THE MARRIAGE OF JANE DOE AND JOHN DOE
                  Petitioner's Financial Summary
```

Fields the matter cannot supply are printed as a blank rule and reported to you
— see below. Set the case style and alignment on the matter detail page
([Chapter 3](03-matters.md)).

### 2. Selection

The criteria that produced the table — the accounts, the date range, the
categories, the tags. **This is what makes the exhibit usable by someone who
did not run the query.** Filters you did not apply are omitted rather than
listed as "none", so it reads as a description and not as a form.

### 3. The table

### 4. Findings

Prose, where the report has any — the compliance matrix puts its gap list here.

### 5. Documents summarized in this exhibit

Filename and Bates range for each **upload** behind the table. Grouped by
upload rather than by statement, because one PDF routinely holds several
statements and listing each separately would send somebody to the same document
five times while looking like five documents.

If a production carried no Bates stamp, it says *"no Bates stamp detected"*
rather than leaving a blank — a reader cannot tell a blank from nobody having
looked.

### 6. The Rule 1006 notice

Every exhibit carries it:

> *This exhibit is a summary prepared from records produced in this case. The
> underlying records must be made available to the other parties, and the party
> offering this summary is responsible for its accuracy. Entries were extracted
> from the produced documents by automated means and may contain errors. Verify
> every date, amount, and description against the original documents before this
> exhibit is shown to anyone or offered in court.*

**Read the last sentence and mean it.** A wrong date inside a statement period
reconciles cleanly and reaches an exhibit unflagged. That is measured, not
hypothetical. The notice is in the file, not just on the screen, because on
screen it is no use once the document has left the building.

---

## Caption warnings

After a download you may see an amber panel:

> **The exhibit downloaded with blanks in its caption:**
> - No cause number is recorded on this matter
> - No case style is recorded on this matter

The file downloaded, with blank rules where those fields go. **Fix them on the
matter detail page and export again before filing.** This warning is the only
thing standing between a blank and a filing.

---

## Things worth knowing

**An export is not the page you are looking at.** The screen shows 200 rows; the
export contains **every** matching line. That is the point — a summary that
stopped at the page size would look complete and be wrong.

**There is a cap of 5,000 rows.** If a query hits it, you are told both on
screen and in the exhibit's own Selection block, so a reader of the finished
document sees it too. Narrow the filters and export in parts.

**Removed lines are excluded** unless **Show removed** is on. When it is on, the
Selection block says so.

**Amounts in a CSV are raw figures, not currency-formatted.** A spreadsheet
reads `-$2,500.00` as text and will not sum it. Currency formatting is for the
exhibit formats.

**Some exports are landscape**, notably the compliance matrix — fourteen columns
in portrait wrap the Bates numbers, and a grid nobody can scan is no grid.

**A ligature quirk in the PDF:** searching the PDF's text for a word containing
"ff", "fi", or "fl" may not find it. The printed page is correct; only the
searchable text layer is affected. Use the DOCX if searchability matters.

---

[← Tagging transactions](09-tagging.md) · [Index](README.md) · Next: [Discovery tracking →](11-discovery-tracking.md)
