BEGIN;
-- docs/memory_design.md の定義へ合わせる。データは失わず、名前と置き場所だけを直す。
--
--   Memory  … ユーザーを覚える      memory_short / memory_long / memory_concern
--   Living  … かぐやの今の状態      living_emotion / living_activity
--   Persona … かぐやの性格          persona_character / persona_favorite
--
-- 以前は「どう保存しているか」で分かれていたため、ユーザーについての情報が
-- mind_* に混ざっていた。ここで「誰の情報か」で並べ直す。

-- --- Memory系 -------------------------------------------------------------
ALTER TABLE raw_memory RENAME TO memory_short;
ALTER INDEX raw_memory_created RENAME TO memory_short_created;
ALTER INDEX raw_memory_processed RENAME TO memory_short_processed;

ALTER TABLE wisdom RENAME TO memory_long;
-- 記憶したときの感情。-1=ネガティブ / 0=普通 / +1=ポジティブ。
-- 「いまの気分に近い記憶を思い出しやすくする」ためだけに使うので3値に留める。
ALTER TABLE memory_long ADD COLUMN IF NOT EXISTS tone smallint NOT NULL DEFAULT 0
    CHECK (tone BETWEEN -1 AND 1);
-- 想起は topic_key と summary の部分一致から始まる。件数が増えると毎回の会話が
-- 遅くなるため、更新順の取り出しに索引を張る。
CREATE INDEX IF NOT EXISTS memory_long_updated ON memory_long (updated_at DESC, id DESC);

-- 「気がかり」は短期でも長期でもない3本目の記憶（人間でいう展望記憶）。
-- ユーザーの予定・体調なので Memory系へ移す。
ALTER TABLE mind_open_loops RENAME TO memory_concern;
ALTER INDEX mind_open_loops_due RENAME TO memory_concern_due;

-- --- Living系 -------------------------------------------------------------
ALTER TABLE mind_emotions RENAME TO living_emotion;

-- 活動・元気さ・会った記録。これまで端末ごとの localStorage と app_settings の
-- ledger に散らばっていたものを1行へまとめる。かぐやはPC上に1人しかいないので、
-- 端末ごとに持つとPCとiPhoneで別々の行動をしてしまう。
CREATE TABLE IF NOT EXISTS living_activity (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    activity text NOT NULL DEFAULT 'idle',
    energy smallint NOT NULL DEFAULT 60 CHECK (energy BETWEEN 0 AND 100),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    hour_counts jsonb NOT NULL DEFAULT '[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]',
    chats integer NOT NULL DEFAULT 0 CHECK (chats >= 0),
    days jsonb NOT NULL DEFAULT '[]',
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 会話回数は mind_meta から、慣れ・利用日数は ledger から引き継ぐ。
-- 取り出せなければ既定値のまま始める（育ち直すだけで、会話も記憶も壊れない）。
INSERT INTO living_activity (id, chats, days, updated_at)
SELECT true,
    GREATEST(
        COALESCE((SELECT value::integer FROM mind_meta WHERE key = 'interactions'), 0),
        COALESCE((SELECT (value->>'relationship_chats')::integer FROM app_settings WHERE key = 'ledger'), 0)
    ),
    COALESCE((SELECT value->'relationship_days' FROM app_settings WHERE key = 'ledger'), '[]'::jsonb),
    now()
WHERE NOT EXISTS (SELECT 1 FROM living_activity)
ON CONFLICT (id) DO NOTHING;

-- --- Persona系 ------------------------------------------------------------
ALTER TABLE persona RENAME TO persona_character;
ALTER TABLE mind_traits RENAME TO persona_favorite;

-- 話し方フィードバックは ledger ではなく、他の接し方と同じ場所へ置く。
-- 自動更新の対象外なので locked を立てる。
INSERT INTO persona_character (key, value, locked)
SELECT 'style_feedback', to_jsonb(value->>'relationship_style_hint'), true
FROM app_settings
WHERE key = 'ledger' AND COALESCE(value->>'relationship_style_hint', '') <> ''
ON CONFLICT (key) DO NOTHING;

-- --- 廃止 -----------------------------------------------------------------
-- mind_phrases    : ユーザーの口癖。プロンプトへ渡しておらず表示のみだった
-- mind_graph_edges: 「かぐや→likes→対象」しか作らず mind_traits の写しだった
-- mind_meta       : 会話回数のみ。living_activity.chats へ移した
DROP TABLE IF EXISTS mind_phrases;
DROP TABLE IF EXISTS mind_graph_edges;
DROP TABLE IF EXISTS mind_meta;

-- ledger から記憶に当たるものを取り除く。残るのは整理回数と声かけの記録だけ。
UPDATE app_settings SET value = value - 'relationship_chats' - 'relationship_days'
    - 'relationship_style_hint' - 'relationship_style_at' - 'relationship_first_seen',
    updated_at = now()
WHERE key = 'ledger';

COMMIT;
