-- Tags each subscriptions row with where it came from. Needed
-- ahead of web checkout (see billing.py) going live: without this,
-- is_premium_active's re-verification path (premium.py) would try to
-- look up an expired web-purchased subscription in the Play
-- Developer API and fail every time.
--
-- Backfilled to 'play' for every existing row, since every one of
-- them really did come from Google Play Billing -- web-purchased
-- rows (source="flutterwave") are only ever inserted going forward,
-- already tagged at insert time.

alter table subscriptions
  add column if not exists source text not null default 'play';
