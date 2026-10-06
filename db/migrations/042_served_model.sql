-- 042_served_model.sql
--
-- Record which model the vendor says ANSWERED, and price the call on that.
--
-- RUN BY HAND, after 041. Safe to run before or after deploying the code that
-- writes these columns: code that finds them missing records usage without
-- them and says so at ERROR, naming this file. Safe to run twice.
--
-- Once this has run, RE-RUNNING 041 FAILS, and that is intended: its
-- CREATE OR REPLACE VIEW cannot reshape these views, and if it could it would
-- silently put back pricing on the requested model. 041 runs in a transaction,
-- so the failure changes nothing. Run 042 again after it if you ever must.
--
-- ── THE MODEL WE ASKED FOR IS NOT EVIDENCE OF THE MODEL THAT ANSWERED ────────
--
-- llm_calls.model is the catalog's name for the request. A vendor serves an
-- alias or a "preview" name from whichever version it currently points at, and
-- says which in its response: `model` on Anthropic and OpenAI-compatible replies
-- (OpenAI resolves an alias to a dated snapshot), `model_version` on Gemini.
-- Priced on the requested name, a call is charged at the rate of a model that
-- may never have run -- and on a large document ingest that is exactly the
-- figure a billing dispute turns on.
--
-- So pricing keys on `billed_model` = served_model where the vendor reported
-- one, else the requested model. The fallback is not a guess dressed as a fact:
-- every row says which it used (`billed_model_source`), and it only ever applies
-- to rows written before this migration or to a response that named no model.
--
-- The consequence is deliberate: when a vendor repoints an alias, the new
-- version appears on llm_unpriced_meters under its own name, instead of being
-- silently charged at the old version's price. That list now also carries the
-- model names that were REQUESTED, so the right row on the vendor's pricing page
-- can be found.
--
-- service_rates.model (041) therefore means "the model that served the call".
-- For every row written before this migration that is the requested model,
-- which is what it meant before, so no existing price changes meaning.
--
-- ── vendor_response_id ───────────────────────────────────────────────────────
--
-- The vendor's own id for the response (msg_..., chatcmpl-..., Gemini's
-- response_id). It is the one reference a vendor's billing support can look up,
-- and it costs a column.

begin;

alter table llm_calls add column if not exists served_model       text;
alter table llm_calls add column if not exists vendor_response_id text;

create index if not exists idx_llm_calls_vendor_response
    on llm_calls (vendor, vendor_response_id)
    where vendor_response_id is not null;

-- Views are dropped rather than replaced: CREATE OR REPLACE VIEW cannot insert
-- columns ahead of existing ones. Nothing depends on either view -- the report
-- function reads llm_call_costs by name at run time, not as a dependency.
drop view if exists llm_unpriced_meters;
drop view if exists llm_call_costs;

-- ── llm_call_costs: every call, priced on the model that served it ───────────

create view llm_call_costs with (security_invoker = on) as
with billed as (
    select c.*,
           coalesce(c.served_model, c.model)                               as billed_model,
           case when c.served_model is null then 'requested' else 'served' end
                                                                           as billed_model_source
      from llm_calls c
),
joined as (
    select
        b.*,
        r_in.price  * b.input_uncached_tokens    / r_in.unit_count  as input_uncached_cost,
        r_rd.price  * b.input_cache_read_tokens  / r_rd.unit_count  as input_cache_read_cost,
        r_wr.price  * b.input_cache_write_tokens / r_wr.unit_count  as input_cache_write_cost,
        r_out.price * b.output_tokens            / r_out.unit_count as output_cost,
        (b.input_uncached_tokens is not null or b.input_cache_read_tokens is not null
         or b.input_cache_write_tokens is not null or b.output_tokens is not null)
                                                                    as usage_reported,
        ((coalesce(b.input_uncached_tokens, 0)    > 0 and r_in.id  is null)
         or (coalesce(b.input_cache_read_tokens, 0)  > 0 and r_rd.id  is null)
         or (coalesce(b.input_cache_write_tokens, 0) > 0 and r_wr.id  is null)
         or (coalesce(b.output_tokens, 0)            > 0 and r_out.id is null))
                                                                    as missing_price
    from billed b
    left join service_rates r_in
           on r_in.provider = b.vendor and r_in.model = b.billed_model
          and r_in.meter = 'input_uncached'
          and b.occurred_at >= r_in.effective_from
          and (r_in.effective_to is null or b.occurred_at < r_in.effective_to)
    left join service_rates r_rd
           on r_rd.provider = b.vendor and r_rd.model = b.billed_model
          and r_rd.meter = 'input_cache_read'
          and b.occurred_at >= r_rd.effective_from
          and (r_rd.effective_to is null or b.occurred_at < r_rd.effective_to)
    left join service_rates r_wr
           on r_wr.provider = b.vendor and r_wr.model = b.billed_model
          and r_wr.meter = 'input_cache_write'
          and b.occurred_at >= r_wr.effective_from
          and (r_wr.effective_to is null or b.occurred_at < r_wr.effective_to)
    left join service_rates r_out
           on r_out.provider = b.vendor and r_out.model = b.billed_model
          and r_out.meter = 'output'
          and b.occurred_at >= r_out.effective_from
          and (r_out.effective_to is null or b.occurred_at < r_out.effective_to)
)
select
    id, occurred_at, firm_id, staff_id, matter_id, auth_uid, source_type, source_id,
    profile, call_type, vendor, model, served_model, billed_model, billed_model_source,
    vendor_response_id, chain_position, outcome, error_class,
    input_uncached_tokens, input_cache_read_tokens, input_cache_write_tokens,
    output_tokens, reasoning_tokens,
    input_uncached_cost, input_cache_read_cost, input_cache_write_cost, output_cost,
    case
        when not usage_reported then 'no_usage'
        when missing_price      then 'unpriced'
        else 'priced'
    end as pricing_status,
    case
        when usage_reported and not missing_price then
            coalesce(input_uncached_cost, 0) + coalesce(input_cache_read_cost, 0)
            + coalesce(input_cache_write_cost, 0) + coalesce(output_cost, 0)
    end as total_cost
from joined;

-- ── llm_unpriced_meters: what to price next, under the name that served it ───
--
-- `model` is the model a price must be entered under. `requested_models` is
-- what the catalog asked for, which is how that model is found on a pricing page
-- when the vendor answered with a dated or versioned name.

create view llm_unpriced_meters with (security_invoker = on) as
select c.vendor,
       coalesce(c.served_model, c.model)                   as model,
       m.meter,
       count(*)                                            as calls,
       sum(m.tokens)                                       as tokens,
       min(c.occurred_at)                                  as first_seen,
       max(c.occurred_at)                                  as last_seen,
       array_agg(distinct c.model order by c.model)        as requested_models
  from llm_calls c
 cross join lateral (values
        ('input_uncached',    c.input_uncached_tokens),
        ('input_cache_read',  c.input_cache_read_tokens),
        ('input_cache_write', c.input_cache_write_tokens),
        ('output',            c.output_tokens)
       ) as m (meter, tokens)
 where coalesce(m.tokens, 0) > 0
   and not exists (
        select 1 from service_rates r
         where r.provider = c.vendor
           and r.model = coalesce(c.served_model, c.model)
           and r.meter = m.meter
           and c.occurred_at >= r.effective_from
           and (r.effective_to is null or c.occurred_at < r.effective_to))
 group by c.vendor, coalesce(c.served_model, c.model), m.meter;

-- ── llm_cost_report: vendor_model groups on the model that served ────────────
--
-- Same signature and return type as 041, so CREATE OR REPLACE keeps its grants;
-- they are restated below anyway so this file stands on its own.

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
    if p_group not in ('vendor_model', 'profile', 'matter', 'staff', 'source') then
        raise exception 'Unknown grouping %; expected vendor_model, profile, matter, staff or source',
            p_group using errcode = 'invalid_parameter_value';
    end if;

    return query
    select
        (date_trunc('month', v.occurred_at at time zone 'UTC'))::date,
        case p_group
            when 'vendor_model' then v.vendor || ' / ' || v.billed_model
            when 'profile'      then v.profile
            when 'matter'       then coalesce(v.matter_id::text, '')
            when 'staff'        then coalesce(st.id::text, '')
            when 'source'       then v.source_type
        end,
        case p_group
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
    left join lateral (
        select s.id, s.email from staff s
         where s.id = v.staff_id
            or (v.staff_id is null and v.auth_uid is not null and s.supabase_uid = v.auth_uid)
         limit 1
    ) st on p_group = 'staff'
    left join matters mt on p_group = 'matter' and mt.id = v.matter_id
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
