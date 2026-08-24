-- Audit trail for OpenAI moderation-API hits on /chat input and
-- output. See: chat.py _flag_moderation, memory.py moderate_text
--
-- Best-effort writes from the app -- inserts there are already
-- wrapped in try/except, so the app works fine before this table
-- exists too. This just turns those writes into real data.
--
-- Observability only, by design: nothing in the app currently
-- blocks or changes a reply based on this data. Deciding what (if
-- anything) should act on it automatically -- e.g. hard-blocking
-- specific categories -- is a product/legal call, not made here.
--
-- Safe to run even if already applied manually earlier.

create table if not exists moderation_flags (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  direction text not null check (direction in ('input', 'output')),
  categories text not null,
  message text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_moderation_flags_user_id on moderation_flags(user_id);
