import asyncio

from app.config import Settings
from app.db_store import PostgresStore
from app.metrics import MetricsRegistry
from app.webhook_processor import KommoWebhookProcessor


class FakeKommo:
    def get_lead(self, lead_id: int):
        return {
            "id": lead_id,
            "status_id": 1,
            "pipeline_id": 1,
            "source_id": "573174274959",
            "_embedded": {"source": {"name": "573174274959"}},
            "custom_fields_values": [],
        }

    def upsert_custom_field_value(self, lead_id: int, field_id: int, value):
        return {"ok": True}

    def run_salesbot(self, bot_id: int, lead_id: int):
        return {"ok": True}


class FakeOrchestrator:
    def answer(self, message: str, thread_id: str, context_hint=None):
        class R:
            route = "kommo"
            answer = "respuesta test"
            metadata = {}

        return R()


def build_processor() -> KommoWebhookProcessor:
    settings = Settings(
        expected_subdomain="auxcontablemicoche",
        dedupe_ttl_seconds=900,
        buffer_window_seconds=0.01,
        buffer_max_messages=8,
        expected_source_id="573174274959",
        switch_field_id=1631120,
        message_field_id=1890488,
        salesbot_id=86970,
        startup_validate_integrations=False,
    )
    return KommoWebhookProcessor(
        settings=settings,
        kommo_client=FakeKommo(),
        orchestrator=FakeOrchestrator(),
        metrics=MetricsRegistry(),
        db_store=PostgresStore(None),
    )


def test_event_id_priority():
    processor = build_processor()
    payload = {"message[add][0][id]": "abc", "message[add][0][chat_id]": "chat"}
    event_id = processor._extract_event_id(payload, {"x-amocrm-requestid": "hdr"})
    assert event_id == "abc"


def test_ingest_dedupe_memory():
    processor = build_processor()
    body = (
        "account[subdomain]=auxcontablemicoche&"
        "message[add][0][id]=evt1&"
        "message[add][0][entity_id]=33137008&"
        "message[add][0][text]=hola"
    ).encode("utf-8")
    first = asyncio.run(processor.ingest(raw_body=body, headers={}))
    second = asyncio.run(processor.ingest(raw_body=body, headers={}))
    assert first["buffered"] is True
    assert second["reason"] == "duplicate"
