"""
tests/test_wire_schema.py - The trimmed output schema, and what must not drift.

WHY THIS EXISTS. The wall clock of a statement import is its OUTPUT TOKENS
divided by about 114 per second, measured three independent ways, so the only
lever on how long an import takes is how many characters the model is asked to
write. Four fields were pure waste on a real 196-line extraction: `flags` at
23.4% with 196 of its 197 objects identical, `description_lines` at 16.2% and
byte-for-byte redundant, `bates_number` at 6.2% for 196 asks with six distinct
answers, and a pile of null-valued keys. Trimming them took the payload down
66.4%.

Three things have to hold, and each has a way to fail silently:

  * `_FLAG_SPEC` is the only place the codes are written down and the prompts
    are BUILT from it. Stating a set in Python and again in English inside a
    prompt is how `_CASH_FLOW_ACCOUNTS` drifted and got a policy statement read
    with bank rules; here it would ask for a code nothing can expand.
  * The reader never drops a flag, whatever shape it arrives in. One chain is
    three vendors complying to different degrees, and a flag records an
    inference somebody may have to answer for.
  * The stored record is unchanged. Normalisation happens at the parse
    boundary, so everything downstream sees what it always saw.

    venv/Scripts/python.exe tests/test_wire_schema.py

No network, no database, no test_data/ — every fixture is built here.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services import statement_service as s  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append(label)


def section(title):
    print()
    print(title)
    print("-" * len(title))


# ---------------------------------------------------------------------------
section("1. The prompts are built from _FLAG_SPEC and nothing survives unfilled")

for name in ("_STATEMENT_SYSTEM", "_TRANSACTIONS_SYSTEM", "_METADATA_SYSTEM"):
    prompt = getattr(s, name)
    left = re.findall(r"<<[A-Z_]+>>", prompt)
    check("%s has no unsubstituted sentinel" % name, not left, left)

for code in s._FLAG_SPEC:
    check("%s reaches the single-pass prompt" % code, code in s._STATEMENT_SYSTEM)
    check("%s reaches the chunk prompt" % code, code in s._TRANSACTIONS_SYSTEM)

check("the catalog lists every code",
      all(c in s._flag_catalog() for c in s._FLAG_SPEC))
check("the catalog carries each default note",
      all(note in s._flag_catalog() for _sev, _f, note in s._FLAG_SPEC.values()))

# A code the prompts name but the spec does not cannot be expanded: it would
# arrive as a bare string and be stored with a guessed severity.
named = set(re.findall(r"\b[A-Z][A-Z_]{6,}\b", s._flag_catalog()))
check("the catalog names no code outside the spec",
      named <= set(s._FLAG_SPEC), sorted(named - set(s._FLAG_SPEC)))

# A warn holds the WHOLE statement in the exceptions queue, so a code that
# describes an ordinary thing a bank prints must not be one. A checks-table row
# with no serial number is ordinary — an ARC or electronic check has none — and
# before this code existed the model reached for AMOUNT_UNCLEAR and parked a
# statement claiming a plainly legible -6,885.66 could not be read.
check("CHECK_NUMBER_NOT_PRINTED is info, not warn",
      s._FLAG_SPEC["CHECK_NUMBER_NOT_PRINTED"][0] == "info")

# A NOTE THAT ONLY SAYS WHAT A CODE IS FOR INVITES THE NEAREST-CODE MISTAKE.
# "the printed text ran off the page" was read as "ran onto a second line" and
# flagged 27 of 286 complete descriptions, because dropping `description_lines`
# left a multi-line entry with nowhere to be described. The note is what the
# model reads, so where a code has a plausible wrong use, the note must exclude
# it by name.
_truncated = s._FLAG_SPEC["DESCRIPTION_TRUNCATED"][2].lower()
check("DESCRIPTION_TRUNCATED's note excludes a merely multi-line entry",
      "several lines" in _truncated and "never" in _truncated, _truncated)
check("...and both prompts carry that exclusion",
      all("merely printed on several lines" in getattr(s, n)
          for n in ("_STATEMENT_SYSTEM", "_TRANSACTIONS_SYSTEM")))
check("...and it concerns check_number, not amount",
      s._FLAG_SPEC["CHECK_NUMBER_NOT_PRINTED"][1] == "check_number")
check("both prompts say not to call such an amount unclear",
      all("do not call it" in getattr(s, n).lower()
          for n in ("_STATEMENT_SYSTEM", "_TRANSACTIONS_SYSTEM")))

# Every code's field_path must be a column a transaction actually has, or the
# flag points at nothing and `_CORRECTABLE` cannot offer the fix.
for code, (sev, field, _note) in s._FLAG_SPEC.items():
    check("%s severity is info or warn" % code, sev in ("info", "warn"), sev)
    check("%s names a correctable field" % code,
          field is None or field in s._CORRECTABLE, field)


# ---------------------------------------------------------------------------
section("2. The prompts no longer pay for what we derive or discard")

for name in ("_STATEMENT_SYSTEM", "_TRANSACTIONS_SYSTEM"):
    prompt = getattr(s, name)
    check("%s does not ask for description_lines" % name,
          "description_lines" not in prompt)
    # Asserted on the SHAPE, not the prose. A prompt reworded or rewrapped must
    # not fail a test about what it asks for - that trap has been sprung here
    # before. What matters is that severity never appears as a key the model
    # fills: not as a bullet, and not inside a JSON example.
    asks = re.findall(r'(?:^\s*[-*]\s*|")severity"?\s*:', prompt, re.M)
    check("%s does not ask for a per-flag severity" % name, not asks, asks)
    check("%s does not ask for field_path" % name, "field_path" not in prompt)
    check("%s tells the model to omit nulls" % name,
          "OMIT ANY KEY" in prompt)

check("the chunk prompt forbids a Bates number outright",
      "Do NOT report a Bates number" in s._TRANSACTIONS_SYSTEM)

# NO COMPACT-JSON INSTRUCTION, AND THAT IS A MEASURED DECISION.
#
# Reasoning said it should save 25%: left to itself a model pretty-prints, and
# `indent=2` measures 1.34x minified on this schema. Tried in production on the
# same 27-page statement and it is a NO-OP - six of the seven chunk passes
# moved by +1.0% in total, and a 25% saving cannot hide inside that. Either the
# model already emits compact JSON or it ignores the instruction; either way the
# paragraph costs attention and buys nothing, so it is gone.
#
# Kept as a test so nobody re-derives the same plausible idea from the same
# plausible arithmetic. If it is ever tried again, compare PER CHUNK: the whole
# +8.8% on the total was one outlier pass, and run-to-run noise on it (+/-27%)
# is larger than the effect being looked for.
for name in ("_STATEMENT_SYSTEM", "_TRANSACTIONS_SYSTEM"):
    check("%s does not ask for compact JSON (measured no-op)" % name,
          "EMIT COMPACT JSON" not in getattr(s, name))
check("the single-pass prompt asks for Bates once per page",
      "bates_by_page" in s._STATEMENT_SYSTEM)


# ---------------------------------------------------------------------------
section("3. Every short key the prompts use has an alias (the drift guard)")

# The prompt's own bullet list is where the model learns the key names. A key
# written there with no entry in _LINE_ALIASES lands in the stored row under a
# name no column has, and PostgREST rejects the whole insert (PGRST204).
def bullet_keys(prompt, after):
    body = prompt.split(after, 1)[-1]
    body = body.split("SIGN RULE", 1)[0]
    return set(re.findall(r"^  - ([a-z_]+):", body, re.M))


for name, marker in (("_STATEMENT_SYSTEM", "Each entry has:"),
                     ("_TRANSACTIONS_SYSTEM", "Each transaction has:")):
    keys = bullet_keys(getattr(s, name), marker)
    check("%s names some keys" % name, len(keys) >= 8, sorted(keys))
    unknown = {k for k in keys
               if k not in s._LINE_ALIASES and k not in s._LINE_ALIASES.values()}
    check("%s uses no key without an alias or a column" % name,
          not unknown, sorted(unknown))


# ---------------------------------------------------------------------------
section("4. Three shapes of one flag converge; nothing is ever dropped")

want = {"code": "YEAR_INFERRED", "severity": "info",
        "field_path": "transaction_date",
        "note": "MM/DD printed; year from statement period"}
for label, shape in (
    ("bare code", ["YEAR_INFERRED"]),
    ("short object", [{"c": "YEAR_INFERRED", "n": want["note"]}]),
    ("legacy long object", [dict(want)]),
    ("short object, no note", [{"c": "YEAR_INFERRED"}]),
):
    check("%s expands to the stored shape" % label, s._expand_flags(shape) == [want],
          s._expand_flags(shape))

# An unrecognised code is kept rather than guessed at or discarded - the same
# argument as stop_reason_class mapping an unknown vendor word to `other`.
out = s._expand_flags([{"c": "SOMETHING_NEW", "n": "nobody has coded for this"}])
check("an unknown code survives", len(out) == 1 and out[0]["code"] == "SOMETHING_NEW")
check("an unknown code keeps its note", out and out[0]["note"] == "nobody has coded for this")
check("an unknown code gets no invented field_path", out and out[0]["field_path"] is None)
check("an unknown code is info, not warn", out and out[0]["severity"] == "info")

check("a severity the model insists on wins over the catalog",
      s._expand_flags([{"c": "YEAR_INFERRED", "severity": "warn"}])[0]["severity"] == "warn")

check("junk is dropped without raising",
      s._expand_flags([None, 7, {}, "", {"c": ""}, []]) == [])
check("a non-list of flags is an empty list",
      s._expand_flags("YEAR_INFERRED") == [] and s._expand_flags(None) == [])


# ---------------------------------------------------------------------------
section("5. Aliases expand, and a canonical key is never second-guessed")

wire = {"n": 4, "date": "2026-02-19", "dprov": "derived", "desc": "AMAZON MKTPLACE",
        "payee": "Amazon Marketplace", "amt": -37.88, "pg": 3, "chk": "2487",
        "bal": 100.5, "loc": "Plano TX", "posted": "2026-02-20", "f": ["YEAR_INFERRED"]}
got = s._normalize_line(wire)
for short, canonical in s._LINE_ALIASES.items():
    if short in wire:
        check("%-7s -> %s" % (short, canonical), canonical in got, got)
check("no short key survives normalisation",
      not (set(got) & set(s._LINE_ALIASES)), sorted(set(got) & set(s._LINE_ALIASES)))

both = s._normalize_line({"amount": -1.0, "amt": -999.0, "desc": "d"})
check("a canonical key wins over its alias", both["amount"] == -1.0, both)

check("a long-form row passes through untouched",
      s._normalize_line({"line_no": 9, "amount": -1.5, "description": "X"})["line_no"] == 9)
check("a non-dict line becomes nothing", s._normalize_line("nope") == {})
check("a non-list of lines becomes an empty list", s._normalize_lines(None) == [])


# ---------------------------------------------------------------------------
section("6. Bates is asked once per page and lands on every line of it")

by_page = {"3": "KF-000142", "4": "KF-000143"}
rows = s._normalize_lines(
    [{"n": 1, "pg": 3}, {"n": 2, "pg": 3}, {"n": 3, "pg": 4}, {"n": 4}], by_page)
check("both lines on page 3 carry its stamp",
      [r.get("bates_number") for r in rows[:2]] == ["KF-000142"] * 2, rows)
check("page 4 carries its own", rows[2].get("bates_number") == "KF-000143")
check("a line with no page carries none", not rows[3].get("bates_number"))
check("an unmapped page carries none",
      not s._normalize_lines([{"n": 1, "pg": 9}], by_page)[0].get("bates_number"))

# A stamp the model put on the line itself is not overwritten by the map.
kept = s._normalize_lines([{"n": 1, "pg": 3, "bates_number": "OWN-1"}], by_page)
check("a line's own stamp is not overwritten", kept[0]["bates_number"] == "OWN-1")
check("an integer page key works too",
      s._normalize_lines([{"n": 1, "pg": 3}], {3: "INT-1"})[0]["bates_number"] == "INT-1")

# The statement-level map is consumed, not stored: `bates_number` is the column.
doc = {"statements": [{"bates_by_page": dict(by_page),
                       "transactions": [{"n": 1, "pg": 3}]}]}
s._normalize_statements(doc)
check("bates_by_page is removed from the statement",
      "bates_by_page" not in doc["statements"][0])
check("...after reaching the line",
      doc["statements"][0]["transactions"][0]["bates_number"] == "KF-000142")
check("a statement with no map still normalises",
      s._normalize_statements({"statements": [{"transactions": [{"n": 1, "amt": -1}]}]})
      ["statements"][0]["transactions"][0]["amount"] == -1)
check("a non-dict answer passes through", s._normalize_statements([1, 2]) == [1, 2])


# ---------------------------------------------------------------------------
section("7. The trim is real, and the stored row is unchanged")

# A row in each shape: what we used to ask for, and what we ask for now.
LONG = {
    "line_no": 4, "transaction_date": "2026-02-19", "posted_date": None,
    "date_provenance": "derived",
    "description": "AMAZON MKTPLACE PMTS Amzn.com/bill WA Order 112-3581302",
    "description_lines": ["AMAZON MKTPLACE PMTS Amzn.com/bill WA", "Order 112-3581302"],
    "counterparty": "Amazon Marketplace", "location": None, "amount": -37.88,
    "running_balance": None, "physical_page_number": 3,
    "bates_number": "KF-000142", "check_number": None,
    "flags": [{"code": "YEAR_INFERRED", "severity": "info",
               "field_path": "transaction_date",
               "note": "MM/DD printed; year from statement period"}],
}
SHORT = {"n": 4, "date": "2026-02-19", "dprov": "derived",
         "desc": LONG["description"], "payee": "Amazon Marketplace",
         "amt": -37.88, "pg": 3, "f": ["YEAR_INFERRED"]}

long_chars = len(json.dumps([LONG] * 196, separators=(",", ":")))
short_chars = (len(json.dumps([SHORT] * 196, separators=(",", ":")))
               + len(json.dumps({"bates_by_page": {"3": "KF-000142"}}, separators=(",", ":"))))
saved = 100.0 * (long_chars - short_chars) / long_chars
print("     196 rows: %d chars -> %d chars  (%.1f%% smaller)"
      % (long_chars, short_chars, saved))
check("the trim is at least 55%", saved >= 55.0, "%.1f%%" % saved)

a = s._normalize_line(LONG)
b = s._normalize_line(SHORT, {"3": "KF-000142"})
READ = ("line_no", "transaction_date", "posted_date", "date_provenance", "description",
        "counterparty", "location", "amount", "running_balance",
        "physical_page_number", "bates_number", "check_number")
for field in READ:
    check("%-21s identical either way" % field,
          (a.get(field) or None) == (b.get(field) or None),
          "%r vs %r" % (a.get(field), b.get(field)))
check("flags identical either way", a["flags"] == b["flags"], (a["flags"], b["flags"]))

# Every field _commit_one reads must survive normalisation by name, or the
# insert silently stores a default.
missing = [f for f in READ if f not in s._LINE_ALIASES.values() and f not in a]
check("no field _commit_one reads goes missing", not missing, missing)


# ---------------------------------------------------------------------------
print()
if FAILURES:
    print("FAILURES: %d" % len(FAILURES))
    for f in FAILURES:
        print("  - %s" % f)
    sys.exit(1)
print("all wire-schema checks passed")
