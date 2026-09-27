-- Backs guest mode: someone can now chat with Ollie before signing
-- up at all (see auth.py's /auth/guest and _resolve_guest_upgrade,
-- chat.py's guest message cap, and the matching ollie-web PR). A
-- guest is a real row in this table -- same id used everywhere else
-- (conversations, memories, moods) -- just unverified and capped
-- much lower than a real free-tier account.
--
-- guest_messages_used is a one-time lifetime counter, unlike the
-- existing daily free-tier count -- see OllieDB.try_consume_guest_
-- message. Once a guest upgrades to a real account (is_guest set
-- back to false, phone/email/password_hash filled in on the SAME
-- row rather than copying data to a new one), the normal daily
-- free-tier logic takes over and this stops being checked.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists is_guest boolean not null default false,
  add column if not exists guest_messages_used integer not null default 0;

create index if not exists idx_users_is_guest on users(is_guest) where is_guest;
