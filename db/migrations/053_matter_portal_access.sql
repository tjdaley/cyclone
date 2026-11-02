-- 053_matter_portal_access.sql
--
-- A per-matter switch for the client portal. Portal access itself is per client
-- (a row in user_roles); this says whether that client may see THIS matter.
--
-- Closing a case should not have to mean revoking the person: a client whose
-- divorce is closed may be given their old accounts again for a later
-- modification or enforcement, and may not. So staff are asked when a matter is
-- closed, and the answer lives on the matter, reversible either way.
--
-- Defaults to true, so no client loses anything by this running, and a matter
-- already closed stays visible until somebody turns it off.
--
-- RUN BY HAND, before deploying the code that reads it. Safe to run twice.

alter table public.matters
    add column if not exists client_portal_enabled boolean not null default true;

notify pgrst, 'reload schema';
