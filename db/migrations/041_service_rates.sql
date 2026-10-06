-- 041_service_rates.sql
--
-- Dated prices, and the cost of every model call priced at read time.
--
-- RUN BY HAND, after 039 and 040. Nothing is seeded: every price comes from the
-- vendor's pricing page, entered through Admin > Costs with its source URL.
--
-- ── PRICED WHEN A REPORT RUNS, NEVER WHEN A CALL IS RECORDED ─────────────────
--
-- llm_calls stores what the vendor counted, which never changes. A price gets
-- entered late -- after the calls it applies to -- and gets corrected. A cost
-- frozen onto the ledger row would be wrong forever; a cost computed from the
-- rate in force on the call's date is right the moment the price is.
--
-- ── ONE PRICE AT A TIME, ENFORCED BY THE DATABASE ────────────────────────────
--
-- Two rows for the same (provider, model, meter) covering the same instant would
-- make the pricing join return both, and every call in the overlap would be
-- priced TWICE -- a doubled cost report with nothing wrong on its face. The
-- exclusion constraint makes that unrepresentable rather than merely unlikely.
-- It needs btree_gist for the text columns.
--
-- ── A PRICE IS COLUMNS, NOT A COMPOSED KEY ───────────────────────────────────
--
-- A key like 'anthropic_claude-opus-5_input' would have to be rebuilt from a
-- call's vendor, model and meter to find its price -- the same string built in
-- two places, which drifts. Columns join exactly.
--
-- ── UNPRICED IS NOT FREE ─────────────────────────────────────────────────────
--
-- A call whose tokens meet no price is `unpriced`, and its cost is NULL, not 0.
-- A call the vendor reported no usage for is `no_usage`, also NULL. A report
-- that summed either as zero would understate spend and look complete. There is
-- deliberately NO fallback from a cache-read price to the full input price: a
-- guessed price is a wrong cost with nothing to show it was guessed.

begin;

create extension if not exists btree_gist;

-- ── service_rates ────────────────────────────────────────────────────────────

create table if not exists service_rates (
    id                    bigint        generated always as identity primary key,
    -- The llm_calls.vendor value this price applies to: anthropic, openai, ...
    provider              text          not null,
    -- The llm_calls.model value, exactly as recorded.
    model                 text          not null,
    -- The four normalized usage columns of llm_calls. Widened when storage and
    -- hosting costs arrive (step 3 of docs/cost-tracking-design.md).
    meter                 text          not null check (meter in (
                              'input_uncached', 'input_cache_read',
                              'input_cache_write', 'output')),
    -- Price per unit_count units: 1,000,000 tokens is how every vendor quotes.
    unit_count            bigint        not null default 1000000 check (unit_count > 0),
    -- Six decimals: some models are priced below a cent per million tokens.
    price                 numeric(14,6) not null check (price >= 0),
    -- One currency until there is a reason for two. Summing mixed currencies is
    -- a silent error, so the second one is a migration, not a data entry.
    currency              text          not null default 'USD' check (currency = 'USD'),
    -- Half-open: [effective_from, effective_to). NULL to means "still in force".
    effective_from        timestamptz   not null,
    effective_to          timestamptz,
    source_url            text,
    note                  text,
    created_by_staff_id   integer,
    created_at            timestamptz   not null default now(),
    updated_at            timestamptz,

    constraint service_rates_period
        check (effective_to is null or effective_to > effective_from),
    constraint service_rates_one_price_at_a_time
        exclude using gist (
            provider with =,
            model    with =,
            meter    with =,
            tstzrange(effective_from, effective_to, '[)') with &&
        )
);

alter table service_rates enable row level security;

drop trigger if exists trg_service_rates_updated_at on service_rates;
create trigger trg_service_rates_updated_at
    before update on service_rates
    for each row execute function set_updated_at();

-- ── llm_call_costs: every call, priced ───────────────────────────────────────
--
-- security_invoker so it reads llm_calls and service_rates as the CALLER and
-- RLS applies through it. A view without it runs as its owner and bypasses RLS
-- on everything it reads -- which is exactly how 004's views were exposed.

create or replace view llm_call_costs with (security_invoker = on) as
with joined as (
    select
        c.*,
        r_in.price  * c.input_uncached_tokens    / r_in.unit_count  as input_uncached_cost,
        r_rd.price  * c.input_cache_read_tokens  / r_rd.unit_count  as input_cache_read_cost,
        r_wr.price  * c.input_cache_write_tokens / r_wr.unit_count  as input_cache_write_cost,
        r_out.price * c.output_tokens            / r_out.unit_count as output_cost,
        (c.input_uncached_tokens is not null or c.input_cache_read_tokens is not null
         or c.input_cache_write_tokens is not null or c.output_tokens is not null)
                                                                    as usage_reported,
        -- A meter that CARRIED tokens and found no price. Zero or unreported
        -- tokens need no price, so a vendor with no cache does not make every
        -- one of its calls unpriced.
        ((coalesce(c.input_uncached_tokens, 0)    > 0 and r_in.id  is null)
         or (coalesce(c.input_cache_read_tokens, 0)  > 0 and r_rd.id  is null)
         or (coalesce(c.input_cache_write_tokens, 0) > 0 and r_wr.id  is null)
         or (coalesce(c.output_tokens, 0)            > 0 and r_out.id is null))
                                                                    as missing_price
    from llm_calls c
    left join service_rates r_in
           on r_in.provider = c.vendor and r_in.model = c.model
          and r_in.meter = 'input_uncached'
          and c.occurred_at >= r_in.effective_from
          and (r_in.effective_to is null or c.occurred_at < r_in.effective_to)
    left join service_rates r_rd
           on r_rd.provider = c.vendor and r_rd.model = c.model
          and r_rd.meter = 'input_cache_read'
          and c.occurred_at >= r_rd.effective_from
          and (r_rd.effective_to is null or c.occurred_at < r_rd.effective_to)
    left join service_rates r_wr
           on r_wr.provider = c.vendor and r_wr.model = c.model
          and r_wr.meter = 'input_cache_write'
          and c.occurred_at >= r_wr.effective_from
          and (r_wr.effective_to is null or c.occurred_at < r_wr.effective_to)
    left join service_rates r_out
           on r_out.provider = c.vendor and r_out.model = c.model
          and r_out.meter = 'output'
          and c.occurred_at >= r_out.effective_from
          and (r_out.effective_to is null or c.occurred_at < r_out.effective_to)
)
select
    id, occurred_at, firm_id, staff_id, matter_id, auth_uid, source_type, source_id,
    profile, call_type, vendor, model, chain_position, outcome, error_class,
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

-- ── llm_unpriced_meters: what to price next ──────────────────────────────────
--
-- Every (vendor, model, meter) that carried tokens with no price in force at
-- the time. This is the to-do list for Admin > Costs, and the reason nothing
-- has to be guessed: it names exactly which prices are missing.

create or replace view llm_unpriced_meters with (security_invoker = on) as
select c.vendor,
       c.model,
       m.meter,
       count(*)             as calls,
       sum(m.tokens)        as tokens,
       min(c.occurred_at)   as first_seen,
       max(c.occurred_at)   as last_seen
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
         where r.provider = c.vendor and r.model = c.model and r.meter = m.meter
           and c.occurred_at >= r.effective_from
           and (r.effective_to is null or c.occurred_at < r.effective_to))
 group by c.vendor, c.model, m.meter;

-- ── set_service_rate: a new price from a date, atomically ────────────────────
--
-- The normal operation is "from this date the price is X": close the price in
-- force and open the new one. Two PostgREST calls cannot do that atomically,
-- and a half-done pair leaves either an overlap (refused by the constraint) or a
-- gap (calls in it silently unpriced). One function, one transaction.
--
-- It refuses to insert BEFORE a price that already starts later: that would
-- rewrite a schedule somebody entered on purpose. Correct that row instead.

create or replace function set_service_rate(
    p_provider        text,
    p_model           text,
    p_meter           text,
    p_price           numeric,
    p_unit_count      bigint,
    p_effective_from  timestamptz,
    p_source_url      text,
    p_note            text,
    p_staff_id        integer
) returns service_rates
language plpgsql
as $$
declare
    later  service_rates;
    result service_rates;
begin
    select * into later
      from service_rates
     where provider = p_provider and model = p_model and meter = p_meter
       and effective_from >= p_effective_from
     order by effective_from
     limit 1;
    if found then
        raise exception
            'A % price for % / % already starts on %. Correct that row, or choose a date after it.',
            p_meter, p_provider, p_model, later.effective_from
            using errcode = 'exclusion_violation';
    end if;

    update service_rates
       set effective_to = p_effective_from
     where provider = p_provider and model = p_model and meter = p_meter
       and effective_from < p_effective_from
       and (effective_to is null or effective_to > p_effective_from);

    insert into service_rates (provider, model, meter, price, unit_count,
                               effective_from, source_url, note, created_by_staff_id)
    values (p_provider, p_model, p_meter, p_price, coalesce(p_unit_count, 1000000),
            p_effective_from, p_source_url, p_note, p_staff_id)
    returning * into result;

    return result;
end;
$$;

-- ── llm_cost_report: spend by month and one dimension ────────────────────────
--
-- Months are UTC, deliberately: vendor invoices are, and a report that rolled
-- over at local midnight would disagree with the invoice at every month edge.
--
-- Returns counts that say how far to trust the money beside them: a month with
-- unpriced or no-usage calls has a cost that is a floor, not a total.

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
            when 'vendor_model' then v.vendor || ' / ' || v.model
            when 'profile'      then v.profile
            when 'matter'       then coalesce(v.matter_id::text, '')
            when 'staff'        then coalesce(st.id::text, '')
            when 'source'       then v.source_type
        end,
        case p_group
            when 'vendor_model' then v.vendor || ' / ' || v.model
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
    where v.occurred_at >= p_from and v.occurred_at < p_to
    group by 1, 2, 3
    order by 1 desc, 13 desc, 3;
end;
$$;

-- ── Functions are callable at /rest/v1/rpc by default ────────────────────────
--
-- Postgres grants EXECUTE on a new function to PUBLIC, and PostgREST serves it
-- to anyone holding the anon key. These run as the caller, so RLS would still
-- refuse the rows -- but a cost report is nobody's business but the firm's, and
-- the backend is the only legitimate caller.

revoke execute on function set_service_rate(text, text, text, numeric, bigint, timestamptz, text, text, integer)
    from public, anon, authenticated;
revoke execute on function llm_cost_report(timestamptz, timestamptz, text)
    from public, anon, authenticated;
grant execute on function set_service_rate(text, text, text, numeric, bigint, timestamptz, text, text, integer)
    to service_role;
grant execute on function llm_cost_report(timestamptz, timestamptz, text)
    to service_role;

commit;
