"""
app/services/diff_explainer_service.py - Summarize human edits to AI drafts.

When a staff member edits a draft before sending, ``lead_agent_runs`` records
both versions and flags ``human_edited=true``. This service compares the two
and produces a short prose explanation that an admin can scan to spot
patterns — "tone always softened on adoption inquiries", "fees consistently
removed", etc. — and feed back into the compose prompt over time.

Runs as a background task on the worker tick. Capped at a small batch per
tick so a backlog never overwhelms the LLM budget.
"""
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


_DIFF_EXPLAINER_SYSTEM = """\
You are reviewing an AI-drafted reply to a prospective client and the edited
version a staff member actually sent. Identify what changed and what it
suggests about how to improve future AI drafts.

In 2-3 sentences, plain text:
1. WHAT changed at a high level (tone shift, factual edits, scope adjustment,
   added/removed content, length, etc.)
2. CATEGORY of change (e.g. "tone", "factual correction", "scope reduction",
   "added information", "stylistic")
3. SIGNAL for prompt tuning — what this hint tells us going forward, or
   "no meaningful signal — minor stylistic edits" if there's nothing
   actionable.

Be concise. No markdown. No bullet lists. Just 2-3 sentences.
"""


class DiffExplainerService:

    def explain_diff(self, draft: str, sent: str) -> str:
        """Call the LLM to summarize what changed between draft and sent. Returns plain text."""
        user_msg = (
            "AI-DRAFTED REPLY:\n"
            "----- begin -----\n%s\n----- end -----\n\n"
            "STAFF-EDITED REPLY (what actually went out):\n"
            "----- begin -----\n%s\n----- end -----"
        ) % (draft or "(empty)", sent or "(empty)")
        return llm_service.complete_fast(_DIFF_EXPLAINER_SYSTEM, user_msg).strip()


diff_explainer_service = DiffExplainerService()
