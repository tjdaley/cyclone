-- check_chase_extraction.sql — diagnostic, not a migration. Read-only.
--
-- Which model actually read the statements whose account number came back
-- unreadable? The profile name says what was ASKED for; extraction.models_used
-- says who ANSWERED.
--
-- >>> Set the matter id: replace 1 in each "matter_id = 1" line below.
--
-- Plain literals rather than \set — that is a psql client meta-command and the
-- Supabase SQL editor is not psql. Run each query separately if the editor
-- returns only the last result set.
--
-- READING QUERY 1: a.account_number_last4 is the account as it stands NOW,
-- while NO_ACCOUNT_MATCH is a flag written on the statement AT INGEST. A row
-- showing a number therefore means "this extraction failed and was repaired
-- afterwards", not "this one worked". Query 4 is the one that separates them.


-- 1. Every statement carrying NO_ACCOUNT_MATCH, and who read it.
SELECT
    s.id                                            AS statement_id,
    a.institution,
    a.account_number_last4                          AS account_last4_now,
    s.period_start,
    s.period_end,
    s.reconciled,
    s.extraction -> 'models_used'                   AS models_used,
    s.extraction ->> 'failed_over'                  AS failed_over,
    jsonb_array_length(COALESCE(s.extraction -> 'passes', '[]'::jsonb)) AS passes,
    s.extraction #>> '{passes,0,attempts}'          AS first_pass_attempts,
    s.extraction ->> 'source_filename'              AS source_filename
FROM financial_account_statements s
JOIN financial_accounts a ON a.id = s.financial_account_id
WHERE s.matter_id = 1
  AND s.flags @> '[{"code": "NO_ACCOUNT_MATCH"}]'::jsonb
ORDER BY s.period_start;


-- 2. Grouped by who read it, with how many lost the account number.
SELECT
    s.extraction -> 'models_used'                                  AS models_used,
    COUNT(*)                                                       AS statements,
    COUNT(*) FILTER (WHERE a.account_number_last4 IS NULL)          AS missing_last4,
    COUNT(*) FILTER (WHERE s.extraction ->> 'failed_over' = 'true') AS failed_over
FROM financial_account_statements s
JOIN financial_accounts a ON a.id = s.financial_account_id
WHERE s.matter_id = 1
GROUP BY 1
ORDER BY statements DESC;


-- 3. Accounts opened with no number — the debris a null last4 leaves behind.
SELECT
    a.id,
    a.institution,
    a.account_number_masked,
    a.name_on_account,
    COUNT(s.id)         AS statements,
    MIN(s.period_start) AS earliest,
    MAX(s.period_end)   AS latest
FROM financial_accounts a
LEFT JOIN financial_account_statements s ON s.financial_account_id = a.id
WHERE a.matter_id = 1
  AND a.account_number_last4 IS NULL
GROUP BY a.id, a.institution, a.account_number_masked, a.name_on_account
ORDER BY a.id;


-- 4. THE ONE THAT SETTLES IT. Every statement by source file, saying whether
--    the EXTRACTION produced an account number — not whether the account has
--    one today. Sorted so each file's successes and failures sit together.
--
--    If a file's name (x4448 / x5410) shows both "read" and "failed" rows, the
--    failure is intermittent within one account, and coalescing has a
--    successful read to draw on. If every x5410 row says "failed", there is
--    nothing to coalesce from and the number has to come out of raw_text.
SELECT
    COALESCE(s.extraction ->> 'source_filename', '(no filename)') AS source_file,
    CASE WHEN s.flags @> '[{"code": "NO_ACCOUNT_MATCH"}]'::jsonb
         THEN 'failed' ELSE 'read' END                            AS extraction_read_the_number,
    COUNT(*)                                                      AS statements,
    MIN(s.period_start)                                           AS earliest,
    MAX(s.period_end)                                             AS latest,
    array_agg(DISTINCT a.account_number_last4)                    AS accounts_now
FROM financial_account_statements s
JOIN financial_accounts a ON a.id = s.financial_account_id
WHERE s.matter_id = 1
GROUP BY 1, 2
ORDER BY source_file, extraction_read_the_number;


-- 5. Does the account number still exist in the stored text? If this returns
--    the number for statements whose extraction failed, the repair needs no
--    re-upload and no LLM call — raw_text is kept on every statement.
--
--    The pattern: the digit run that repeats on EVERY page of the statement.
--    Nothing else on a bank statement behaves that way — a barcode, a mail
--    routing line, and a transaction reference each appear once.
SELECT
    s.id                                  AS statement_id,
    s.extraction ->> 'source_filename'    AS source_file,
    length(s.raw_text)                    AS raw_text_chars,
    (SELECT array_agg(DISTINCT m[1])
       FROM regexp_matches(s.raw_text, '(\d{10,20})', 'g') AS m) AS long_digit_runs
FROM financial_account_statements s
WHERE s.matter_id = 1
  AND s.flags @> '[{"code": "NO_ACCOUNT_MATCH"}]'::jsonb
ORDER BY s.period_start;
