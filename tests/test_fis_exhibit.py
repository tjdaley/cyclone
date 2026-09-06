"""
tests/test_fis_exhibit.py - The statement as a document.

The FIS is a form, and its indentation is not decoration: "Airfare" under
"Travel" under "Entertainment" is what the line means. This suite guards that
the hierarchy survives into all three exhibit formats, that a form prints no
column headings while the CSV still does, and that everything which would make
a figure wrong travels with it instead of staying on the screen that produced it.

Run:  venv/Scripts/python.exe tests/test_fis_exhibit.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.matter import ClientAlignment  # noqa: E402
from services.exhibit_service import (  # noqa: E402
    Column, Exhibit, Row, to_csv, to_docx, to_markdown, to_pdf,
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


# -- Row keeps behaving like the tuple it replaced ----------------------------

print("\nA Row is still a row")

row = Row(cells=("Airfare/Busfare", "-1504.98"), depth=2)
check("indexes", row[0], "Airfare/Busfare")
check("measures", len(row), 2)
check("iterates", list(row), ["Airfare/Busfare", "-1504.98"])
check("and carries its depth", row.depth, 2)

# Bare tuples still work, so no flat report has to know Row exists.
plain = Exhibit(name="x", columns=(Column("A"),), rows=(("one",), ("two",)))
check("bare tuples are normalised at construction",
      all(isinstance(r, Row) for r in plain.rows), True)

# Assigning after construction skips __post_init__, which is why the renderers
# normalise as well.
plain.rows = (("three",),)
check("and assignment after the fact still renders",
      to_csv(plain).decode("utf-8-sig").strip().split("\r\n")[1], "three")


# -- The form -----------------------------------------------------------------

class FakeMatter:
    id = 1
    matter_number = "416-56988-2024"
    court_name = "416th Judicial District Court"
    county = "Collin"
    state = "Texas"
    matter_name = "Doe divorce"
    case_style = "In the Matter of the Marriage of John Doe and Jane Doe"
    client_alignment = ClientAlignment.petitioner


def statement_exhibit() -> Exhibit:
    """A miniature FIS: headings, two levels of nesting, and a ruled net."""
    from services.exhibit_service import caption_lines

    caption, _ = caption_lines(FakeMatter(), "Financial Information Statement")
    return Exhibit(
        name="Financial Information Statement",
        caption=caption,
        columns=(Column("Category"), Column("Monthly", numeric=True, money=True)),
        rows=(
            Row(("Income", ""), depth=0, heading=True),
            Row(("Salary & Wages (W-2)", "13595.25"), depth=1),
            Row(("Housing", ""), depth=0, heading=True),
            Row(("Property Taxes (paid annually)", "-563.96"), depth=1),
            Row(("Utilities", ""), depth=1, heading=True),
            Row(("Electricity", "-250.00"), depth=2),
            Row(("NET CASH FLOW PER MONTH", "12781.29"), rule=True),
        ),
        selection=(("Averaged over", "8 whole months"),),
        footnotes=("Statements are missing for 2 of the 8 months in this window.",),
        show_headers=False,
    )


print("\nCSV keeps its headers -- it is data, not a form")

csv_text = to_csv(statement_exhibit()).decode("utf-8-sig")
rows = csv_text.strip().split("\r\n")
check("header row present", rows[0], "Category,Monthly")
check("one row per line plus the header", len(rows), 8)
# Row 0 is the header, 1 is the "Income" heading, 2 the salary line, and 6 the
# twice-nested Electricity.
check("figures stay raw for the spreadsheet", rows[2].endswith(",13595.25"), True)
check("a heading row carries an empty figure", rows[1], "Income,")
check("no indentation leaked into a cell", rows[6].startswith("Electricity"), True)
check("no caption", "Cause No" in csv_text, False)


print("\nMarkdown carries the hierarchy")

md = to_markdown(statement_exhibit()).decode("utf-8")
check_true("headings are bold", "**Income**" in md)
check_true("a nested line is indented with non-breaking spaces",
           "    Electricity" in md)
check_true("one level up is indented once", "  Salary" in md)
check_true("the net is ruled off and bold", "**NET CASH FLOW PER MONTH**" in md)
check_true("and its figure is bold too", "**$12,781.29**" in md)
check_true("money is formatted", "-$563.96" in md)
check_true("the recurrence legend rides on the label", "(paid annually)" in md)
check_true("the coverage warning is a footnote under the table",
           "Statements are missing" in md)

# A form has no column headings, but the separator row must survive or the
# block stops being a table at all.
first_table_line = next(l for l in md.splitlines() if l.startswith("|"))
check("headings are blank", first_table_line, "|  |  |")
check_true("separator still present", "| --- | ---: |" in md)


print("\nDOCX indents and bolds")

import io  # noqa: E402
import zipfile  # noqa: E402

docx_bytes = to_docx(statement_exhibit())
with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
    document = archive.read("word/document.xml").decode("utf-8")
check_true("a table was written", "<w:tbl>" in document)
check_true("indentation is real paragraph indent, not spaces", "w:ind " in document)
check_true("bold runs for headings", "<w:b/>" in document)
check_true("the net row is there", "NET CASH FLOW PER MONTH" in document)
check("no header row was written",
      document.index("Income") < document.index("Salary"), True)
check_true("money formatted", "-$563.96" in document)


print("\nPDF indents and rules")

import pymupdf  # noqa: E402

pdf_bytes = to_pdf(statement_exhibit())
check("pdf magic", pdf_bytes[:5], b"%PDF-")
with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
    text = "\n".join(page.get_text() for page in doc)
    words = doc[0].get_text("words")

check_true("caption on the page", "416-56988-2024" in text)
check_true("net on the page", "NET CASH FLOW PER MONTH" in text)
check_true("footnote on the page", "Statements are missing" in text)
check_true("page numbered", "Page 1 of" in text)


def left_edge(needle: str) -> float:
    """Where a word starts on the page, for checking the indent visually."""
    return min(w[0] for w in words if w[4].startswith(needle))


check_true("a nested line sits right of its parent",
           left_edge("Electricity") > left_edge("Utilities"))
check_true("and its parent right of the top level",
           left_edge("Utilities") > left_edge("Housing"))
check_true("top-level headings share a left edge",
           abs(left_edge("Income") - left_edge("Housing")) < 1.0)


print("\nA flat report is untouched by any of this")

flat = Exhibit(
    name="Transactions",
    columns=(Column("Date"), Column("Amount", numeric=True, money=True)),
    rows=(("2026-01-04", "-2500.00"), ("2026-01-06", "10000.00")),
)
flat_md = to_markdown(flat).decode("utf-8")
check_true("headers printed by default", "| Date | Amount |" in flat_md)
check("no stray indentation", " " in flat_md, False)
check("nothing bolded in the body", flat_md.count("**"), 0)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all FIS exhibit checks passed")
