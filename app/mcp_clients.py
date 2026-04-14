from __future__ import annotations

from typing import Any

import requests


class MCPClientError(Exception):
    pass


class MCPClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout_seconds: int = 25) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        payload = {"name": tool_name, "arguments": arguments or {}}
        response = requests.post(
            self._url("/mcp/call"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise MCPClientError(
                f"MCP tool failed ({tool_name}): {body.get('error') or 'unknown error'}"
            )
        return body.get("data")


class KommoMCPClient(MCPClient):
    def get_account(self) -> dict[str, Any]:
        return self.call_tool("kommo_get_account", {})

    def get_lead(self, lead_id: int) -> dict[str, Any]:
        return self.call_tool("kommo_get_lead", {"lead_id": lead_id})

    def get_contact(self, contact_id: int) -> dict[str, Any]:
        return self.call_tool("kommo_get_contact", {"contact_id": contact_id})

    def upsert_custom_field_value(self, lead_id: int, field_id: int, value: Any) -> dict[str, Any]:
        return self.call_tool(
            "kommo_upsert_custom_field_value",
            {"lead_id": lead_id, "field_id": field_id, "value": value},
        )

    def run_salesbot(self, bot_id: int, lead_id: int) -> dict[str, Any]:
        return self.call_tool("kommo_run_salesbot", {"bot_id": bot_id, "lead_id": lead_id})
