-- Backs the web client's free voice trial: one full voice exchange
-- (record a message, hear Ollie's spoken reply) per calendar day,
-- not a lifetime seconds budget -- see database.py's
-- OllieDB.try_consume_voice_exchange_today and chat.py's
-- /chat/voice include_audio=True path.
--
-- Separate from voice_trial_seconds_used (migration 006), which the
-- Android app's existing /speak-based voice flow keeps using
-- unchanged -- this column and that one are two independent trial
-- models serving two different client flows.
--
-- Default is a fixed past date rather than null: try_consume_voice_
-- exchange_today's optimistic-concurrency compare-and-set needs a
-- real, always-comparable value to check against (same reason
-- voice_trial_seconds_used defaults to 0 rather than null).
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists voice_trial_date date not null default '1970-01-01';
