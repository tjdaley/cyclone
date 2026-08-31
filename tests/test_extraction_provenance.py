"""
The statement records which model actually read it.

`extraction.profile` says what was *asked for*. It does not say who answered,
and on a long statement it cannot: the document is read in passes and the
failover chain is walked independently on each one, so a single statement can
be part Claude and part OpenAI — which is exactly what happened while Anthropic
was failing and every call fell through.

When a figure from one of these statements is put in front of a court, "which
tool produced it" has one right answer, and it is not the profile name.
"""
import sys

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

import services.statement_service as mod  # noqa: E402
from services.llm_service import LLMResult  # noqa: E402
from services.statement_service import statement_service  # noqa: E402

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class FakeLLM:
    """Serves each call from a scripted (vendor, model, attempts) sequence."""

    def __init__(self, script, last_page=9):
        self.script = list(script)
        self.last_page = last_page
        self.seen = 0

    def complete_detailed(self, system, body, profile=None, **kwargs):
        vendor, model, attempts = self.script[min(self.seen, len(self.script) - 1)]
        self.seen += 1
        if "indexing" in system:
            text = ('{"statements": [{"first_page": 1, "last_page": %d, "account": {},'
                    ' "period": {}, "balances": {}}]}' % self.last_page)
        elif "extracting transactions from PART" in system:
            text = '{"transactions": []}'
        else:
            text = '{"statements": [{"account": {}, "period": {}, "balances": {}, "transactions": []}]}'
        return LLMResult(text=text, profile=profile or "extract_account_statement",
                         vendor=vendor, model=model, attempts=attempts)


def document(page_count):
    out = []
    for page in range(1, page_count + 1):
        out.append("<<<PAGE %d>>>" % page)
        out.append("PAGE %d of %d" % (page, page_count))
    return "\n".join(out)


# ── 1. A short statement: one pass, one model ────────────────────────────
print("1. single-pass document")
mod.llm_service = FakeLLM([("anthropic", "claude-opus-5", 1)])
result = statement_service.extract(document(3))
prov = result["extraction"]
check("one pass recorded", len(prov["passes"]), 1)
check("names the vendor", prov["passes"][0]["vendor"], "anthropic")
check("names the model", prov["passes"][0]["model"], "claude-opus-5")
check("labels what it read", prov["passes"][0]["pass"], "whole document")
check("summary", prov["models_used"], ["anthropic/claude-opus-5"])
check("no failover", prov["failed_over"], False)

# ── 2. A long statement read entirely by the first choice ────────────────
print("2. multi-pass, one model throughout")
mod.llm_service = FakeLLM([("anthropic", "claude-opus-5", 1)], last_page=9)
prov = statement_service.extract(document(9))["extraction"]
# 9 pages: an index pass plus chunks 1-4, 5-8, 9.
check("four passes", len(prov["passes"]), 4)
check("index first", prov["passes"][0]["pass"], "index")
check("page ranges labelled",
      [p["pass"] for p in prov["passes"][1:]], ["pages 1-4", "pages 5-8", "pages 9-9"])
check("one model", prov["models_used"], ["anthropic/claude-opus-5"])
check("no failover", prov["failed_over"], False)

# ── 3. The real case: the chain falls through mid-document ───────────────
# Anthropic answered the index pass, then started failing; OpenAI read the
# rest. One name for the whole statement would be a guess.
print("3. two models on one statement")
mod.llm_service = FakeLLM([
    ("anthropic", "claude-opus-5", 1),
    ("openai", "gpt-5.4", 2),
    ("openai", "gpt-5.4", 2),
    ("gemini", "gemini-3.1-pro-preview", 3),
], last_page=9)
prov = statement_service.extract(document(9))["extraction"]
check("every pass attributed",
      [(p["vendor"], p["pass"]) for p in prov["passes"]],
      [("anthropic", "index"), ("openai", "pages 1-4"),
       ("openai", "pages 5-8"), ("gemini", "pages 9-9")])
check("summary lists each model once", prov["models_used"],
      ["anthropic/claude-opus-5", "openai/gpt-5.4", "gemini/gemini-3.1-pro-preview"])
check("failover recorded", prov["failed_over"], True)
check("attempts kept per pass", [p["attempts"] for p in prov["passes"]], [1, 2, 2, 3])

# ── 4. It survives into the statement's extraction column ────────────────
print("4. reaches the stored record")


class Row(dict):
    __getattr__ = dict.get


class FakeAccounts:
    def __init__(self): self.rows = []
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, i, l4): return None
    def others_with_last4(self, m, i, l4): return []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeStatements:
    def __init__(self): self.rows = []
    def get_by_account(self, a): return []
    def find_period(self, a, s, e): return None
    def find_overlapping(self, a, s, e): return []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


a, s, t = FakeAccounts(), FakeStatements(), FakeTransactions()
mod.FinancialAccountRepository = lambda m: a
mod.FinancialAccountStatementRepository = lambda m: s
mod.FinancialAccountTransactionRepository = lambda m: t

extracted = {
    "statements": [{
        "account": {"institution": "First Financial Bank", "account_type": "checking",
                    "account_number_last4": "9260"},
        "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},
        "balances": {"beginning_balance": 0, "ending_balance": 0},
        "transactions": [],
    }],
    "extraction": {
        "passes": [{"pass": "index", "vendor": "anthropic", "model": "claude-opus-5", "attempts": 1}],
        "models_used": ["anthropic/claude-opus-5"],
        "failed_over": False,
    },
}
statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1, extracted=extracted, raw_text="<<<PAGE 1>>>\nx\n",
)
stored = s.rows[0]["extraction"]
check("profile still recorded", stored["profile"], "extract_account_statement")
check("models recorded alongside it", stored["models_used"], ["anthropic/claude-opus-5"])
check("per-pass detail kept", stored["passes"][0]["model"], "claude-opus-5")
check("failover flag kept", stored["failed_over"], False)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
