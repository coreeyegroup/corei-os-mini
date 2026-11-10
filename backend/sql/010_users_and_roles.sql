-- Users, roles and server-side sessions (Process Flow v1.4 §7, Blueprint §15).
-- Roles: ADMIN (users only), TRADING_OPS (configuration), RISK_APPROVER (order-sending
-- approval), OBSERVER (read only). A user may hold several roles.

CREATE TABLE cfg_users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE CHECK (username ~ '^[a-z][a-z0-9._-]{2,63}$'),
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    password_hash TEXT NOT NULL,
    roles TEXT[] NOT NULL CHECK (cardinality(roles) >= 1
        AND roles <@ ARRAY['ADMIN', 'TRADING_OPS', 'RISK_APPROVER', 'OBSERVER']::TEXT[]),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    must_change_password BOOLEAN NOT NULL DEFAULT true,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only a SHA-256 of the session token is stored; the token lives in the browser cookie.
CREATE TABLE cfg_sessions (
    token_hash BYTEA PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES cfg_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cfg_sessions_user ON cfg_sessions (user_id);
