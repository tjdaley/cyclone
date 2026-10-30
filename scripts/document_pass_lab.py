"""
scripts/document_pass_lab.py - One document block versus the chunked pipeline.

THE QUESTION. `_MAX_SINGLE_PASS_PAGES = 6` exists because one call reliably
gives up on a long statement: a 27-page, 286-entry First Financial statement
came back with 38 rows, valid JSON, a seventh of its output budget used,
nothing failed — the model just stopped. Above six pages the document is
therefore indexed and walked in chunks.

That constant was calibrated against a 32,000-token output ceiling and an
older model generation. Both are gone. Measured since: a 10-page, 196-line
Chase card statement read in ONE pass agreed with the chunked pipeline on
every amount, every date and every page number, and was RIGHT on the one
figure the chunked read could not make out.

So the ceiling wants re-measuring, and this is the instrument. It reads
`stop_reason` (046) after every call, which is the thing that tells "the model
decided it was done" from "it ran out of room" — the distinction the whole
chunked design turns on and which nothing could see when it was built.

    venv/Scripts/python.exe scripts/document_pass_lab.py \
        --pdf test_data/ocr_diagnosis/cccf204b-....pdf \
        --truth test_data/ocr_diagnosis/cccf204b-..._truth.json \
        --vendor anthropic

Needs vendor API keys in the environment. COSTS MONEY: one call per vendor per
run, over a whole PDF. Nothing is written to the database.
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services import llm_usage  # noqa: E402
from services.llm_service import llm_service  # noqa: E402
from services.statement_service import _STATEMENT_SYSTEM  # noqa: E402
from util.llm_profiles import LLMCandidate, LLMProfile, llm_profiles  # noqa: E402

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def dec(v):
    return Decimal(str(v)).quantize(Decimal("0.01")) if v is not None else None


def one_candidate(profile_name, vendor):
    """
    The chain, narrowed to one vendor.

    A lab reaches past the failover deliberately: the point is to learn how
    each vendor behaves on this call, and a chain would hide the second one
    behind the first's success.
    """
    profile = llm_profiles.get(profile_name)
    picked = [c for c in profile.chain if c.vendor == vendor]
    if not picked:
        raise SystemExit("profile %r has no %s candidate; it has %s"
                         % (profile_name, vendor, [c.vendor for c in profile.chain]))
    return profile.model_copy(update={"chain": [picked[0]]}), picked[0]


def with_deadline(profile, candidate, seconds):
    """
    A longer deadline than the profile carries, because the profile's is too short.

    `extract_account_statement` allows 300s per call, sized for a CHUNK. The
    same statement read in one pass took six minutes through claude.ai — so the
    profile's own ceiling would kill the experiment at the five-minute mark and
    look like a vendor failure. With `max_retries=0` that 300s is now exactly
    300s rather than silently three times it.

    Gemini's migration guidance asks for at least fifteen minutes on a long
    call, which is where the default comes from.
    """
    return (profile.model_copy(update={"timeout_seconds": seconds}),
            candidate.model_copy(update={"timeout_seconds": seconds}))


def keep(text, pdf_path, label, out_dir):
    """
    Write the answer down before anything can reject it.

    A pass over a whole statement costs real money and ten to fifteen minutes,
    and the first version of this lab discarded the reply whenever `--truth` was
    absent: one 809-second call, 92,027 tokens, and nothing left but the row of
    counters. **The case that most needs the file is the one where parsing
    fails** — a truncation and a bad escape are told apart by position in the
    text, and the text was exactly what went missing. So this runs on the raw
    string, before `json.loads` is offered a chance to raise.

    It lands beside the PDF, which is gitignored and already holds this
    client's registers; nothing here goes in the repository.
    """
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    path = os.path.join(out_dir or os.path.dirname(os.path.abspath(pdf_path)),
                        "%s_%s.json" % (base, label))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text or "")
        print("     kept %s (%d chars)" % (path, len(text or "")))
    except OSError as e:  # noqa: BLE001 -- losing the file must not lose the run
        print("     COULD NOT KEEP the response: %s" % e)
    return path


def rows_in(parsed):
    """Transactions across every statement the answer reported."""
    return [t for s in parsed.get("statements", []) for t in (s.get("transactions") or [])]


def run(pdf_path, profile_name, vendor, message, timeout, out_dir=None):
    profile, candidate = one_candidate(profile_name, vendor)
    profile, candidate = with_deadline(profile, candidate, timeout)
    handler = llm_service._document_handlers.get(vendor)
    if handler is None:
        raise SystemExit("no document handler for %r" % vendor)

    pdf = open(pdf_path, "rb").read()
    print("  %-10s %-22s %6.1f KB  max_tokens=%s timeout=%ss"
          % (vendor, candidate.model, len(pdf) / 1024.0,
             candidate.max_tokens or profile.max_tokens,
             candidate.timeout_seconds or profile.timeout_seconds))

    llm_usage.begin_attempt()
    clock = time.monotonic()
    try:
        text = handler(profile, candidate, _STATEMENT_SYSTEM, message, pdf,
                       os.path.basename(pdf_path))
    except Exception as e:  # noqa: BLE001 — a lab reports the failure, it does not fail over
        print("     FAILED after %.0fs: %s: %s" % (time.monotonic() - clock,
                                                   type(e).__name__, str(e)[:160]))
        return None
    elapsed = time.monotonic() - clock

    noted = llm_usage._NOTED.get() or {}
    counts = noted.get("counts") or {}
    out = counts.get("output_tokens")
    print("     %.0fs  served=%s  in=%s out=%s  stop=%s/%s%s"
          % (elapsed, noted.get("served_model"), counts.get("input_uncached_tokens"), out,
             noted.get("stop_reason"), noted.get("stop_reason_class"),
             "  %.0f tok/s" % (out / elapsed) if out and elapsed else ""))
    # THE SIGNAL THE CHUNKED DESIGN WAS BUILT WITHOUT. 'complete' on a short
    # answer means the model decided it was done: no limit was reached, so no
    # limit can be raised, and splitting is the only remedy.
    if noted.get("stop_reason_class") == "truncated":
        print("     ^ TRUNCATED: it ran out of room. A bigger ceiling may help.")
    elif noted.get("stop_reason_class") == "complete":
        print("     ^ complete: whatever it returned, it believes it finished.")

    keep(text, pdf_path, "%s_onepass" % vendor, out_dir)

    body = FENCE.sub("", text or "").strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        at_end = e.pos >= len(body) - 2
        print("     JSON would not parse (%s at %d of %d) — %s"
              % (e.msg, e.pos, len(body), "cut off mid-answer" if at_end else "malformed"))
        return None

    # The count is the whole question, so it is reported with or without a
    # truth file. 'complete' on a SHORT answer is the early-stop failure this
    # lab exists to detect, and the counters alone cannot show it.
    rows = rows_in(parsed)
    print("     %d statement(s), %d transaction(s)"
          % (len(parsed.get("statements") or []), len(rows)))
    return parsed, elapsed


def compare(parsed, truth_path):
    rows = []
    for st in parsed.get("statements", []):
        rows.extend(st.get("transactions") or [])
    truth = json.load(open(truth_path, encoding="utf-8"))
    if isinstance(truth, dict):
        truth = [t for s in truth.get("statements", []) for t in s.get("transactions") or []]
    rows.sort(key=lambda r: r.get("line_no") or 0)
    truth.sort(key=lambda r: r.get("line_no") or 0)

    print("     rows=%d (truth %d)  sum=%s (truth %s)" % (
        len(rows), len(truth),
        sum(dec(r.get("amount")) or 0 for r in rows),
        sum(dec(r.get("amount")) or 0 for r in truth)))
    if len(rows) != len(truth):
        print("     ROW COUNT DIFFERS — the comparison below is positional and "
              "will drift after the first missing line.")

    hit = {k: 0 for k in ("amount", "transaction_date", "physical_page_number")}
    for a, b in zip(rows, truth):
        if dec(a.get("amount")) == dec(b.get("amount")):
            hit["amount"] += 1
        if a.get("transaction_date") == b.get("transaction_date"):
            hit["transaction_date"] += 1
        if a.get("physical_page_number") == b.get("physical_page_number"):
            hit["physical_page_number"] += 1
    n = max(len(truth), 1)
    for field, got in hit.items():
        mark = "OK " if got == len(truth) else "   "
        print("     %s %-22s %3d / %d  %5.1f%%" % (mark, field, got, len(truth), 100.0 * got / n))


def chunk_pass(pdf_path, profile_name, vendor, pages_arg, period, account, timeout, out_dir):
    """
    Reproduce ONE chunk of the production walk, exactly as the worker makes it.

    WHY THIS EXISTS. On a 27-page statement one chunk — pages 5-8 — spends
    21,286 output tokens where its neighbours spend 6-9k, for the same ~47
    stored lines: 453 tokens per line against 135. It is also the chunk that
    timed out at 300s on the first run, so it has been the expensive one every
    time. Three hypotheses were guessed at from the ledger and two were wrong,
    because a token counter cannot say what the extra tokens SAY.

    So this builds the identical message: `_statement_context` for the context
    block, `_slice_body` for the page slice including the fenced continuation
    page, and the same "PRIMARY PAGES" line. Then it writes the answer to disk
    and counts the rows, which is the number the ledger never had.

    It is the chunk prompt over extracted TEXT, not a document block — the two
    paths are different calls and comparing them would prove nothing.
    """
    from services.statement_service import _TRANSACTIONS_SYSTEM, statement_service  # noqa: E402
    from services import bates_service  # noqa: E402
    # The singleton, not the module: `pdf_service.pdf_service` is the instance.
    from services.pdf_service import pdf_service  # noqa: E402

    start, _, stop = pages_arg.partition("-")
    start, stop = int(start), int(stop or start)

    print("  extracting text (this may cost one vision call per image-only page)")
    raw_text = pdf_service.extract_text(open(pdf_path, "rb").read(), page_markers=True)
    pages = bates_service.split_pages(raw_text)
    held = sorted(pages)
    if not held:
        raise SystemExit("no page markers in the extracted text")
    print("  %d pages of text; asking for %d-%d of %d" % (len(held), start, stop, held[-1]))

    first_date, _, last_date = (period or "").partition(":")
    institution, _, rest = (account or "::").partition(":")
    last4, _, acct_type = rest.partition(":")
    statement = {
        "account": {"institution": institution or None,
                    "account_number_last4": last4 or None,
                    "account_type": acct_type or None},
        "period": {"start_date": first_date or None, "end_date": last_date or None},
    }
    context = statement_service._statement_context(statement)
    body = statement_service._slice_body(pages, start, stop, held[-1])
    message = "%s\n\nPRIMARY PAGES: %d to %d.\n\n%s" % (context, start, stop, body)

    profile, candidate = one_candidate(profile_name, vendor)
    profile, candidate = with_deadline(profile, candidate, timeout)
    print("  %-10s %-22s message %d chars  max_tokens=%s timeout=%ss"
          % (vendor, candidate.model, len(message),
             candidate.max_tokens or profile.max_tokens,
             candidate.timeout_seconds or profile.timeout_seconds))

    llm_usage.begin_attempt()
    clock = time.monotonic()
    try:
        handler = llm_service._text_handlers.get(vendor)
        if handler is None:
            raise SystemExit("no text handler for %r; have %s"
                             % (vendor, sorted(llm_service._text_handlers)))
        text = handler(profile, candidate, _TRANSACTIONS_SYSTEM, message)
    except Exception as e:  # noqa: BLE001
        print("     FAILED after %.0fs: %s: %s"
              % (time.monotonic() - clock, type(e).__name__, str(e)[:160]))
        return None
    elapsed = time.monotonic() - clock

    noted = llm_usage._NOTED.get() or {}
    counts = noted.get("counts") or {}
    out = counts.get("output_tokens")
    print("     %.0fs  served=%s  in=%s out=%s  stop=%s/%s"
          % (elapsed, noted.get("served_model"), counts.get("input_uncached_tokens"),
             out, noted.get("stop_reason"), noted.get("stop_reason_class")))

    path = keep(text, pdf_path, "%s_pages%d-%d" % (vendor, start, stop), out_dir)
    body_text = FENCE.sub("", text or "").strip()

    # THE NUMBERS THE LEDGER COULD NOT GIVE. Whitespace share says whether the
    # answer is pretty-printed; rows-per-token says whether the model is
    # emitting far more rows than survive into the database.
    ws = sum(1 for c in body_text if c in " \n\t\r")
    print("     %d chars, %.1f%% whitespace" % (len(body_text), 100.0 * ws / max(len(body_text), 1)))
    if out:
        print("     %.1f chars per output token" % (len(body_text) / out))
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError as e:
        at_end = e.pos >= len(body_text) - 2
        print("     JSON would not parse (%s at %d of %d) — %s"
              % (e.msg, e.pos, len(body_text), "cut off mid-answer" if at_end else "malformed"))
        return None
    rows = parsed.get("transactions") or []
    print("     %d transactions returned, %d securities rows omitted"
          % (len(rows), parsed.get("securities_transactions_omitted") or 0))
    if rows and out:
        print("     %.0f output tokens per returned row" % (out / len(rows)))
    on_page = {}
    for r in rows:
        key = r.get("pg", r.get("physical_page_number"))
        on_page[key] = on_page.get(key, 0) + 1
    print("     rows by page: %s" % dict(sorted(on_page.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    print("     kept at %s — read it to see what the tokens actually say" % path)
    return parsed, elapsed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--truth", help="a known-good extraction to score against")
    ap.add_argument("--profile", default="extract_account_statement")
    ap.add_argument("--vendor", action="append",
                    help="repeatable; default is every vendor with a document handler")
    ap.add_argument("--message", default="Extract this statement.")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="per-call deadline in seconds (default 900; the profile's 300 "
                         "is sized for a chunk and would kill a single pass)")
    ap.add_argument("--out", help="where to keep each response (default: beside the PDF)")
    ap.add_argument("--pages", help="e.g. 5-8 — reproduce ONE chunk of the production "
                                    "walk over that page slice, with the chunk prompt, "
                                    "instead of one document-block pass over the whole PDF")
    ap.add_argument("--period", default="",
                    help="start:end, e.g. 2023-11-28:2023-12-26. The chunk prompt needs it "
                         "or a line printed '12/04' has no year")
    ap.add_argument("--account", default="",
                    help="institution:last4:type, e.g. 'First Financial:9260:checking'")
    args = ap.parse_args()

    vendors = args.vendor or ["anthropic", "openai", "gemini"]
    if args.pages:
        print("ONE CHUNK OF THE WALK: %s, pages %s" % (os.path.basename(args.pdf), args.pages))
        print("prompt=_TRANSACTIONS_SYSTEM — the same message the worker builds")
        print()
        for vendor in vendors:
            chunk_pass(args.pdf, args.profile, vendor, args.pages, args.period,
                       args.account, args.timeout, args.out)
            print()
        return 0

    print("DOCUMENT-BLOCK PASS: %s" % os.path.basename(args.pdf))
    print("profile=%s  prompt=_STATEMENT_SYSTEM (%d chars)" % (args.profile, len(_STATEMENT_SYSTEM)))
    print()
    for vendor in vendors:
        result = run(args.pdf, args.profile, vendor, args.message, args.timeout, args.out)
        if result and args.truth:
            compare(result[0], args.truth)
        print()
    print("Compare the wall clock against the chunked path for the same file: a")
    print("10-page statement there is 1 index + 3 chunk passes + one vision call")
    print("per OCR'd page, all sequential.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
