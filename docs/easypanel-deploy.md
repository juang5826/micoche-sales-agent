# EasyPanel Deploy (VPS Mi Coche)

## 1) Crear servicio

1. Tipo: App / Dockerfile.
2. Nombre: `micoche-sales-agent`.
3. Puerto interno: `8080`.

## 2) Variables minimas

1. `TENANT_KEY=micoche`
2. `EXPECTED_SUBDOMAIN=auxcontablemicoche`
3. `EXPECTED_SOURCE_ID=573174274959`
4. `KOMMO_MCP_URL=https://tgvfvsruvfzrmfohbgwx.supabase.co/functions/v1/kommo-mcp`
5. `KOMMO_MCP_API_KEY` si lo activas en Supabase
6. PostgreSQL Supabase:
   - `SUPABASE_DB_URL` con `sslmode=require`, o
   - `SUPABASE_DB_PASSWORD` para inferir el host.
7. `OPENAI_API_KEY` si quieres respuestas generadas con LLM.

## 3) Healthcheck

1. Path: `/health`
2. Interval: 15s
3. Timeout: 5s
4. Retries: 5

## 4) Validacion post deploy

1. `GET /health` devuelve `status=ok`.
2. `POST /chat` responde con `route=informacion_micoche`.
3. Webhook de prueba a `/webhooks/kommo`.
4. Verificar en `/health` que `kommo` y `postgres` esten en `ok` o `disabled`.
