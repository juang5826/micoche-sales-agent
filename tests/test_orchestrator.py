from app.orchestrator import MiCocheMAFOrchestrator


class FakeKommo:
    def get_lead(self, lead_id: int):
        return {
            "id": lead_id,
            "status_id": 123,
            "pipeline_id": 456,
            "_embedded": {"source": {"name": "web"}},
        }


class FakeSimplyBook:
    def get_company_info(self):
        return {"name": "Mi Coche", "login": "micochemos"}

    def get_event_list(self, only_visible: bool = False, only_active: bool = False):
        return {"1": {"name": "B1"}}

    def get_available_slots(self, payload):
        return {"slots": ["08:00", "09:00"], "payload": payload}

    def create_booking(self, payload):
        return {"booking_id": 999, "payload": payload}

    def cancel_booking(self, payload):
        return {"ok": True, "payload": payload}


def test_route_info():
    orchestrator = MiCocheMAFOrchestrator(kommo=FakeKommo(), simplybook=FakeSimplyBook(), llm=None)
    result = orchestrator.answer(
        message="Cual es el precio del curso B1?",
        thread_id="1",
        context_hint={},
    )
    assert result.route == "informacion_micoche"
    assert "Mi Coche" in result.answer or "categorías" in result.answer


def test_route_booking_create():
    orchestrator = MiCocheMAFOrchestrator(kommo=FakeKommo(), simplybook=FakeSimplyBook(), llm=None)
    result = orchestrator.answer(
        message="Quiero agendar 2026-03-05 09:00 soy Juan mi celular es 3001234567",
        thread_id="2",
        context_hint={"event_id": 1, "provider_id": 1},
    )
    assert result.route == "simplybook"
    assert "Reserva" in result.answer or "reserva" in result.answer


def test_route_kommo():
    orchestrator = MiCocheMAFOrchestrator(kommo=FakeKommo(), simplybook=FakeSimplyBook(), llm=None)
    result = orchestrator.answer(
        message="continua mi gestion",
        thread_id="3",
        context_hint={"lead_id": 10},
    )
    assert result.route == "kommo"
    assert "Lead 10" in result.answer

