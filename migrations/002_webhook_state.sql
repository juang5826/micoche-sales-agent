CREATE TABLE IF NOT EXISTS agentes_micoche.webhook_dedupe_events (
    event_id TEXT PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_dedupe_expires
    ON agentes_micoche.webhook_dedupe_events(expires_at);

CREATE TABLE IF NOT EXISTS agentes_micoche.webhook_lead_buffer (
    lead_id BIGINT PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    flush_after TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_buffer_flush_after
    ON agentes_micoche.webhook_lead_buffer(flush_after);

