-- Daily streak: consecutive local-calendar days the user has
-- talked to Ollie. See: database.py OllieDB.update_streak
--
-- last_streak_date is the user's own local date (computed from
-- their utc_offset_minutes), not a server/UTC date -- a streak
-- that rolled over at UTC midnight would feel broken for anyone
-- outside UTC.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists current_streak integer not null default 0;

alter table users
  add column if not exists last_streak_date date;
