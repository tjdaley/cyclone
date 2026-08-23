-- 019_clients_referred_to_staff.sql
-- Adds the referral-routing column the Client model has always declared.
--
-- db/models/client.py and schemas/client.py both carry referred_to_staff_id,
-- so every insert sends it — Pydantic's model_dump() includes a field even when
-- it is None. PostgREST rejects the whole row for an unknown column, which
-- broke BOTH client-creation paths:
--
--   POST /api/v1/clients               (the Clients page)
--   POST /api/v1/matters/intake/commit (opening a file from a pleading)
--
-- with: "Could not find the 'referred_to_staff_id' column of 'clients' in the
-- schema cache" (PGRST204).
--
-- ON DELETE SET NULL: removing a staff member should not block deletion or
-- orphan the client — the referral history simply loses its target.

alter table clients
    add column if not exists referred_to_staff_id integer
        references staff (id) on delete set null;

-- Supports "which clients were referred to this attorney".
create index if not exists idx_clients_referred_to_staff
    on clients (referred_to_staff_id);

-- PostgREST caches the schema; without this the column stays invisible to the
-- API until the connection pool happens to refresh.
notify pgrst, 'reload schema';
