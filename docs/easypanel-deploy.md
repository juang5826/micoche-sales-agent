# EasyPanel Deploy (VPS Mi Coche)

## 1) Crear servicio

1. Tipo: App / Dockerfile.
2. Nombre: `micoche-sales-agent`.
3. Puerto interno: `8080`.

## 2) Variables mínimas

1. `TENANT_KEY=micoche`
2. `EXPECTED_SUBDOMAIN=auxcontablemicoche`
3. `EXPECTED_SIMPLYBOOK_LOGIN=micochemos`
4. `EXPECTED_SOURCE_ID=573174274959`
5. `KOMMO_MCP_URL=https://tgvfvsruvfzrmfohbgwx.supabase.co/functions/v1/kommo-mcp`
6. `SIMPLYBOOK_MCP_URL=https://tgvfvsruvfzrmfohbgwx.supabase.co/functions/v1/simplybook-mcp`
7. `KOMMO_MCP_API_KEY` y `SIMPLYBOOK_MCP_API_KEY` (si los activas en Supabase)
8. PostgreSQL Supabase:
   - `SUPABASE_DB_URL` (connection string completa con `sslmode=require`), o
   - `SUPABASE_DB_PASSWORD` (el host se infiere desde `KOMMO_MCP_URL`).

## 3) Healthcheck

1. Path: `/health`
2. Interval: 15s
3. Timeout: 5s
4. Retries: 5

## 4) Validación post deploy

1. `GET /health` devuelve `status=ok`.
2. `POST /mcp/call` con `provider=kommo` y `tool_name=kommo_get_account`.
3. `POST /mcp/call` con `provider=simplybook` y `tool_name=simplybook_get_company_info`.
4. Webhook test a `/webhooks/kommo` con payload de ejemplo.
