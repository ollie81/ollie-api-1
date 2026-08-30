-- Backs the relationship/journey system ("Our Space") -- a
-- non-streak signal for how well Ollie and the user know each
-- other, computed from genuine duration + depth instead of a
-- "don't break the chain" mechanic. See relationship.py,
-- database.py update_streak / get_journey_summary, journey.py.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists total_active_days integer not null default 0;

-- One-time backfill for existing users: computes their REAL
-- lifetime distinct-day count from conversation history instead of
-- leaving everyone at 0 (which would understate long-time users'
-- actual relationship with Ollie). Only touches rows still at the
-- column's default, so it's safe to re-run -- it will never
-- overwrite a total_active_days value that update_streak has
-- already started incrementing for real.
update users u
set total_active_days = coalesce((
  select count(distinct date(c.created_at))
  from conversations c
  where c.user_id = u.id and c.sender = 'user'
), 0)
where u.total_active_days = 0;
