"""
Reading an Anthropic reply, and knowing which models take a temperature.

Both of these silently pushed every Anthropic call onto the next vendor.

  * `response.content[0].text` assumed the answer was the first block. A model
    with extended thinking returns its reasoning first, so the opening block is
    a ThinkingBlock with no `text` at all. The AttributeError was caught by the
    failover, logged as a warning, and read as "the vendor failed" — when the
    answer was sitting in the very next block.

  * `"-5" in model` was meant to spot the 5.x family, which rejects
    `temperature`. It also matches `claude-haiku-4-5-20251001`, a 4.5 model
    that takes temperature fine. That one does not error: it quietly drops the
    setting on tasks that ask for 0.0, so a guardrail stops being
    deterministic and nothing says so.
"""
import sys

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from services.llm_service import _anthropic_takes_temperature, _anthropic_text  # noqa: E402

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Block:
    """Stands in for an SDK content block; a thinking block has no `text`."""

    def __init__(self, type_, text=None):
        self.type = type_
        if text is not None:
            self.text = text


class Response:
    def __init__(self, *blocks):
        self.content = list(blocks)


# ── 1. The reply that was being thrown away ──────────────────────────────
print("1. extended thinking")
reply = Response(Block("thinking"), Block("text", '{"statements": []}'))
check("reads past the thinking block", _anthropic_text(reply), '{"statements": []}')

print("2. an ordinary reply")
check("plain text still works",
      _anthropic_text(Response(Block("text", "hello"))), "hello")

print("3. several text blocks")
check("joined in order",
      _anthropic_text(Response(Block("text", "abc"), Block("text", "def"))), "abcdef")

print("4. blocks that are not text at all")
check("redacted thinking skipped",
      _anthropic_text(Response(Block("redacted_thinking"), Block("text", "x"))), "x")
check("no text block reads as empty",
      _anthropic_text(Response(Block("thinking"))), "")
check("an empty response reads as empty", _anthropic_text(Response()), "")

# ── 5. Which models take a temperature ───────────────────────────────────
print("5. temperature support by model")
for model, want in [
    ("claude-opus-5", False),
    ("claude-sonnet-5", False),
    ("claude-fable-5", False),
    # 4.5 and 4.6 are not the 5.x family, whatever substring they contain.
    ("claude-haiku-4-5-20251001", True),
    ("claude-opus-4-6", True),
    ("claude-opus-4-6-lite", True),
    ("claude-3-5-sonnet-20241022", True),
]:
    check("%-30s -> %s" % (model, want), _anthropic_takes_temperature(model), want)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
