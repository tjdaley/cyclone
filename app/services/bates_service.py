"""
app/services/bates_service.py - Find the Bates series stamped on a production.

A Bates number is not an extraction problem, it is a **pattern** problem. The
stamp is one token printed in the same place on every page of a production, and
its numeric part advances by one per page. That last property is the whole
trick: nothing else on a bank statement behaves that way. An account number
repeats, a check number jumps around, an amount is unrelated to its neighbours.
A token that increments in lockstep with the page is a Bates stamp and very
little else.

So this runs in Python over the page-marked text, not through the model. That
makes it exact, free, and auditable, and it removes a hallucination surface: a
model asked for "the Bates number" on an unstamped page will happily produce a
plausible one, and a citation to a number that is not on the document is worse
than no citation at all.

The one inference that must never happen here: **filling in the stamp for an
unstamped page from its neighbours.** The series makes it trivially easy — page
14 sits between KF-000013 and KF-000015 — and it would put a number in an
exhibit that does not appear on the document it cites. Unstamped stays null; the
gap is reported instead.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# The marker pdf_service writes between pages when page_markers=True.
_PAGE_MARKER = re.compile(r"^<<<PAGE (\d+)>>>$", re.MULTILINE)

# A Bates candidate: an optional prefix, optional separator, then the digits.
#
# Four digits is the floor and it is doing real work — it is what excludes
# "Page 3 of 12", a two-digit day, and a three-digit area code. Real stamps are
# zero-padded to a fixed width precisely so a production sorts, so the floor
# costs nothing.
_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])"                # not mid-token
    r"([A-Za-z][A-Za-z0-9._]{0,19}?)?" # optional prefix, e.g. KF / SALMONS / DEF
    r"[\s._-]{0,2}"                    # optional separator
    r"(\d{4,10})"                      # the number, zero-padded in practice
    r"(?![0-9])"
)

# How much of the document a series has to cover before it is believed.
# Productions do get partial stamps — an exhibit slipped in unstamped, a page
# re-scanned — so this is not 1.0.
_MIN_PAGE_COVERAGE = 0.6

# How often the number must advance by exactly the page delta.
#
# Deliberately loose, and this is the subtle part: a production with pages
# missing is *precisely* a run whose steps are not all one, and that is the case
# worth detecting. Gating hard on unit steps would reject the incomplete
# productions and accept only the complete ones — exactly backwards. Strict
# monotonicity below is the real gate; this only keeps something wild out.
_MIN_STEP_SCORE = 0.5


@dataclass
class BatesSeries:
    """One detected Bates run over a document."""

    prefix: str
    digits: int
    #: What sits between prefix and digits, as printed: "-", " ", or "".
    separator: str = ""
    #: Physical page number → the stamp exactly as printed on it.
    by_page: dict[int, str] = field(default_factory=dict)
    #: Pages in the document that carried no stamp from this series.
    unstamped_pages: list[int] = field(default_factory=list)
    #: Numbers missing from the run that no page we hold accounts for — pages
    #: absent from the production itself.
    gaps: list[str] = field(default_factory=list)
    confidence: str = "high"

    @property
    def first(self) -> Optional[str]:
        return self.by_page[min(self.by_page)] if self.by_page else None

    @property
    def last(self) -> Optional[str]:
        return self.by_page[max(self.by_page)] if self.by_page else None

    def format(self, value: int) -> str:
        """
        Render a number in this series' own form.

        Separator and zero padding included: a gap reported as ``KF000144``
        when the production stamps ``KF-000144`` sends someone searching for a
        string that appears nowhere.
        """
        return "%s%s%0*d" % (self.prefix, self.separator, self.digits, value)

    def summary(self) -> dict:
        """A JSON-safe description, for the statement's provenance record."""
        return {
            "prefix": self.prefix,
            "separator": self.separator,
            "digits": self.digits,
            "first": self.first,
            "last": self.last,
            "pages_stamped": len(self.by_page),
            "unstamped_pages": self.unstamped_pages,
            "gaps": self.gaps,
            "confidence": self.confidence,
        }


def split_pages(raw_text: str) -> dict[int, str]:
    """
    Split page-marked text into ``{page_number: text}``.

    :param raw_text: Output of ``pdf_service.extract_text(..., page_markers=True)``.
    :return: Text per page. Empty when the text carries no markers, which is
        the honest answer — without them there is no page to attribute a stamp
        to, and guessing one would be worse than returning nothing.
    :rtype: dict[int, str]
    """
    marks = list(_PAGE_MARKER.finditer(raw_text))
    if not marks:
        return {}
    pages: dict[int, str] = {}
    for index, mark in enumerate(marks):
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(raw_text)
        pages[int(mark.group(1))] = raw_text[start:end]
    return pages


def _candidates(page_text: str) -> list[tuple[str, str, int, int, str, bool]]:
    """
    Every Bates-shaped token on a page.

    :return: ``(prefix, separator, digit_width, value, raw, is_edge)`` per
        match. ``is_edge`` marks a token in the first or last two non-blank
        lines — a stamp lives in a page corner, and extracted text tends to put
        corners at the edges.
    :rtype: list[tuple[str, str, int, int, str, bool]]
    """
    lines = [ln for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return []
    edge = set(lines[:2] + lines[-2:])

    found: list[tuple[str, str, int, int, str, bool]] = []
    for line in lines:
        for match in _CANDIDATE.finditer(line):
            raw_prefix = match.group(1) or ""
            digits = match.group(2)
            prefix = raw_prefix.strip(" ._-")
            # Whatever the document prints between the two halves. Taken from
            # the match rather than assumed, so the series renders back exactly.
            separator = match.group(0)[len(raw_prefix):-len(digits)]
            found.append((
                prefix, separator, len(digits), int(digits),
                match.group(0).strip(), line in edge,
            ))
    return found


def _gaps(
    series: BatesSeries,
    per_page: dict[int, tuple[int, str, bool, str]],
    unstamped_pages: list[int],
) -> list[str]:
    """
    Numbers missing from the run that no page in hand accounts for.

    Two very different things produce a hole in a Bates run, and conflating
    them turns a useful alarm into noise:

    * **A page we do not have.** The production skips from KF-000143 to
      KF-000146. That is a discovery problem worth raising.
    * **A page we do have but could not read a stamp on.** A re-scan, a
      cropped corner. The page is here; only the stamp is unreadable.

    So an unstamped page is projected onto the run — page 4 sitting between
    stamped pages 3 and 5 accounts for the number between them — and any hole
    it explains is not reported.

    Note the direction of that inference. It is used only to *suppress* a false
    alarm, never to write a stamp: the projected number is deliberately not put
    into ``by_page``, because it does not appear on the document.

    :return: The unexplained numbers, rendered in the series' own form.
    :rtype: list[str]
    """
    stamped = {page: value for page, (value, _, _, _) in per_page.items()}
    if not stamped:
        return []

    # Project each unstamped page onto the run from its nearest stamped anchor.
    anchors = sorted(stamped)
    accounted: set[int] = set()
    for page in unstamped_pages:
        nearest = min(anchors, key=lambda a: abs(a - page))
        accounted.add(stamped[nearest] + (page - nearest))

    present = set(stamped.values()) | accounted
    return [
        series.format(n)
        for n in range(min(stamped.values()), max(stamped.values()) + 1)
        if n not in present
    ]


def detect(pages: dict[int, str], prefix_hint: Optional[str] = None) -> Optional[BatesSeries]:
    """
    Find the Bates series running through a document.

    Groups every candidate by ``(prefix, digit width)``. A group has to increase
    strictly from page to page — that alone rules out the other long numbers on
    a statement, since an account number repeats and a balance goes up and down.
    Among the survivors, the winner is the one covering most of the document
    with the most unit-sized steps.

    :param pages: ``{page_number: text}`` from :func:`split_pages`.
    :param prefix_hint: A prefix the user supplied, e.g. ``"KF-"``. Filters the
        candidates before scoring — an escape hatch for an odd production, not
        the normal path.
    :return: The series, or None when nothing scores well enough. None is a
        real answer: most documents are not Bates-stamped at all.
    :rtype: Optional[BatesSeries]
    """
    if not pages:
        return None

    wanted = (prefix_hint or "").strip().strip(" ._-").lower() or None

    # (prefix, width) -> {page: (value, raw, is_edge, separator)}
    groups: dict[tuple[str, int], dict[int, tuple[int, str, bool, str]]] = {}
    for page, text in pages.items():
        for prefix, separator, width, value, raw, is_edge in _candidates(text):
            if wanted is not None and prefix.lower() != wanted:
                continue
            key = (prefix, width)
            # One stamp per page per series. If a page shows the same shape
            # twice, the edge one wins — that is where a stamp sits.
            existing = groups.setdefault(key, {}).get(page)
            if existing is None or (is_edge and not existing[2]):
                groups[key][page] = (value, raw, is_edge, separator)

    total_pages = len(pages)
    best: Optional[tuple[float, BatesSeries]] = None

    for (prefix, width), per_page in groups.items():
        if len(per_page) < 2:
            continue
        coverage = len(per_page) / total_pages
        if coverage < _MIN_PAGE_COVERAGE:
            continue

        ordered = sorted(per_page.items())
        steps = 0
        exact = 0
        monotonic = True
        for (page_a, (value_a, *_)), (page_b, (value_b, *_)) in zip(ordered, ordered[1:]):
            steps += 1
            if value_b <= value_a:
                # A Bates run never goes backwards or repeats. This is the hard
                # gate, and it is what excludes the other long numbers on a
                # statement: an account number repeats, a balance fluctuates.
                monotonic = False
                break
            if value_b - value_a == page_b - page_a:
                exact += 1
        if not monotonic:
            continue
        step_score = exact / steps if steps else 0.0
        if step_score < _MIN_STEP_SCORE:
            continue

        edge_score = sum(1 for _, (_, _, is_edge, _) in ordered if is_edge) / len(ordered)
        # A bare number can be a real stamp, but it is also what every other
        # long number on the page looks like, so it has to win on merit.
        prefix_bonus = 0.15 if prefix else 0.0
        score = step_score * 2 + coverage + edge_score * 0.5 + prefix_bonus

        separators = [sep for _, (_, _, _, sep) in ordered]
        unstamped = sorted(set(pages) - set(per_page))
        series = BatesSeries(
            prefix=prefix,
            digits=width,
            separator=max(set(separators), key=separators.count),
            by_page={page: raw for page, (_, raw, _, _) in ordered},
            unstamped_pages=unstamped,
            # Confidence is about "is this the Bates series", not "is the
            # production complete". A prefixed run covering the document is
            # certain even where it has holes — the holes are the finding.
            confidence=(
                "high" if coverage > 0.9 and (step_score >= 0.8 or prefix) else "low"
            ),
        )
        series.gaps = _gaps(series, per_page, unstamped)

        if best is None or score > best[0]:
            best = (score, series)

    if best is None:
        if wanted is not None:
            LOGGER.info("bates_service.detect: no series matched prefix hint over %d pages", total_pages)
        return None

    series = best[1]
    LOGGER.info(
        "bates_service.detect: series prefix=%r digits=%d pages=%d/%d gaps=%d confidence=%s",
        series.prefix, series.digits, len(series.by_page), total_pages,
        len(series.gaps), series.confidence,
    )
    return series
