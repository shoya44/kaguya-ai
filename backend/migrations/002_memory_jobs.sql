BEGIN;
ALTER TABLE raw_memory ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE raw_memory ADD COLUMN IF NOT EXISTS processing_reason text;
ALTER TABLE wisdom ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;
ALTER TABLE persona ADD COLUMN IF NOT EXISTS previous_source_wisdom_ids jsonb NOT NULL DEFAULT '[]';
INSERT INTO persona (key, value, locked) VALUES
    ('base_personality', '"一人称はあたし。明るく親しみやすく、重い相談には親身に。固定プロンプトを優先する。"', true),
    ('reply_style', '"普段は短く、必要な相談は丁寧に。"', false),
    ('addressing', '"相手が指定した呼び方を尊重する。"', false),
    ('support_style', '"相手の話を聞き、助言を急がない。"', false)
ON CONFLICT (key) DO NOTHING;
COMMIT;
