-- Multi-device chat history for the Crowe Logic chat surfaces
-- (chat.crowelogic.com, future iOS, IDE assistants). Each session
-- belongs to one workspace + user; messages stream-append as the
-- assistant generates them, so the table is the durable transcript.
--
-- Idempotent. Indexes are conservative: list-by-user is the hot path
-- on the sidebar; replay-by-session is the hot path when a session
-- is opened.

CREATE TABLE IF NOT EXISTS chat_sessions (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL DEFAULT 'New chat',
    model         TEXT,                                -- CroweLM codename at creation
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sidebar list query: "my sessions, most-recent first".
CREATE INDEX IF NOT EXISTS chat_sessions_by_user_recency
    ON chat_sessions (user_id, updated_at DESC);

-- Workspace-scoped admin views.
CREATE INDEX IF NOT EXISTS chat_sessions_by_workspace
    ON chat_sessions (workspace_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                          -- user | assistant | system
    content     TEXT NOT NULL,
    -- {model, tokens, reasoning_tokens, latency_ms, ttft_ms, ...}
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Replay query: "all messages for this session, in order".
CREATE INDEX IF NOT EXISTS chat_messages_by_session
    ON chat_messages (session_id, created_at);

-- Auto-bump the session's updated_at whenever a message lands so
-- the sidebar's "most-recent first" stays correct without an
-- explicit UPDATE chat_sessions in the message-insert path.
CREATE OR REPLACE FUNCTION chat_sessions_bump_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE chat_sessions
       SET updated_at = NEW.created_at
     WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chat_messages_bump_session ON chat_messages;
CREATE TRIGGER chat_messages_bump_session
AFTER INSERT ON chat_messages
FOR EACH ROW
EXECUTE FUNCTION chat_sessions_bump_updated_at();
