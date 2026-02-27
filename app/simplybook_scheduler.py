from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.mcp_clients import MCPClientError, SimplyBookMCPClient


@dataclass
class SchedulingResult:
    success: bool
    action: str
    message: str
    data: dict[str, Any]


class SimplyBookScheduler:
    def __init__(self, client: SimplyBookMCPClient) -> None:
        self.client = client

    def extract_request(self, message: str, context_hint: dict[str, Any] | None = None) -> dict[str, Any]:
        context_hint = context_hint or {}
        text = message.strip()

        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
        email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
        phone_match = re.search(r"(\+?\d[\d\s-]{6,}\d)", text)

        event_id = context_hint.get("event_id")
        provider_id = context_hint.get("provider_id")
        client_name = context_hint.get("client_name")
        client_email = context_hint.get("client_email")
        client_phone = context_hint.get("client_phone")

        if not client_name:
            name_match = re.search(r"(?:soy|me llamo)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{4,60})", text, re.IGNORECASE)
            if name_match:
                client_name = name_match.group(1).strip()

        if not client_email and email_match:
            client_email = email_match.group(1).strip()
        if not client_phone and phone_match:
            client_phone = re.sub(r"\s+", "", phone_match.group(1))

        date_value = context_hint.get("date") or (date_match.group(1) if date_match else None)
        time_value = context_hint.get("time") or (
            f"{time_match.group(1).zfill(2)}:{time_match.group(2)}" if time_match else None
        )

        action = "info"
        lower = text.lower()
        if any(k in lower for k in ("disponibilidad", "cupos", "horarios disponibles", "qué horas")):
            action = "availability"
        if any(k in lower for k in ("agendar", "reservar", "reserva", "cita")):
            action = "book"
        if any(k in lower for k in ("cancelar cita", "cancelar reserva")):
            action = "cancel"

        return {
            "action": action,
            "event_id": event_id,
            "provider_id": provider_id,
            "date": date_value,
            "time": time_value,
            "client_name": client_name,
            "client_email": client_email,
            "client_phone": client_phone,
            "raw_message": text,
        }

    def handle(self, message: str, context_hint: dict[str, Any] | None = None) -> SchedulingResult:
        req = self.extract_request(message=message, context_hint=context_hint)
        action = req["action"]
        if action == "availability":
            return self._availability(req)
        if action == "book":
            return self._book(req)
        if action == "cancel":
            return self._cancel(req)
        return self._info()

    def _info(self) -> SchedulingResult:
        return SchedulingResult(
            success=True,
            action="info",
            message=(
                "Te ayudo con el agendamiento. Para avanzar necesito tipo de clase, fecha (YYYY-MM-DD), "
                "hora (HH:MM), nombre y celular."
            ),
            data={},
        )

    def _availability(self, req: dict[str, Any]) -> SchedulingResult:
        date_value = req.get("date")
        event_id = req.get("event_id")
        provider_id = req.get("provider_id")
        if not date_value:
            return SchedulingResult(
                success=False,
                action="availability",
                message="Para consultar disponibilidad necesito la fecha en formato YYYY-MM-DD.",
                data=req,
            )

        payload = {"date": date_value, "event_id": event_id, "provider_id": provider_id}
        try:
            data = self.client.get_available_slots(payload=payload)
            return SchedulingResult(
                success=True,
                action="availability",
                message=f"Disponibilidad consultada para {date_value}. ¿Qué hora prefieres?",
                data={"request": req, "availability": data},
            )
        except MCPClientError as exc:
            return SchedulingResult(
                success=False,
                action="availability",
                message=(
                    "No pude consultar disponibilidad automática en este momento. "
                    "Si me compartes fecha y franja, lo intento de nuevo."
                ),
                data={"request": req, "error": str(exc)},
            )

    def _book(self, req: dict[str, Any]) -> SchedulingResult:
        required = ["date", "time", "client_name", "client_phone"]
        missing = [name for name in required if not req.get(name)]
        if missing:
            missing_str = ", ".join(missing)
            return SchedulingResult(
                success=False,
                action="book",
                message=f"Para agendar me faltan estos datos: {missing_str}.",
                data=req,
            )

        payload = {
            "event_id": req.get("event_id"),
            "provider_id": req.get("provider_id"),
            "start_datetime": self._build_start_datetime(req["date"], req["time"]),
            "client_name": req["client_name"],
            "client_email": req.get("client_email"),
            "client_phone": req["client_phone"],
        }
        try:
            result = self.client.create_booking(payload=payload)
            booking_id = self._extract_booking_id(result)
            confirmation = f"Reserva creada correctamente. Id de reserva: {booking_id}." if booking_id else "Reserva creada correctamente."
            return SchedulingResult(
                success=True,
                action="book",
                message=confirmation,
                data={"request": req, "booking": result, "booking_id": booking_id},
            )
        except MCPClientError as exc:
            return SchedulingResult(
                success=False,
                action="book",
                message=(
                    "No pude cerrar la reserva de forma automática. "
                    "Ya tengo tus datos y puedo reintentar en cuanto me confirmes."
                ),
                data={"request": req, "error": str(exc)},
            )

    def _cancel(self, req: dict[str, Any]) -> SchedulingResult:
        text = str(req.get("raw_message", ""))
        booking_id_match = re.search(r"\b(?:id|reserva)\s*[:#]?\s*(\d+)\b", text, re.IGNORECASE)
        booking_id = booking_id_match.group(1) if booking_id_match else None
        if not booking_id:
            return SchedulingResult(
                success=False,
                action="cancel",
                message="Para cancelar necesito el id de la reserva.",
                data=req,
            )
        try:
            result = self.client.cancel_booking({"booking_id": booking_id})
            return SchedulingResult(
                success=True,
                action="cancel",
                message=f"Reserva {booking_id} cancelada correctamente.",
                data={"request": req, "result": result},
            )
        except MCPClientError as exc:
            return SchedulingResult(
                success=False,
                action="cancel",
                message="No pude cancelar la reserva en este momento. ¿Me confirmas el id para reintentar?",
                data={"request": req, "error": str(exc)},
            )

    @staticmethod
    def _build_start_datetime(date_value: str, time_value: str) -> str:
        dt = datetime.fromisoformat(f"{date_value}T{time_value}:00")
        return dt.isoformat()

    @staticmethod
    def _extract_booking_id(result: Any) -> str | None:
        if isinstance(result, dict):
            for key in ("booking_id", "id", "reservation_id", "appointment_id"):
                value = result.get(key)
                if value is not None:
                    return str(value)
        return None

