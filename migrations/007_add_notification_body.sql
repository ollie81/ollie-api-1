-- Lets a scheduled_events row carry a ready-to-send notification
-- body distinct from event_summary (which stays the raw/canonical
-- text used for dedup topic_keys and in-conversation context).
-- See: event_scheduler.py _personalize_reminder, maybe_schedule_reminder
--
-- Currently only populated for explicit reminders -- a reminder
-- notification that just said the bare extracted text ("drink
-- water") with none of Ollie's actual voice wasn't what it was
-- supposed to sound like. Nullable and unused for check-ins for
-- now; run_due_notifications falls back to the templated check-in
-- line when this is empty.
--
-- Safe to run even if already applied manually earlier.

alter table scheduled_events
  add column if not exists notification_body text;
