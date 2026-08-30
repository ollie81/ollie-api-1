-- Backs the grace-period account deletion flow -- deletion is
-- requested (this timestamp gets set) rather than carried out
-- immediately; a scheduled job purges anyone whose grace period has
-- elapsed (see database.py's ACCOUNT_DELETION_GRACE_DAYS), and
-- logging back in during the window clears it (see auth.py).
alter table users add column if not exists deletion_requested_at timestamptz;
