from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.db_store import PostgresStore
from app.llm_client import OpenAIHTTPClient
from app.mcp_clients import KommoMCPClient
from app.metrics import MetricsRegistry
from app.models import ChatRequest, ChatResponse, HealthResponse, WebhookAck
from app.orchestrator import MiCocheMAFOrchestrator
from app.webhook_processor import KommoWebhookProcessor

settings: Settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(settings.app_name)

metrics = MetricsRegistry()
integration_state: dict[str, str] = {
    "kommo": "unknown",
    "postgres": "unknown",
    "openai": "configured" if settings.openai_api_key else "disabled",
}
db_store = PostgresStore(settings.resolved_supabase_db_url())

kommo_client = KommoMCPClient(
    base_url=settings.kommo_mcp_url,
    api_key=settings.kommo_mcp_api_key,
    timeout_seconds=settings.request_timeout_seconds,
)
llm_client = OpenAIHTTPClient(
    api_key=settings.openai_api_key,
    model=settings.openai_model,
    timeout_seconds=settings.request_timeout_seconds,
    base_url=settings.openai_base_url,
)
orchestrator = MiCocheMAFOrchestrator(
    llm=llm_client,
    temperature=settings.llm_temperature,
    max_output_tokens=settings.llm_max_output_tokens,
)
webhook_processor = KommoWebhookProcessor(
    settings=settings,
    kommo_client=kommo_client,
    orchestrator=orchestrator,
    metrics=metrics,
    db_store=db_store,
)


def _validate_integrations() -> None:
    account = kommo_client.get_account()
    subdomain = str(account.get("subdomain", "")).strip().lower()
    if settings.expected_subdomain and subdomain != settings.expected_subdomain.strip().lower():
        raise RuntimeError(
            f"Kommo subdomain mismatch. expected={settings.expected_subdomain} got={subdomain}"
        )
    integration_state["kommo"] = "ok"

    db_health = db_store.health()
    if db_health.enabled:
        if not db_health.ok:
            raise RuntimeError(f"Postgres health failed: {db_health.detail}")
        integration_state["postgres"] = "ok"
    else:
        integration_state["postgres"] = "disabled"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.startup_validate_integrations:
        try:
            await run_in_threadpool(_validate_integrations)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Startup integration validation failed.")
            integration_state["kommo"] = "error"
            integration_state["postgres"] = "error"
            raise RuntimeError(str(exc)) from exc
    await webhook_processor.start()
    yield
    await webhook_processor.stop()
    db_store.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.environment,
        tenant=settings.tenant_key,
        integrations=integration_state,
    )


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_endpoint() -> str:
    return metrics.as_prometheus()


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    metrics.inc("chat_requests_total")
    started = perf_counter()
    inferred_thread = request.thread_id or str(request.context_hint.get("lead_id") or uuid4())
    user_id = str(request.context_hint.get("user_id")) if request.context_hint.get("user_id") else None
    try:
        await run_in_threadpool(
            db_store.ensure_session,
            inferred_thread,
            user_id,
            request.context_hint,
        )
        result = await run_in_threadpool(
            orchestrator.answer,
            request.message,
            inferred_thread,
            request.context_hint,
        )
        await run_in_threadpool(db_store.add_message, inferred_thread, "user", request.message)
        await run_in_threadpool(db_store.add_message, inferred_thread, "assistant", result.answer)
        await run_in_threadpool(
            db_store.log_agent_run,
            inferred_thread,
            user_id,
            settings.openai_model if llm_client.enabled else None,
            request.message,
            result.answer,
            True,
            int((perf_counter() - started) * 1000),
            None,
        )
        return ChatResponse(
            thread_id=inferred_thread,
            route=result.route,
            answer=result.answer,
            metadata=result.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        metrics.inc("chat_errors_total")
        logger.exception("Chat endpoint failed.")
        await run_in_threadpool(
            db_store.log_agent_run,
            inferred_thread,
            user_id,
            settings.openai_model if llm_client.enabled else None,
            request.message,
            None,
            False,
            int((perf_counter() - started) * 1000),
            str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhooks/kommo", response_model=WebhookAck)
async def kommo_webhook_endpoint(request: Request) -> WebhookAck:
    try:
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        result = await webhook_processor.ingest(raw_body=body, headers=headers)
        return WebhookAck(**result)
    except ValueError as exc:
        metrics.inc("webhook_errors_total")
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        metrics.inc("webhook_errors_total")
        logger.exception("Webhook endpoint failed.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
