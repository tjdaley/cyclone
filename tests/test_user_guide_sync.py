"""
tests/test_user_guide_sync.py - The published guide still matches the source.

`docs/user-guide/` is where the guide is written. `frontend/public/user-guide/`
is a generated copy, and it exists for one unavoidable reason: the frontend
image is built with `context: ./frontend`, so nothing above that directory is
reachable when the bundle is built.

Two copies of anything is the failure this project spends its effort
preventing, so the second one is only safe while something checks it. That is
this file. Without it, the chapter a paralegal reads on the site would go on
describing last month's software with nothing anywhere saying so — the exact
shape of every other failure in this codebase: it looks fine.

It also checks the things the page assumes about the markdown, because both
are cheap here and expensive in a browser:

  1. The published copy is byte-for-byte the source, with nothing extra or
     missing (`sync_user_guide.py --check`).
  2. Every chapter the index links to exists, and every chapter file is
     reachable from the index — an unlinked chapter is invisible in the one
     place it was written to be read.
  3. Every internal `.md#fragment` link points at a heading that exists. These
     are rewritten into routes by the page, so a stale one is a dead link on
     the site rather than a 404 on GitHub.

Run:  venv/Scripts/python.exe tests/test_user_guide_sync.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import sync_user_guide as sync  # noqa: E402

SOURCE = os.path.join(ROOT, "docs", "user-guide")
DEST = os.path.join(ROOT, "frontend", "public", "user-guide")

FAILURES: list[str] = []

#: Any markdown link: `[text](target)`.
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def chapters() -> list[str]:
    return sorted(n for n in os.listdir(SOURCE) if n.endswith(".md"))


def heading_ids(body: str) -> set[str]:
    """
    The anchors a chapter offers, by the rule GitHub uses.

    Mirrors `headingId` in `frontend/src/lib/userGuide.ts`. The two are written
    against the same rule rather than sharing code, so this catches one of them
    drifting -- which shows up as a link that scrolls nowhere.
    """
    found = set()
    for line in body.splitlines():
        if not line.startswith("#"):
            continue
        text = line.lstrip("#").strip()
        text = _LINK.sub(lambda m: m.group(0).split("]")[0][1:], text)  # keep link text
        slug = re.sub(r"[^\w\- ]+", "", text.lower()).strip()
        found.add(re.sub(r"\s+", "-", slug))
    return found


print("The published copy is current")

problems = sync.differences(sync.repo_root() / sync.SOURCE, sync.repo_root() / sync.DEST)
check("nothing is stale", problems, [])
if problems:
    print("       run: python scripts/sync_user_guide.py")

# The client guide is published the same way and drifts the same way. It is one
# page with no manifest, so `wanted()` is what decides which shape it has.
client_source = sync.repo_root() / sync.CLIENT_SOURCE
check("the client guide is published",
      sync.differences(client_source, sync.repo_root() / sync.CLIENT_DEST), [])
check_true("and it is one page, with no manifest",
           list(sync.wanted(client_source)) == ["README.md"])

published = sorted(n for n in os.listdir(DEST) if n.endswith(".md"))
check("every chapter is published", published, chapters())
check_true("the manifest is published", os.path.exists(os.path.join(DEST, "manifest.json")))


print("")
print("The index and the files agree")

index = read(os.path.join(SOURCE, sync.INDEX))
linked = set()
for target in _LINK.findall(index):
    name = target.split("#")[0]
    if name.endswith(".md"):
        linked.add(name)

missing = sorted(n for n in linked if not os.path.exists(os.path.join(SOURCE, n)))
check("the index links to nothing that is missing", missing, [])

unlisted = sorted(n for n in chapters() if n != sync.INDEX and n not in linked)
check("no chapter is unreachable from the index", unlisted, [])

manifest = sync.build_manifest(sync.repo_root() / sync.SOURCE)
in_manifest = [c["file"] for part in manifest["parts"] for c in part["chapters"]]
check("the manifest holds every chapter once",
      sorted(in_manifest), sorted(n for n in chapters() if n != sync.INDEX))
check("and keeps the index's order", in_manifest,
      [n for n in _LINK.findall(index) if n.endswith(".md") and n != sync.INDEX])
check_true("grouped into parts", len(manifest["parts"]) > 1)


print("")
print("Cross-references land on a heading that exists")

anchors = {name: heading_ids(read(os.path.join(SOURCE, name))) for name in chapters()}
broken: list[str] = []
for name in chapters():
    body = read(os.path.join(SOURCE, name))
    for target in _LINK.findall(body):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, fragment = target.partition("#")
        where = file_part or name
        if not where.endswith(".md"):
            continue
        if where not in anchors:
            broken.append("%s -> %s (no such file)" % (name, target))
            continue
        if fragment and fragment not in anchors[where]:
            broken.append("%s -> %s (no such heading)" % (name, target))

check("no dead internal links", broken, [])


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all user-guide checks passed")
