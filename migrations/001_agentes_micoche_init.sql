CREATE SCHEMA IF NOT EXISTS agentes_micoche;

CREATE TABLE IF NOT EXISTS agentes_micoche.chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agentes_micoche.chat_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agentes_micoche.chat_sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agentes_micoche.tool_events (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT,
    user_id TEXT,
    tool_name TEXT NOT NULL,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms INTEGER,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agentes_micoche.agent_runs (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT,
    user_id TEXT,
    model_id TEXT,
    input_message TEXT NOT NULL,
    output_message TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms INTEGER,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agentes_micoche.rag_products (
    id BIGSERIAL PRIMARY KEY,
    product_id TEXT UNIQUE NOT NULL,
    sku TEXT,
    name TEXT NOT NULL,
    category TEXT,
    brand TEXT,
    price NUMERIC(18,2),
    currency TEXT,
    stock_qty NUMERIC(18,3),
    unit TEXT,
    description TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    source JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    search_text TSVECTOR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agentes_micoche.rag_product_chunks (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES agentes_micoche.rag_products(id) ON DELETE CASCADE,
    chunk_no INTEGER NOT NULL CHECK (chunk_no >= 0),
    chunk_text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_text TSVECTOR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(product_id, chunk_no)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON agentes_micoche.chat_messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_events_session_created
    ON agentes_micoche.tool_events(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_runs_session_created
    ON agentes_micoche.agent_runs(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_rag_products_search_text
    ON agentes_micoche.rag_products USING GIN(search_text);

CREATE INDEX IF NOT EXISTS idx_rag_products_category
    ON agentes_micoche.rag_products(category);

CREATE INDEX IF NOT EXISTS idx_rag_products_brand
    ON agentes_micoche.rag_products(brand);

CREATE INDEX IF NOT EXISTS idx_rag_products_sku
    ON agentes_micoche.rag_products(sku);

CREATE INDEX IF NOT EXISTS idx_rag_products_updated_at
    ON agentes_micoche.rag_products(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_product_chunks_search_text
    ON agentes_micoche.rag_product_chunks USING GIN(search_text);

CREATE INDEX IF NOT EXISTS idx_rag_product_chunks_product_id
    ON agentes_micoche.rag_product_chunks(product_id);

