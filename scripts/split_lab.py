"""Page-boundary lab for scanned document piles.

A stack of unrelated documents scanned as one PDF has to be cut back into
documents before anything else in the pipeline can run. This measures how much
of that cutting is FREE -- available from what the pages themselves print --
before any model is asked anything.

It answers four questions, in this order:

  1. Does this pile have a usable text layer at all?  (features)
     If it does not, splitting cannot be measured or done until OCR has run,
     and step 3 of the pipeline has to precede step 2 for this input.

  2. Where do the documents actually start?            (worksheet / truth)
     Marked by hand. Nothing else can produce this, and every later number is
     scored against it.

  3. How many of those boundaries does a printed signal already find?  (score)
     The misses are the boundaries a model would have to judge. That count,
     not the total page count, is the recurring per-production cost.

  4. Cut the pile into documents.                      (split)
     So the pieces can be used to test classification.

NO MODEL IS CALLED AND NOTHING LEAVES THIS MACHINE. The model pass belongs in a
second command, added once there is a baseline to compare it against.

Why this reads PyMuPDF directly, against CLAUDE.md's rule
---------------------------------------------------------
`pdf_service` is the only module that may import pymupdf, so that extraction is
sanitized and falls back to vision consistently. That fallback is precisely what
must not happen here: the measurement IS what the text layer contains, and a
service that silently replaces an unreadable page with an OCR call would both
hide the finding and send client pages to a vendor. This is a scripts/ tool, it
imports nothing from app/, and it is not on any code path that serves a request.

Output files hold client document text. Write them outside the repository.

Usage
-----
    venv/Scripts/python.exe scripts/split_lab.py features  PILE.pdf --out feat.csv
    venv/Scripts/python.exe scripts/split_lab.py worksheet PILE.pdf --out truth.csv
    venv/Scripts/python.exe scripts/split_lab.py truth     PILE.pdf --pages 1,5,9,14 --out truth.csv
    venv/Scripts/python.exe scripts/split_lab.py score     PILE.pdf --truth truth.csv
    venv/Scripts/python.exe scripts/split_lab.py split     PILE.pdf --truth truth.csv --outdir OUT
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover - environment problem, not a code path
    print("pymupdf is not installed. Use the venv interpreter:")
    print(r"  venv\Scripts\python.exe scripts\split_lab.py ...")
    raise SystemExit(2)


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

# The word boundary is the whole pattern: PAGE\s1 also matches inside "Page 10",
# "Page 19" and "Page 142", so one long document reads as a dozen first pages.
# Same bug, same fix, as statement_service._PAGE_ONE.
_PAGE_ONE = re.compile(r"page\s+1(?!\d)", re.I)
_PAGE_OF = re.compile(r"page\s+(\d{1,3})\s+of\s+(\d{1,3})", re.I)
_BATES = re.compile(r"\b([A-Z]{1,8})[\s_\-]?(\d{4,8})\b")

# Pagination prints in a header or a footer, never mid-paragraph. Searching only
# the ends of the page keeps body prose from reading as pagination.
_EDGE_CHARS = 500

# A token that is a word, a number, a date, or money. OCR noise is not.
_WORDLIKE = re.compile(
    r"^[A-Za-z]{2,}$"            # a word
    r"|^[A-Za-z]$"               # an initial
    r"|^\$?[\d,]+(\.\d+)?$"      # 1,234.56  $99
    r"|^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$"  # a date
    r"|^\d{1,4}$"
)

# Below this share of word-like tokens, a page has text but not language.
# The ratio below -- non-ASCII characters -- is blind to OCR noise, because
# "hankohanerk.:a,Lorri/ATI'41" is entirely ordinary ASCII. Measured on a real
# pile: that rule reported 0 garbled pages on a file with three unusable ones.
_MIN_WORDLIKE = 0.60

_GOOD = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " \t\r\n.,;:!?'\"()[]{}/\\-_&@#$%*+=<>|~`^"
)

# A page with less text than this is treated as carrying no usable text layer.
# Deliberately the same order of magnitude as pdf_service's own floor; tune it
# against a real pile rather than reasoning about it.
_MIN_TEXT_CHARS = 100

# Below this, a page is a separator sheet or a blank back-of-page.
_BLANKISH_CHARS = 20


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------


@dataclass
class PageFeatures:
    page: int  # 1-based, as a human and physical_page_number count
    chars: int
    garbage_ratio: float
    wordlike: float
    header_date: str | None
    accounts: frozenset[str]
    has_text: bool
    blankish: bool
    page_one: bool
    page_of: tuple[int, int] | None
    top: str
    top_tokens: frozenset[str]
    shape: tuple[int, int, int]
    images: int
    bates: tuple[str, int] | None
    snippet: str
    text: str = field(repr=False, default="")


def _garbage_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch not in _GOOD)
    return round(bad / len(text), 4)


def _wordlike_ratio(text: str) -> float:
    """Share of tokens that are language, not OCR debris.

    This is the garbage measure that works. `_garbage_ratio` is kept because a
    broken encoding and a bad scan are different faults with different fixes,
    and only one of them is repaired by OCR'ing the page again.
    """
    tokens = [t.strip(".,;:!?()[]{}\"'") for t in text.split()]
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return 0.0
    return round(sum(1 for t in tokens if _WORDLIKE.match(t)) / len(tokens), 4)


_MONTHS = ("january february march april may june july august september "
           "october november december").split()
_DATE_WORD = re.compile(
    r"\b(%s)\w*\.?\s+(\d{1,2})\s*,?\s+(\d{4})\b" % "|".join(_MONTHS), re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b")


def _header_date(text: str) -> str | None:
    """The first date printed in the page's header, normalised.

    Only the header: a transaction date in the body changes every line, and the
    claim being made is about the document's own date, which is constant through
    a statement and different in the next one.
    """
    # 200, not 300: on a short page -- a message insert, a closing summary --
    # 300 characters reaches the body, and a date in the body is a transaction
    # date. Measured: it read 3 November off an "Important Messages" page and
    # reported two boundaries around it, both wrong.
    head = text[:200]
    m = _DATE_WORD.search(head)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(3)),
                                   _MONTHS.index(m.group(1).lower()) + 1,
                                   int(m.group(2)))
    m = _DATE_NUM.search(head)
    if m:
        year = int(m.group(3))
        year += 2000 if year < 100 and year < 50 else 1900 if year < 100 else 0
        return "%04d-%02d-%02d" % (year, int(m.group(1)), int(m.group(2)))
    return None


def _top_fingerprint(text: str) -> str:
    """The page's masthead, with everything variable removed.

    Two consecutive pages of one document usually print the same header; a new
    document prints a different one. Digits go because a page number and a date
    change on every page of the same document.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = " ".join(lines[:3])
    head = re.sub(r"[^A-Za-z ]+", " ", head)
    return re.sub(r"\s+", " ", head).strip().upper()[:60]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _snippet(text: str, width: int = 90) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:width]


def _bates(text: str) -> tuple[str, int] | None:
    edge = text[-_EDGE_CHARS:] + "\n" + text[:_EDGE_CHARS]
    best: tuple[str, int] | None = None
    for prefix, digits in _BATES.findall(edge):
        best = (prefix, int(digits))
    return best


def read_features(path: Path) -> list[PageFeatures]:
    out: list[PageFeatures] = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            text = page.get_text() or ""
            edges = text[:_EDGE_CHARS] + "\n" + text[-_EDGE_CHARS:]
            match = _PAGE_OF.search(edges)
            rect = page.rect
            out.append(
                PageFeatures(
                    page=index,
                    chars=len(text.strip()),
                    garbage_ratio=_garbage_ratio(text),
                    wordlike=_wordlike_ratio(text),
                    header_date=_header_date(text),
                    accounts=_header_accounts(text),
                    has_text=len(text.strip()) >= _MIN_TEXT_CHARS,
                    blankish=len(text.strip()) < _BLANKISH_CHARS,
                    page_one=bool(_PAGE_ONE.search(edges)),
                    page_of=(int(match.group(1)), int(match.group(2))) if match else None,
                    top=_top_fingerprint(text),
                    top_tokens=frozenset(_top_fingerprint(text).split()),
                    shape=(round(rect.width), round(rect.height), page.rotation),
                    images=len(page.get_images(full=False)),
                    bates=_bates(text),
                    snippet=_snippet(text),
                    text=text,
                )
            )
    return out


# --------------------------------------------------------------------------
# Signals -- each returns the pages it says begin a document
# --------------------------------------------------------------------------


def sig_page_of_reset(f: list[PageFeatures]) -> set[int]:
    """"Page 1 of N" in a header or footer."""
    return {p.page for p in f if p.page_of and p.page_of[0] == 1}


def sig_page_one(f: list[PageFeatures]) -> set[int]:
    """A bare "page 1", word-bounded."""
    return {p.page for p in f if p.page_one}


# Below this token overlap, two consecutive mastheads are different documents.
# Exact inequality was the first rule and it was useless: on a real 84-page pile
# it fired on 82 of 83 candidate pages, because the first lines of a dense page
# carry content that changes every page. A constant is not a signal.
_HEADER_SAME = 0.34


def sig_header_change(f: list[PageFeatures]) -> set[int]:
    """The masthead is mostly different words from the page before.

    The generalization of statement_service._statement_pages: cut where the
    document's own printing changes. Measured by token overlap, because "not
    byte-identical" is true of almost every page of almost every document.
    """
    hits = set()
    for prev, cur in zip(f, f[1:]):
        if cur.top_tokens and prev.top_tokens:
            if _jaccard(cur.top_tokens, prev.top_tokens) < _HEADER_SAME:
                hits.add(cur.page)
    return hits


def continuation_pages(f: list[PageFeatures]) -> set[int]:
    """Pages whose masthead repeats the page before: negative evidence.

    Not scored as a boundary signal -- it is the opposite claim. Reported so a
    noisy positive signal can be vetoed by it.
    """
    return {
        cur.page
        for prev, cur in zip(f, f[1:])
        if cur.top_tokens and prev.top_tokens
        and _jaccard(cur.top_tokens, prev.top_tokens) >= 0.75
    }


def sig_shape_change(f: list[PageFeatures]) -> set[int]:
    """Page size or rotation differs. A receipt scan after a letter."""
    return {cur.page for prev, cur in zip(f, f[1:]) if cur.shape != prev.shape}


def sig_after_blank(f: list[PageFeatures]) -> set[int]:
    """The previous page was blank -- a separator sheet, or a document's last
    page backed by nothing."""
    return {cur.page for prev, cur in zip(f, f[1:]) if prev.blankish and not cur.blankish}


def sig_text_layer_change(f: list[PageFeatures]) -> set[int]:
    """A page with a text layer following one without, or the reverse. Says the
    two pages were produced by different means, so usually by different
    documents."""
    return {cur.page for prev, cur in zip(f, f[1:]) if cur.has_text != prev.has_text}


def sig_bates_jump(f: list[PageFeatures]) -> set[int]:
    """The Bates number skipped. Evidence about the PRODUCTION, not about a
    document boundary -- scored to show that, since it is the signal people
    reach for first."""
    hits = set()
    for prev, cur in zip(f, f[1:]):
        if prev.bates and cur.bates and prev.bates[0] == cur.bates[0]:
            if cur.bates[1] - prev.bates[1] != 1:
                hits.add(cur.page)
    return hits


# A digit group a statement prints in its header: 4880 5304 6662, or 000123456.
# Four or more digits, so a page number, a year and a dollar amount are out.
_ACCT_GROUP = re.compile(r"\b\d{4,}(?:\s\d{3,})*\b")


# The label, not the position. Every digit group in a header was the first
# rule and it was useless: a statement's cover sheet prints a PO box, a ZIP and
# a mail code, so "this page names no account" was never true and the signal
# fired both entering and leaving every cover page.
_ACCT_LABEL = re.compile(r"account[^0-9A-Za-z]{0,4}(?:no|num|number|#|it|11)?[^0-9]{0,6}", re.I)


def _header_accounts(text: str) -> frozenset[str]:
    """Digit groups the header labels as an account number, spaces removed.

    This is `_statement_pages()`'s method rather than a new idea: cut where the
    document's own printed identity changes. Measured on a real BofA
    production, the header carries the account number on nearly every page, and
    it separates two statements covering the SAME period -- a combined
    production of checking and savings for one month, where the date signal
    cannot fire at all.

    The optional `it` and `11` in the label pattern are not typos: this is OCR
    output, and a hash glyph comes back as either of them on the same document.
    """
    head = text[:300]
    found = set()
    for label in _ACCT_LABEL.finditer(head):
        m = _ACCT_GROUP.search(head, label.end(), label.end() + 40)
        if m:
            digits = m.group(0).replace(" ", "")
            # Six, because "your account ... 2018" put a YEAR in this set and a
            # cover page then disagreed with the statement behind it. A ZIP is
            # five. An account number short enough to be excluded here would be
            # ambiguous with both.
            if len(digits) >= 6:
                found.add(digits)
    return frozenset(found)


def _earliest_possible(f: list[PageFeatures], page: int, speaks) -> int:
    """Back a boundary up over the pages that said nothing.

    A value-change signal can only say the document changed SOMEWHERE between
    the last page that spoke and this one. Reporting it at the page where the
    change was noticed is reliably wrong by the length of the silent run: a
    statement's cover sheet carries no date and no account number, so every
    boundary landed one page late, which welds each cover sheet onto the
    statement before it and strips it from its own.

    The earliest candidate is chosen because a letterhead or cover page at the
    front of the next document is the dominant reason for silence at a
    boundary. When that is wrong it is wrong by one page, in a direction a
    reviewer sees immediately -- a statement that opens with somebody else's
    closing page.
    """
    i = page - 1  # 0-based index of the page that noticed
    while i - 1 >= 0 and not speaks(f[i - 1]):
        i -= 1
    return f[i].page


def sig_acct_change(f: list[PageFeatures]) -> set[int]:
    """The header names an account that is not the last account named.

    Compared against the last page that named one, NOT against the previous
    page. A statement's cover sheet and its message insert name no account, so
    comparing adjacent pages makes every such page break the chain and fire
    twice -- once going in and once coming out. The claim being made is "the
    account changed", and a page that says nothing has not changed it.
    """
    speaks = lambda x: bool(x.accounts)
    hits = set()
    last: frozenset[str] = frozenset()
    for cur in f:
        if not cur.accounts:
            continue
        if last and not (cur.accounts & last):
            hits.add(_earliest_possible(f, cur.page, speaks))
        last = cur.accounts
    return hits


def sig_date_change(f: list[PageFeatures]) -> set[int]:
    """The date printed in the header is not the date on the page before.

    A statement prints one date throughout; the next statement prints another.
    The rationale is independent of any one pile: documents in a scanned stack
    are not in chronological order, so the date is not monotonic across
    boundaries and is constant within a document. The same argument as Bates
    monotonicity, with the direction dropped because a pile can be filed either
    way round.
    """
    speaks = lambda x: x.header_date is not None
    hits = set()
    last = None
    for cur in f:
        if cur.header_date is None:
            continue
        if last is not None and cur.header_date != last:
            hits.add(_earliest_possible(f, cur.page, speaks))
        last = cur.header_date
    return hits


def sig_wordlike_drop(f: list[PageFeatures]) -> set[int]:
    """Language gives way to OCR debris, or the reverse.

    A different kind of document, not a different page of one: a clean
    statement followed by a photographed check. Same family as shape_change.
    """
    return {
        cur.page
        for prev, cur in zip(f, f[1:])
        if abs(cur.wordlike - prev.wordlike) >= 0.25
    }


def sig_short_page(f: list[PageFeatures]) -> set[int]:
    """A page far shorter than the pile's typical page.

    SPECULATIVE. A one-page scrap -- a check, a receipt -- is a document whose
    only distinguishing feature is its brevity. But the last page of a long
    statement is also short, so this may be unusable. It is here to be
    measured, not because it is believed.
    """
    lengths = sorted(x.chars for x in f)
    if not lengths:
        return set()
    median = lengths[len(lengths) // 2]
    if median <= 0:
        return set()
    return {
        cur.page
        for prev, cur in zip(f, f[1:])
        if cur.chars < 0.35 * median <= prev.chars
    }


SIGNALS = {
    "page_of_reset": sig_page_of_reset,
    "page_one": sig_page_one,
    "header_change": sig_header_change,
    "shape_change": sig_shape_change,
    "after_blank": sig_after_blank,
    "text_layer_change": sig_text_layer_change,
    "bates_jump": sig_bates_jump,
    "acct_change": sig_acct_change,
    "date_change": sig_date_change,
    "wordlike_drop": sig_wordlike_drop,
    "short_page": sig_short_page,
}

# Combinations worth reporting as a policy rather than as evidence.
COMBOS = {
    "pagination only": ("page_of_reset", "page_one"),
    "pagination + blank": ("page_of_reset", "page_one", "after_blank"),
    "pagination + date": ("page_of_reset", "page_one", "date_change"),
    "pagination+date+acct": ("page_of_reset", "page_one", "date_change",
                             "acct_change"),
    "pagination+date+kind": ("page_of_reset", "page_one", "date_change",
                             "wordlike_drop", "shape_change"),
    "everything": tuple(SIGNALS),
}


# --------------------------------------------------------------------------
# Truth
# --------------------------------------------------------------------------

_TRUE = {"1", "y", "yes", "x", "t", "true"}


def read_truth(path: Path) -> set[int]:
    starts: set[int] = set()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if (row.get("starts_doc") or "").strip().lower() in _TRUE:
                starts.add(int(row["page"]))
    return starts


def write_truth(path: Path, pages: int, starts: set[int], feats: list[PageFeatures]) -> None:
    """Write the worksheet.

    `hdr_date` and `acct` are shown because they are FACTS read off the page,
    not the detector's opinion about where a document starts. That distinction
    is the whole reason `starts_doc` stays blank: a fact helps somebody mark
    the truth, a guess makes them agree with it.
    """
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["page", "starts_doc", "hdr_date", "acct", "chars",
                         "wordlike", "snippet", "note"])
        for f in feats:
            writer.writerow([
                f.page, "1" if f.page in starts else "",
                f.header_date or "", " ".join(sorted(f.accounts)),
                f.chars, f.wordlike, f.snippet, "",
            ])
    print("Wrote %s (%d pages, %d marked)" % (path, pages, len(starts)))


def parse_pages(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_one(pred: set[int], truth: set[int], pages: int) -> dict:
    """Scored over pages 2..N.

    Page 1 always begins a document, so counting it inflates every signal by a
    free true positive and makes a signal that fires nowhere look useful.
    """
    universe = set(range(2, pages + 1))
    pred = pred & universe
    truth = truth & universe
    tp = len(pred & truth)
    fp = len(pred - truth)
    fn = len(truth - pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "fires": len(pred),
        "fire_rate": len(pred) / max(1, len(universe)),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _row(name: str, s: dict) -> str:
    flag = "  <- fires on %.0f%% of pages" % (100 * s["fire_rate"]) if s["fire_rate"] > 0.5 else ""
    return "  %-22s %6d %5d %5d %5d   %5.0f%% %6.0f%%  %5.2f%s" % (
        name,
        s["fires"],
        s["tp"],
        s["fp"],
        s["fn"],
        100 * s["precision"],
        100 * s["recall"],
        s["f1"],
        flag,
    )


def cmd_score(args: argparse.Namespace) -> int:
    feats = read_features(Path(args.pdf))
    pages = len(feats)
    truth = read_truth(Path(args.truth))
    if not truth:
        print("No pages marked in %s. Mark starts_doc first." % args.truth)
        return 2

    # Reusing one pile's truth against another EXPORT of the same pile is the
    # engine comparison and is the most useful thing this tool does. It is also
    # the easiest way to produce a confident wrong measurement: the same truth
    # against a file whose pages do not correspond scores noise. The page count
    # is the only check available, and it is worth having.
    over = sorted(pp for pp in truth if pp > pages)
    if over:
        print("The truth file marks pages past the end of this PDF (%s), so it")
        print("does not describe this file. %d pages here." % (over[:5], pages))
        return 2
    if max(truth) < 0.5 * pages:
        print("Warning: the truth file marks nothing past page %d of %d."
              % (max(truth), pages))
        print("If it was marked against a different file, every figure below")
        print("is meaningless. Check before reading them.")
        print("")

    report_text_layer(feats)

    print("\nBoundaries marked by hand: %d documents over %d pages" % (len(truth), pages))
    print("Scored over pages 2-%d; page 1 is excluded as a free true positive.\n" % pages)

    print("  %-22s %6s %5s %5s %5s   %6s %7s  %5s" % (
        "signal", "fires", "TP", "FP", "FN", "prec", "recall", "F1"))
    fired: dict[str, set[int]] = {}
    for name, fn in SIGNALS.items():
        fired[name] = fn(feats)
        print(_row(name, score_one(fired[name], truth, pages)))

    print()
    for label, names in COMBOS.items():
        union: set[int] = set()
        for n in names:
            union |= fired[n]
        print(_row(label, score_one(union, truth, pages)))

    universe = set(range(2, pages + 1))
    marked = truth & universe

    # A signal that fires on most pages has high recall and no value. Including
    # one in the union produced a headline of "0 boundaries need a model" on a
    # real pile where the honest answer was 5 of 21. Usable means precise AND
    # selective; the ceiling is reported separately and labelled as a ceiling.
    _MIN_FIRES = 5  # below this, precision is not estimable either way
    usable, noisy, unproven = [], [], []
    for name in SIGNALS:
        st = score_one(fired[name], truth, pages)
        if st["fires"] == 0:
            continue
        elif st["fire_rate"] > 0.5 or st["precision"] < 0.9:
            noisy.append(name)
        elif st["fires"] < _MIN_FIRES:
            unproven.append("%s (n=%d)" % (name, st["fires"]))
        else:
            usable.append(name)

    baseline: set[int] = set()
    for n in usable:
        baseline |= fired[n]
    ceiling: set[int] = set()
    for n in SIGNALS:
        ceiling |= fired[n]

    missed = sorted((marked - baseline) & universe)
    unreachable = sorted((marked - ceiling) & universe)

    print("")
    print("--- The number that decides the model's job ---")
    print("Free baseline uses only signals that are precise and selective:")
    print("  %s" % (", ".join(usable) or "(none qualified)"))
    if noisy:
        print("Excluded as imprecise or near-constant: %s" % ", ".join(noisy))
    if unproven:
        print("Precise but too few fires to judge: %s" % ", ".join(unproven))
        print("  Not counted in the baseline. Validate on a second pile.")
    # A second tier, because refusing to report anything is worst exactly where
    # the decision is hardest. On a 396-page pile of scraps nothing reached 90%
    # precision, and the honest answer was not silence: geometry and brevity
    # found 45 of 168 boundaries at a cost of 19 false splits, and that is a
    # trade somebody can accept or decline.
    #
    # The two errors are not priced alike. A false split shows up as a one-page
    # fragment a reviewer sees at a glance. A missed boundary welds two
    # documents together, mislabels both, and carries the wrong request
    # mappings into the production index -- invisible unless somebody reads the
    # document. So a below-bar signal is usually still worth taking.
    workable = []
    for name in SIGNALS:
        st = score_one(fired[name], truth, pages)
        if st["fires"] >= _MIN_FIRES and st["fire_rate"] <= 0.5 and st["precision"] >= 0.5:
            workable.append(name)
    best: set[int] = set()
    for n in workable:
        best |= fired[n]
    best_found = len((best & marked))
    best_false = len((best & universe) - marked)

    if not usable:
        print("")
        print("NO SIGNAL HERE IS CLEAN (none at 90% precision).")
        if workable:
            print("Best available, combining %s:" % ", ".join(workable))
            print("  finds %d of %d boundaries (%.0f%%), at %d false splits"
                  % (best_found, len(marked),
                     100 * best_found / max(1, len(marked)), best_false))
            print("  A false split is a fragment a reviewer sees. A missed one")
            print("  welds two documents and mislabels both. Usually worth it.")
        else:
            print("Nothing fired often enough to judge, which is a statement")
            print("about the sample rather than about the signals.")
        print("")
        picked = choose_signals(fired, truth, pages, args.fp_cost)
        if picked:
            chosen: set[int] = set()
            for n in picked:
                chosen |= fired[n]
            st = score_one(chosen, truth, pages)
            print("")
            print("--- Best combination for THIS pile (false split costs %.2f of"
                  % args.fp_cost)
            print("    a missed boundary) ---")
            print("  %s" % ", ".join(picked))
            print("  finds %d of %d (%.0f%%), at %d false splits, %.0f%% precision"
                  % (st["tp"], len(marked), 100 * st["recall"], st["fp"],
                     100 * st["precision"]))
            print("  Left out: %s"
                  % (", ".join(n for n in SIGNALS if n not in picked) or "none"))
        print("")
        print(_verdict(best_found, len(marked), text_layer_usable(feats)))
        return 0

    print("")
    print("Boundaries the free baseline finds:  %d of %d (%.0f%%)"
          % (len(marked) - len(missed), len(marked),
             100 * (len(marked) - len(missed)) / max(1, len(marked))))
    print("Boundaries a model must judge:       %d of %d (%.0f%%)"
          % (len(missed), len(marked), 100 * len(missed) / max(1, len(marked))))
    print("That second figure is the recurring per-production cost.")
    print("")
    print(_verdict(len(marked) - len(missed), len(marked), text_layer_usable(feats)))
    if len(unreachable) < len(missed):
        print("")
        print("A noisy signal would reach %d more, at %d false splits."
              % (len(missed) - len(unreachable), len(ceiling - marked)))
        print("Worth tuning only if its precision can be raised; as it stands")
        print("a reviewer pays for every one of those false splits.")

    # _HEADER_SAME is a guess until a real pile sets it, so sweep it here
    # rather than asking anybody to edit a constant and re-run. Same rule as
    # the other thresholds: the number comes from the labeled set.
    print("")
    print("--- Masthead-overlap threshold sweep ---")
    print("  Above the threshold two pages count as the same document.")
    print("  %9s %6s %5s %5s   %6s %7s" % ("threshold", "fires", "TP", "FP", "prec", "recall"))
    for t in (0.15, 0.25, 0.34, 0.5, 0.65, 0.8):
        hits = {
            cur.page
            for prev, cur in zip(feats, feats[1:])
            if cur.top_tokens and prev.top_tokens
            and _jaccard(cur.top_tokens, prev.top_tokens) < t
        }
        st = score_one(hits, truth, pages)
        print("  %9.2f %6d %5d %5d   %5.0f%% %6.0f%%%s" % (
            t, st["fires"], st["tp"], st["fp"],
            100 * st["precision"], 100 * st["recall"],
            "   <- in use" if abs(t - _HEADER_SAME) < 1e-9 else ""))
    print("  Pick the highest threshold that keeps precision at 100%, then set")
    print("  _HEADER_SAME to it. A masthead signal is only worth having while")
    print("  it is precise -- recall is what the model pass is for.")

    # Mutual confirmation is worth more than either signal's own precision:
    # pagination reads a footer, the header identity reads the document's own
    # account and period, and they fail for unrelated reasons. A page both
    # agree on, that pagination alone did not reach, is the best candidate
    # there is for a boundary a model would otherwise have to judge.
    pag = fired["page_of_reset"] | fired["page_one"]
    seconded = sorted((fired["acct_change"] & fired["date_change"]) - pag)
    if seconded:
        print("")
        print("Pages the header's account AND period both call a boundary,")
        print("which pagination missed: %s" % ", ".join(map(str, seconded)))
        print("Two signals that fail for unrelated reasons agreeing is the")
        print("strongest free evidence available. Check these by eye first.")

    picked = choose_signals(fired, truth, pages, args.fp_cost)
    if picked:
        chosen: set[int] = set()
        for n in picked:
            chosen |= fired[n]
        st = score_one(chosen, truth, pages)
        print("")
        print("--- Best combination for THIS pile (false split costs %.2f of a"
              % args.fp_cost)
        print("    missed boundary) ---")
        print("  %s" % ", ".join(picked))
        print("  finds %d of %d boundaries (%.0f%%), at %d false splits, %.0f%% precision"
              % (st["tp"], len(truth & set(range(2, pages + 1))),
                 100 * st["recall"], st["fp"], 100 * st["precision"]))
        print("  Signals left out because their marginal contribution did not")
        print("  pay: %s" % (", ".join(n for n in SIGNALS if n not in picked) or "none"))

    cont = continuation_pages(feats)
    vetoed = sorted((baseline & cont) - marked)
    if vetoed:
        print("")
        print("False splits a repeated-masthead veto would remove: %d" % len(vetoed))

    if missed:
        print("\nMissed boundaries -- read these to see which signal to add:")
        by_page = {f.page: f for f in feats}
        for p in missed[: args.list_limit]:
            print("  p%-4d %s" % (p, by_page[p].snippet[:76]))
        if len(missed) > args.list_limit:
            print("  ... and %d more" % (len(missed) - args.list_limit))

    strong = fired["page_of_reset"] | fired["page_one"] | fired["after_blank"]
    false_calls = sorted((strong - truth) & set(range(2, pages + 1)))
    if false_calls:
        print("\nPagination/blank signals firing where no document starts:")
        by_page = {f.page: f for f in feats}
        for p in false_calls[: args.list_limit]:
            print("  p%-4d %s" % (p, by_page[p].snippet[:76]))
        if len(false_calls) > args.list_limit:
            print("  ... and %d more" % (len(false_calls) - args.list_limit))

    return 0


def text_layer_usable(feats: list[PageFeatures]) -> bool:
    """Is there enough text here for any of this to mean anything?

    Half the pages, deliberately low: a production with a few photographs in it
    is still measurable, and the question this gates is whether the signals are
    reading documents or reading noise.
    """
    return sum(1 for f in feats if f.has_text) >= 0.5 * max(1, len(feats))


def choose_signals(fired: dict, truth: set, pages: int, fp_cost: float) -> list:
    """Greedily pick the signal set worth running on THIS pile.

    Taking every signal above a precision floor is wrong in both directions: it
    admits one that adds a false split for every boundary it finds, and it
    drops one that is imprecise alone but catches boundaries nothing else
    reaches. What matters is each signal's MARGINAL contribution to the set, not
    its precision in isolation.

    `fp_cost` prices a false split against a missed boundary. It defaults below
    1 because the two errors are not symmetric: a false split is a fragment a
    reviewer sees at a glance, while a missed boundary welds two documents,
    mislabels both, and carries the wrong request mappings into the production
    index where nothing reveals it. Being explicit about the ratio is the point
    -- it is a policy, and it belongs on the command line rather than inside a
    threshold somebody has to reverse-engineer.
    """
    universe = set(range(2, pages + 1))
    marked = truth & universe
    chosen, current = [], set()
    while True:
        best, best_gain = None, 0.0
        for name, hits in fired.items():
            if name in chosen:
                continue
            after = (current | hits) & universe
            gain = (len(after & marked) - len(current & marked)) - fp_cost * (
                len(after - marked) - len(current - marked))
            if gain > best_gain:
                best, best_gain = name, gain
        if best is None:
            return chosen
        chosen.append(best)
        current |= fired[best]


def _verdict(found: int, marked: int, layered: bool) -> str:
    """Say what this pile IS, because that is a routing decision.

    The free-signal coverage is not only a cost estimate. Two real piles from
    one firm came out inverted -- an orderly run of bank statements where
    pagination found 76% of boundaries and geometry found almost nothing, and a
    396-page stack of scraps where pagination found NOTHING and geometry and
    brevity carried it. There is no one signal set, so the pipeline has to
    decide per upload what it is looking at, and this is the number that
    decides it.
    """
    share = found / max(1, marked)
    if not layered:
        head = "VERDICT: no text layer, so nothing here is final. OCR and re-run."
    elif share >= 0.6:
        head = "VERDICT: mechanically splittable. The model judges the rest."
    elif share >= 0.25:
        head = "VERDICT: partly splittable. Expect a review pass on every split."
    else:
        head = ("VERDICT: NOT mechanically splittable. A model asked about every "
                "page boundary on a pile like this is spending money to guess.")
    return head + """
A pile this tool cannot split is not a pile to throw a model at by default.
Refusing it to a person with a stated reason is the same argument as
refuse_oversized_upload: advice does not stop an unworkable import."""


def report_text_layer(feats: list[PageFeatures]) -> None:
    pages = len(feats)
    with_text = sum(1 for f in feats if f.has_text)
    blankish = sum(1 for f in feats if f.blankish)
    garbled = sum(1 for f in feats if f.has_text and f.garbage_ratio > 0.10)
    not_language = sum(1 for f in feats if f.has_text and f.wordlike < _MIN_WORDLIKE)
    stamped = sum(1 for f in feats if f.bates)

    print("--- Text layer ---")
    print("  pages                      %d" % pages)
    print("  usable text layer          %d (%.0f%%)" % (with_text, 100 * with_text / max(1, pages)))
    print("  effectively blank          %d" % blankish)
    print("  text but >10%% garbage      %d  (non-ASCII; blind to OCR noise)" % garbled)
    print("  text but <60%% word-like    %d  (this is the useful one)" % not_language)
    print("  carrying a Bates token     %d  (loose match -- an account or" % stamped)
    print("                                 invoice number also matches)")

    # Ranking empty pages by how word-like they are measures nothing: on a
    # layerless pile it listed eight pages at 0.00 with blank snippets, under a
    # heading saying this is where the threshold belongs.
    worst = sorted((f for f in feats if not f.blankish), key=lambda f: f.wordlike)[:8]
    if worst and worst[0].wordlike < 0.9:
        print("")
        print("  Least word-like pages -- this is where _MIN_WORDLIKE belongs.")
        print("  Read them, then set the threshold just above the worst page")
        print("  that is actually readable. A page of OCR debris scores like")
        print("  prose under the non-ASCII rule, so only this list separates")
        print("  them.")
        for f in worst:
            print("    %.2f  p%-4d %s" % (f.wordlike, f.page, f.snippet[:58]))

    if with_text < 0.5 * pages:
        print("\n  Most of this pile has no text layer. Splitting cannot be")
        print("  measured or done on it until OCR has run, so step 3 has to")
        print("  precede step 2 for scanned input. OCR this file first, then")
        print("  re-run against the OCR'd copy.")


def cmd_features(args: argparse.Namespace) -> int:
    feats = read_features(Path(args.pdf))
    report_text_layer(feats)
    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "page", "chars", "garbage_ratio", "wordlike", "has_text", "blankish",
            "page_one", "page_of", "hdr_date", "acct", "top_fingerprint",
            "width", "height", "rotation", "images", "bates", "snippet",
        ])
        for f in feats:
            writer.writerow([
                f.page, f.chars, f.garbage_ratio, f.wordlike,
                int(f.has_text), int(f.blankish),
                int(f.page_one), "%d of %d" % f.page_of if f.page_of else "",
                f.header_date or "", " ".join(sorted(f.accounts)),
                f.top, f.shape[0], f.shape[1], f.shape[2], f.images,
                "%s %d" % f.bates if f.bates else "", f.snippet,
            ])
    print("\nWrote %s" % out)
    print("This file holds client document text. Keep it out of the repository.")

    if not text_layer_usable(feats):
        print("")
        print("No boundary list, because every signal here would be measuring")
        print("scanner noise. On this file they produced 39 confident guesses")
        print("from pages holding no text -- the has-text and word-like flags")
        print("flapping between blank pages and pages with a stray text")
        print("object. A list that cannot be right must not be printed beside")
        print("a warning saying so; that is how the warning gets ignored.")
        return 0

    fired: set[int] = set()
    for fn in SIGNALS.values():
        fired |= fn(feats)
    print("")
    print("Pages a printed signal says begin a document (%d):" % len(fired))
    print("  " + ",".join(str(p) for p in sorted(fired)))
    print("")
    print("This is a guess, not truth. Do not paste it into the worksheet --")
    print("truth anchored on the thing being measured scores itself.")
    return 0


def cmd_worksheet(args: argparse.Namespace) -> int:
    feats = read_features(Path(args.pdf))
    report_text_layer(feats)
    out = Path(args.out)
    write_truth(out, len(feats), set(), feats)
    print("\nPut 1 in starts_doc on every page that BEGINS a document.")
    print("Page 1 always does. The snippet is usually enough to tell.")
    print("The column is deliberately blank: pre-filling it with the")
    print("signals' guess would anchor the truth to what it is measuring.")
    print("\nThis file holds client document text. Keep it out of the repository.")
    return 0


def cmd_truth(args: argparse.Namespace) -> int:
    feats = read_features(Path(args.pdf))
    starts = parse_pages(args.pages)
    starts.add(1)
    bad = sorted(p for p in starts if p < 1 or p > len(feats))
    if bad:
        print("Pages outside the document (1-%d): %s" % (len(feats), bad))
        return 2
    write_truth(Path(args.out), len(feats), starts, feats)
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    feats = read_features(Path(args.pdf))
    pages = len(feats)

    if args.truth:
        starts = read_truth(Path(args.truth))
        source = "hand-marked truth"
    elif not text_layer_usable(feats):
        print("This pile has no usable text layer, so a signal-based split")
        print("would cut it at scanner noise and write a folder of documents")
        print("that are not documents. OCR it first, or pass --truth.")
        return 2
    else:
        starts = set()
        for fn in SIGNALS.values():
            starts |= fn(feats)
        source = "printed signals (a guess)"
    starts.add(1)

    ordered = sorted(p for p in starts if 1 <= p <= pages)
    spans = [(a, b - 1) for a, b in zip(ordered, ordered[1:] + [pages + 1])]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    by_page = {f.page: f for f in feats}

    manifest = outdir / "manifest.csv"
    with pymupdf.open(args.pdf) as src, manifest.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "first_page", "last_page", "pages", "snippet"])
        for seq, (first, last) in enumerate(spans, start=1):
            name = "doc%03d_p%04d-%04d.pdf" % (seq, first, last)
            with pymupdf.open() as dst:
                dst.insert_pdf(src, from_page=first - 1, to_page=last - 1)
                dst.save(outdir / name)
            writer.writerow([name, first, last, last - first + 1, by_page[first].snippet])

    print("Split on %s: %d documents from %d pages" % (source, len(spans), pages))
    print("Wrote %d files and %s" % (len(spans), manifest))
    print("These files hold client documents. Keep them out of the repository.")
    return 0


def _natural(path: Path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def cmd_from_parts(args: argparse.Namespace) -> int:
    """Derive truth from a folder of PDFs this pile was already split into.

    If the pile was split by hand once, the boundaries are already recorded --
    in the page counts of the parts. Summing them reconstructs the truth file
    with nothing marked by hand, and two checks make that safe to trust.
    """
    feats = read_features(Path(args.pdf))
    parts = sorted(Path(args.parts).glob("*.pdf"), key=_natural)
    if not parts:
        print("No PDFs in %s" % args.parts)
        return 2

    counts = []
    for part in parts:
        with pymupdf.open(part) as doc:
            counts.append(len(doc))

    total = sum(counts)
    print("Parts: %d files, %d pages. Pile: %d pages." % (len(parts), total, len(feats)))
    if total != len(feats):
        print("")
        print("Page counts do not add up, so the parts are not this pile's")
        print("split -- a file is missing, duplicated, or from another pile.")
        print("Refusing to write a truth file from it.")
        return 2

    starts, page = [], 1
    for n in counts:
        starts.append(page)
        page += n

    # The sum can be right while the ORDER is wrong, which would misplace every
    # boundary after the first mistake. Checking each part's first page against
    # the pile page it claims to be is what makes the order verifiable.
    mismatched = []
    by_page = {f.page: f for f in feats}
    for part, first in zip(parts, starts):
        with pymupdf.open(part) as doc:
            got = _top_fingerprint(doc[0].get_text() or "")
        want = by_page[first].top
        if got and want and _jaccard(frozenset(got.split()), frozenset(want.split())) < 0.5:
            mismatched.append((part.name, first))

    if mismatched:
        print("")
        print("%d parts do not match the pile page they would start at:" % len(mismatched))
        for name, first in mismatched[:15]:
            print("  p%-4d %s" % (first, name))
        print("")
        print("Usually a sort-order problem. Rename the parts so they sort in")
        print("pile order, or mark the worksheet by hand instead.")
        return 2

    print("Every part's first page matches the pile page it starts at.")
    write_truth(Path(args.out), len(feats), set(starts), feats)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure how much of a scanned pile's document boundaries are free.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fp-cost", type=float, default=0.5,
                        help="what a false split costs as a fraction of a missed "
                             "boundary (default 0.5 -- accept two false splits to "
                             "gain one real one)")
    parser.add_argument("--list-limit", type=int, default=25,
                        help="how many missed/false pages to print (default 25)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("features", help="per-page deterministic features; no model, no network")
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("worksheet", help="blank truth worksheet with page snippets")
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_worksheet)

    p = sub.add_parser("truth", help="build a truth file from a page list, e.g. --pages 1,5,9,14")
    p.add_argument("pdf")
    p.add_argument("--pages", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_truth)

    p = sub.add_parser("from-parts",
                       help="derive truth from a folder this pile was already split into")
    p.add_argument("pdf")
    p.add_argument("--parts", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_from_parts)

    p = sub.add_parser("score", help="signals vs hand-marked truth")
    p.add_argument("pdf")
    p.add_argument("--truth", required=True)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("split", help="write one PDF per document")
    p.add_argument("pdf")
    p.add_argument("--truth", help="omit to split on the signals' guess instead")
    p.add_argument("--outdir", required=True)
    p.set_defaults(func=cmd_split)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
