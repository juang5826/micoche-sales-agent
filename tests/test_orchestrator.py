from app.orchestrator import MiCocheMAFOrchestrator


def test_answer_always_uses_info_route():
    orchestrator = MiCocheMAFOrchestrator(llm=None)
    result = orchestrator.answer(
        message="Quiero agendar una cita para manana",
        thread_id="1",
        context_hint={},
    )
    assert result.route == "informacion_micoche"


def test_answer_fallback_mentions_general_info():
    orchestrator = MiCocheMAFOrchestrator(llm=None)
    result = orchestrator.answer(
        message="Cual es el precio del curso B1?",
        thread_id="2",
        context_hint={},
    )
    assert "Mi Coche" in result.answer
    assert "A2, B1, C1, C2 y C3" in result.answer
