-- Backs the share-link feature: captures which existing user's
-- share link a new signup came through, so referrals are actually
-- measurable instead of guessed at. Stores the referrer's user id
-- as text, not a foreign key -- a referral link with a stale or
-- tampered id should just go unrecorded, never block a signup.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists referred_by text;
