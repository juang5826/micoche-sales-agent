from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

from app.config import Settings
from app.db_store import PostgresStore
from app.media_processor import MediaProcessor, AUDIO_MIMES, IMAGE_MIMES
from app.metrics import MetricsRegistry
from app.mcp_clients import KommoMCPClient
from app.orchestrator import MiCocheMAFOrchestrator
from app.utils import filter_agent_output, normalize_bool, sanitize_plain_text

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
        metrics: MetricsRegistry,
        db_store: PostgresStore,
        media_processor: MediaProcessor | None = None,
    ) -> None:
        self.settings = settings
        self.kommo = kommo_client
        self.orchestrator = orchestrator
        self.metrics = metrics
        self.db_store = db_store
        self.media = media_processor
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
            "event_id": event_id,
        }

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

    async def _log_skip(
        self,
        thread_id: str,
        user_id: str | None,
        consolidated: str,
        reason: str,
        started: float,
    ) -> None:
        """Log a skipped batch into agent_runs for observability."""
        if not self.db_store.enabled:
            return
        try:
            await asyncio.to_thread(
                self.db_store.log_agent_run,
                thread_id,
                user_id,
                None,
                consolidated,
                None,
                False,
                int((time.perf_counter() - started) * 1000),
                reason,
            )
        except Exception:
            logger.exception("Failed to log skip for lead %s", thread_id)

    async def _flush_batch(self, batch: LeadBatch) -> None:
        self.metrics.inc("webhook_flush_batches_total")
        lead_id = batch.lead_id
        thread_id = str(lead_id)
        consolidated = self._consolidate_messages(batch.messages)
        started = time.perf_counter()
        user_id = str(batch.context.get("contact_id")) if batch.context.get("contact_id") else None

        # Always persist the incoming customer message — even if the batch
        # ends up being skipped below. This way we keep collecting data
        # from every CRM conversation to keep improving the agent later.
        if self.db_store.enabled:
            try:
                await asyncio.to_thread(
                    self.db_store.ensure_session,
                    thread_id,
                    user_id,
                    {"lead_id": lead_id, **batch.context},
                )
                await asyncio.to_thread(
                    self.db_store.add_message, thread_id, "user", consolidated
                )
            except Exception:
                logger.exception(
                    "Failed to persist inbound user message for lead %s", lead_id
                )

        try:
            lead = self.kommo.get_lead(lead_id=lead_id)
        except Exception:  # noqa: BLE001
            self.metrics.inc("webhook_flush_errors_total")
            logger.exception("Failed to retrieve lead %s during flush.", lead_id)
            await self._log_skip(thread_id, user_id, consolidated, "lead_fetch_failed", started)
            return

        if not self._is_source_allowed(lead):
            self.metrics.inc("webhook_skipped_source_total")
            logger.info("Lead %s skipped due source mismatch.", lead_id)
            await self._log_skip(thread_id, user_id, consolidated, "skipped_source", started)
            return

        if not self._is_pipeline_allowed(lead):
            self.metrics.inc("webhook_skipped_pipeline_total")
            logger.info("Lead %s skipped — pipeline %s not allowed.", lead_id, lead.get("pipeline_id"))
            await self._log_skip(thread_id, user_id, consolidated, "skipped_pipeline", started)
            return

        if not self._is_phone_allowed(lead, batch.context.get("contact_id")):
            self.metrics.inc("webhook_skipped_phone_total")
            logger.info("Lead %s skipped — contact phone not in whitelist.", lead_id)
            await self._log_skip(thread_id, user_id, consolidated, "skipped_phone", started)
            return

        if self._is_switch_active(lead):
            self.metrics.inc("webhook_skipped_switch_total")
            logger.info("Lead %s skipped due IA switch active.", lead_id)
            await self._log_skip(thread_id, user_id, consolidated, "skipped_switch", started)
            return

        try:
            answer = self.orchestrator.answer(
                message=consolidated,
                thread_id=thread_id,
                context_hint={"lead_id": lead_id, **batch.context},
            )
            filtered = filter_agent_output(answer.answer)
            plain_response = filtered.text

            self.kommo.upsert_custom_field_value(
                lead_id=lead_id,
                field_id=self.settings.message_field_id,
                value=plain_response,
            )
            self.kommo.run_salesbot(
                bot_id=self.settings.salesbot_id,
                lead_id=lead_id,
            )

            if self.db_store.enabled:
                # User message was already saved at the top of the flush,
                # so only save the assistant reply here.
                await asyncio.to_thread(self.db_store.add_message, thread_id, "assistant", plain_response)
                await asyncio.to_thread(
                    self.db_store.log_agent_run,
                    thread_id,
                    user_id,
                    self.settings.openai_model if self.orchestrator.enabled else None,
                    consolidated,
                    plain_response,
                    True,
                    int((time.perf_counter() - started) * 1000),
                    None,
                )
            self.metrics.inc("webhook_response_sent_total")
            self.metrics.inc("llm_input_tokens_total", answer.metadata.get("input_tokens", 0))
            self.metrics.inc("llm_output_tokens_total", answer.metadata.get("output_tokens", 0))
            if filtered.should_escalate:
                self.metrics.inc("webhook_escalations_total")
                logger.info("Lead %s marked for escalation to human.", lead_id)
                try:
                    self.kommo.upsert_custom_field_value(
                        lead_id=lead_id,
                        field_id=self.settings.switch_field_id,
                        value=True,
                    )
                    logger.info("Lead %s: IA switch activated — human takeover.", lead_id)
                except Exception:
                    logger.exception("Failed to activate IA switch for lead %s", lead_id)
        except Exception as exc:  # noqa: BLE001
            self.metrics.inc("webhook_flush_errors_total")
            logger.exception("Failed to process flush for lead %s", lead_id)
            if self.db_store.enabled:
                await asyncio.to_thread(
                    self.db_store.log_agent_run,
                    thread_id,
                    user_id,
                    self.settings.openai_model if self.orchestrator.enabled else None,
                    consolidated,
                    None,
                    False,
                    int((time.perf_counter() - started) * 1000),
                    str(exc),
                )

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
            or self._hash_fallback_event_id(payload)
        )

    @staticmethod
    def _hash_fallback_event_id(payload: dict[str, str]) -> str:
        entity = payload.get("message[add][0][entity_id]", "unknown")
        text = payload.get("message[add][0][text]", "")
        raw = f"{int(time.time())}:{entity}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _extract_lead_id(self, payload: dict[str, str]) -> int | None:
        return (
            self._to_int(payload.get("message[add][0][entity_id]"))
            or self._to_int(payload.get("leads[status][0][id]"))
            or self._to_int(payload.get("contacts[add][0][linked_leads_id]"))
        )

    def _extract_message_text(self, payload: dict[str, str]) -> str:
        raw = (
            payload.get("message[add][0][text]")
            or payload.get("message[add][0][message]")
            or payload.get("message[add][0][caption]")
            or ""
        ).strip()

        # Check for media URL in the payload
        media_url = (
            payload.get("message[add][0][media]")
            or payload.get("message[add][0][file_url]")
            or payload.get("message[add][0][attachment][link]")
            or ""
        ).strip()
        content_type = (
            payload.get("message[add][0][content_type]")
            or payload.get("message[add][0][media_type]")
            or payload.get("message[add][0][attachment][type]")
            or ""
        ).strip()

        # Try to process media if we have a URL and a media processor
        if media_url and self.media and self.media.enabled:
            media_type = self.media.detect_media_type(content_type)

            if media_type == "audio":
                self.metrics.inc("media_audio_received_total")
                result = self.media.process_media_url(media_url, content_type)
                if result.success and result.text:
                    self.metrics.inc("media_audio_transcribed_total")
                    # Combine caption (if any) with transcription
                    prefix = f"{raw}\n" if raw else ""
                    return f"{prefix}(audio transcrito) {result.text}"

            elif media_type == "image":
                self.metrics.inc("media_image_received_total")
                result = self.media.process_media_url(media_url, content_type)
                if result.success and result.text:
                    self.metrics.inc("media_image_analyzed_total")
                    prefix = f"{raw}\n" if raw else ""
                    return f"{prefix}(imagen recibida) {result.text}"

        # Normalize unsupported media messages
        _unsupported = (
            "messagecontextinfo is not yet supported",
            "this message type can't be displayed",
            "this message type can not be displayed",
        )
        if raw.lower() in _unsupported or not raw:
            return "(el cliente envio un archivo, imagen o audio que no se puede leer)"
        return raw

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
        raw_candidates = {
            str(source_name).strip(),
            str(source_external_id).strip(),
            str(source_obj_id).strip(),
            str(source_id).strip(),
        }
        # Remove null-like values
        raw_candidates.discard("None")
        raw_candidates.discard("")
        raw_candidates.discard("0")
        if not raw_candidates:
            logger.debug("Lead %s has no source data — allowing.", lead.get("id"))
            return True
        # Normalize: strip +, spaces, dashes for flexible matching
        norm = lambda v: v.replace("+", "").replace(" ", "").replace("-", "").strip()
        norm_expected = norm(expected)
        return any(norm(c) == norm_expected for c in raw_candidates)

    def _is_pipeline_allowed(self, lead: dict[str, Any]) -> bool:
        """Check if the lead is in an allowed pipeline and not in a closed status."""
        allowed = self.settings.allowed_pipeline_ids
        if allowed:
            pipeline_id = lead.get("pipeline_id")
            if pipeline_id is not None and int(pipeline_id) not in allowed:
                return False

        if self.settings.skip_closed_statuses:
            status_id = lead.get("status_id")
            if status_id is not None and int(status_id) in (142, 143):
                return False

        return True

    def _is_phone_allowed(self, lead: dict[str, Any], contact_id: int | str | None) -> bool:
        """Check if any contact's phone is in the test whitelist.
        Checks the webhook contact_id first, then falls back to the lead's
        main contact.  If whitelist is empty, all phones are allowed."""
        whitelist = self.settings.test_phone_whitelist
        if not whitelist:
            return True  # No whitelist = allow all

        normalized_whitelist = {
            p.replace("+", "").replace(" ", "").replace("-", "").strip()
            for p in whitelist
        }

        # Build list of contact IDs to try: webhook contact first, then
        # every contact embedded in the lead (main contact usually first).
        candidate_ids: list[int] = []
        if contact_id:
            candidate_ids.append(int(contact_id))
        for c in (lead.get("_embedded") or {}).get("contacts") or []:
            cid = c.get("id")
            if cid and int(cid) not in candidate_ids:
                candidate_ids.append(int(cid))

        if not candidate_ids:
            logger.debug("Lead %s has no contacts — cannot check phone.", lead.get("id"))
            return False

        for cid in candidate_ids:
            try:
                contact = self.kommo.get_contact(contact_id=cid)
            except Exception:
                logger.exception("Failed to get contact %s for phone check.", cid)
                continue
            phone = self._extract_contact_phone(contact)
            if not phone:
                continue
            normalized_phone = phone.replace("+", "").replace(" ", "").replace("-", "").strip()
            if normalized_phone in normalized_whitelist:
                logger.debug("Contact %s phone %s matches whitelist.", cid, normalized_phone)
                return True
            logger.debug("Contact %s phone %s not in whitelist.", cid, normalized_phone)

        logger.info("Lead %s: no contact phone matched whitelist %s", lead.get("id"), normalized_whitelist)
        return False

    @staticmethod
    def _extract_contact_phone(contact: dict[str, Any]) -> str | None:
        """Extract phone number from Kommo contact object."""
        fields = contact.get("custom_fields_values") or []
        for field in fields:
            code = field.get("field_code", "")
            if code == "PHONE":
                values = field.get("values") or []
                if values:
                    return str(values[0].get("value", "")).strip()
        return None

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
        return "\n".join(messages)

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(str(value))
        except ValueError:
            return None
