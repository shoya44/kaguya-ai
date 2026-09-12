BEGIN;
-- 「同じ状態が繰り返されると性格になる」ための集計値。
-- 感情の履歴そのものは残さない。毎ターン1行増える記録は掃除も要るうえ、
-- 週次で傾向を見るのに必要なのは「その状態が何回あったか」だけだった。
ALTER TABLE living_emotion ADD COLUMN IF NOT EXISTS samples integer NOT NULL DEFAULT 0
    CHECK (samples >= 0);
ALTER TABLE living_emotion ADD COLUMN IF NOT EXISTS high_count integer NOT NULL DEFAULT 0
    CHECK (high_count >= 0);
COMMIT;
