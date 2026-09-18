-- Tags each subscriptions row with where it came from. Needed
-- ahead of Stripe web checkout (see billing.py) going live: without
-- this, is_premium_active's re-verification path (premium.py) would
-- try to look up an expired Stripe subscription in the Play
-- Developer API and fail every time.
--
-- Backfilled to 'play' for every existing row, since every one of
-- them really did come from Google Play Billing -- Stripe rows are
-- only ever inserted going forward, already tagged 'stripe' at
-- insert time.

alter table subscriptions
  add column if not exists source text not null default 'play';
