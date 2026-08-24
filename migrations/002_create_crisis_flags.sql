-- Audit trail for self-harm/abuse keyword matches in /chat.
-- See: chat.py _flag_crisis_message
--
-- Best-effort writes from the app -- inserts there are already
-- wrapped in try/except, so the app works fine before this table
-- exists too. This just turns those writes into real data.
--
-- Safe to run even if already applied manually earlier.

create table if not exists crisis_flags (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  message text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_crisis_flags_user_id on crisis_flags(user_id);
