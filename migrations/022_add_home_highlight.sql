-- Backs a live, personal line on the home screen ("you seemed
-- stressed yesterday -- want to talk?") instead of a static greeting
-- -- see journey.py's home_highlight field and daily_message.py's
-- _process_morning_checkin, which now saves the same personalized
-- text it already generates for the morning push notification,
-- rather than discarding it after sending. This way anyone who
-- opens the app sees it, notifications on or off.
--
-- Safe to run even if already applied manually earlier.

alter table users
  add column if not exists last_home_highlight_text text,
  add column if not exists last_home_highlight_date date;
