from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import Json
from psycopg2.pool import SimpleConnectionPool


@dataclass
class DBHealth:
    enabled: bool
    ok: bool
    detail: str


class PostgresStore:
    def __init__(self, dsn: str | None) -> None:
        self.dsn = (dsn or "").strip()
        self.enabled = bool(self.dsn)
        self._pool: SimpleConnectionPool | None = None
        if self.enabled:
            self._pool = SimpleConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=self.dsn,
            )

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        if not self._pool:
            raise RuntimeError("Postgres pool is not initialized.")
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def health(self) -> DBHealth:
        if not self.enabled:
            return DBHealth(enabled=False, ok=True, detail="disabled")
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    _ = cur.fetchone()
            return DBHealth(enabled=True, ok=True, detail="ok")
        except Exception as exc:  # noqa: BLE001
            return DBHealth(enabled=True, ok=False, detail=str(exc))

    def ensure_session(
        self,
        session_id: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        payload = metadata or {}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agentes_micoche.chat_sessions (session_id, user_id, metadata, last_activity)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (session_id)
                    DO UPDATE SET
                        user_id = COALESCE(EXCLUDED.user_id, agentes_micoche.chat_sessions.user_id),
                        metadata = COALESCE(EXCLUDED.metadata, agentes_micoche.chat_sessions.metadata),
                        last_activity = NOW();
                    """,
                    (session_id, user_id, Json(payload)),
                )
            conn.commit()

    def add_message(self, session_id: str, role: str, content: str) -> None:
        if not self.enabled:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agentes_micoche.chat_messages (session_id, role, content)
                    VALUES (%s, %s, %s);
                    """,
                    (session_id, role, content),
                )
                cur.execute(
                    """
                    UPDATE agentes_micoche.chat_sessions
                    SET last_activity = NOW()
                    WHERE session_id = %s;
                    """,
                    (session_id,),
                )
            conn.commit()

    def log_tool_event(
        self,
        session_id: str | None,
        user_id: str | None,
        tool_name: str,
        request_payload: dict[str, Any] | None,
        response_payload: Any,
        success: bool,
        duration_ms: int | None = None,
        error_text: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        response_json: Any
        if isinstance(response_payload, (dict, list, str, int, float, bool)) or response_payload is None:
            response_json = response_payload
        else:
            response_json = {"raw": str(response_payload)}

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agentes_micoche.tool_events (
                        session_id, user_id, tool_name, request_payload, response_payload,
                        success, duration_ms, error_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        session_id,
                        user_id,
                        tool_name,
                        Json(request_payload or {}),
                        Json(response_json),
                        success,
                        duration_ms,
                        error_text,
                    ),
                )
            conn.commit()

    def log_agent_run(
        self,
        session_id: str | None,
        user_id: str | None,
        model_id: str | None,
        input_message: str,
        output_message: str | None,
        success: bool,
        duration_ms: int | None = None,
        error_text: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agentes_micoche.agent_runs (
                        session_id, user_id, model_id, input_message, output_message,
                        success, duration_ms, error_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        session_id,
                        user_id,
                        model_id,
                        input_message,
                        output_message,
                        success,
                        duration_ms,
                        error_text,
                    ),
                )
            conn.commit()

    def delete_session(self, session_id: str) -> bool:
        if not self.enabled:
            return False
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agentes_micoche.chat_sessions WHERE session_id = %s;",
                    (session_id,),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def close(self) -> None:
        if self._pool:
            self._pool.closeall()
            self._pool = None

