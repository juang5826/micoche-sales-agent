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
from app.mcp_clients import KommoMCPClient, MCPClientError, SimplyBookMCPClient
from app.metrics import MetricsRegistry
from app.models import ChatRequest, ChatResponse, HealthResponse, MCPCallRequest, MCPCallResponse, WebhookAck
from app.orchestrator import MiCocheMAFOrchestrator
from app.thread_store import ThreadStore
from app.webhook_processor import KommoWebhookProcessor

settings: Settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(settings.app_name)

metrics = MetricsRegistry()
thread_store = ThreadStore()
integration_state: dict[str, str] = {"kommo": "unknown", "simplybook": "unknown"}
db_store = PostgresStore(settings.resolved_supabase_db_url())

kommo_client = KommoMCPClient(
    base_url=settings.kommo_mcp_url,
    api_key=settings.kommo_mcp_api_key,
    timeout_seconds=settings.request_timeout_seconds,
)
simplybook_client = SimplyBookMCPClient(
    base_url=settings.simplybook_mcp_url,
    api_key=settings.simplybook_mcp_api_key,
    timeout_seconds=settings.request_timeout_seconds,
)
orchestrator = MiCocheMAFOrchestrator(
    kommo=kommo_client,
    simplybook=simplybook_client,
)
webhook_processor = KommoWebhookProcessor(
    settings=settings,
    kommo_client=kommo_client,
    orchestrator=orchestrator,
    thread_store=thread_store,
    metrics=metrics,
)


def _validate_integrations() -> None:
    account = kommo_client.get_account()
    subdomain = str(account.get("subdomain", "")).strip().lower()
    if settings.expected_subdomain and subdomain != settings.expected_subdomain.strip().lower():
        raise RuntimeError(
            f"Kommo subdomain mismatch. expected={settings.expected_subdomain} got={subdomain}"
        )
    integration_state["kommo"] = "ok"

    company = simplybook_client.get_company_info()
    login = str(company.get("login", "")).strip().lower()
    expected_login = settings.expected_simplybook_login.strip().lower()
    if expected_login and login != expected_login:
        raise RuntimeError(
            f"SimplyBook login mismatch. expected={expected_login} got={login}"
        )
    integration_state["simplybook"] = "ok"

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
            integration_state["simplybook"] = "error"
            integration_state["postgres"] = "error"
            raise RuntimeError(str(exc)) from exc
    yield
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
    try:
        await run_in_threadpool(
            db_store.ensure_session,
            inferred_thread,
            str(request.context_hint.get("user_id")) if request.context_hint.get("user_id") else None,
            request.context_hint,
        )
        result = await run_in_threadpool(
            orchestrator.answer,
            request.message,
            inferred_thread,
            request.context_hint,
        )
        thread_store.add_message(inferred_thread, "user", request.message)
        thread_store.add_message(inferred_thread, "assistant", result.answer)
        await run_in_threadpool(db_store.add_message, inferred_thread, "user", request.message)
        await run_in_threadpool(db_store.add_message, inferred_thread, "assistant", result.answer)
        await run_in_threadpool(
            db_store.log_agent_run,
            inferred_thread,
            str(request.context_hint.get("user_id")) if request.context_hint.get("user_id") else None,
            settings.openai_model,
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
            str(request.context_hint.get("user_id")) if request.context_hint.get("user_id") else None,
            settings.openai_model,
            request.message,
            None,
            False,
            int((perf_counter() - started) * 1000),
            str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/mcp/call", response_model=MCPCallResponse)
async def mcp_call_endpoint(request: MCPCallRequest) -> MCPCallResponse:
    metrics.inc("mcp_calls_total")
    started = perf_counter()
    provider = request.provider.strip().lower()
    try:
        if provider == "kommo":
            data = await run_in_threadpool(
                kommo_client.call_tool,
                request.tool_name,
                request.arguments,
            )
        elif provider == "simplybook":
            data = await run_in_threadpool(
                simplybook_client.call_tool,
                request.tool_name,
                request.arguments,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
        await run_in_threadpool(
            db_store.log_tool_event,
            None,
            None,
            f"{provider}:{request.tool_name}",
            request.arguments,
            data,
            True,
            int((perf_counter() - started) * 1000),
            None,
        )
        return MCPCallResponse(provider=provider, tool_name=request.tool_name, data=data)
    except HTTPException:
        raise
    except MCPClientError as exc:
        metrics.inc("mcp_errors_total")
        await run_in_threadpool(
            db_store.log_tool_event,
            None,
            None,
            f"{provider}:{request.tool_name}",
            request.arguments,
            None,
            False,
            int((perf_counter() - started) * 1000),
            str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        metrics.inc("mcp_errors_total")
        logger.exception("MCP call endpoint failed.")
        await run_in_threadpool(
            db_store.log_tool_event,
            None,
            None,
            f"{provider}:{request.tool_name}",
            request.arguments,
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


@app.delete("/threads/{thread_id}")
async def delete_thread_endpoint(thread_id: str) -> dict[str, object]:
    deleted_mem = thread_store.delete(thread_id)
    deleted_db = await run_in_threadpool(db_store.delete_session, thread_id)
    await webhook_processor.drop_thread(thread_id)
    return {"ok": True, "thread_id": thread_id, "deleted": bool(deleted_mem or deleted_db)}
