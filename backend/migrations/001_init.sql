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
