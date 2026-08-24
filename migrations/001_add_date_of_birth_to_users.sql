-- Adds date_of_birth to support the signup age gate.
-- See: auth.py SignupRequest.date_of_birth / _check_age_gate
--
-- Nullable and optional on purpose, matching the API: older app
-- builds don't send a birthdate yet, and the backend only
-- enforces the age check when one is actually provided.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists date_of_birth date;
