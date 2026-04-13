from __future__ import annotations

from typing import Any

import requests


from dataclasses import dataclass


class LLMClientError(Exception):
    pass


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class OpenAIHTTPClient:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout_seconds: int = 25,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")
        self.last_usage = LLMUsage()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.enabled:
            raise LLMClientError("OpenAI API key is not configured.")
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _parse_output_text(self, body: dict[str, Any]) -> str:
        output_text = str(body.get("output_text", "")).strip()
        if output_text:
            return output_text
        output = body.get("output")
        if isinstance(output, list):
            chunks: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        text = str(part.get("text", "")).strip()
                        if text:
                            chunks.append(text)
            if chunks:
                return "\n".join(chunks)
        return ""

    def generate_text(
        self,
        system_prompt: str,
        user_message: str,
        context: dict[str, Any] | None = None,
        max_output_tokens: int = 500,
        temperature: float = 0.2,
    ) -> str:
        if not self.enabled:
            raise LLMClientError("OpenAI API key is not configured.")

        context_block = ""
        if context:
            _internal_keys = {"thread_id", "lead_id", "contact_id", "talk_id", "event_id", "user_id"}
            safe_context = {k: v for k, v in context.items() if k not in _internal_keys}
            if safe_context:
                context_lines = [f"- {k}: {v}" for k, v in safe_context.items()]
                context_block = "Contexto:\n" + "\n".join(context_lines)

        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"{user_message}\n\n{context_block}".strip(),
                        }
                    ],
                },
            ],
        }

        response = requests.post(
            f"{self.base_url}/responses",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        text = self._parse_output_text(data)
        if not text:
            raise LLMClientError("OpenAI response contained no text output.")
        self.last_usage = self._parse_usage(data)
        return text.strip()

    @staticmethod
    def _parse_usage(data: dict[str, Any]) -> LLMUsage:
        usage = data.get("usage") or {}
        return LLMUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )
