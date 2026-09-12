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
