"""
app/services/kb_retrieval_service.py - Stable retrieval seam for the compose agent.

The compose pipeline calls retrieve_context() with the PNC's message and any
extracted issues; this service returns the KB fragments that are relevant plus
an ``answerable`` flag.

Phase 1 implementation: load the entire active KB into the retrieval agent's
own system prompt and let the LLM extract what's relevant per issue. When the
KB outgrows the context window — or when you want tool calls (e.g. "next
court hearing" against live matter data) — swap the implementation behind
this interface; the rest of the pipeline (compose, guardrail, send) doesn't
notice.

Fails safe: any LLM/parse error returns answerable=False so the compose
pipeline escalates rather than hallucinating.
"""
import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from db.models.kb_article import KbArticleInDB
from db.repositories.kb_article import KbArticleRepository
from db_handler import DatabaseManager
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class KbRetrievalResult(BaseModel):
    """What the retrieval seam returns to the compose pipeline."""
    answerable: bool = Field(..., description="True if at least one issue can be addressed from the current KB")
    fragments: list[str] = Field(default_factory=list, description="KB snippets ready to drop into the compose prompt")
    unanswerable_issues: list[str] = Field(default_factory=list, description="Issues the retrieval agent says aren't covered")
    notes: Optional[str] = Field(default=None, description="Free-form observations from the retrieval agent (for the run trace)")


_RETRIEVAL_SYSTEM = """\
You are a knowledge-base retrieval assistant for a law firm's CRM email agent.

The firm's KB is provided below as a series of articles. Your job: read the
prospective client's (PNC) message + the extracted issues, and return a JSON
object identifying:

1. Which KB content is RELEVANT to answering the message (paraphrased excerpts
   are fine; keep them short and faithful to the source).
2. Whether the questions in the message can be answered AT ALL from the KB.
   Substantive legal questions ("what are my chances", "is this enforceable",
   "should I do X") are NOT something the KB can answer — list those under
   unanswerable_issues so they get escalated to an attorney.

Hard rules:
- Do not invent information not in the KB.
- Be selective: only include fragments that genuinely help answer the message.
- Returning zero fragments is fine if nothing in the KB applies.
- ``answerable`` should be true ONLY if at least one issue can be substantively
  addressed from the KB. A message that is ENTIRELY substantive legal
  questions → answerable=false.

Respond ONLY with a JSON object, no markdown:
{
  "answerable": <bool>,
  "fragments": ["<short relevant excerpt>", ...],
  "unanswerable_issues": ["<issue text>", ...],
  "notes": "<one sentence or null>"
}
"""


def _strip_markdown_fences(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def _build_kb_block(articles: list[KbArticleInDB]) -> str:
    """Concatenate active KB articles into a single retrieval-prompt block."""
    if not articles:
        return "(no KB articles configured)"
    sections: list[str] = []
    for a in articles:
        title = a.topic if not a.subtopic else "%s › %s" % (a.topic, a.subtopic)
        sections.append("### %s\n%s" % (title, a.body_md))
    return "\n\n".join(sections)


class KbRetrievalService:

    def retrieve_context(
        self,
        cyclone_db: DatabaseManager,
        message_text: str,
        issues: list[str],
    ) -> KbRetrievalResult:
        """Return KB context relevant to a PNC message. Fail-safe."""
        articles = KbArticleRepository(cyclone_db).list_active()
        if not articles:
            LOGGER.warning("kb_retrieval: no active KB articles; can't answer anything")
            return KbRetrievalResult(
                answerable=False,
                unanswerable_issues=issues or [message_text[:200]],
                notes="No active KB articles configured.",
            )

        kb_block = _build_kb_block(articles)
        issues_block = (
            "\n".join("- %s" % i for i in issues)
            if issues else "(none extracted; treat the whole message as the question)"
        )
        user_msg = (
            "PROSPECTIVE CLIENT MESSAGE:\n"
            "----- begin -----\n%s\n----- end -----\n\n"
            "ISSUES EXTRACTED FROM THE MESSAGE:\n%s\n\n"
            "KNOWLEDGE BASE:\n%s"
        ) % (message_text or "(empty)", issues_block, kb_block)

        try:
            response = llm_service.complete(_RETRIEVAL_SYSTEM, user_msg, profile="select_kb_articles")
        except Exception as e:  # noqa: BLE001 — never let an LLM error break the pipeline
            LOGGER.error("kb_retrieval: LLM call failed err=%s", str(e))
            return KbRetrievalResult(
                answerable=False,
                unanswerable_issues=issues or [message_text[:200]],
                notes="Retrieval LLM error: %s" % str(e),
            )

        try:
            obj = json.loads(_strip_markdown_fences(response))
        except json.JSONDecodeError:
            LOGGER.warning("kb_retrieval: non-JSON response")
            return KbRetrievalResult(
                answerable=False,
                unanswerable_issues=issues or [message_text[:200]],
                notes="Retrieval JSON parse failed.",
            )

        return KbRetrievalResult(
            answerable=bool(obj.get("answerable")),
            fragments=[str(f) for f in (obj.get("fragments") or [])],  # type: ignore[no-any-return]
            unanswerable_issues=[str(i) for i in (obj.get("unanswerable_issues") or [])],  # type: ignore[no-any-return]
            notes=obj.get("notes"),
        )


kb_retrieval_service = KbRetrievalService()
