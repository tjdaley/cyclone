-- 030_matter_caption.sql
--
-- The two pieces of a court caption that Cyclone could not previously state.
--
-- Everything else the heading of an exhibit needs is already on `matters`:
-- matter_number is the cause number, and court_name, county, and state give the
-- second line. These two were missing:
--
--   case_style        The formal style of the case, as it is written on a filing:
--                     "IN THE MATTER OF THE MARRIAGE OF JANE DOE AND JOHN DOE".
--                     matter_name is the internal short name ("Doe divorce") and
--                     is not the same string — a caption that reads like a file
--                     folder label is wrong on a document handed to a court.
--
--   client_alignment  Which side we are on, in the words the caption uses. It is
--                     what makes an exhibit "Petitioner's Financial Summary"
--                     rather than an untitled table. `designation` is captured
--                     at intake for OPPOSING parties only (stored on
--                     matter_opposing_parties.relationship); our own client's
--                     side of that answer had nowhere to live.
--
-- Both are nullable. Matters opened before this migration have neither, and a
-- pre-suit matter may never have a cause number at all — the exhibit renderer
-- prints a signature-line blank and reports what is missing rather than
-- refusing to build or printing the word "None" onto a court document.
--
-- Run after 029.

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS case_style       text,
    ADD COLUMN IF NOT EXISTS client_alignment text;

-- The vocabulary of a caption, across the matter types Cyclone handles: family
-- (petitioner/respondent, and the counter- forms that appear once a counter-
-- petition is filed), civil (plaintiff/defendant), and the probate and
-- post-judgment forms. 'other' is the escape hatch, and NULL means nobody has
-- said yet.
--
-- A CHECK is invisible to util/schema_check.py, which compares columns only. If
-- this list needs widening later, that is its own migration — see 016, which
-- widened the matter_opposing_counsel role CHECK for exactly this reason.
ALTER TABLE matters
    DROP CONSTRAINT IF EXISTS matters_client_alignment_check;

ALTER TABLE matters
    ADD CONSTRAINT matters_client_alignment_check CHECK (
        client_alignment IS NULL OR client_alignment IN (
            'petitioner',
            'respondent',
            'counter_petitioner',
            'counter_respondent',
            'intervenor',
            'plaintiff',
            'defendant',
            'applicant',
            'movant',
            'other'
        )
    );

COMMENT ON COLUMN matters.case_style IS
    'Formal style of the case as written on a filing. Distinct from matter_name, '
    'which is the internal short name.';

COMMENT ON COLUMN matters.client_alignment IS
    'Which side our client is on, in caption vocabulary. Titles every exhibit '
    'generated for this matter.';
