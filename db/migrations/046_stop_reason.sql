-- 046_stop_reason.sql
--
-- Record WHY a vendor stopped generating.
--
-- RUN BY HAND, after 045. Safe to run before or after deploying the code that
-- writes these columns: code that finds them missing records usage without them
-- and says so at ERROR, naming this file. Safe to run twice.
--
-- ── THE QUESTION THE LEDGER COULD NOT ANSWER ─────────────────────────────────
--
-- "The extraction came back short -- did the model run out of room, or did it
-- decide it was done?" Those want opposite fixes: a bigger ceiling, or smaller
-- chunks. Until now the only evidence was output_tokens against a max_tokens
-- that lives in a JSON file inside the image, which is an inference, not a fact.
--
-- Every vendor states it outright and we were throwing it away. It is read in
-- the same place as the served model, from the same response:
--
--   Anthropic   response.stop_reason                     end_turn | max_tokens | refusal | ...
--   OpenAI-ish  response.choices[0].finish_reason        stop | length | content_filter | ...
--   Gemini      response.candidates[0].finish_reason     STOP | MAX_TOKENS | SAFETY | ...
--               response.prompt_feedback.block_reason    when a prompt is blocked outright
--
-- ── WHY TWO COLUMNS ──────────────────────────────────────────────────────────
--
-- `stop_reason` is the vendor's own word, verbatim and lowercased, never
-- interpreted. Same argument as usage_raw: these field names were written
-- against the SDKs as documented, and SDKs drift, so the vendor's statement is
-- kept so a wrong reading can be recomputed over history.
--
-- `stop_reason_class` is that word reconciled across three vocabularies into
-- the four answers anybody actually queries:
--
--   complete   the model finished saying what it had to say
--   truncated  it hit the output ceiling (max_tokens / length / MAX_TOKENS)
--   refused    content filters stopped it (refusal / content_filter / SAFETY,
--              PROHIBITED_CONTENT, BLOCKLIST, SPII, RECITATION)
--   other      anything else, including a value no vendor documented yet
--
-- Without the class, "show me the calls that truncated" is a query you can only
-- write if you already know all three spellings -- which is the same trap as
-- summing raw token counts across vendors (CLAUDE.md §11).
--
-- ── 'refused' IS NOT A BONUS, IT IS A BLIND SPOT CLOSING ─────────────────────
--
-- Gemini's filters decline material describing crimes against minors, which in
-- family law is a normal Tuesday: a petition alleging abuse, a protective-order
-- affidavit, a CPS record. CLAUDE.md §11 records that the refusal is INVISIBLE
-- -- the failover simply serves the next model and nothing anywhere says why.
-- This is the column that makes it countable, which matters for a vendor that
-- sits last in every chain precisely because of it.
--
-- ── THE VIEWS ARE DELIBERATELY NOT TOUCHED ───────────────────────────────────
--
-- llm_call_costs and llm_unpriced_meters select explicit column lists, so these
-- columns do not appear there. That is intended: a stop reason is a diagnostic,
-- not a cost dimension, and reshaping both views (which means restating all of
-- 042's view body) to carry a field no price depends on is a large surface for
-- no gain. Query llm_calls directly:
--
--   select vendor, served_model, stop_reason, count(*)
--     from llm_calls
--    where stop_reason_class = 'truncated'
--      and occurred_at >= now() - interval '7 days'
--    group by 1, 2, 3 order by 4 desc;
--
-- Unlike 042, this migration therefore creates no ordering hazard with 041.

begin;

alter table llm_calls add column if not exists stop_reason       text;
alter table llm_calls add column if not exists stop_reason_class text;

-- Only the rows worth looking for. A partial index because the overwhelming
-- majority of calls are 'complete', and nobody queries for those.
create index if not exists idx_llm_calls_stop_reason_class
    on llm_calls (stop_reason_class, occurred_at)
    where stop_reason_class in ('truncated', 'refused', 'other');

commit;
