-- Backs the "reply to a specific message" feature in the chat
-- screen -- lets a message quote an earlier one (usually one of
-- Ollie's) instead of just being the next line in the flow.
alter table conversations add column if not exists reply_to_id uuid references conversations(id) on delete set null;
