# Mi Coche Sales Agent

Servicio FastAPI reducido al caso de uso actual: responder informacion general de Mi Coche.

## Alcance actual

1. Respuestas sobre categorias, horarios, ubicacion, medios de pago y requisitos.
2. Uso opcional de OpenAI para redactar la respuesta con base en una base fija.
3. Integracion con `kommo-mcp` para recibir mensajes por webhook y devolver la respuesta al lead.
4. Dedupe y buffer anti-spam por lead.
5. Persistencia opcional en PostgreSQL para buffer y trazas.

## Endpoints

1. `GET /health`
2. `POST /chat`
3. `POST /webhooks/kommo`
4. `GET /metrics`

## Configuracion

1. Copia `.env.example` a `.env`.
2. Configura `KOMMO_MCP_URL` y `KOMMO_MCP_API_KEY` si aplica.
3. Ajusta IDs de Kommo si cambian.
4. Configura `OPENAI_API_KEY` si quieres respuestas generadas por LLM.
5. Para persistencia PostgreSQL en `agentes_micoche`, usa una de estas opciones:
   - `SUPABASE_DB_URL` completa, o
   - `SUPABASE_DB_PASSWORD` para inferir el host desde `KOMMO_MCP_URL`.

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

## Pruebas

```bash
python -m pytest -q
```

## EasyPanel

1. Crear servicio `micoche-sales-agent`.
2. Build context: este directorio.
3. Puerto: `8080`.
4. Healthcheck: `GET /health`.
5. Variables: las de `.env.example`.

## Migracion DB

Archivos:

1. `migrations/001_agentes_micoche_init.sql`
2. `migrations/002_webhook_state.sql`

El servicio sigue usando el esquema `agentes_micoche` para persistir trazas del chat y el estado del webhook.
