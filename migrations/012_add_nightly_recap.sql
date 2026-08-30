-- Backs the nightly recap -- a second daily proactive moment
-- alongside the existing morning check-in (which now reuses
-- last_daily_message_date / next_daily_message_at from migration
-- 005, unchanged). See: daily_message.py.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists last_nightly_recap_date date;

alter table users
  add column if not exists next_nightly_recap_at timestamptz;
