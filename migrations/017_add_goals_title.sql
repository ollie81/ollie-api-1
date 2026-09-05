-- Fixes a production error: every chat message where the goal
-- detector (memory.py's goal-mention/completion logic, added in
-- migration 010) decided the user mentioned a goal was crashing the
-- whole /chat request with "column goals.title does not exist" --
-- database.py's save_goal/complete_goal have always filtered and
-- inserted on goals.title, but no migration ever actually added
-- that column to the goals table itself.
--
-- Nullable, not backfilled: no existing row could ever have had a
-- title (the column didn't exist to write one into), and the app
-- always supplies one on insert going forward.
--
-- Safe to run even if already applied manually earlier.

alter table goals
  add column if not exists title text;
