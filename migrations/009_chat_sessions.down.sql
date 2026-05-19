-- Rollback of 009_chat_sessions.sql

DROP TRIGGER IF EXISTS chat_messages_bump_session ON chat_messages;
DROP FUNCTION IF EXISTS chat_sessions_bump_updated_at();
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_sessions;
