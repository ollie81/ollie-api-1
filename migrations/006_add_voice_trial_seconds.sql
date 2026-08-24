-- Backs the free one-minute voice trial (real Ollie replies spoken
-- aloud via /speak, not the separate canned /speak/preview line).
-- See: database.py OllieDB.try_consume_voice_trial
--
-- A running lifetime total, not a daily allowance -- "test it
-- once" per the product intent, not an ongoing free voice tier.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists voice_trial_seconds_used integer not null default 0;
