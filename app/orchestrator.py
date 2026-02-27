from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.mcp_clients import KommoMCPClient, SimplyBookMCPClient
from app.utils import sanitize_plain_text

BOOKING_KEYWORDS = {
    "agendar",
    "agenda",
    "reserva",
    "reservar",
    "cita",
    "cupo",
    "horario",
    "disponible",
    "disponibilidad",
    "clase practica",
    "simplybook",
}


@dataclass
class OrchestratorResult:
    route: str
    answer: str
    metadata: dict[str, Any]


class MiCocheMAFOrchestrator:
    """
    Orquestador principal del agente Mi Coche.
    Diseño MAF-style: enruta intención y delega a submódulos especializados.
    """

    def __init__(self, kommo: KommoMCPClient, simplybook: SimplyBookMCPClient) -> None:
        self.kommo = kommo
        self.simplybook = simplybook

    def route_intent(self, message: str) -> str:
        text = message.lower()
        if any(word in text for word in BOOKING_KEYWORDS):
            return "simplybook"
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
        return self._answer_kommo(message=message, thread_id=thread_id, context_hint=context_hint)

    def _answer_simplybook(
        self,
        message: str,
        thread_id: str,
        context_hint: dict[str, Any],
    ) -> OrchestratorResult:
        company = self.simplybook.get_company_info()
        events = self.simplybook.get_event_list(only_visible=False, only_active=False)
        event_count = len(events.keys()) if isinstance(events, dict) else 0

        response = (
            f"Estoy consultando la agenda de {company.get('name', 'Mi Coche')}. "
            f"En este momento tengo {event_count} tipos de clase configurados. "
            "Para agendarte necesito confirmar tipo de clase, fecha, hora y sede."
        )

        return OrchestratorResult(
            route="simplybook",
            answer=sanitize_plain_text(response),
            metadata={
                "company_login": company.get("login"),
                "company_name": company.get("name"),
                "available_service_count": event_count,
                "thread_id": thread_id,
            },
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

