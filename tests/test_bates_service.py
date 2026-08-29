import sys
sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from services.bates_service import detect, split_pages

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# ── 1. A normal production: stamp in the footer, noise everywhere ────────
def page(n, stamp=None, extra=""):
    body = (
        "FIRST NATIONAL BANK\n"
        "Account ending in 4357\n"
        "Statement Period 03/01/2025 - 03/31/2025\n"
        "Page %d of 12\n"
        "03/04  FSP*PILOT POINT FEED S PILOT POINT TX    -128.44\n"
        "03/05  CHECK 1042                               -250.00\n"
        "Customer service 1-800-555-0199\n"
        "%s"
    ) % (n, extra)
    return body + ("\n" + stamp if stamp else "")


pages = {n: page(n, "KF-%06d" % (140 + n)) for n in range(1, 13)}
s = detect(pages)
print("1. clean production")
check("detected", s is not None, True)
check("prefix", s.prefix, "KF")
check("digits", s.digits, 6)
check("first", s.first, "KF-000141")
check("last", s.last, "KF-000152")
check("gaps", s.gaps, [])
check("confidence", s.confidence, "high")
check("page 7 stamp", s.by_page[7], "KF-000147")

# ── 2. Two pages missing from the production ─────────────────────────────
partial = {n: page(n, "KF-%06d" % (140 + n)) for n in [1, 2, 3, 6, 7, 8, 9, 10, 11, 12]}
# Renumber so pages are consecutive but the Bates jumps — that IS the gap.
partial = {i + 1: page(i + 1, "KF-%06d" % v) for i, v in enumerate(
    [141, 142, 143, 146, 147, 148, 149, 150, 151, 152])}
s2 = detect(partial)
print("2. production with a hole")
check("detected", s2 is not None, True)
check("gaps found", s2.gaps if s2 else None, ["KF-000144", "KF-000145"])

# ── 3. Unstamped document: must NOT invent a series ──────────────────────
plain = {n: page(n) for n in range(1, 13)}
s3 = detect(plain)
print("3. no stamp at all")
check("returns None", s3, None)

# ── 4. Bare-number stamp, no prefix ──────────────────────────────────────
bare = {n: page(n, "%06d" % (500 + n)) for n in range(1, 13)}
s4 = detect(bare)
print("4. bare numeric stamp")
check("detected", s4 is not None, True)
check("prefix empty", s4.prefix if s4 else None, "")
check("first", s4.first if s4 else None, "000501")

# ── 5. Prefix hint disambiguates a competing series ──────────────────────
both = {n: page(n, "KF-%06d" % (140 + n), extra="DEF-%06d\n" % (900 + n))
        for n in range(1, 13)}
s5 = detect(both, prefix_hint="DEF-")
print("5. two series, hint picks one")
check("prefix honoured", s5.prefix if s5 else None, "DEF")
check("first", s5.first if s5 else None, "DEF-000901")

# ── 6. Some pages unstamped mid-run ──────────────────────────────────────
spotty = {n: page(n, None if n in (4, 5) else "KF-%06d" % (140 + n)) for n in range(1, 13)}
s6 = detect(spotty)
print("6. partially stamped")
check("detected", s6 is not None, True)
check("unstamped pages", s6.unstamped_pages if s6 else None, [4, 5])
check("no invented stamps", (4 in s6.by_page or 5 in s6.by_page) if s6 else None, False)
# Pages 4 and 5 are present, only unstamped. They must NOT be reported as
# production gaps - the page is here, we just could not read a stamp on it.
check("pages we hold are not gaps", s6.gaps if s6 else None, [])

# ── 7. split_pages ───────────────────────────────────────────────────────
marked = "<<<PAGE 1>>>\nalpha\n\n<<<PAGE 2>>>\nbeta"
sp = split_pages(marked)
print("7. page splitting")
check("two pages", sorted(sp), [1, 2])
check("page 1 text", sp[1].strip(), "alpha")
check("unmarked text yields nothing", split_pages("no markers here"), {})

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
