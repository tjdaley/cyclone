"""
app/services/exhibit_service.py - Turning query results into a court exhibit.

One document description, four renderings. An ``Exhibit`` says what the document
contains — caption, what was selected, the table, the totals — and each renderer
says how that looks in its own format. Anything in Cyclone that produces a table
worth taking to court builds an ``Exhibit``; none of them learn to write DOCX.

The caption is a **template**, not renderer code. Firms disagree about captions,
and the disagreement is always about wording and order, never about how bold
text is written into a .docx. Keeping the template as a handful of format
strings means the future "let this firm edit its own caption" release changes
data rather than three renderers — see ``_SYSTEM_CAPTION`` below.

CSV is deliberately not an exhibit. It is the clean extraction: header row, data
rows, nothing else, so it can be read by a spreadsheet or handed to a model
without a preamble to strip first. The exhibit formats carry the caption and the
verification notice; the CSV carries the data.
"""
import csv
import html
import io
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from db.models.matter import ClientAlignment
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# A blank on a court document is a rule, not the word "None". Anything the
# matter cannot supply is printed as a fill-in and reported in `warnings`.
_BLANK = "__________"

# Sits immediately above the Rule 1006 notice, which is the point of it: the
# notice says the underlying records are available for examination, and this
# block names them.
_SOURCES_HEADING = "Documents summarized in this exhibit"

# Under Texas Rule of Evidence 1006 a summary may stand in for voluminous
# records only if the originals are available and the summary is accurate — and
# the proponent carries that accuracy, not the tool that drew the table. The
# second sentence is the specific one: extraction is measured, not perfect, and
# a wrong date inside a statement period is exactly the error that reconciles
# cleanly and reaches an exhibit unflagged.
NOTICE = (
    "This exhibit is a summary prepared from records produced in this case. The "
    "underlying records must be made available to the other parties, and the party "
    "offering this summary is responsible for its accuracy. Entries were extracted "
    "from the produced documents by automated means and may contain errors. Verify "
    "every date, amount, and description against the original documents before this "
    "exhibit is shown to anyone or offered in court."
)


# The .docx table face. Aptos is Microsoft's current default — a humanist sans
# that stays legible small, which is what lets twelve months of Bates numbers sit
# at 9pt without crowding.
#
# OOXML HAS NO FONT STACK. A run names exactly one face per script class; there
# is no CSS-style list to fall through. The nearest thing is `w:altName` in
# fontTable.xml, which names what to use when the font is missing — Word honours
# it, LibreOffice reads it, and Google Docs substitutes by its own rules and
# will likely ignore it. Times New Roman is the fallback because it is present
# or auto-substituted everywhere (LibreOffice maps it to Liberation Serif), so
# the worst case is a document that reads plainly rather than one that reflows.
_DOCX_TABLE_FONT = "Aptos"
_DOCX_TABLE_FALLBACK = "Times New Roman"
_DOCX_TABLE_PT = 9


# ── The document description ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Run:
    """A stretch of text with one set of styles. The unit every renderer draws."""
    text: str
    bold: bool = False
    underline: bool = False


@dataclass(frozen=True)
class Line:
    """One line of the caption."""
    runs: tuple[Run, ...]
    align: str = "center"
    blank_after: bool = False


@dataclass(frozen=True)
class Column:
    """
    A table column.

    ``numeric`` right-aligns it. ``money`` additionally formats it as currency
    **in the exhibit formats only** — the CSV keeps the raw value, because a
    spreadsheet given "-$2,500.00" reads it as text and will not add it up.
    """
    heading: str
    numeric: bool = False
    money: bool = False
    center: bool = False
    """
    Centre the column in the exhibit formats. No effect on the CSV.

    For a column of marks rather than values. The compliance grid puts an X in
    a month it holds an unstamped statement for, and a mark hanging off the left
    edge of a wide cell reads as an accident rather than an entry.
    """
    csv_only: bool = False
    """
    Carried in the data file, left out of the printed exhibit.

    For a value the exhibit shows some other way. The compliance matrix names
    its account in a centred title above each grid — repeating it down a column
    wraps the name and steals width from twelve months — but the CSV has no
    titles, and a grid whose rows do not say which account they belong to cannot
    be sorted, filtered, or pivoted. Same data, two presentations.
    """


@dataclass(frozen=True)
class Row:
    """
    One row of a table, with how it sits in a hierarchy.

    Added for the Financial Information Statement, where the indentation **is**
    the form: "Airfare" under "Travel" under "Entertainment" is not decoration,
    it is what the line means. Everything that was a bare tuple of strings still
    works — Row iterates, indexes, and measures as its cells — so a flat report
    never has to know this exists.
    """
    cells: tuple[str, ...]
    #: Indent level. 0 is a top-level heading on the form.
    depth: int = 0
    #: A section heading. Bold, and usually carrying no figure of its own —
    #: though it may, because a transaction can be filed straight to a heading.
    heading: bool = False
    #: A total: ruled off above, and bold.
    rule: bool = False
    #: Draw as one centred cell spanning the table, rather than as columns.
    #:
    #: A title, not a row of data: the compliance matrix puts the account name
    #: above its grid this way. The CSV skips these entirely — it carries the
    #: same fact in a `csv_only` column, where it can be sorted on.
    full_width: bool = False
    #: Start a fresh page here, in the formats that have pages.
    #:
    #: Honoured by the PDF and nowhere else, on purpose. Markdown has no pages,
    #: and a .docx is edited before it is filed — an author who wants two small
    #: grids on one sheet should not have to delete a break somebody guessed at.
    #: The PDF is the one output handed across a counsel table as printed.
    page_break: bool = False

    def __iter__(self):
        return iter(self.cells)

    def __len__(self) -> int:
        return len(self.cells)

    def __getitem__(self, index):
        return self.cells[index]


@dataclass
class Exhibit:
    """
    A table with everything needed to put it in front of a court.

    ``selection`` is what makes this usable by someone — or something — that did
    not run the query: a table of forty transactions means nothing without the
    criteria that produced it. It is also the part a model needs most when the
    markdown is pasted in and asked for a print-ready exhibit.
    """
    name: str
    caption: tuple[Line, ...] = ()
    columns: tuple[Column, ...] = ()
    rows: tuple[Any, ...] = ()
    selection: tuple[tuple[str, str], ...] = ()
    summary: tuple[tuple[str, str], ...] = ()
    #: Marks that qualify individual rows — "† institution inferred". They ride
    #: with the table rather than in `selection`, because a reader meeting a
    #: dagger in a cell looks directly below the table for what it means, and a
    #: mark whose explanation is missing is worse than no mark at all.
    footnotes: tuple[str, ...] = ()
    findings: tuple[tuple[str, str], ...] = ()
    """
    What the table is evidence *of*, when that cannot be a row.

    The compliance matrix is the case this exists for: its grid says which
    months hold a statement, but the thing a motion asks for is the days nothing
    covers — prose, per account, of no fixed width. A row of a fourteen-column
    table is the wrong container for a sentence, and a footnote is the wrong
    weight for the most important content on the page.

    Rendered like `selection` — label, then value — under `findings_title`, and
    placed directly after the table so it reads as the table's conclusion.
    """
    findings_title: str = "Findings"
    sources: tuple[tuple[str, str], ...] = ()
    """
    The documents this exhibit summarizes: ``(filename, Bates range)`` each.

    Rendered immediately before the Rule 1006 notice, and paired with it on
    purpose — the notice says the underlying records are available for
    examination, and this says *which* records, by the name and the stamp
    somebody has to go and pull. A summary that cannot be traced back to the
    documents behind it is the objection the rule exists to answer.

    Absent from the CSV, like the caption and the notice: that file is the data.
    """
    notice: str = NOTICE
    warnings: list[str] = field(default_factory=list)
    landscape: bool = False
    """
    Turn the page sideways, in every format that has one.

    A property of the document, not of the renderer: a seven-column schedule is
    cramped in portrait whether it is a PDF or a .docx, while a two-column
    statement is wrong sideways in both.
    """
    margin_inches: float = 1.0
    """
    Page margin for the PDF, on all four sides.

    A property of the document for the same reason ``landscape`` is: an exhibit
    that does not fit is not fixed by the renderer trying harder. One inch is
    right for a caption-led table of transactions and wrong for a grid of twelve
    months — measured, the compliance matrix needs 648pt and one-inch margins on
    landscape letter leave exactly 648pt, so December falls off the edge by a
    hair.

    Half an inch is the smallest value worth using. It buys a whole extra
    column's width, and it stays inside the non-printable border of the copiers
    and scanners these documents pass through — a quarter inch buys another 36pt
    and risks losing the edge of the page on any of them.
    """
    show_headers: bool = True
    """
    Whether the exhibit formats print a header row.

    A Financial Information Statement has none — it is a form, and its columns
    are self-evident. The CSV prints them regardless: that file is data, and
    data without a header row is a puzzle.
    """

    def __post_init__(self) -> None:
        # Callers may pass plain tuples. Normalising here means every renderer
        # sees one shape and no report has to opt in to the hierarchy it does
        # not use.
        self.rows = tuple(
            row if isinstance(row, Row) else Row(tuple(row)) for row in self.rows
        )

    @property
    def filename_stem(self) -> str:
        """A safe filename built from the exhibit's own name."""
        stem = re.sub(r"[^A-Za-z0-9]+", "_", self.name).strip("_")
        return stem or "exhibit"


# ── The caption template ─────────────────────────────────────────────────────

# The firm-wide caption. Each entry is (template, blank_after).
#
# `**bold**` and `__underline__` mark styles; `{name}` interpolates from the
# context built by `_caption_context`. A line whose text resolves to nothing is
# dropped, which is how a matter with no case style on file still produces a
# usable heading rather than a stranded blank line.
#
# FUTURE: per-firm and per-user overrides. The override belongs in a table keyed
# the way matter_preferences will be — a NULL user id meaning "the firm's
# template" and a row meaning "this user's" — and it stores exactly this: a list
# of template strings. Nothing below this constant needs to change to allow it;
# `caption_lines` already takes the template as an argument for that reason.
_SYSTEM_CAPTION: tuple[tuple[str, bool], ...] = (
    ("**Cause No: __{cause_number}__**", False),
    ("**In the {court_name} of {county} County, {state}**", False),
    ("{case_style}", True),
    ("**{alignment_possessive}{exhibit_name}**", True),
)

_MARKUP = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def _parse_runs(text: str) -> tuple[Run, ...]:
    """
    Split a template line into styled runs.

    Bold and underline do not nest here: a caption needs "**Cause No: __x__**"
    to come out with the whole line bold and only the number underlined, so a
    bold span is re-scanned for underline spans inside it.
    """
    runs: list[Run] = []

    def emit(chunk: str, bold: bool) -> None:
        if not chunk:
            return
        if bold:
            position = 0
            for match in re.finditer(r"__(.+?)__", chunk):
                if match.start() > position:
                    runs.append(Run(chunk[position:match.start()], bold=True))
                runs.append(Run(match.group(1), bold=True, underline=True))
                position = match.end()
            if position < len(chunk):
                runs.append(Run(chunk[position:], bold=True))
        else:
            runs.append(Run(chunk))

    position = 0
    for match in _MARKUP.finditer(text):
        if match.start() > position:
            emit(text[position:match.start()], False)
        if match.group(1) is not None:
            emit(match.group(1), True)
        else:
            runs.append(Run(match.group(2), underline=True))
        position = match.end()
    if position < len(text):
        emit(text[position:], False)
    return tuple(runs)


def _caption_context(matter: Any, exhibit_name: str) -> tuple[dict[str, str], list[str]]:
    """
    Resolve the template's placeholders against a matter.

    Every value a court document needs and this matter does not have becomes a
    printed blank and a warning. Refusing to build the exhibit would be worse —
    the attorney often wants the numbers long before a cause number exists — and
    printing "None" onto a caption would be worse still.
    """
    warnings: list[str] = []

    def required(value: Optional[str], label: str) -> str:
        if value and value.strip():
            return value.strip()
        warnings.append("No %s on this matter — the exhibit shows a blank." % label)
        return _BLANK

    cause_number = required(getattr(matter, "matter_number", None), "cause number")
    court_name = required(getattr(matter, "court_name", None), "court name")

    case_style = (getattr(matter, "case_style", None) or "").strip()
    if not case_style:
        # The internal short name at least identifies the case, which an empty
        # line does not. Flagged, because it is not caption language.
        case_style = (getattr(matter, "matter_name", None) or "").strip()
        warnings.append(
            "No case style on this matter — using the matter name, which is not "
            "how a caption should read. Set the case style on the matter."
        )

    alignment = getattr(matter, "client_alignment", None)
    if alignment is None:
        # The exhibit is still titled, just not attributed to a side.
        alignment_possessive = ""
        warnings.append(
            "No party alignment on this matter — the exhibit is titled without one. "
            "Set it to title this \"Petitioner's %s\"." % exhibit_name
        )
    else:
        if not isinstance(alignment, ClientAlignment):
            alignment = ClientAlignment(alignment)
        alignment_possessive = "%s's " % alignment.caption

    return {
        "cause_number": cause_number,
        "court_name": court_name,
        "county": (getattr(matter, "county", None) or _BLANK).strip(),
        "state": (getattr(matter, "state", None) or "Texas").strip(),
        "case_style": case_style,
        "alignment_possessive": alignment_possessive,
        "exhibit_name": exhibit_name,
    }, warnings


def caption_lines(
    matter: Any,
    exhibit_name: str,
    template: tuple[tuple[str, bool], ...] = _SYSTEM_CAPTION,
) -> tuple[tuple[Line, ...], list[str]]:
    """
    Build the caption for a matter.

    :param template: The caption to use. Defaults to the firm-wide one; the
        parameter is what a future per-firm override will pass instead.
    :return: ``(lines, warnings)`` — warnings name what the matter could not
        supply, so the UI can say so before anybody prints it.
    :rtype: tuple[tuple[Line, ...], list[str]]
    """
    context, warnings = _caption_context(matter, exhibit_name)
    lines: list[Line] = []
    for text, blank_after in template:
        # Parse the markup FIRST, then substitute into each run. Interpolating
        # before parsing would let a value be read as markup: the blank rule is
        # a row of underscores and would be eaten as an __underline__ marker,
        # and a case style containing ** would corrupt the rest of the caption.
        # A value is content; only the template carries style.
        try:
            runs = tuple(
                Run(run.text.format(**context), bold=run.bold, underline=run.underline)
                for run in _parse_runs(text)
            )
        except KeyError as e:
            LOGGER.error("exhibit_service: caption template references unknown field %s", str(e))
            continue
        if not any(run.text.strip() for run in runs):
            continue
        lines.append(Line(runs=runs, blank_after=blank_after))
    return tuple(lines), warnings


def _as_rows(rows: tuple[Any, ...]) -> tuple[Row, ...]:
    """
    Every row as a Row, whatever the caller handed over.

    ``__post_init__`` covers construction, but assigning ``exhibit.rows`` after
    the fact is reasonable and skips it. Normalising where the rows are read
    means the invariant holds however they arrived.
    """
    return tuple(row if isinstance(row, Row) else Row(tuple(row)) for row in rows)


def _plain(line: Line) -> str:
    return "".join(run.text for run in line.runs)


def money(value: Any) -> str:
    """
    Format an amount as currency without ever parsing it as a float.

    The value arrives as a string precisely so exact cents survive Postgres
    ``numeric``; running it through ``float`` to add thousands separators would
    undo that at the last step, in the one place where the figure is about to be
    read into evidence. Grouping is done on the integer part as digits, so the
    fractional part is never arithmetic at all.

    A negative reads ``-$1,200.00`` rather than the accounting parenthesis: a
    minus sign needs no convention explained to whoever is reading the exhibit.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    negative = text.startswith("-")
    text = text.lstrip("+-")
    whole, _, fraction = text.partition(".")
    if not whole.isdigit():
        # Not a number after all — hand it back untouched rather than mangling it.
        return str(value)
    grouped = "{:,}".format(int(whole))
    cents = (fraction + "00")[:2]
    return "%s$%s.%s" % ("-" if negative else "", grouped, cents)


def _visible(exhibit: Exhibit) -> list[tuple[int, Column]]:
    """
    The columns the printed exhibit draws, with their index into a row's cells.

    The index is what matters: a row always carries every cell, including those
    only the CSV wants, so a renderer that zipped cells against visible columns
    would silently shift every value one place left.
    """
    return [(index, column) for index, column in enumerate(exhibit.columns)
            if not column.csv_only]


def _cell(value: str, column: Column) -> str:
    """One table cell, formatted for an exhibit rather than for a spreadsheet."""
    return money(value) if column.money else (value or "")


# ── Renderers ────────────────────────────────────────────────────────────────

def to_csv(exhibit: Exhibit) -> bytes:
    """
    The clean extraction: header row, data rows, nothing else.

    No caption and no notice, on purpose. This file is meant to be opened in a
    spreadsheet or handed to a model, and a preamble above the header turns a
    valid CSV into something every reader has to be told how to skip. The
    exhibit formats are where the caption and the verification notice live.

    Written with a UTF-8 BOM: Excel reads a plain UTF-8 CSV as the system
    codepage and mangles anything non-ASCII in a payee name.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([column.heading for column in exhibit.columns])
    # Cells only. Depth is presentation, and leading whitespace in a CSV cell is
    # something every reader then has to strip.
    #
    # A full-width row is skipped outright: it is a title the printed exhibit
    # draws above a grid, and this file carries the same fact in a column, where
    # it can be sorted on. Emitting both would put a heading in the middle of
    # the data and break every filter applied to it.
    writer.writerows(row.cells for row in _as_rows(exhibit.rows) if not row.full_width)
    return buffer.getvalue().encode("utf-8-sig")


def to_markdown(exhibit: Exhibit) -> bytes:
    """
    The exhibit as markdown — the format meant to be pasted into a model.

    Centring is not expressed. Markdown has no way to say it that survives being
    read as text, and this rendering is optimised for a reader that will lay the
    document out itself: what it needs is the caption's content and the criteria
    that produced the table, not its typography.
    """
    out: list[str] = []
    for line in exhibit.caption:
        out.append(_md_line(line))
        out.append("")

    # The table leads. Selection follows it rather than preceding it: the
    # exhibit's substance is the evidence, and how it was selected is the
    # methodology note a reader turns to afterwards.
    if exhibit.columns:
        # The separator row is required even when the headings are blank:
        # without it the block is not a table, just lines with pipes in them.
        headings = [c.heading if exhibit.show_headers else ""
                    for _, c in _visible(exhibit)]
        out.append("| " + " | ".join(headings) + " |")
        out.append("| " + " | ".join(
            "---:" if c.numeric else ":---:" if c.center else "---"
            for _, c in _visible(exhibit)) + " |")
        for row in _as_rows(exhibit.rows):
            visible = _visible(exhibit)
            if row.full_width:
                # Markdown tables cannot span columns. The title takes the first
                # cell with the rest blank — as close as the format gets, and it
                # still reads as a heading above the grid.
                out.append("| " + " | ".join(
                    ["**%s**" % _md_cell(row.cells[0] if row.cells else "")]
                    + [""] * (len(visible) - 1)) + " |")
                continue
            cells = [
                _md_cell(_cell(row.cells[index] if index < len(row.cells) else "", column))
                for index, column in visible
            ]
            if cells:
                # Non-breaking spaces: a markdown table parser strips ordinary
                # leading whitespace, and here the indentation is the form.
                cells[0] = ("  " * row.depth) + cells[0]
                if row.heading or row.rule:
                    cells[0] = "**%s**" % cells[0]
                    if len(cells) > 1 and cells[-1].strip():
                        cells[-1] = "**%s**" % cells[-1]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    if exhibit.findings:
        out.append("## %s" % exhibit.findings_title)
        out.append("")
        for label, value in exhibit.findings:
            out.append("- **%s:** %s" % (label, value))
        out.append("")

    if exhibit.footnotes:
        for note in exhibit.footnotes:
            out.append("*%s*" % note)
            out.append("")

    if exhibit.summary:
        out.append("## Totals")
        out.append("")
        for label, value in exhibit.summary:
            out.append("- **%s:** %s" % (label, value))
        out.append("")

    if exhibit.selection:
        out.append("## Selection")
        out.append("")
        for label, value in exhibit.selection:
            out.append("- **%s:** %s" % (label, value))
        out.append("")

    if exhibit.sources:
        out.append("## %s" % _SOURCES_HEADING)
        out.append("")
        for label, value in exhibit.sources:
            out.append("- **%s:** %s" % (label, value))
        out.append("")

    out.append("---")
    out.append("")
    out.append("*%s*" % exhibit.notice)
    out.append("")
    return "\n".join(out).encode("utf-8")


def _md_line(line: Line) -> str:
    """
    One caption line as markdown.

    Adjacent runs sharing a style are merged before the markers go on. Emitting
    per run would close and reopen emphasis mid-phrase — ``**Cause No: ****DF-24
    -01234**`` — which renders as literal asterisks. Underline has no markdown
    spelling and is dropped; bold already carries the emphasis, and the point of
    this rendering is the content, not the typography.
    """
    parts: list[str] = []
    buffer: list[str] = []
    bold: Optional[bool] = None

    def flush() -> None:
        if buffer:
            joined = "".join(buffer)
            parts.append("**%s**" % joined if bold else joined)

    for run in line.runs:
        if bold is not None and run.bold != bold:
            flush()
            buffer.clear()
        bold = run.bold
        buffer.append(run.text)
    flush()
    return "".join(parts)


def _md_cell(value: str) -> str:
    """A pipe inside a description would end the cell early."""
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _docx_page_number(paragraph: Any) -> None:
    """
    Put a live "Page N of M" in a paragraph.

    python-docx has no page-number API because a page number is not text — it is
    a field Word evaluates at layout time, when it finally knows where the pages
    fell. So the field is written as raw OOXML: begin, the instruction, end.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def field(instruction: str) -> None:
        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = instruction
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        run._r.append(begin)
        run._r.append(instr)
        run._r.append(end)

    paragraph.add_run("Page ")
    field(" PAGE ")
    paragraph.add_run(" of ")
    field(" NUMPAGES ")


def _docx_repeat_header(table: Any) -> None:
    """
    Mark the first row as a header Word repeats on every page.

    A table that runs to a second page without its headings makes the reader
    count columns to find out which one holds the amount. `w:tblHeader` is the
    flag; python-docx does not surface it.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    properties.append(repeat)


def _docx_face(run: Any, bold: bool = False) -> None:
    """
    Put one run in the table face.

    Set on all four script classes, not just `run.font.name`. python-docx writes
    only `w:ascii` and `w:hAnsi`; a cell containing a character Word classes as
    complex-script or East Asian then renders in the document default, and a
    single stray glyph in a different face is the kind of thing nobody sees
    until it is printed.
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = _DOCX_TABLE_FONT
    run.font.size = Pt(_DOCX_TABLE_PT)
    run.bold = bold
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attribute), _DOCX_TABLE_FONT)


def _docx_declare_font(document: Any) -> None:
    """
    Tell a reader without the table face what to use instead.

    OOXML has no font stack. `w:altName` in fontTable.xml is the whole of the
    fallback mechanism: Word uses it when the named font is missing and
    LibreOffice reads it too. Google Docs substitutes by its own rules and will
    probably ignore it, which is why the fallback is a face nobody lacks.

    Written by editing the part directly — python-docx models styles and not the
    font table — and any failure is swallowed. A missing declaration costs a
    substitution somebody else picks; a raised exception would cost the export.
    """
    try:
        part = next(p for p in document.part.package.iter_parts()
                    if "fontTable" in str(p.partname))
        text = part.blob.decode("utf-8")
        if _DOCX_TABLE_FONT in text or "</w:fonts>" not in text:
            return
        declaration = (
            '<w:font w:name="%s">'
            '<w:altName w:val="%s"/>'
            '<w:charset w:val="00"/>'
            '<w:family w:val="swiss"/>'
            '<w:pitch w:val="variable"/>'
            "</w:font>" % (_DOCX_TABLE_FONT, _DOCX_TABLE_FALLBACK)
        )
        part._blob = text.replace("</w:fonts>", declaration + "</w:fonts>").encode("utf-8")
    except Exception as e:  # noqa: BLE001 — a font hint must never cost the document
        LOGGER.warning("exhibit_service: could not declare the table font: %s", str(e))


def _docx_light_borders(table: Any) -> None:
    """
    Horizontal hairlines only.

    The Table Grid style boxes every cell, which makes an exhibit read as a
    spreadsheet and competes with the figures for attention. A printed financial
    table separates its rows and leaves the columns to alignment.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge, style, size, colour in (
        ("top", "none", 0, "auto"),
        ("left", "none", 0, "auto"),
        ("bottom", "none", 0, "auto"),
        ("right", "none", 0, "auto"),
        ("insideH", "single", 2, "D8D8D8"),   # 2 eighths of a point
        ("insideV", "none", 0, "auto"),
    ):
        element = OxmlElement("w:%s" % edge)
        element.set(qn("w:val"), style)
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:color"), colour)
        borders.append(element)
    properties.append(borders)


def _docx_align(paragraph: Any, column: "Column") -> None:
    """Set a paragraph to its column's alignment, header cell or data cell.

    Word leaves a paragraph left-aligned unless told otherwise, so a column
    whose numbers are right-aligned and whose heading is not reads as two
    columns overlapping. One function, called from both places, is what keeps
    them from drifting apart again.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    if column.numeric:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif column.center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def to_docx(exhibit: Exhibit) -> bytes:
    """The exhibit as a Word document, caption centred and table ruled."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    document = Document()

    if exhibit.landscape:
        # Word needs both: the flag and the swapped dimensions. Setting the
        # orientation alone leaves a portrait-shaped page labelled landscape.
        from docx.enum.section import WD_ORIENT

        section = document.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

    # Left and right only. Width is what a wide grid runs out of; the top and
    # bottom stay at Word's inch, where a filing expects them and nothing is
    # under pressure.
    if exhibit.margin_inches != 1.0:
        margins = document.sections[0]
        margins.left_margin = Inches(exhibit.margin_inches)
        margins.right_margin = Inches(exhibit.margin_inches)

    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _docx_page_number(footer)
    for run in footer.runs:
        run.font.size = Pt(9)

    for line in exhibit.caption:
        paragraph = document.add_paragraph()
        paragraph.alignment = (
            WD_ALIGN_PARAGRAPH.CENTER if line.align == "center" else WD_ALIGN_PARAGRAPH.LEFT
        )
        for run in line.runs:
            drawn = paragraph.add_run(run.text)
            drawn.bold = run.bold
            drawn.underline = run.underline
        if line.blank_after:
            document.add_paragraph()

    if exhibit.columns:
        visible = _visible(exhibit)

        def start_table():
            """A fresh grid, with its own repeating header."""
            fresh = document.add_table(rows=1 if exhibit.show_headers else 0,
                                       cols=len(visible))
            fresh.style = "Table Grid"
            _docx_light_borders(fresh)
            if exhibit.show_headers:
                for cell, (_, column) in zip(fresh.rows[0].cells, visible):
                    cell.text = ""
                    _docx_face(cell.paragraphs[0].add_run(column.heading), bold=True)
                    # THE HEADING FOLLOWS ITS COLUMN. A centred X sitting under a
                    # left-hugging "Feb" reads as belonging to the month before
                    # it, which on a grid whose whole content is position is the
                    # one mistake it cannot afford. Markdown and the PDF have
                    # always aligned the two together; only Word did not.
                    _docx_align(cell.paragraphs[0], column)
                _docx_repeat_header(fresh)
            return fresh

        # A FULL-WIDTH ROW ENDS ONE TABLE AND BEGINS ANOTHER, rather than
        # spanning inside a single one. Word repeats a header row across a
        # *natural* page break and not across a manual one, so an author who
        # splits a long table where they want it loses the month names for every
        # page after — which is the whole reason to break it there. One table
        # per account gives them the break for free, and each grid keeps its own
        # repeating header if it is tall enough to need it.
        _docx_declare_font(document)
        drawn_rows = _as_rows(exhibit.rows)
        opens_with_title = bool(drawn_rows) and drawn_rows[0].full_width
        table = None if opens_with_title else start_table()

        for row in drawn_rows:
            if row.full_width:
                if table is not None:
                    document.add_paragraph()
                heading = document.add_paragraph()
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                heading.add_run(row.cells[0] if row.cells else "").bold = True
                table = None
                continue
            if table is None:
                table = start_table()
            cells = table.add_row().cells
            for position, (cell, (index, column)) in enumerate(zip(cells, visible)):
                cell.text = ""
                paragraph = cell.paragraphs[0]
                value = row.cells[index] if index < len(row.cells) else ""
                _docx_face(paragraph.add_run(_cell(value, column)),
                           bold=row.heading or row.rule)
                if position == 0 and row.depth:
                    paragraph.paragraph_format.left_indent = Inches(0.2 * row.depth)
                _docx_align(paragraph, column)
        document.add_paragraph()

    if exhibit.findings:
        heading = document.add_paragraph()
        heading.add_run(exhibit.findings_title).bold = True
        for label, value in exhibit.findings:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run("%s: " % label).bold = True
            paragraph.add_run(value)
        document.add_paragraph()

    for note in exhibit.footnotes:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(note)
        run.italic = True
        run.font.size = Pt(8)

    if exhibit.summary:
        heading = document.add_paragraph()
        heading.add_run("Totals").bold = True
        for label, value in exhibit.summary:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run("%s: " % label).bold = True
            paragraph.add_run(value)
        document.add_paragraph()

    if exhibit.selection:
        heading = document.add_paragraph()
        heading.add_run("Selection").bold = True
        for label, value in exhibit.selection:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run("%s: " % label).bold = True
            paragraph.add_run(value)
        document.add_paragraph()

    if exhibit.sources:
        heading = document.add_paragraph()
        heading.add_run(_SOURCES_HEADING).bold = True
        for label, value in exhibit.sources:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run("%s: " % label).bold = True
            paragraph.add_run(value)
        document.add_paragraph()

    notice = document.add_paragraph()
    notice_run = notice.add_run(exhibit.notice)
    notice_run.italic = True
    notice_run.font.size = Pt(8)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_PDF_STYLE = ("<style>"
              "body{font-family:serif;font-size:10pt}"
              "table{width:100%;border-collapse:collapse}"
              "th,td{border:0;border-bottom:0.5px solid #d8d8d8;padding:3.5px 4px;"
              "font-size:8.5pt;text-align:left}"
              "th{border-bottom:1px solid #999;font-weight:bold}"
              "td.c,th.c{text-align:center}"
              "td.t{text-align:center;font-weight:bold;font-size:9.5pt;"
              "padding-top:7px;border-bottom:1px solid #999}"
              "td.n,th.n{text-align:right}"
              "td.r{border-top:1px solid #666;border-bottom:0}"
              "p{margin:2pt 0}"
              ".c{text-align:center}"
              ".notice{font-size:7.5pt;font-style:italic;color:#333}"
              "</style>")


def _esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def _pdf_caption(exhibit: Exhibit) -> str:
    parts: list[str] = []
    for line in exhibit.caption:
        inner = "".join(
            "%s%s%s%s%s" % (
                "<b>" if run.bold else "", "<u>" if run.underline else "",
                _esc(run.text),
                "</u>" if run.underline else "", "</b>" if run.bold else "",
            )
            for run in line.runs
        )
        parts.append('<p class="c">%s</p>' % inner)
        if line.blank_after:
            parts.append("<p>&#160;</p>")
    return "".join(parts)


def _pdf_list(title: str, entries: tuple[tuple[str, str], ...]) -> str:
    if not entries:
        return ""
    parts = ["<p>&#160;</p><p><b>%s</b></p>" % _esc(title)]
    parts += ["<p>&#8226; <b>%s:</b> %s</p>" % (_esc(label), _esc(value))
              for label, value in entries]
    return "".join(parts)


def _pdf_header_row(exhibit: Exhibit) -> str:
    # A form has no column headings; its columns are self-evident. Returning an
    # empty string leaves each per-page chunk table headerless too.
    if not exhibit.show_headers:
        return ""
    return "<tr>%s</tr>" % "".join(
        '<th class="%s">%s</th>' % ("n" if c.numeric else "c" if c.center else "",
                                    _esc(c.heading))
        for _, c in _visible(exhibit)
    )


def _pdf_rows(exhibit: Exhibit) -> list[tuple[str, bool, bool]]:
    """
    The table rows as ``(markup, starts_a_page, is_a_title)``.

    The flags travel with the markup because by the time the paging loop sees a
    row it is a bare string: the loop still needs to know where it may not cut,
    and which rows are titles that belong above a header rather than below it.
    """
    rows: list[tuple[str, bool, bool]] = []
    for row in _as_rows(exhibit.rows):
        visible = _visible(exhibit)
        if row.full_width:
            # One centred cell spanning the grid: the account's name as a title
            # over its own table, rather than a column repeated down every year
            # row where it wraps and steals width from twelve months.
            rows.append(('<tr><td class="t" colspan="%d">%s</td></tr>'
                         % (len(visible), _esc(row.cells[0] if row.cells else "")),
                         row.page_break, True))
            continue
        drawn = []
        for position, (index, column) in enumerate(visible):
            value = row.cells[index] if index < len(row.cells) else ""
            text = _esc(_cell(value, column))
            if row.heading or row.rule:
                text = "<b>%s</b>" % text
            classes = "n" if column.numeric else "c" if column.center else ""
            if row.rule:
                classes = (classes + " r").strip()
            style = (' style="padding-left:%dpt"' % (4 + row.depth * 12)
                     if position == 0 and row.depth else "")
            drawn.append('<td class="%s"%s>%s</td>' % (classes, style, text))
        rows.append(("<tr>%s</tr>" % "".join(drawn), row.page_break, False))
    return rows


def to_pdf(exhibit: Exhibit) -> bytes:
    """
    The exhibit as a PDF, laid out by PyMuPDF's Story engine.

    PyMuPDF is already a dependency — it is what reads the statements in the
    first place — so this adds no new one. WeasyPrint would render richer CSS
    but needs native Pango and Cairo in the image, a real cost for a caption and
    a ruled table.

    **Story does not repeat a table header across pages.** ``<thead>`` is a
    paged-media idea and the engine has no page model — handed 120 rows it emits
    four pages and pages two through four begin mid-data, leaving the reader to
    count columns to find the amount. So the table is not one story: rows are
    measured a page at a time and each page gets its own small table carrying
    its own header. ``place()`` reports whether the content fitted, which is the
    only measurement needed — try a batch, shrink until it fits, then grow while
    it still does. The fit is exact, not conservative: the 120-row case still
    lands in four pages.
    """
    import pymupdf

    page_rect = pymupdf.paper_rect("letter-l" if exhibit.landscape else "letter")
    # The bottom is pulled up a further 18pt to leave the footer its band.
    margin = max(18.0, exhibit.margin_inches * 72)
    content = page_rect + (margin, margin, -margin, -(margin + 18))
    header = _pdf_header_row(exhibit)
    rows = _pdf_rows(exhibit)

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    state: dict[str, Any] = {"device": None, "top": content.y0, "pages": 0}

    def begin() -> None:
        state["device"] = writer.begin_page(page_rect)
        state["top"] = content.y0
        state["pages"] += 1

    def remaining() -> Any:
        return pymupdf.Rect(content.x0, state["top"], content.x1, content.y1)

    def flow(markup: str) -> None:
        """Place free-flowing content, continuing onto new pages as needed."""
        if not markup:
            return
        story = pymupdf.Story(html=_PDF_STYLE + markup)
        while True:
            if remaining().height < 24:
                writer.end_page()
                begin()
            more, filled = story.place(remaining())
            story.draw(state["device"])
            state["top"] = bottom(filled) + 6
            if not more:
                return
            writer.end_page()
            begin()

    def bottom(filled: Any) -> float:
        """place() reports the filled area as a plain (x0, y0, x1, y1) tuple."""
        return float(filled[3])

    def fits(batch: list[tuple[str, bool, bool]], rect: Any):
        # A TITLE LEADING A CHUNK GOES ABOVE THE HEADER, NOT BELOW IT. The
        # header is re-emitted on every page, so a title left in row order sits
        # under the month names — the account it names reads as though it were
        # part of the data rather than the caption of the grid.
        lead, body = "", batch
        if batch and batch[0][2]:
            lead, body = batch[0][0], batch[1:]
        markup = "<table>" + lead + header + "".join(m for m, _, _ in body) + "</table>"
        story = pymupdf.Story(html=_PDF_STYLE + markup)
        more, filled = story.place(rect)
        return (not more), story, filled

    begin()
    flow(_pdf_caption(exhibit))

    pending = list(rows)
    guess = 40
    while pending:
        # A table needs room for its header plus a row before it is worth
        # starting; otherwise take the next page.
        if remaining().height < 60:
            writer.end_page()
            begin()

        # THE BATCH MAY NOT CROSS A PAGE BREAK. A row that asks to start a page
        # caps how far this one can run, so the fit search below never proposes
        # a batch spanning two grids — a compliance matrix is read one account
        # at a time across a counsel table, and an account split over a fold is
        # the one thing the grid must not do.
        limit = len(pending)
        for index in range(1, len(pending)):
            if pending[index][1]:
                limit = index
                break

        count = min(limit, guess)
        ok, story, filled = fits(pending[:count], remaining())
        while not ok and count > 1:
            count = max(1, int(count * 0.85))
            ok, story, filled = fits(pending[:count], remaining())
        while count < limit:
            grown_ok, grown, grown_filled = fits(pending[:count + 1], remaining())
            if not grown_ok:
                break
            count += 1
            story, filled = grown, grown_filled

        if not ok and count == 1:
            # One row will not fit even on an empty page — a description long
            # enough to overflow. Draw it anyway rather than looping forever.
            LOGGER.warning("exhibit_service.to_pdf: a single row did not fit a page")

        story.place(remaining())
        story.draw(state["device"])
        state["top"] = bottom(filled) + 6
        pending = pending[count:]
        guess = max(5, count)

        # The next grid begins. Give it the page it asked for.
        if pending and pending[0][1]:
            writer.end_page()
            begin()

    # Findings before the footnotes: they are the table's conclusion, and the
    # footnotes only explain the marks inside it.
    flow(_pdf_list(exhibit.findings_title, exhibit.findings))
    for note in exhibit.footnotes:
        flow('<p class="notice">%s</p>' % _esc(note))
    flow(_pdf_list("Totals", exhibit.summary))
    flow(_pdf_list("Selection", exhibit.selection))
    flow(_pdf_list(_SOURCES_HEADING, exhibit.sources))
    flow('<p>&#160;</p><p class="notice">%s</p>' % _esc(exhibit.notice))
    writer.end_page()
    writer.close()

    return _stamp_page_numbers(buffer.getvalue(), margin)


def _stamp_page_numbers(pdf_bytes: bytes, margin: float = 72.0) -> bytes:
    """
    Write "Page N of M" centred in the footer band of every page.

    Done after layout because M is not known until then — the same reason Word
    stores a NUMPAGES field rather than a number.

    The baseline follows the margin rather than sitting at a fixed height: the
    footer band is the 18pt strip below the content, and at half-inch margins a
    number hard-coded 50pt from the foot lands back inside the table.
    """
    import pymupdf

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = document.page_count
        for index, page in enumerate(document, start=1):
            label = "Page %d of %d" % (index, total)
            width = pymupdf.get_text_length(label, fontname="helv", fontsize=9)
            page.insert_text(
                ((page.rect.width - width) / 2, page.rect.height - margin - 5),
                label, fontname="helv", fontsize=9, color=(0.2, 0.2, 0.2),
            )
        return document.tobytes()
    finally:
        document.close()


RENDERERS = {
    "csv": (to_csv, "text/csv", "csv"),
    "md": (to_markdown, "text/markdown; charset=utf-8", "md"),
    "docx": (to_docx,
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": (to_pdf, "application/pdf", "pdf"),
}


def render(exhibit: Exhibit, fmt: str) -> tuple[bytes, str, str]:
    """
    Render an exhibit.

    :return: ``(content, media_type, filename)``
    :rtype: tuple[bytes, str, str]
    """
    try:
        renderer, media_type, extension = RENDERERS[fmt]
    except KeyError:
        raise ValueError("Unknown export format: %s" % fmt) from None
    content = renderer(exhibit)
    LOGGER.info("exhibit_service.render: format=%s rows=%d bytes=%d",
                fmt, len(exhibit.rows), len(content))
    return content, media_type, "%s.%s" % (exhibit.filename_stem, extension)
