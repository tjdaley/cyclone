-- 035_production_scope.sql
--
-- Who has to produce an account, and how far back.
--
-- A compliance matrix without these answers a question nobody asked. "Which
-- months are missing" is meaningless on its own — missing against what? The
-- obligation is set by a discovery request, and there are two of them running
-- in opposite directions:
--
--   * Opposing counsel propounds on us: produce everything since 1/1/2002.
--     That governs the accounts WE are responsible for.
--   * We propound on them: produce everything since 1/1/2023. That governs the
--     accounts THEY are responsible for.
--
-- Same grid, two reports, and each account belongs to one of them — or, when
-- both sides were ordered to produce it, to both.
--
-- WHY THE DATES LIVE ON THE MATTER. discovery_documents.look_back_date already
-- holds the inbound one: it is extracted from the preamble when a request is
-- ingested, and that module models only discovery "served on our client by
-- opposing counsel". There is no model for what we propound, so the outbound
-- date has nowhere to live — and neither date is always in a document anyway,
-- since scope is settled by agreement at least as often as by a request. An
-- explicit field on the matter is available in every case. Pre-filling the
-- inbound one from an ingested request is a later convenience, not a
-- substitute.
--
-- WHY RESPONSIBILITY IS NOT DERIVED FROM ownership. They usually agree and
-- sometimes do not: a joint account both sides were ordered to produce, an
-- account the other party holds whose statements we have and they do not.
-- Ownership decides how an asset divides; responsibility decides whose motion
-- to compel it is. Deriving one from the other would be a legal conclusion
-- drawn by a schema, so it defaults to 'unknown' and a person sets it — the
-- same rule financial_accounts.ownership already follows.
--
-- Run after 034.

-- ── How far back each side must go ───────────────────────────────────────

alter table matters
    add column if not exists client_produces_since   date,
    add column if not exists opposing_produces_since date;

-- Named for WHO PRODUCES, not for who asked. The other way round reads
-- backwards at every call site: the date opposing counsel propounded is the one
-- that binds our client, and a column called "opposing_lookback" holding it
-- would be misread by everyone including its author.
comment on column matters.client_produces_since is
    'Earliest date OUR client must produce documents for — the look-back in the request '
    'opposing counsel served on us. Bounds the compliance matrix for accounts we are '
    'responsible for. Null means no scope has been recorded, and the matrix falls back to '
    'the range the data itself covers.';

comment on column matters.opposing_produces_since is
    'Earliest date the OTHER side must produce documents for — the look-back in the request '
    'we served on them. Bounds the compliance matrix for accounts they are responsible for.';


-- ── Whose obligation each account is ─────────────────────────────────────

alter table financial_accounts
    add column if not exists production_responsibility text not null default 'unknown';

alter table financial_accounts
    drop constraint if exists financial_accounts_production_responsibility_check;

alter table financial_accounts
    add constraint financial_accounts_production_responsibility_check check (
        production_responsibility in ('client', 'opposing', 'both', 'unknown')
    );

comment on column financial_accounts.production_responsibility is
    'Who must produce statements for this account: client, opposing, both, or unknown. '
    'Selects which compliance report the account appears on and therefore which look-back '
    'date bounds it. Defaults to unknown and is set by a person — it usually follows '
    'ownership and sometimes does not, and which it is decides whose motion to compel it is.';

-- The matrix filters on it per report.
create index if not exists idx_accounts_production_responsibility
    on financial_accounts (matter_id, production_responsibility);
