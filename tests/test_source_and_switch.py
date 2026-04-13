"""Tests for source filtering and IA switch logic in webhook processor."""
import asyncio

from app.config import Settings
from app.db_store import PostgresStore
from app.metrics import MetricsRegistry
from app.webhook_processor import KommoWebhookProcessor


class FakeKommoWithSource:
    def __init__(self, source_name: str, switch_active: bool = False):
        self.source_name = source_name
        self.switch_active = switch_active
        self.salesbot_called = False
        self.field_written = False

    def get_lead(self, lead_id: int):
        fields = []
        if self.switch_active:
            fields = [{"field_id": 1631120, "values": [{"value": True}]}]
        return {
            "id": lead_id,
            "source_id": 112450,
            "_embedded": {"source": {"id": 112450, "name": self.source_name}},
            "custom_fields_values": fields,
        }

    def upsert_custom_field_value(self, lead_id: int, field_id: int, value):
        self.field_written = True
        return {"ok": True}

    def run_salesbot(self, bot_id: int, lead_id: int):
        self.salesbot_called = True
        return {"ok": True}


class FakeOrch:
    def answer(self, message: str, thread_id: str, context_hint=None):
        class R:
            route = "informacion_micoche"
            answer = "Respuesta de prueba"
            metadata = {"input_tokens": 10, "output_tokens": 20}
        return R()


def _settings(**overrides):
    defaults = dict(
        expected_subdomain="auxcontablemicoche",
        dedupe_ttl_seconds=900,
        buffer_window_seconds=0.01,
        buffer_max_messages=8,
        expected_source_id="573134246298",
        switch_field_id=1631120,
        message_field_id=1890488,
        salesbot_id=86970,
        startup_validate_integrations=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _build(kommo, **kwargs):
    return KommoWebhookProcessor(
        settings=_settings(**kwargs),
        kommo_client=kommo,
        orchestrator=FakeOrch(),
        metrics=MetricsRegistry(),
        db_store=PostgresStore(None),
    )


def test_source_mismatch_skips_lead():
    kommo = FakeKommoWithSource(source_name="3009999999")
    proc = _build(kommo)
    batch = proc.__class__.__mro__  # just need to call _flush_batch
    # Use internal _flush_batch via ingest + buffer
    body = (
        "account[subdomain]=auxcontablemicoche&"
        "message[add][0][id]=src1&"
        "message[add][0][entity_id]=999&"
        "message[add][0][text]=hola"
    ).encode()
    asyncio.run(proc.ingest(raw_body=body, headers={}))
    # Flush immediately
    import time; time.sleep(0.05)
    # The salesbot should NOT have been called because source doesn't match
    assert kommo.salesbot_called is False


def test_correct_source_allows_flush():
    kommo = FakeKommoWithSource(source_name="573134246298")
    proc = _build(kommo)
    from app.webhook_processor import LeadBatch
    batch = LeadBatch(lead_id=888, messages=["hola"], event_ids=["e1"], context={})
    asyncio.run(proc._flush_batch(batch))
    assert kommo.salesbot_called is True
    assert kommo.field_written is True


def test_switch_active_skips_lead():
    kommo = FakeKommoWithSource(source_name="573134246298", switch_active=True)
    proc = _build(kommo)
    from app.webhook_processor import LeadBatch
    batch = LeadBatch(lead_id=777, messages=["hola"], event_ids=["e2"], context={})
    asyncio.run(proc._flush_batch(batch))
    assert kommo.salesbot_called is False


def test_consolidate_messages_no_numbering():
    proc = _build(FakeKommoWithSource(source_name="573134246298"))
    assert proc._consolidate_messages(["hola", "quiero info", "del B1"]) == "hola\nquiero info\ndel B1"
    assert proc._consolidate_messages(["solo uno"]) == "solo uno"
    assert proc._consolidate_messages([]) == ""
