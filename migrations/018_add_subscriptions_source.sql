-- Tags each subscriptions row with where it came from. Needed ahead
-- of a web checkout going live (payment processor not yet chosen --
-- see premium.py): without this, is_premium_active's re-verification
-- path would try to look up an expired web-purchased subscription in
-- the Play Developer API and fail every time.
--
-- Backfilled to 'play' for every existing row, since every one of
-- them really did come from Google Play Billing -- web-purchased
-- rows are only ever inserted going forward, tagged with whatever
-- processor is eventually wired up.

alter table subscriptions
  add column if not exists source text not null default 'play';
