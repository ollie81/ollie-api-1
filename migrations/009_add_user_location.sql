-- Backs letting Ollie talk like a local -- references to the
-- user's own culture, food, sports, slang, holidays -- instead of
-- defaulting to generic/US-centric ones.
-- See: chat.py _location_block, settings.py /location
--
-- All nullable, no default: entirely opt-in from Settings. A user
-- who never sets these just gets today's behavior, unchanged.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists country text,
  add column if not exists region text,
  add column if not exists district text;
