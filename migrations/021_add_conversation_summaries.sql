-- Backs a permanent, per-day memory of what was actually discussed --
-- separate from the categorized "memories" table (identity, goals,
-- struggles, etc.), which only ever captures isolated facts, never
-- "what did we generally talk about on day X". See
-- daily_message.py's run_conversation_summaries and
-- database.py's get_recent_conversation_summaries /
-- build_memory_context's "RECENT DAYS" block, which surfaces these
-- back into every future conversation.
--
-- One row per user per completed local day, generated regardless of
-- notification settings -- this is about what Ollie remembers, not
-- what gets pushed to a phone. unique(user_id, summary_date) makes
-- the write idempotent if the job ever runs twice for the same day.
--
-- last_conversation_summary_date on users tracks which day was last
-- processed, so the job never re-summarizes (or re-skips) the same
-- day twice.
--
-- Safe to run even if already applied manually earlier.

create table if not exists conversation_summaries (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  summary_date date not null,
  summary_text text not null,
  created_at timestamptz not null default now(),
  unique (user_id, summary_date)
);

create index if not exists idx_conversation_summaries_user_id
  on conversation_summaries (user_id, summary_date desc);

alter table users
  add column if not exists last_conversation_summary_date date;
