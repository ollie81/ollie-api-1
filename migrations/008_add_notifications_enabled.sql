-- Backs the Settings notification toggle, and gates whether
-- create_notification/daily_message actually send a push (in-app
-- notification rows are always saved regardless).
-- See: settings.py, notification_service.py, daily_message.py
--
-- The app-layer already treats a NULL/missing value as "enabled"
-- (see settings.py's "is not False" check), but this was never
-- actually added as a real column -- every query naming it
-- explicitly (create_notification's push check, daily_message's
-- batch query, the toggle's UPDATE) has been failing outright,
-- not just falling back to a default.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists notifications_enabled boolean not null default true;
