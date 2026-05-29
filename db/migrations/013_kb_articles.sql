-- 013_kb_articles.sql
-- Phase C.1: knowledge base for the CRM compose agent.
--
-- One row per editable piece of firm-fact content (operating hours, locations,
-- meeting types, fee structure outline, practice areas, geography, etc.).
--
-- Phase 1 retrieval strategy is "whole-KB-in-prompt": the kb_retrieval agent
-- concatenates every active row into its system prompt and the LLM extracts
-- what's relevant per issue. When the KB outgrows the context window, swap
-- the retrieval implementation behind the same agent interface — no other
-- pipeline change required.

create table kb_articles (
    id bigserial primary key,
    topic text not null,
    subtopic text,
    body_md text not null,
    active boolean not null default true,
    sort_order integer not null default 0,
    created_at timestamptz default now() not null,
    updated_at timestamptz default now() not null
);

create index idx_kb_articles_active on kb_articles(active);
create index idx_kb_articles_sort on kb_articles(sort_order, topic, subtopic);

create trigger trg_kb_articles_updated_at
    before update on kb_articles
    for each row execute function set_updated_at();
