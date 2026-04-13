from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework import AgentSession, tool

from app.micoche_knowledge import MICOCHE_INFO_PROMPT
from app.rag_client import RAGClient
from app.utils import sanitize_plain_text

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    route: str
    answer: str
    metadata: dict[str, Any]


# RAG client singleton — set by MiCocheMAFOrchestrator.__init__
_rag_client: RAGClient | None = None


@tool
def buscar_informacion(consulta: str) -> str:
    """Busca informacion sobre cursos, precios, horarios, requisitos, combos y politicas de Mi Coche.
    Usa esta herramienta SIEMPRE que el cliente pregunte por precios, costos, horas,
    requisitos, horarios, medios de pago, combos, promociones o cualquier dato especifico
    de los cursos de conduccion."""
    if _rag_client and _rag_client.enabled:
        context = _rag_client.search_as_context(consulta)
        if context:
            return context
    return "No se encontro informacion especifica. Ofrece conectar con un asesor."


class MiCocheMAFOrchestrator:
    """
    Orquestador basado en Microsoft Agent Framework.
    Usa OpenAIChatCompletionClient con sessions para memoria de conversacion
    y RAG via function tool para buscar informacion en Supabase.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.4-mini",
        temperature: float = 0.5,
        max_output_tokens: int = 380,
        rag_client: RAGClient | None = None,
        db_store: Any = None,
    ) -> None:
        global _rag_client
        self.api_key = (api_key or "").strip()
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.model = model
        self._agent = None
        self._sessions: dict[str, AgentSession] = {}
        self._session_last_used: dict[str, float] = {}
        self._max_sessions: int = 500
        self._session_ttl: float = 3600.0  # 1 hour
        self._db_store = db_store

        if rag_client:
            _rag_client = rag_client

        if self.api_key:
            client = OpenAIChatCompletionClient(
                api_key=self.api_key,
                model=model,
            )

            tools = []
            if _rag_client and _rag_client.enabled:
                tools.append(buscar_informacion)
                logger.info("RAG tool enabled — agent will search Supabase for answers.")

            self._agent = client.as_agent(
                name="Maria",
                instructions=MICOCHE_INFO_PROMPT,
                tools=tools if tools else None,
            )

    @property
    def enabled(self) -> bool:
        return self._agent is not None

    def _cleanup_stale_sessions(self) -> None:
        """Remove sessions older than TTL or when over max capacity."""
        import time
        now = time.time()
        # Remove expired sessions
        expired = [
            tid for tid, ts in self._session_last_used.items()
            if now - ts > self._session_ttl
        ]
        for tid in expired:
            self._sessions.pop(tid, None)
            self._session_last_used.pop(tid, None)
        if expired:
            logger.info("Cleaned up %d expired sessions. Active: %d", len(expired), len(self._sessions))

        # If still over max, remove oldest
        if len(self._sessions) > self._max_sessions:
            sorted_sessions = sorted(self._session_last_used.items(), key=lambda x: x[1])
            to_remove = len(self._sessions) - self._max_sessions
            for tid, _ in sorted_sessions[:to_remove]:
                self._sessions.pop(tid, None)
                self._session_last_used.pop(tid, None)
            logger.info("Evicted %d sessions to stay under %d max.", to_remove, self._max_sessions)

    def _get_or_create_session(self, thread_id: str) -> AgentSession:
        import time
        self._cleanup_stale_sessions()

        if thread_id not in self._sessions:
            session = self._agent.create_session()
            # Restore conversation history from DB if available
            if self._db_store and self._db_store.enabled:
                try:
                    history = self._db_store.get_recent_messages(thread_id, limit=20)
                    if history:
                        for msg in history:
                            session.add_message(
                                role=msg["role"],
                                content=msg["content"],
                            )
                        logger.info(
                            "Restored %d messages for thread %s from DB.",
                            len(history),
                            thread_id,
                        )
                except Exception:
                    logger.exception("Failed to restore history for thread %s", thread_id)
            self._sessions[thread_id] = session

        self._session_last_used[thread_id] = time.time()
        return self._sessions[thread_id]

    def answer(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> OrchestratorResult:
        context_hint = context_hint or {}

        if self._agent:
            try:
                session = self._get_or_create_session(thread_id)

                # Build context block from hint (filter internal keys)
                context_block = ""
                _internal_keys = {"thread_id", "lead_id", "contact_id", "talk_id", "event_id", "user_id"}
                safe_context = {k: v for k, v in context_hint.items() if k not in _internal_keys}
                if safe_context:
                    context_lines = [f"- {k}: {v}" for k, v in safe_context.items()]
                    context_block = "\n\nContexto:\n" + "\n".join(context_lines)

                full_message = f"{message}{context_block}".strip()

                # Run agent with session (MAF handles memory + tool calls)
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass

                if loop and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run,
                            self._agent.run(full_message, session=session),
                        ).result()
                else:
                    result = asyncio.run(
                        self._agent.run(full_message, session=session)
                    )

                answer_text = str(result) if result else ""

                return OrchestratorResult(
                    route="informacion_micoche",
                    answer=sanitize_plain_text(answer_text),
                    metadata={
                        "thread_id": thread_id,
                        "used_llm": True,
                        "framework": "microsoft-agent-framework",
                        "model": self.model,
                        "rag_enabled": _rag_client is not None and _rag_client.enabled,
                    },
                )
            except Exception:
                logger.exception("MAF agent failed, using fallback.")

        fallback = (
            "Te ayudo con la informacion general de Mi Coche. "
            "Manejamos categorias A2, B1, C1, C2 y C3, con horarios de teoria y practica, "
            "medios de pago, requisitos y ubicacion. "
            "Dime la categoria o el dato que necesitas y te respondo puntual."
        )
        return OrchestratorResult(
            route="informacion_micoche",
            answer=sanitize_plain_text(fallback),
            metadata={"thread_id": thread_id, "used_llm": False},
        )

    def clear_session(self, thread_id: str) -> None:
        self._sessions.pop(thread_id, None)
