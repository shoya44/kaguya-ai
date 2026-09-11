BEGIN;
-- 会話から予約する時刻つきの声かけ。本文はユーザーの発言に由来するため、
-- settings.json ではなく会話と同じDBに置く。
CREATE TABLE IF NOT EXISTS reminders (
    id uuid PRIMARY KEY,
    due_at timestamptz NOT NULL,
    message text NOT NULL CHECK (length(message) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);
-- 未配信かつ期限到来のものを毎回引くため、この順で複合インデックスを張る。
CREATE INDEX IF NOT EXISTS reminders_pending ON reminders (delivered_at, due_at);
COMMIT;
