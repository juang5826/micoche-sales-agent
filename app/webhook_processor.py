from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

from app.config import Settings
from app.db_store import PostgresStore
from app.media_processor import MediaProcessor
from app.metrics import MetricsRegistry
from app.mcp_clients import KommoMCPClient
from app.orchestrator import MiCocheMAFOrchestrator
from app.thread_store import ThreadStore
from app.utils import normalize_bool, sanitize_plain_text

logger = logging.getLogger("webhook_processor")


class DedupeCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def add_if_new(self, key: str) -> bool:
        now = time.time()
        async with self._lock:
            expired = [k for k, ts in self._cache.items() if ts <= now]
            for k in expired:
                self._cache.pop(k, None)
            if key in self._cache:
                return False
            self._cache[key] = now + self.ttl_seconds
            return True


@dataclass
class LeadBatch:
    lead_id: int
    messages: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task | None = None


class LeadMessageBuffer:
    def __init__(
        self,
        window_seconds: float,
        max_messages: int,
        flush_handler: Callable[[LeadBatch], Awaitable[None]],
    ) -> None:
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.flush_handler = flush_handler
        self._entries: dict[int, LeadBatch] = {}
        self._lock = asyncio.Lock()

    async def add(self, lead_id: int, message: str, event_id: str, context: dict[str, Any]) -> int:
        async with self._lock:
            entry = self._entries.get(lead_id) or LeadBatch(lead_id=lead_id)
            entry.messages.append(message)
            entry.event_ids.append(event_id)
            entry.context = context
            if len(entry.messages) > self.max_messages:
                entry.messages = entry.messages[-self.max_messages :]
                entry.event_ids = entry.event_ids[-self.max_messages :]
            if entry.task and not entry.task.done():
                entry.task.cancel()
            entry.task = asyncio.create_task(self._delayed_flush(lead_id))
            self._entries[lead_id] = entry
            return len(entry.messages)

    async def drop(self, lead_id: int) -> None:
        async with self._lock:
            entry = self._entries.pop(lead_id, None)
            if entry and entry.task and not entry.task.done():
                entry.task.cancel()

    async def _delayed_flush(self, lead_id: int) -> None:
        try:
            await asyncio.sleep(self.window_seconds)
            async with self._lock:
                entry = self._entries.pop(lead_id, None)
            if entry:
                await self.flush_handler(entry)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("Failed while flushing lead batch.")


class KommoWebhookProcessor:
    def __init__(
        self,
        settings: Settings,
        kommo_client: KommoMCPClient,
        orchestrator: MiCocheMAFOrchestrator,
        thread_store: ThreadStore,
        metrics: MetricsRegistry,
        db_store: PostgresStore,
        media_processor: MediaProcessor | None = None,
    ) -> None:
        self.settings = settings
        self.kommo = kommo_client
        self.orchestrator = orchestrator
        self.thread_store = thread_store
        self.metrics = metrics
        self.db_store = db_store
        self.media_processor = media_processor
        self.dedupe = DedupeCache(ttl_seconds=settings.dedupe_ttl_seconds)
        self.buffer = LeadMessageBuffer(
            window_seconds=settings.buffer_window_seconds,
            max_messages=settings.buffer_max_messages,
            flush_handler=self._flush_batch,
        )
        self._flush_loop_task: asyncio.Task | None = None
        self._flush_loop_running = False

    async def start(self) -> None:
        if self.db_store.enabled and not self._flush_loop_task:
            self._flush_loop_running = True
            self._flush_loop_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._flush_loop_running = False
        if self._flush_loop_task and not self._flush_loop_task.done():
            self._flush_loop_task.cancel()
            try:
                await self._flush_loop_task
            except asyncio.CancelledError:
                pass
        self._flush_loop_task = None

    async def ingest(self, raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        self.metrics.inc("webhook_received_total")
        payload = self._parse_form_encoded(raw_body.decode("utf-8", errors="ignore"))
        self._validate_subdomain(payload)

        event_id = self._extract_event_id(payload, headers)
        is_new = await self._dedupe_add_if_new(event_id)
        if not is_new:
            self.metrics.inc("webhook_deduped_total")
            return {"ok": True, "buffered": False, "event_id": event_id, "reason": "duplicate"}

        lead_id = self._extract_lead_id(payload)
        if lead_id is None:
            self.metrics.inc("webhook_ignored_total")
            return {"ok": True, "buffered": False, "event_id": event_id, "reason": "missing_lead_id"}

        message = self._extract_message_text(payload)
        context = {
            "lead_id": lead_id,
            "contact_id": self._to_int(payload.get("message[add][0][contact_id]")),
            "talk_id": payload.get("message[add][0][talk_id]"),
            "author": payload.get("message[add][0][author][name]"),
            "source": payload.get("message[add][0][source][external_id]"),
            "event_id": event_id,
        }

        if self.media_processor and self.settings.media_enabled:
            media = await asyncio.to_thread(self.media_processor.enrich_from_payload, payload)
            if media.summaries:
                media_block = "\n".join(f"- {item}" for item in media.summaries)
                message = f"{message}\n\nContexto multimedia:\n{media_block}".strip()
            if media.errors:
                context["media_errors"] = media.errors
                self.metrics.inc("webhook_media_errors_total", len(media.errors))

        if self.db_store.enabled:
            count = await asyncio.to_thread(
                self.db_store.buffer_add_message,
                lead_id,
                message,
                event_id,
                context,
                self.settings.buffer_max_messages,
                self.settings.buffer_window_seconds,
            )
        else:
            count = await self.buffer.add(
                lead_id=lead_id,
                message=message,
                event_id=event_id,
                context=context,
            )
        self.metrics.inc("webhook_buffered_messages_total")
        return {"ok": True, "buffered": True, "event_id": event_id, "lead_id": lead_id, "buffer_count": count}

    async def drop_thread(self, thread_id: str) -> None:
        lead_id = self._to_int(thread_id)
        if not lead_id:
            return
        if self.db_store.enabled:
            await asyncio.to_thread(self.db_store.drop_buffer_lead, lead_id)
        else:
            await self.buffer.drop(lead_id)

    async def flush_due(self) -> int:
        if not self.db_store.enabled:
            return 0
        batches = await asyncio.to_thread(self.db_store.pop_due_buffers, 50)
        for item in batches:
            batch = LeadBatch(
                lead_id=item["lead_id"],
                messages=item.get("messages", []),
                event_ids=item.get("event_ids", []),
                context=item.get("context", {}),
            )
            await self._flush_batch(batch)
        return len(batches)

    async def _flush_loop(self) -> None:
        while self._flush_loop_running:
            try:
                flushed = await self.flush_due()
                if flushed == 0:
                    await asyncio.sleep(0.6)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("Webhook flush loop failed.")
                await asyncio.sleep(1.2)

    async def _dedupe_add_if_new(self, event_id: str) -> bool:
        if self.db_store.enabled:
            return await asyncio.to_thread(
                self.db_store.dedupe_add_if_new,
                event_id,
                self.settings.dedupe_ttl_seconds,
            )
        return await self.dedupe.add_if_new(event_id)

    async def _flush_batch(self, batch: LeadBatch) -> None:
        self.metrics.inc("webhook_flush_batches_total")
        lead_id = batch.lead_id
        thread_id = str(lead_id)
        consolidated = self._consolidate_messages(batch.messages)

        try:
            lead = self.kommo.get_lead(lead_id=lead_id)
        except Exception:  # noqa: BLE001
            self.metrics.inc("webhook_flush_errors_total")
            logger.exception("Failed to retrieve lead %s during flush.", lead_id)
            return

        if not self._is_source_allowed(lead):
            self.metrics.inc("webhook_skipped_source_total")
            logger.info("Lead %s skipped due source mismatch.", lead_id)
            return

        if self._is_switch_active(lead):
            self.metrics.inc("webhook_skipped_switch_total")
            logger.info("Lead %s skipped due IA switch active.", lead_id)
            return

        try:
            answer = self.orchestrator.answer(
                message=consolidated,
                thread_id=thread_id,
                context_hint={"lead_id": lead_id, **batch.context},
            )
            plain_response = sanitize_plain_text(answer.answer)
            self.thread_store.add_message(thread_id, "user", consolidated)
            self.thread_store.add_message(thread_id, "assistant", plain_response)

            self.kommo.upsert_custom_field_value(
                lead_id=lead_id,
                field_id=self.settings.message_field_id,
                value=plain_response,
            )
            self.kommo.run_salesbot(
                bot_id=self.settings.salesbot_id,
                lead_id=lead_id,
            )
            self.metrics.inc("webhook_response_sent_total")
        except Exception:  # noqa: BLE001
            self.metrics.inc("webhook_flush_errors_total")
            logger.exception("Failed to process flush for lead %s", lead_id)

    def _parse_form_encoded(self, body: str) -> dict[str, str]:
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def _validate_subdomain(self, payload: dict[str, str]) -> None:
        incoming = (payload.get("account[subdomain]") or "").strip().lower()
        expected = self.settings.expected_subdomain.strip().lower()
        if expected and incoming and incoming != expected:
            raise ValueError(f"Invalid subdomain: {incoming}")

    def _extract_event_id(self, payload: dict[str, str], headers: dict[str, str]) -> str:
        return (
            payload.get("message[add][0][id]")
            or payload.get("message[add][0][chat_id]")
            or headers.get("x-amocrm-requestid")
            or f"{int(time.time())}:{payload.get('message[add][0][entity_id]', 'unknown')}"
        )

    def _extract_lead_id(self, payload: dict[str, str]) -> int | None:
        return (
            self._to_int(payload.get("message[add][0][entity_id]"))
            or self._to_int(payload.get("leads[status][0][id]"))
            or self._to_int(payload.get("contacts[add][0][linked_leads_id]"))
        )

    def _extract_message_text(self, payload: dict[str, str]) -> str:
        return (
            payload.get("message[add][0][text]")
            or payload.get("message[add][0][message]")
            or payload.get("message[add][0][caption]")
            or "(mensaje sin texto)"
        ).strip()

    def _is_source_allowed(self, lead: dict[str, Any]) -> bool:
        expected = self.settings.expected_source_id.strip()
        if not expected:
            return True
        embedded = lead.get("_embedded") or {}
        source_obj = embedded.get("source") or {}
        source_name = source_obj.get("name")
        source_external_id = source_obj.get("external_id")
        source_obj_id = source_obj.get("id")
        source_id = lead.get("source_id")
        candidates = {
            str(source_name).strip(),
            str(source_external_id).strip(),
            str(source_obj_id).strip(),
            str(source_id).strip(),
        }
        return expected in candidates

    def _is_switch_active(self, lead: dict[str, Any]) -> bool:
        fields = lead.get("custom_fields_values") or []
        for field in fields:
            if int(field.get("field_id", -1)) != self.settings.switch_field_id:
                continue
            values = field.get("values") or []
            if not values:
                return False
            value = values[0].get("value")
            normalized = normalize_bool(value)
            return normalized is True
        return False

    def _consolidate_messages(self, messages: list[str]) -> str:
        if len(messages) <= 1:
            return messages[0] if messages else ""
        return "\n".join([f"{index + 1}. {msg}" for index, msg in enumerate(messages)])

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(str(value))
        except ValueError:
            return None
