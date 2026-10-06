-- 043_cost_report_by_firm.sql
--
-- Spend grouped by FIRM.
--
-- RUN BY HAND, after 042. Safe to run twice. Nothing else changes: same
-- function signature, same return type, one more value for p_group.
--
-- ── WHY, WITH ONE FIRM ───────────────────────────────────────────────────────
--
-- `llm_calls.firm_id` has been on every row since 039, precisely because
-- attribution cannot be added after the fact. This makes it readable. Today it
-- answers with one row; the day a second firm exists it is the first question
-- anybody asks, and the figures for the months before that day are already
-- correct rather than being backfilled from a guess.
--
-- It is also the grouping that says what the OTHER groupings mean. "Spend by
-- matter" across two firms is a list nobody can act on without knowing whose
-- matters they are.
--
-- ── THE LABEL COMES FROM `firms`, NOT FROM THE LEDGER ────────────────────────
--
-- A firm can be renamed, and a cost report re-run after a rename should say the
-- name the firm has now -- the id is what identifies it, which is why group_key
-- is the id and not the name. Same reasoning as the matter and staff groupings.

begin;

create or replace function llm_cost_report(
    p_from   timestamptz,
    p_to     timestamptz,
    p_group  text
) returns table (
    month                     date,
    group_key                 text,
    group_label               text,
    calls                     bigint,
    failed_calls              bigint,
    no_usage_calls            bigint,
    unpriced_calls            bigint,
    input_uncached_tokens     bigint,
    input_cache_read_tokens   bigint,
    input_cache_write_tokens  bigint,
    output_tokens             bigint,
    priced_cost               numeric,
    priced_cost_precise       numeric
)
language plpgsql
stable
as $$
begin
    if p_group not in ('firm', 'vendor_model', 'profile', 'matter', 'staff', 'source') then
        raise exception 'Unknown grouping %; expected firm, vendor_model, profile, matter, staff or source',
            p_group using errcode = 'invalid_parameter_value';
    end if;

    return query
    select
        (date_trunc('month', v.occurred_at at time zone 'UTC'))::date,
        case p_group
            when 'firm'         then v.firm_id::text
            when 'vendor_model' then v.vendor || ' / ' || v.billed_model
            when 'profile'      then v.profile
            when 'matter'       then coalesce(v.matter_id::text, '')
            when 'staff'        then coalesce(st.id::text, '')
            when 'source'       then v.source_type
        end,
        case p_group
            -- The FK makes a missing firm impossible; the coalesce is there so a
            -- report can never answer with a blank cell if one ever is.
            when 'firm'         then coalesce(fm.name, '(firm ' || v.firm_id::text || ')')
            when 'vendor_model' then v.vendor || ' / ' || v.billed_model
            when 'profile'      then v.profile
            when 'matter'       then coalesce(mt.matter_name, '(no matter)')
            when 'staff'        then coalesce(st.email, '(no staff member)')
            when 'source'       then v.source_type
        end,
        count(*),
        count(*) filter (where v.outcome = 'failed'),
        count(*) filter (where v.pricing_status = 'no_usage'),
        count(*) filter (where v.pricing_status = 'unpriced'),
        sum(v.input_uncached_tokens)::bigint,
        sum(v.input_cache_read_tokens)::bigint,
        sum(v.input_cache_write_tokens)::bigint,
        sum(v.output_tokens)::bigint,
        round(coalesce(sum(v.total_cost), 0), 2),
        round(coalesce(sum(v.total_cost), 0), 6)
    from llm_call_costs v
    -- A job records staff_id; a web request records only auth_uid. LATERAL with
    -- LIMIT 1 so a duplicated supabase_uid could never multiply a call's cost.
    left join lateral (
        select s.id, s.email from staff s
         where s.id = v.staff_id
            or (v.staff_id is null and v.auth_uid is not null and s.supabase_uid = v.auth_uid)
         limit 1
    ) st on p_group = 'staff'
    left join matters mt on p_group = 'matter' and mt.id = v.matter_id
    left join firms fm on p_group = 'firm' and fm.id = v.firm_id
    where v.occurred_at >= p_from and v.occurred_at < p_to
    group by 1, 2, 3
    order by 1 desc, 13 desc, 3;
end;
$$;

revoke execute on function llm_cost_report(timestamptz, timestamptz, text)
    from public, anon, authenticated;
grant execute on function llm_cost_report(timestamptz, timestamptz, text)
    to service_role;

commit;
