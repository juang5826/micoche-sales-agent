from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.llm_client import LLMClientError, OpenAIHTTPClient
from app.micoche_knowledge import MICOHE_INFO_BASE, MICOHE_INFO_PROMPT
from app.utils import sanitize_plain_text


@dataclass
class OrchestratorResult:
    route: str
    answer: str
    metadata: dict[str, Any]


class MiCocheMAFOrchestrator:
    """
    Orquestador reducido al caso de uso actual:
    responder informacion general de Mi Coche.
    """

    def __init__(
        self,
        llm: OpenAIHTTPClient | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 380,
    ) -> None:
        self.llm = llm
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def answer(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        context_hint = context_hint or {}
        if self.llm and self.llm.enabled:
            try:
                answer = self.llm.generate_text(
                    system_prompt=f"{MICOHE_INFO_PROMPT}\n\nBase de conocimiento:\n{MICOHE_INFO_BASE}",
                    user_message=message,
                    context={"thread_id": thread_id, **context_hint},
                    max_output_tokens=self.max_output_tokens,
                    temperature=self.temperature,
                )
                return OrchestratorResult(
                    route="informacion_micoche",
                    answer=sanitize_plain_text(answer),
                    metadata={"thread_id": thread_id, "used_llm": True},
                )
            except LLMClientError:
                pass

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
