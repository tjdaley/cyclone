"""
tests/test_exhibit_service.py - The caption template and the four renderings.

Two things are worth guarding here. The first is that a matter missing part of
its caption still produces a usable document: an attorney wants the numbers long
before a cause number exists, and the failure mode to prevent is not "no export"
but the word "None" printed onto something filed with a court.

The second is that the four formats stay honest about what each is for. The CSV
is a clean extraction and must carry no preamble; the three exhibit formats must
all carry the verification notice, because a summary separated from that notice
is the one that gets relied on without checking.

Run:  venv/Scripts/python.exe tests/test_exhibit_service.py
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.matter import ClientAlignment  # noqa: E402
from services.exhibit_service import (  # noqa: E402
    NOTICE, Column, Exhibit, caption_lines, render, to_csv, to_docx, to_markdown, to_pdf,
)

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# MuPDF's built-in fonts substitute ligature glyphs, so the PDF's text layer
# holds "oﬀered" where the page reads "offered". The document looks right; only
# extraction and Ctrl-F see the difference. Tests that search PDF text normalise
# it rather than pretending the substitution is not happening.
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
              "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}


def deligature(text: str) -> str:
    for glyph, plain_text in _LIGATURES.items():
        text = text.replace(glyph, plain_text)
    return text


class FakeMatter:
    def __init__(self, **kwargs):
        self.id = 1
        self.matter_number = "DF-24-01234"
        self.court_name = "401st Judicial District Court"
        self.county = "Parker"
        self.state = "Texas"
        self.matter_name = "Salmons divorce"
        self.case_style = "IN THE MATTER OF THE MARRIAGE OF GABRIEL SALMONS AND ANA SALMONS"
        self.client_alignment = ClientAlignment.petitioner
        for k, v in kwargs.items():
            setattr(self, k, v)


def plain(lines) -> list[str]:
    return ["".join(run.text for run in line.runs) for line in lines]


def build(matter=None, name="Financial Summary") -> Exhibit:
    caption, warnings = caption_lines(matter or FakeMatter(), name)
    return Exhibit(
        name=name,
        caption=caption,
        columns=(Column("Date"), Column("Description"),
                 Column("Amount", numeric=True, money=True)),
        rows=(("2023-03-04", "Transfer to XXX4070", "-2500.00"),
              ("2023-03-06", "Deposit | payroll", "10000.00")),
        selection=(("Accounts", "First Financial Bank x9260"),),
        summary=(("Transactions", "2"), ("Net", "7500.00")),
        warnings=warnings,
    )


# ── The caption ──────────────────────────────────────────────────────────────

print("\nA complete matter")

lines, warnings = caption_lines(FakeMatter(), "Financial Summary")
check("no warnings", warnings, [])
check("four lines", plain(lines), [
    "Cause No: DF-24-01234",
    "In the 401st Judicial District Court of Parker County, Texas",
    "IN THE MATTER OF THE MARRIAGE OF GABRIEL SALMONS AND ANA SALMONS",
    "Petitioner's Financial Summary",
])
check("every caption line is centred", {line.align for line in lines}, {"center"})

# The style markup: whole line bold, only the cause number underlined.
cause = lines[0]
check("cause line runs", [(r.text, r.bold, r.underline) for r in cause.runs],
      [("Cause No: ", True, False), ("DF-24-01234", True, True)])
check("case style is not bold", [r.bold for r in lines[2].runs], [False])
check("exhibit title is bold, not underlined",
      [(r.bold, r.underline) for r in lines[3].runs], [(True, False)])
check("blank line after the case style", lines[2].blank_after, True)
check("blank line after the title", lines[3].blank_after, True)

print("\nAlignment vocabulary")

for alignment, expected in (
    (ClientAlignment.respondent, "Respondent's Financial Summary"),
    (ClientAlignment.counter_petitioner, "Counter-Petitioner's Financial Summary"),
    (ClientAlignment.intervenor, "Intervenor's Financial Summary"),
    (ClientAlignment.other, "Party's Financial Summary"),
):
    lines, _ = caption_lines(FakeMatter(client_alignment=alignment), "Financial Summary")
    check(alignment.value, plain(lines)[3], expected)

print("\nA matter that cannot fill in its own caption")

lines, warnings = caption_lines(
    FakeMatter(matter_number=None, court_name=None, case_style=None, client_alignment=None),
    "Financial Summary",
)
text = plain(lines)
check("cause number is a blank, never the word None", text[0], "Cause No: __________")
check("court is a blank", text[1], "In the __________ of Parker County, Texas")
check("falls back to the matter name for the style", text[2], "Salmons divorce")
check("title survives with no alignment", text[3], "Financial Summary")
check("no 'None' anywhere in the caption", any("None" in line for line in text), False)
check("four warnings raised", len(warnings), 4)
check_true("warnings name the cause number", any("cause number" in w for w in warnings))
check_true("warnings name the case style", any("case style" in w for w in warnings))
check_true("warnings name the alignment", any("alignment" in w for w in warnings))

print("\nA blank case style with no matter name is dropped, not left stranded")
lines, _ = caption_lines(
    FakeMatter(case_style=None, matter_name=""), "Financial Summary")
check("three lines, style omitted", len(lines), 3)
check("no empty line printed", all(plain([line])[0].strip() for line in lines), True)


# ── CSV: the clean extraction ────────────────────────────────────────────────

print("\nCSV is a clean extraction")

csv_bytes = to_csv(build())
body = csv_bytes.decode("utf-8-sig")
rows = body.strip().split("\r\n")
check("header row is the first row", rows[0], "Date,Description,Amount")
check("row count is header plus data", len(rows), 3)

# The CSV keeps raw figures on purpose: a spreadsheet handed "-$2,500.00"
# treats it as text and will not sum the column.
check("amounts stay raw, not currency-formatted", rows[1].endswith(",-2500.00"), True)
check("no dollar signs anywhere", "$" in body, False)
check("no caption in the file", "Cause No" in body, False)
check("no notice in the file", "Rule" in body or NOTICE[:30] in body, False)
check("BOM for Excel", csv_bytes[:3], b"\xef\xbb\xbf")
check_true("a comma inside a value is quoted",
           '"' in to_csv(Exhibit(name="x", columns=(Column("A"),), rows=(("a,b",),))).decode("utf-8-sig"))


# ── Markdown ─────────────────────────────────────────────────────────────────

print("\nMarkdown carries the context a reader needs")

md = to_markdown(build()).decode("utf-8")
check_true("caption present", "**Cause No: DF-24-01234**" in md)
check_true("title present", "**Petitioner's Financial Summary**" in md)
check_true("selection block present", "## Selection" in md)
check_true("selection content present", "**Accounts:** First Financial Bank x9260" in md)
check_true("table header", "| Date | Description | Amount |" in md)
check_true("amount column right-aligned", "---:" in md)
check_true("totals block", "## Totals" in md)
check_true("notice present", NOTICE[:40] in md)
check_true("a pipe inside a description is escaped", "Deposit \\| payroll" in md)

# The evidence leads; how it was selected is the methodology note that follows.
check("table comes before Totals", md.index("| Date |") < md.index("## Totals"), True)
check("Totals comes before Selection", md.index("## Totals") < md.index("## Selection"), True)
check("Selection comes before the notice",
      md.index("## Selection") < md.index(NOTICE[:40]), True)

print("\nAmounts read as currency in an exhibit")
check_true("negative", "-$2,500.00" in md)
check_true("positive with a thousands separator", "$10,000.00" in md)
check("the raw value is gone", "-2500.00" in md, False)


# ── DOCX ─────────────────────────────────────────────────────────────────────

print("\nDOCX is a real Word document")

docx_bytes = to_docx(build())
check("zip magic", docx_bytes[:2], b"PK")
with zipfile.ZipFile(__import__("io").BytesIO(docx_bytes)) as archive:
    document = archive.read("word/document.xml").decode("utf-8")
check_true("caption text present", "DF-24-01234" in document)
check_true("centred paragraphs", 'w:val="center"' in document)
check_true("bold runs", "<w:b/>" in document or 'w:b w:val="1"' in document)
check_true("underline on the cause number", "<w:u " in document)
check_true("a table was written", "<w:tbl>" in document)
check_true("notice present", NOTICE[:40].replace("&", "&amp;") in document)
check_true("amounts are currency", "-$2,500.00" in document)
check_true("the header row repeats on every page", "<w:tblHeader" in document)
check("Totals precedes Selection",
      document.index("Totals") < document.index("Selection"), True)

with zipfile.ZipFile(__import__("io").BytesIO(docx_bytes)) as archive:
    names = archive.namelist()
    footer_part = next((n for n in names if n.startswith("word/footer")), None)
check_true("a footer part exists", footer_part)
with zipfile.ZipFile(__import__("io").BytesIO(docx_bytes)) as archive:
    footer = archive.read(footer_part).decode("utf-8")
check_true("the footer carries a live PAGE field", "PAGE" in footer)
check_true("and a total-pages field", "NUMPAGES" in footer)
check_true("centred", 'w:val="center"' in footer)


# ── PDF ──────────────────────────────────────────────────────────────────────

print("\nPDF renders through PyMuPDF")

import pymupdf  # noqa: E402

pdf_bytes = to_pdf(build())
check("pdf magic", pdf_bytes[:5], b"%PDF-")
check_true("non-trivial document", len(pdf_bytes) > 1000)

with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
    text = "\n".join(page.get_text() for page in document)
check_true("caption made it onto the page", "DF-24-01234" in text)
check_true("case style on the page", "GABRIEL SALMONS" in text)
check_true("table content on the page", "Transfer to XXX4070" in text)
check_true("notice on the page", "verify every date" in text.lower() or "Verify every" in text)

check_true("amounts are currency", "-$2,500.00" in text)
check_true("page number stamped", "Page 1 of 1" in text)

print("\nA PDF that runs to several pages")

# PyMuPDF's Story does not repeat a <thead>: handed this many rows it emits
# several pages and every one after the first begins mid-data. The renderer
# therefore measures a page at a time and gives each its own header.
long_exhibit = build()
long_exhibit.rows = tuple(
    ("2024-11-%02d" % (i % 28 + 1), "Payment to vendor number %d" % i, "-1200.00")
    for i in range(160)
)
with pymupdf.open(stream=to_pdf(long_exhibit), filetype="pdf") as document:
    pages = [page.get_text() for page in document]

check_true("more than one page", len(pages) > 1)

# Only pages that actually carry rows need the header. The last page holds the
# totals, the selection, and the notice, and has no table on it at all.
with_rows = [i for i, page in enumerate(pages) if "Payment to vendor number" in page]
check_true("rows span several pages", len(with_rows) > 1)
check("every page carrying rows repeats the table header",
      [i + 1 for i in with_rows if "Description" not in pages[i]], [])

missing_footer = [
    i + 1 for i, page in enumerate(pages)
    if "Page %d of %d" % (i + 1, len(pages)) not in page
]
check("every page is numbered", missing_footer, [])
check("the caption appears once, on the first page",
      sum("Cause No" in page for page in pages), 1)
check("the notice appears once, at the end",
      sum("in court." in deligature(page) for page in pages), 1)
check("no row was lost across the page breaks",
      sum(page.count("Payment to vendor number") for page in pages), 160)

print("\nHTML special characters in a description survive the PDF")
exhibit = build()
exhibit.rows = (("2023-03-04", "PMT <AT&T> & CO", "-50.00"),)
with pymupdf.open(stream=to_pdf(exhibit), filetype="pdf") as document:
    text = "\n".join(page.get_text() for page in document)
check_true("ampersand and angle brackets are escaped, not dropped", "AT&T" in text)


# ── Dispatch ─────────────────────────────────────────────────────────────────

print("\nrender() dispatch")

for fmt, media, extension in (
    ("csv", "text/csv", "csv"),
    ("md", "text/markdown; charset=utf-8", "md"),
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    ("pdf", "application/pdf", "pdf"),
):
    content, media_type, filename = render(build(), fmt)
    check("%s media type" % fmt, media_type, media)
    check("%s filename" % fmt, filename, "Financial_Summary.%s" % extension)
    check_true("%s produced bytes" % fmt, len(content) > 0)

try:
    render(build(), "xlsx")
    check("an unknown format is refused", "no error", "ValueError")
except ValueError as e:
    check_true("an unknown format is refused", "xlsx" in str(e))

print("\nFilenames")
check("a name with punctuation is made safe",
      Exhibit(name="Gabe's Accounts (2023)").filename_stem, "Gabe_s_Accounts_2023")
check("an empty name still yields a file", Exhibit(name="").filename_stem, "exhibit")

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all exhibit checks passed")
