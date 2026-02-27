from __future__ import annotations

from typing import Any

import requests


class LLMClientError(Exception):
    pass


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
            context_lines = [f"- {k}: {v}" for k, v in context.items()]
            context_block = "Contexto interno:\n" + "\n".join(context_lines)

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
        return text.strip()

    def describe_image_url(
        self,
        system_prompt: str,
        user_prompt: str,
        image_url: str,
        max_output_tokens: int = 220,
    ) -> str:
        if not self.enabled:
            raise LLMClientError("OpenAI API key is not configured.")
        payload = {
            "model": self.model,
            "max_output_tokens": max_output_tokens,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {"type": "input_image", "image_url": image_url},
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
        body = response.json()
        text = self._parse_output_text(body)
        if not text:
            raise LLMClientError("Image analysis returned empty text.")
        return text.strip()
