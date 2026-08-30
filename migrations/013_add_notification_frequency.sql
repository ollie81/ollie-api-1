-- Backs Phase 5: the Off/Low/Normal/Frequent proactive-notification
-- control, and the "you disappeared" re-engagement check-in.
-- See: settings.py /notification-frequency, daily_message.py,
-- event_scheduler.py.
--
-- notification_frequency governs ONLY Ollie-INITIATED proactive
-- messages (morning check-in, nightly recap, event check-ins, the
-- disappeared check) -- separate from notifications_enabled (the
-- master push switch, added in migration 008), which also covers
-- reminders. Explicit reminders always send regardless of this
-- setting -- the user asked for those directly.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists notification_frequency text not null default 'normal';

alter table users
  add column if not exists last_message_at timestamptz;

alter table users
  add column if not exists last_disappeared_checkin_at timestamptz;
