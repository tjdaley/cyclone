-- 015_pleading_status.sql
-- Explicit lifecycle state for a pleading.
--
-- Until now "live" was only a convention: the frontend inferred it by checking
-- whether some other pleading amended this one. That inference cannot express a
-- withdrawn or otherwise inactive pleading at all, and it also treated every
-- supplement as not-live even though a supplement ADDS to the live pleading
-- rather than replacing it.
--
-- status is maintained two ways:
--   * commit_ingest marks the amended pleading 'superseded' automatically when
--     a new pleading declares amends_pleading_id
--   * an attorney sets any value by hand via PATCH /api/v1/pleadings/{id}
--
-- The backfill marks anything already superseded by an existing amendment.

alter table matter_pleadings
    add column if not exists status text not null default 'live';

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'matter_pleadings_status_check'
    ) then
        alter table matter_pleadings
            add constraint matter_pleadings_status_check
            check (status in ('live', 'superseded', 'withdrawn', 'inactive'));
    end if;
end $$;

update matter_pleadings p
   set status = 'superseded'
 where p.status = 'live'
   and exists (
       select 1 from matter_pleadings a where a.amends_pleading_id = p.id
   );

create index if not exists idx_matter_pleadings_status
    on matter_pleadings (matter_id, status);
