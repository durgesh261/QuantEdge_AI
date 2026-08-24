-- V7: Add password_reset_tokens table
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    token_hash  VARCHAR(64)  NOT NULL UNIQUE,
    user_id     UUID         NOT NULL,
    expires_at  TIMESTAMPTZ  NOT NULL,
    used        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_prt_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prt_token_hash ON password_reset_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_prt_user_id    ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_prt_expires_at ON password_reset_tokens(expires_at);
