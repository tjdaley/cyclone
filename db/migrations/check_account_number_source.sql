-- check_account_number_source.sql — diagnostic, not a migration. Read-only.
--
-- Which step produced a given account number?  Three can set it, and they leave
-- different traces on the STATEMENT — which is what makes this answerable after
-- a merge has deleted the account itself:
--
--   1. The statement extraction (the big LLM read).  Leaves NO flag when the
--      pattern agreed or found nothing.  Silence is its signature.
--   2. account_number_service.detect() — repetition across pages.  Leaves
--      ACCOUNT_NUMBER_DERIVED whose note says "printed on the most pages".
--   3. account_number_service.ask() — the narrow question to a fast model.
--      Also ACCOUNT_NUMBER_DERIVED, but the note says "re-reading the first
--      N page(s)".
--
-- ACCOUNT_NUMBER_CONFLICT and _AMBIGUOUS both mean the extraction's value was
-- KEPT while something disagreed with it, so the number came from step 1.
--
-- >>> Set the matter id: replace 1 in each "matter_id = 1" line.
-- >>> Set the bad last four: replace '0021' in query 2.
--
-- Run each query separately if the editor returns only the last result set.


-- 1. Every account-number decision on the matter, and which step made it.
SELECT
    s.id                                   AS statement_id,
    s.extraction ->> 'source_filename'     AS source_file,
    s.period_start,
    a.account_number_last4                 AS account_last4_now,
    f ->> 'code'                           AS flag,
    CASE
        WHEN f ->> 'note' LIKE '%printed on the most pages%'  THEN 'pattern (page repetition)'
        WHEN f ->> 'note' LIKE '%re-reading the first%'       THEN 'lookup (fast LLM, verified)'
        WHEN f ->> 'code' IN ('ACCOUNT_NUMBER_CONFLICT', 'ACCOUNT_NUMBER_AMBIGUOUS')
                                                              THEN 'statement extraction (kept, disputed)'
        ELSE 'unknown'
    END                                    AS produced_by,
    f ->> 'note'                           AS note
FROM financial_account_statements s
JOIN financial_accounts a ON a.id = s.financial_account_id
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.flags, '[]'::jsonb)) f
WHERE s.matter_id = 5
  AND f ->> 'code' LIKE 'ACCOUNT_NUMBER%'
ORDER BY s.period_start;


-- 2. THE ONE THAT ANSWERS THE QUESTION. Any flag whose note names a number
--    ending in the bad last four. The note quotes the full number it chose, so
--    a barcode picked by the pattern or by the lookup is named here even though
--    the account it created has since been merged away.
SELECT
    s.id                               AS statement_id,
    s.extraction ->> 'source_filename' AS source_file,
    f ->> 'code'                       AS flag,
    CASE
        WHEN f ->> 'note' LIKE '%printed on the most pages%' THEN 'pattern (page repetition)'
        WHEN f ->> 'note' LIKE '%re-reading the first%'      THEN 'lookup (fast LLM, verified)'
        ELSE 'statement extraction'
    END                                AS produced_by,
    substring(f ->> 'note' from '[0-9]{5,}') AS number_it_chose,
    f ->> 'note'                       AS note
FROM financial_account_statements s
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.flags, '[]'::jsonb)) f
WHERE s.matter_id = 5
  AND f ->> 'code' LIKE 'ACCOUNT_NUMBER%'
  AND f ->> 'note' ~ '[0-9]*0021\M'
ORDER BY s.id;


-- 3. If query 2 returns nothing, the number came from the statement extraction
--    itself, unchallenged — no flag is written when nothing disagreed. These
--    are the statements on this matter with no account-number flag at all, so
--    whatever last4 they carry came straight out of the big LLM read.
--
--    NOTE: after a merge the barcode-derived account row is gone, so there is
--    no surviving record of the value it held. Its identity is established by
--    elimination — query 2 empty plus the statement listed here.
SELECT
    s.id                               AS statement_id,
    s.extraction ->> 'source_filename' AS source_file,
    s.period_start,
    s.period_end,
    a.account_number_last4             AS account_last4_now
FROM financial_account_statements s
JOIN financial_accounts a ON a.id = s.financial_account_id
WHERE s.matter_id = 5
  AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(COALESCE(s.flags, '[]'::jsonb)) f
      WHERE f ->> 'code' LIKE 'ACCOUNT_NUMBER%'
  )
ORDER BY s.period_start;


-- 4. Is the number ending 0021 even in the stored text, and how does it behave?
--    A barcode changes on every statement and prints on one page; an account
--    number repeats. Seeing several different ...0021 values across statements
--    confirms a barcode rather than a real account.
SELECT
    s.id                               AS statement_id,
    s.extraction ->> 'source_filename' AS source_file,
    (SELECT array_agg(DISTINCT m[1])
       FROM regexp_matches(s.raw_text, '([0-9]{6,20}0021)', 'g') AS m) AS runs_ending_0021
FROM financial_account_statements s
WHERE s.matter_id = 5
  AND s.raw_text ~ '[0-9]{6,20}0021'
ORDER BY s.period_start;
