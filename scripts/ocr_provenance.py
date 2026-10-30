"""
scripts/ocr_provenance.py - Which tool touched these PDFs, and can we believe them?

WHY THIS EXISTS. Two of the firm's own workflows put an OCR text layer over
scanned statements, and both were invisible until a font name gave them away:

  * Adobe Acrobat run over a scanned pile before splitting it, a habit from
    before Cyclone, done so Egnyte could search the text.
  * A Tesseract script a litigation assistant generated to split a pile on
    "Page 1". The splitting worked; the OCR did not.

An OCR tool signs its work — Acrobat writes a font called HiddenHorzOCR,
Tesseract writes GlyphLessFont — so every file can be attributed to the
workflow that produced it without anyone having to remember. That is what
says whose process to change, and how large the re-import backlog is.

It reads structure only: fonts, image coverage, page text. **No vendor calls,
no cost, nothing written.** Safe to point at a whole production.

    venv/Scripts/python.exe scripts/ocr_provenance.py <folder> [--csv out.csv]

Storage holds the real corpus under intake/<job-id>.pdf; download a sample to a
folder first. The source_file and storage_path columns of the triage query name
them.
"""
import argparse
import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
from services.pdf_service import (  # noqa: E402
    foreign_ocr_layer, ocr_tool, page_needs_ocr, scrambled_encoding,
    untrustworthy_text_layer,
)


def survey(path):
    """One file's provenance and verdict. Never raises: a bad file is a row."""
    row = {"file": os.path.basename(path), "pages": 0, "tool": "", "verdict": "",
           "pages_needing_ocr": 0, "pages_ocr_today": 0, "pages_ocr_after": 0}
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        row["verdict"] = "could not open (%s)" % type(e).__name__
        return row
    try:
        row["pages"] = doc.page_count
        tools = {t for t in (ocr_tool(doc, i) for i in range(min(doc.page_count, 8))) if t}
        row["tool"] = " and ".join(sorted(tools))
        verdict = untrustworthy_text_layer(doc)
        row["verdict"] = (verdict or "trusted").split(" (")[0]
        needing = sum(1 for i in range(doc.page_count)
                      if page_needs_ocr(doc.load_page(i).get_text().strip()))
        row["pages_needing_ocr"] = needing
        # What it cost before this check existed, and what it costs now. The
        # difference is the vision calls the detector adds - and the figures
        # that were silently wrong before it.
        row["pages_ocr_today"] = needing
        row["pages_ocr_after"] = doc.page_count if verdict else needing
        if not row["tool"] and verdict and "scrambled" in verdict:
            row["tool"] = "(none - the bank's own font encoding)"
    except Exception as e:  # noqa: BLE001
        row["verdict"] = "failed mid-survey (%s)" % type(e).__name__
    finally:
        doc.close()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--csv", help="also write every row here")
    args = parser.parse_args()

    paths = []
    for root, _dirs, names in os.walk(args.folder):
        paths.extend(os.path.join(root, n) for n in sorted(names)
                     if n.lower().endswith(".pdf"))
    if not paths:
        print("No PDFs under %s" % args.folder)
        return 1

    rows = [survey(p) for p in paths]

    print("%-44s %5s  %-22s %-34s %s" % ("file", "pages", "ocr tool", "verdict", "ocr pages now->after"))
    print("-" * 128)
    for r in rows:
        print("%-44s %5d  %-22s %-34s %d -> %d" % (
            r["file"][:43], r["pages"], r["tool"][:22] or "-", r["verdict"][:34],
            r["pages_ocr_today"], r["pages_ocr_after"]))

    print()
    print("=" * 60)
    print("%d file(s), %d page(s)" % (len(rows), sum(r["pages"] for r in rows)))
    print()
    print("BY OCR TOOL - this is whose workflow produced the file")
    for tool, n in Counter(r["tool"] or "no OCR layer" for r in rows).most_common():
        print("  %-40s %4d" % (tool, n))
    print()
    print("BY VERDICT")
    for verdict, n in Counter(r["verdict"] for r in rows).most_common():
        print("  %-40s %4d" % (verdict, n))
    print()
    before = sum(r["pages_ocr_today"] for r in rows)
    after = sum(r["pages_ocr_after"] for r in rows)
    print("VISION CALLS over this set: %d -> %d  (%+d)" % (before, after, after - before))
    print("  Every added call buys a page whose figures were previously read")
    print("  from a text layer nobody should have believed.")
    distrusted = [r for r in rows if r["verdict"] != "trusted"
                  and not r["verdict"].startswith(("could not", "failed"))]
    if distrusted:
        print()
        print("RE-IMPORT BACKLOG: %d file(s), %d page(s) of vision work."
              % (len(distrusted), sum(r["pages"] for r in distrusted)))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print("\nwrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
