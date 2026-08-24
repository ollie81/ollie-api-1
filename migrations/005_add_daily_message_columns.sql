-- Backs the daily proactive message (Ollie messages first, once a
-- day, at a randomized local time within a daytime window). See:
-- daily_message.py
--
-- last_known_utc_offset_minutes is updated opportunistically on
-- every /chat and /chat/voice call (see database.py
-- remember_utc_offset) -- the background job that sends these
-- messages has no live request to read a fresh offset from, so it
-- needs a persisted, best-effort-recent one.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists last_known_utc_offset_minutes integer;

alter table users
  add column if not exists last_daily_message_date date;

alter table users
  add column if not exists next_daily_message_at timestamptz;
