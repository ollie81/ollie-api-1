-- Adds email/password as a third sign-in method alongside phone
-- and Google. Deliberately a NEW, separate `email` column rather
-- than reusing the existing `phone` column the way Google sign-in
-- does -- Google already writes verified emails into `phone` for
-- every existing Google user, and changing that lookup now would
-- silently split each returning Google user into a duplicate
-- account. Google's behavior is untouched by this migration.
--
-- email_otp_hash/email_otp_expires_at back this app's own OTP
-- generation for email signup + password reset (see auth.py,
-- email_service.py) -- unlike phone, which hands OTP state
-- entirely to Twilio Verify via otp_sid/otp_expires_at (migration
-- predates this file), there's no third-party "verify" product for
-- email, so the code is generated, hashed, and checked here. Only
-- ever the SHA-256 hash is stored, same as refresh_tokens.token_hash
-- -- never the raw code.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists email text;

alter table users
  add column if not exists email_verified boolean not null default false;

alter table users
  add column if not exists email_otp_hash text;

alter table users
  add column if not exists email_otp_expires_at timestamptz;

-- Pending signup verification, BEFORE a users row exists. Keeping
-- this separate from `users` (rather than creating an unverified
-- users row at request-otp time) means an abandoned signup attempt
-- can never permanently squat an email address -- see auth.py's
-- request_email_signup_otp / email_signup for the security
-- rationale (mirrors what Twilio Verify already does for phone,
-- just self-managed since there's no email equivalent in use here).
create table if not exists email_signup_otps (
  email text primary key,
  otp_hash text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table email_signup_otps enable row level security;
