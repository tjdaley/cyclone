-- 022_discovery_item_parent.sql
-- Makes the discovery hierarchy real: matter -> document -> item -> response.
--
-- Two problems, both left over from 007 splitting discovery_requests into a
-- parent document table plus items:
--
--   1. discovery_request_items.discovery_request_id is NULLABLE, so an item can
--      exist with no parent document. The hierarchy is a convention, not a rule.
--   2. matter_id is denormalized onto items for fast matter-level filtering
--      (get_by_matter, get_pending_client). Useful, but nothing stops it from
--      disagreeing with the parent document's matter_id.
--
-- Rather than drop the shortcut, this makes it provably correct: a composite
-- foreign key on (discovery_request_id, matter_id) means an item's matter_id
-- MUST equal its document's. The convenient column can no longer lie, and the
-- single-table queries in DiscoveryRequestItemRepository keep working.
--
-- Verified safe against current data before writing this: 56 items, 0 with a
-- null parent, 0 whose matter_id disagrees with its document.

-- 1. Every item belongs to a document.
alter table discovery_request_items
    alter column discovery_request_id set not null;

-- 2. A composite FK needs a matching unique key on the parent.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'discovery_requests_id_matter_uniq'
    ) then
        alter table discovery_requests
            add constraint discovery_requests_id_matter_uniq unique (id, matter_id);
    end if;
end $$;

-- 3. Replace the single-column parent FK with the composite one, so matter_id
--    is checked against the document rather than merely against matters.
alter table discovery_request_items
    drop constraint if exists discovery_request_items_discovery_request_id_fkey;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'discovery_request_items_parent_fkey'
    ) then
        alter table discovery_request_items
            add constraint discovery_request_items_parent_fkey
                foreign key (discovery_request_id, matter_id)
                references discovery_requests (id, matter_id)
                on delete cascade;
    end if;
end $$;

-- matter_id still points at matters through its own FK
-- (discovery_requests_matter_id_fkey), which stays in place: deleting a matter
-- must still be blocked or cascade as it does today.

notify pgrst, 'reload schema';
