from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.llm_client import LLMClientError, OpenAIHTTPClient
from app.mcp_clients import KommoMCPClient, SimplyBookMCPClient
from app.micoche_knowledge import MICOHE_INFO_BASE, MICOHE_INFO_PROMPT
from app.simplybook_scheduler import SimplyBookScheduler
from app.utils import sanitize_plain_text

BOOKING_KEYWORDS = {
    "agendar",
    "agenda",
    "reserva",
    "reservar",
    "cita",
    "cupo",
    "horario disponible",
    "disponibilidad",
    "simplybook",
}

INFO_KEYWORDS = {
    "precio",
    "valor",
    "curso",
    "licencia",
    "requisito",
    "runt",
    "horario",
    "ubicacion",
    "dirección",
    "direccion",
    "meddipay",
    "financiación",
    "financiacion",
    "examen médico",
    "examen medico",
}


@dataclass
class OrchestratorResult:
    route: str
    answer: str
    metadata: dict[str, Any]


class MiCocheMAFOrchestrator:
    """
    Orquestador principal del agente Mi Coche.
    Rutas:
    - simplybook: agenda/citas
    - informacion_micoche: cursos, precios, horarios y requisitos
    - kommo: estado comercial del lead en CRM
    """

    def __init__(
        self,
        kommo: KommoMCPClient,
        simplybook: SimplyBookMCPClient,
        llm: OpenAIHTTPClient | None = None,
    ) -> None:
        self.kommo = kommo
        self.simplybook = simplybook
        self.llm = llm
        self.scheduler = SimplyBookScheduler(simplybook)

    def route_intent(self, message: str) -> str:
        text = message.lower()
        if any(word in text for word in BOOKING_KEYWORDS):
            return "simplybook"
        if any(word in text for word in INFO_KEYWORDS):
            return "informacion_micoche"
        return "kommo"

    def answer(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        context_hint = context_hint or {}
        route = self.route_intent(message)

        if route == "simplybook":
            return self._answer_simplybook(message=message, thread_id=thread_id, context_hint=context_hint)
        if route == "informacion_micoche":
            return self._answer_info(message=message, thread_id=thread_id, context_hint=context_hint)
        return self._answer_kommo(message=message, thread_id=thread_id, context_hint=context_hint)

    def _answer_simplybook(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any],
    ) -> OrchestratorResult:
        company = self.simplybook.get_company_info()
        schedule_result = self.scheduler.handle(message=message, context_hint=context_hint)
        base_metadata = {
            "thread_id": thread_id,
            "company_login": company.get("login"),
            "company_name": company.get("name"),
            "scheduling_action": schedule_result.action,
            "scheduling_success": schedule_result.success,
        }

        if self.llm and self.llm.enabled:
            prompt = (
                "Eres asesor de agenda de Mi Coche. Responde breve en texto plano. "
                "Si faltan datos para cerrar reserva, pide solo lo faltante."
            )
            try:
                answer = self.llm.generate_text(
                    system_prompt=prompt,
                    user_message=message,
                    context={
                        "resultado_agenda": schedule_result.message,
                        "empresa": company.get("name"),
                        "accion": schedule_result.action,
                        "exito": schedule_result.success,
                    },
                    max_output_tokens=280,
                    temperature=0.1,
                )
                return OrchestratorResult(
                    route="simplybook",
                    answer=sanitize_plain_text(answer),
                    metadata={**base_metadata, "scheduler_data": schedule_result.data},
                )
            except LLMClientError:
                pass

        return OrchestratorResult(
            route="simplybook",
            answer=sanitize_plain_text(schedule_result.message),
            metadata={**base_metadata, "scheduler_data": schedule_result.data},
        )

    def _answer_info(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any],
    ) -> OrchestratorResult:
        if self.llm and self.llm.enabled:
            try:
                answer = self.llm.generate_text(
                    system_prompt=f"{MICOHE_INFO_PROMPT}\n\nBase de conocimiento:\n{MICOHE_INFO_BASE}",
                    user_message=message,
                    context={"thread_id": thread_id, **context_hint},
                    max_output_tokens=380,
                    temperature=0.2,
                )
                return OrchestratorResult(
                    route="informacion_micoche",
                    answer=sanitize_plain_text(answer),
                    metadata={"thread_id": thread_id, "used_llm": True},
                )
            except LLMClientError:
                pass

        fallback = (
            "Te ayudo con la información de Mi Coche. "
            "Manejamos categorías A2, B1, C1, C2 y C3. "
            "Si me dices la categoría te comparto valores, requisitos y horarios exactos."
        )
        return OrchestratorResult(
            route="informacion_micoche",
            answer=sanitize_plain_text(fallback),
            metadata={"thread_id": thread_id, "used_llm": False},
        )

    def _answer_kommo(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any],
    ) -> OrchestratorResult:
        lead_id = context_hint.get("lead_id")
        if lead_id is None:
            response = (
                "Puedo ayudarte con el proceso comercial, pero necesito el lead en contexto "
                "para validar estado y continuar."
            )
            return OrchestratorResult(
                route="kommo",
                answer=sanitize_plain_text(response),
                metadata={"thread_id": thread_id, "lead_found": False},
            )

        lead = self.kommo.get_lead(int(lead_id))
        status_id = lead.get("status_id")
        pipeline_id = lead.get("pipeline_id")
        source = ((lead.get("_embedded") or {}).get("source") or {}).get("name")

        if self.llm and self.llm.enabled:
            try:
                answer = self.llm.generate_text(
                    system_prompt=(
                        "Eres un asesor comercial CRM. Resume el estado del lead y da el siguiente paso sugerido "
                        "en no más de 4 líneas."
                    ),
                    user_message=message,
                    context={
                        "lead_id": lead.get("id"),
                        "status_id": status_id,
                        "pipeline_id": pipeline_id,
                        "source": source,
                    },
                    max_output_tokens=240,
                    temperature=0.1,
                )
                return OrchestratorResult(
                    route="kommo",
                    answer=sanitize_plain_text(answer),
                    metadata={
                        "lead_found": True,
                        "lead_id": lead.get("id"),
                        "status_id": status_id,
                        "pipeline_id": pipeline_id,
                        "source": source,
                        "thread_id": thread_id,
                    },
                )
            except LLMClientError:
                pass

        response = (
            f"Lead {lead.get('id')} validado. "
            f"Estado actual {status_id} en embudo {pipeline_id}. "
            f"Fuente {source}. "
            "Listo para continuar con la gestión comercial."
        )
        return OrchestratorResult(
            route="kommo",
            answer=sanitize_plain_text(response),
            metadata={
                "lead_found": True,
                "lead_id": lead.get("id"),
                "status_id": status_id,
                "pipeline_id": pipeline_id,
                "source": source,
                "thread_id": thread_id,
            },
        )

