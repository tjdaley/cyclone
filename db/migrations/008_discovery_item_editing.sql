-- 008_discovery_item_editing.sql
--
-- Adds:
--   1. response column to discovery_request_items
--   2. standard_privileges lookup table (seeded)
--   3. standard_objections lookup table (seeded with applies_to filter)

BEGIN;

-- 1. Add response column
ALTER TABLE discovery_request_items
    ADD COLUMN IF NOT EXISTS response TEXT;

-- 2. Standard privileges
CREATE TABLE IF NOT EXISTS standard_privileges (
    id    SERIAL PRIMARY KEY,
    slug  TEXT   NOT NULL UNIQUE,
    text  TEXT   NOT NULL
);

INSERT INTO standard_privileges (slug, text) VALUES
    ('attorney-client',     'The responding party objects to this request to the extent it calls for information protected by the attorney-client privilege.'),
    ('work-product',        'The responding party objects to this request to the extent it seeks materials protected by the attorney work product doctrine.'),
    ('spousal-privilege',   'The responding party objects to this request to the extent it calls for confidential communications between spouses.'),
    ('fifth-amendment',     'The responding party objects to this request on the grounds that a response may tend to incriminate the responding party, and therefore invokes the protections of the Fifth Amendment to the United States Constitution and Article I, Section 10 of the Texas Constitution.'),
    ('trade-secret',        'The responding party objects to this request to the extent it calls for information constituting a trade secret or other confidential commercial information protected under Texas Rule of Civil Procedure 76a.')
ON CONFLICT (slug) DO NOTHING;

-- 3. Standard objections
CREATE TABLE IF NOT EXISTS standard_objections (
    id          SERIAL  PRIMARY KEY,
    slug        TEXT    NOT NULL UNIQUE,
    applies_to  TEXT[]  NOT NULL DEFAULT '{"*"}',
    text        TEXT    NOT NULL
);

INSERT INTO standard_objections (slug, applies_to, text) VALUES
    ('relevance',           '{"*"}',
     'The responding party objects to this request on the grounds that it is not relevant to any party''s claims or defenses and is not proportional to the needs of this case.'),
    ('overbroad',           '{"*"}',
     'The responding party objects to this request as overbroad, unduly burdensome, and not proportional to the needs of this case.'),
    ('vague',               '{"*"}',
     'The responding party objects to this request as vague, ambiguous, and incapable of reasonably certain interpretation.'),
    ('unduly-burdensome',   '{"*"}',
     'The responding party objects to this request as unduly burdensome. The burden and expense of the proposed discovery outweighs its likely benefit, considering the needs of the case, the amount in controversy, the parties'' resources, the importance of the issues at stake, and the importance of the proposed discovery in resolving the issues.'),
    ('not-in-possession',   '{"interrogatories","production","admissions"}',
     'The responding party objects to this request to the extent it seeks information or documents not within the possession, custody, or control of the responding party.'),
    ('privacy',             '{"interrogatories","production"}',
     'The responding party objects to this request to the extent it calls for private, confidential, or sensitive personal information whose disclosure is not warranted under the circumstances of this case.'),
    ('compound',            '{"interrogatories","admissions"}',
     'The responding party objects to this request on the grounds that it is compound in nature and contains multiple discrete subparts, in violation of the Texas Rules of Civil Procedure.'),
    ('assumes-facts',       '{"interrogatories","admissions"}',
     'The responding party objects to this request on the grounds that it assumes facts not in evidence and is therefore misleading.'),
    ('calls-for-legal',     '{"interrogatories"}',
     'The responding party objects to this request to the extent it calls for a pure legal conclusion.'),
    ('equal-access',        '{"production"}',
     'The responding party objects to this request on the grounds that the documents sought are equally available to the requesting party through public records or other means.')
ON CONFLICT (slug) DO NOTHING;

COMMIT;
