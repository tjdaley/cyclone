"""
scripts/sync_user_guide.py - Publish docs/user-guide into the frontend bundle.

WHY A COPY EXISTS AT ALL
────────────────────────────────────────────────────────────────────────────
The frontend image is built with `context: ./frontend`, so nothing above that
directory exists when `npm run build` runs inside the container. `docs/` is
therefore unreachable at build time, and a page that renders the guide has to
read it from somewhere inside `frontend/`.

Two copies of anything is the failure mode this project spends most of its
effort preventing, so the copy is **generated, never edited**, and
`tests/test_user_guide_sync.py` fails the moment it stops matching its source.
Drift is then a failed test rather than a chapter that quietly describes last
month's software.

`docs/user-guide/` remains the only place anyone writes.

WHAT IT WRITES
────────────────────────────────────────────────────────────────────────────
Every chapter, byte for byte, plus `manifest.json` — the table of contents the
page renders its navigation from.

The manifest is built by READING THE GUIDE'S OWN INDEX rather than by sorting
filenames. `README.md` already groups the chapters into parts and writes one
sentence about each, in an order somebody chose; reproducing that by convention
would mean two answers to "what order are the chapters in", and the one in the
repository is the one a reader sees on GitHub.

A chapter file that the index does not link to still ships, under "More" — a
new chapter somebody forgot to index is a thing to notice, not to hide.

    python scripts/sync_user_guide.py            # write
    python scripts/sync_user_guide.py --check    # exit 1 if stale, write nothing
"""
import argparse
import json
import re
import sys
from pathlib import Path

#: The guide, and where it is published. Both relative to the repository root.
SOURCE = Path("docs/user-guide")
DEST = Path("frontend/public/user-guide")

#: The index file. Rendered as the guide's front page and parsed for the order.
INDEX = "README.md"

MANIFEST = "manifest.json"

#: `## Part 2 — Setting up work`
_PART = re.compile(r"^##\s+(.+?)\s*$")

#: A table row whose first cell links to a chapter, with the blurb beside it:
#: `| [4. Importing statements](04-importing-statements.md) | Dropping PDFs… |`
_ROW = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+\.md)\)\s*\|\s*(.*?)\s*\|\s*$")

#: `4. Importing statements` -> number and title, kept apart so the page can
#: set them in different type. A chapter with no number keeps its whole title.
_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")


def repo_root() -> Path:
    """The checkout root, derived from this file rather than the shell's cwd."""
    return Path(__file__).resolve().parent.parent


def _chapter(link_text: str, filename: str, summary: str) -> dict:
    """One entry in the manifest."""
    number, title = "", link_text.strip()
    numbered = _NUMBERED.match(title)
    if numbered:
        number, title = numbered.group(1), numbered.group(2)
    return {
        "slug": filename[:-3],          # the URL: /guide/<slug>
        "file": filename,
        "number": number,
        "title": title,
        "summary": summary.strip(),
    }


def build_manifest(source: Path) -> dict:
    """
    Read the guide's index into the structure the page navigates by.

    :param source: The ``docs/user-guide`` directory.
    :return: ``{"parts": [{"title": ..., "chapters": [...]}]}``.
    :rtype: dict
    :raises FileNotFoundError: When the index is missing — without it there is
        no order, and inventing one would be the second source of truth this
        script exists to avoid.
    """
    index = source / INDEX
    parts: list[dict] = []
    seen: set[str] = set()

    part_title = ""
    for line in index.read_text(encoding="utf-8").splitlines():
        heading = _PART.match(line)
        if heading:
            part_title = heading.group(1).strip()
            continue
        row = _ROW.match(line)
        if not row:
            continue
        link_text, filename, summary = row.groups()
        if filename == INDEX or not (source / filename).exists():
            # A link to something that is not a chapter file, or a broken one.
            # Reported by the link check in the test suite, not here.
            continue
        if filename in seen:
            continue
        seen.add(filename)
        if not parts or parts[-1]["title"] != part_title:
            parts.append({"title": part_title, "chapters": []})
        parts[-1]["chapters"].append(_chapter(link_text, filename, summary))

    # A chapter nobody indexed still ships. Silence would make it invisible in
    # the one place it was written to be read.
    orphans = sorted(
        p.name for p in source.glob("*.md")
        if p.name != INDEX and p.name not in seen
    )
    if orphans:
        parts.append({
            "title": "More",
            "chapters": [_chapter(p[:-3], p, "") for p in orphans],
        })

    return {"index": INDEX, "parts": parts}


def desired_files(source: Path) -> dict[str, str]:
    """
    Everything the published directory should contain, by name.

    :param source: The ``docs/user-guide`` directory.
    :return: Filename to exact contents.
    :rtype: dict[str, str]
    """
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob("*.md"))}
    # Sorted keys and a trailing newline so the file is stable across runs --
    # a manifest that reordered itself would show as drift on every check.
    files[MANIFEST] = json.dumps(build_manifest(source), indent=2, sort_keys=False) + "\n"
    return files


def differences(source: Path, dest: Path) -> list[str]:
    """
    What is stale, in the words the person running ``--check`` needs.

    :return: One sentence per problem; empty when the copy is current.
    :rtype: list[str]
    """
    want = desired_files(source)
    problems: list[str] = []
    for name, content in want.items():
        published = dest / name
        if not published.exists():
            problems.append("%s is not published" % name)
        elif published.read_text(encoding="utf-8") != content:
            problems.append("%s differs from %s" % (name, source / name))
    for published in sorted(dest.glob("*")):
        if published.is_file() and published.name not in want:
            problems.append("%s is published but no longer in the guide" % published.name)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report staleness and write nothing")
    args = parser.parse_args()

    root = repo_root()
    source, dest = root / SOURCE, root / DEST

    if not source.is_dir():
        print("No guide at %s" % source, file=sys.stderr)
        return 2

    if args.check:
        problems = differences(source, dest)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("\nRun: python scripts/sync_user_guide.py", file=sys.stderr)
            return 1
        print("%s is current (%d files)" % (DEST, len(desired_files(source))))
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    want = desired_files(source)
    for name, content in want.items():
        (dest / name).write_text(content, encoding="utf-8", newline="\n")
    # A chapter deleted from the guide has to leave the site too, or it stays
    # reachable by URL long after anybody can find the file it came from.
    for published in sorted(dest.glob("*")):
        if published.is_file() and published.name not in want:
            published.unlink()
            print("removed %s" % published.name)
    print("published %d files to %s" % (len(want), DEST))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
