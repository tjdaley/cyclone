-- 017_discovery_client_instructions.sql
-- Attorney instructions to the client for a single discovery request.
--
-- Distinct from `response`, which is the formal answer served on the other
-- side. This is internal work-product telling the client what to gather and
-- what not to bother with, e.g. "Produce bank statements but do not pull the
-- check registers — I am going to object to that part."
--
-- It must never appear in the Word export of discovery responses.

alter table discovery_request_items
    add column if not exists instructions_to_client text;
