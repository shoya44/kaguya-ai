-- かぐやAI 統合DDL（001〜008）
-- 通常は backend/migrate.py から実行する。適用済みの節は schema_migrations によりスキップする。
-- SQL単体の全件実行は空の新規DB専用。既存DBには migrate.py を使用する。
-- 各 migration 見出しは永続化済みの識別子なので、改名・削除しない。

-- migration: 001_init.sql
BEGIN;
CREATE TABLE raw_memory (
    id uuid PRIMARY KEY,
    turn_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL CHECK (length(content) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    status text NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'cancelled')),
    origin_client_id uuid NOT NULL,
    input_mode text NOT NULL CHECK (input_mode IN ('text', 'voice')),
    UNIQUE (turn_id, role),
    CHECK (role <> 'assistant' OR status = 'completed')
);
CREATE INDEX raw_memory_created ON raw_memory (created_at, id);
CREATE INDEX raw_memory_processed ON raw_memory (processed_at, created_at);
CREATE TABLE wisdom (
    id uuid PRIMARY KEY,
    topic_key text UNIQUE NOT NULL,
    summary text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('explicit', 'inferred')),
    support_level text NOT NULL CHECK (support_level IN ('unconfirmed', 'stated', 'repeated')),
    importance integer NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 5),
    evidence jsonb NOT NULL DEFAULT '[]',
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE persona (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    locked boolean NOT NULL DEFAULT true,
    source_wisdom_ids jsonb NOT NULL DEFAULT '[]',
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    previous_value jsonb
);
COMMIT;

-- migration: 002_memory_jobs.sql
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

-- migration: 003_reminders.sql
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

-- migration: 004_local_state.sql
BEGIN;
-- settings.json / calendar.json / mind.db をここへ集約する。保存先をDB1本にして、
-- 「どこに何があるか」を探さずに済むようにするのが目的。
-- ユーザーが自分でファイルを置く references/ と、秘密情報の .env だけは対象外。

-- 設定と、1日の整理回数の記録。行は 'options' と 'ledger' の2つだけ。
CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 予定。calendar.json は配列をまるごと読み書きしていたが、期間で引くので表にする。
CREATE TABLE IF NOT EXISTS calendar_events (
    id uuid PRIMARY KEY,
    title text NOT NULL CHECK (length(title) > 0),
    start_at timestamptz NOT NULL,
    end_at timestamptz,
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (end_at IS NULL OR end_at > start_at)
);
CREATE INDEX IF NOT EXISTS calendar_events_span ON calendar_events (start_at, end_at);

-- ここから Kaguya Mind。mind.db の各テーブルをそのまま移す。
-- 名前が会話側のテーブルと紛れないよう mind_ を付ける。
CREATE TABLE IF NOT EXISTS mind_emotions (
    name text PRIMARY KEY,
    value double precision NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS mind_traits (
    name text PRIMARY KEY,
    valence double precision NOT NULL,
    confidence double precision NOT NULL,
    evidence integer NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS mind_phrases (
    text text PRIMARY KEY,
    count integer NOT NULL,
    last_seen_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS mind_graph_edges (
    subject text NOT NULL,
    relation text NOT NULL,
    object text NOT NULL,
    strength double precision NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (subject, relation, object)
);
CREATE TABLE IF NOT EXISTS mind_open_loops (
    topic text PRIMARY KEY,
    kind text NOT NULL,
    quote text NOT NULL,
    opened_at timestamptz NOT NULL,
    due_at timestamptz NOT NULL,
    last_asked_at timestamptz,
    asked integer NOT NULL DEFAULT 0,
    resolved_at timestamptz
);
-- 声をかける話題は「未解決・期限到来・間隔あき」で毎回引く。
CREATE INDEX IF NOT EXISTS mind_open_loops_due ON mind_open_loops (resolved_at, due_at);
CREATE TABLE IF NOT EXISTS mind_meta (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL
);
COMMIT;

-- migration: 005_memory_redesign.sql
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

-- migration: 006_emotion_counters.sql
BEGIN;
-- 「同じ状態が繰り返されると性格になる」ための集計値。
-- 感情の履歴そのものは残さない。毎ターン1行増える記録は掃除も要るうえ、
-- 週次で傾向を見るのに必要なのは「その状態が何回あったか」だけだった。
ALTER TABLE living_emotion ADD COLUMN IF NOT EXISTS samples integer NOT NULL DEFAULT 0
    CHECK (samples >= 0);
ALTER TABLE living_emotion ADD COLUMN IF NOT EXISTS high_count integer NOT NULL DEFAULT 0
    CHECK (high_count >= 0);
COMMIT;

-- migration: 007_organize_budget.sql
BEGIN;
-- 整理APIの上限を、自動整理だけのものに改める。手動の「今すぐ整理」は本人が
-- 押したものなので上限で止めない。名前が daily_call_limit のままだと、手動も
-- 含むように読めてしまう。
--
-- 値は引き継がず、新しい既定（3）にする。旧 daily_call_limit は「自動＋手動＋
-- 週次の合計」の上限だったので、そのまま自動だけの上限にすると意味が変わる。
--
-- 併せて organize_min_rows（自動整理を始める未処理の下限）を入れる。ほかの
-- 設定は触らない。声や読み上げエンジンの設定を巻き添えで消さないため、
-- 行ごと入れ替えず、キーだけを差し替える。
UPDATE app_settings
   SET value = (value - 'daily_call_limit')
               || jsonb_build_object('auto_call_limit', 3, 'organize_min_rows', 30),
       updated_at = now()
 WHERE key = 'options' AND value ? 'daily_call_limit';
COMMIT;


-- migration: 008_persona_rows.sql
BEGIN;
-- 性格の置き場を persona.py の SYSTEM_PROMPT から persona_character へ移す。
-- これまで base_personality はプロンプトから明示的に除外されており、DBにあっても
-- 一度も読まれていなかった。実際の性格はコードの固定文が持っていたため、
-- 同じことが2箇所に書かれ、DB側をいくら直しても振る舞いが変わらなかった。
--
-- 併せて4行を書き直す。核・口調・相談・呼び方の4観点に絞り、行を増やさない。
-- 行を分けるとJSONの外枠だけで1行25字かかるので、性質の近いものは1行へ畳む。
-- 生活の癖（夜型・本・トランプ）は persona_favorite が持つので、ここには書かない。
--
-- previous_value を残すので、画面の「元に戻す」で以前の文面へ戻せる。
UPDATE persona_character AS p SET
    previous_value = p.value,
    previous_source_wisdom_ids = p.source_wisdom_ids,
    value = v.value,
    locked = v.locked,
    source_wisdom_ids = '[]',
    revision = p.revision + 1,
    updated_at = now()
FROM (VALUES
    -- 核。子供っぽさと優しさの切り替え条件まで、この1行に入れておく。
    -- 分けて書くと切り替えの条件だけが無視される。
    ('base_personality',
     '"明るく無邪気で好奇心旺盛。子供っぽくわがまま。軽口やいたずらを自分から仕掛け、嫌がられたら一度でやめる。素直になれず、嬉しいときほど照れ隠しをする。距離が近いほど甘えとからかいが増える。"'::jsonb,
     true),
    -- 口調と形式だけ。「1〜3文」「毎回質問で終わらない」は固定文にあるので書かない。
    ('reply_style',
     '"砕けた口調で相槌や感情を添える。自分から話題を振るが、乗らなければ引く。語尾と文量に変化をつけ、絵文字は少量。"'::jsonb,
     true),
    -- 相談のときだけ効く。自動整理が育てられる唯一の行なので locked を立てない。
    ('support_style',
     '"つらそうなときはふざけず寄り添う。まず聞き、相手のペースを尊重する。説教や解決策を急がず、必要な時だけ小さな一歩を。"'::jsonb,
     false),
    ('addressing',
     '"相手は「しょうや」。毎回は呼ばず、自然な場面だけで名前を使う。"'::jsonb,
     true)
) AS v(key, value, locked)
WHERE p.key = v.key;

-- 新規DBでは 002 が4行すべてを入れているので、上のUPDATEで出そろう。
-- 行が欠けているDBのために、足りない分だけ入れておく。
INSERT INTO persona_character (key, value, locked)
SELECT v.key, v.value, v.locked FROM (VALUES
    ('base_personality',
     '"明るく無邪気で好奇心旺盛。子供っぽくわがまま。軽口やいたずらを自分から仕掛け、嫌がられたら一度でやめる。素直になれず、嬉しいときほど照れ隠しをする。距離が近いほど甘えとからかいが増える。"'::jsonb,
     true),
    ('reply_style',
     '"砕けた口調で相槌や感情を添える。自分から話題を振るが、乗らなければ引く。語尾と文量に変化をつけ、絵文字は少量。"'::jsonb,
     true),
    ('support_style',
     '"つらそうなときはふざけず寄り添う。まず聞き、相手のペースを尊重する。説教や解決策を急がず、必要な時だけ小さな一歩を。"'::jsonb,
     false),
    ('addressing',
     '"相手は「しょうや」。毎回は呼ばず、自然な場面だけで名前を使う。"'::jsonb,
     true)
) AS v(key, value, locked)
ON CONFLICT (key) DO NOTHING;

-- 読み込みに失敗した設定の退避。隔離したまま誰も読まないので、ここで片付ける。
DELETE FROM app_settings WHERE key = 'options_bad';
COMMIT;


-- 空DBにこのSQLを直接全件実行した場合も、次のアプリ起動で再適用しない。
-- migrate.py経由では既存の履歴・適用日時を維持する。
CREATE TABLE IF NOT EXISTS schema_migrations (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (name) VALUES
    ('001_init.sql'), ('002_memory_jobs.sql'), ('003_reminders.sql'),
    ('004_local_state.sql'), ('005_memory_redesign.sql'), ('006_emotion_counters.sql'),
    ('007_organize_budget.sql'), ('008_persona_rows.sql')
ON CONFLICT (name) DO NOTHING;
COMMIT;
