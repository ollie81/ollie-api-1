-- Upgrades the memory system: categorized memories (identity,
-- preference, accomplishment, struggle, person, event, promise),
-- goal completion tracking, and a per-user memory on/off switch.
-- See: memory.py extract_memory_worthy / detect_goal_completion,
-- database.py save_memory / complete_goal, settings.py /memories,
-- /memory/enabled.
--
-- All additive and backward compatible -- existing rows just get
-- category = null (still readable, just uncategorized) until the
-- next time each is naturally re-saved.
--
-- Safe to run even if already applied manually earlier.

alter table memories
  add column if not exists category text,
  add column if not exists created_at timestamptz not null default now(),
  add column if not exists updated_at timestamptz not null default now();

alter table goals
  add column if not exists completed_at timestamptz;

alter table users
  add column if not exists memory_enabled boolean not null default true;

-- ------------------------------------------------------------
-- RLS -- read this before assuming it changes access behavior.
--
-- This app does NOT use Supabase Auth -- it has its own JWT system
-- (see auth.py), and the Flutter app never talks to Supabase
-- directly, only through the FastAPI backend, which connects with
-- a service-role key. Service-role bypasses RLS entirely, on every
-- table, regardless of policy. So the actual thing protecting user
-- data today is the backend scoping every query to the requesting
-- user's id (e.g. .eq("user_id", current_user["id"])) -- not RLS.
--
-- Enabling RLS below with no permissive policy is still worth
-- doing: it means "only the service-role key can touch this table"
-- (the reality already), clears Supabase's dashboard security
-- warnings, and becomes load-bearing automatically if anything
-- ever queries Supabase directly with a non-service-role key in
-- the future. It changes nothing about how the app behaves today.
-- ------------------------------------------------------------

alter table users enable row level security;
alter table sessions enable row level security;
alter table conversations enable row level security;
alter table memories enable row level security;
alter table goals enable row level security;
alter table moods enable row level security;
alter table user_interests enable row level security;
alter table message_usage enable row level security;
alter table subscriptions enable row level security;
alter table notifications enable row level security;
alter table scheduled_events enable row level security;
alter table refresh_tokens enable row level security;
alter table crisis_flags enable row level security;
alter table moderation_flags enable row level security;
