# Mi Coche Sales Agent (MAF-style)

Servicio FastAPI para reemplazar el flujo n8n del agente comercial de Mi Coche, integrado con:

1. `kommo-mcp` (Supabase)
2. `simplybook-mcp` (Supabase)

## Endpoints

1. `GET /health`
2. `POST /chat`
3. `POST /mcp/call`
4. `POST /webhooks/kommo`
5. `GET /metrics`
6. `DELETE /threads/{thread_id}`

## Comportamiento clave implementado

1. Validación de subdominio esperado (`auxcontablemicoche`).
2. Dedupe de eventos con TTL.
3. Buffer anti-spam por lead (ventana + máximo mensajes).
4. Consolidación de mensajes en un único input al orquestador.
5. Validación de source esperada y switch IA antes de responder.
6. Escritura de respuesta en custom field y disparo de salesbot.

## Configuración

1. Copia `.env.example` a `.env`.
2. Completa claves MCP (`KOMMO_MCP_API_KEY`, `SIMPLYBOOK_MCP_API_KEY`) si aplican.
3. Ajusta IDs de Kommo si cambian.
4. Para persistencia PostgreSQL en `agentes_micoche`, usa una de estas opciones:
   - `SUPABASE_DB_URL` completa, o
   - `SUPABASE_DB_PASSWORD` (el host `db.<project-ref>.supabase.co` se infiere desde `KOMMO_MCP_URL`).

## Ejecutar local

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Docker

```bash
docker build -t micoche-sales-agent .
docker run --env-file .env -p 8080:8080 micoche-sales-agent
```

## EasyPanel (VPS Mi Coche)

1. Crear nuevo servicio `micoche-sales-agent`.
2. Build context: este directorio.
3. Puerto: `8080`.
4. Healthcheck: `GET /health`.
5. Variables: las de `.env.example`.

## Migración DB (opcional recomendada)

Archivo:

1. `migrations/001_agentes_micoche_init.sql`

Define esquema `agentes_micoche` con tablas de memoria de chat, eventos de tools, corridas de agente y tablas RAG base.

Cuando `SUPABASE_DB_URL` o `SUPABASE_DB_PASSWORD` está definido:

1. El agente valida conexión Postgres en startup.
2. `chat_sessions` y `chat_messages` se guardan en DB.
3. `tool_events` y `agent_runs` se registran automáticamente.
