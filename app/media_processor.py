from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from app.llm_client import LLMClientError, OpenAIHTTPClient

URL_REGEX = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac"}


@dataclass
class MediaContextResult:
    summaries: list[str]
    errors: list[str]
    media_count: int


class MediaProcessor:
    def __init__(
        self,
        llm_client: OpenAIHTTPClient | None,
        timeout_seconds: int = 12,
        max_bytes: int = 8_000_000,
    ) -> None:
        self.llm = llm_client
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def enrich_from_payload(self, payload: dict[str, str]) -> MediaContextResult:
        urls = self._extract_urls(payload)
        summaries: list[str] = []
        errors: list[str] = []

        for url in urls:
            media_type = self._detect_media_type(url)
            if media_type == "unknown":
                continue
            try:
                blob, mime = self._download(url)
                if media_type == "image":
                    summaries.append(self._describe_image(url=url))
                elif media_type == "audio":
                    summaries.append(self._transcribe_audio(url=url, data=blob, mime_type=mime))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {exc}")

        return MediaContextResult(summaries=summaries, errors=errors, media_count=len(urls))

    def _extract_urls(self, payload: dict[str, str]) -> list[str]:
        found: list[str] = []
        for key, value in payload.items():
            text = str(value or "")
            found.extend(URL_REGEX.findall(text))

            if text.startswith("{") or text.startswith("["):
                try:
                    parsed = json.loads(text)
                    found.extend(self._find_urls_recursive(parsed))
                except Exception:  # noqa: BLE001
                    _ = key
                    continue

        deduped: list[str] = []
        seen: set[str] = set()
        for item in found:
            clean = item.strip().rstrip(".,)")
            if clean not in seen:
                seen.add(clean)
                deduped.append(clean)
        return deduped

    def _find_urls_recursive(self, node: Any) -> list[str]:
        urls: list[str] = []
        if isinstance(node, dict):
            for value in node.values():
                urls.extend(self._find_urls_recursive(value))
        elif isinstance(node, list):
            for item in node:
                urls.extend(self._find_urls_recursive(item))
        elif isinstance(node, str):
            urls.extend(URL_REGEX.findall(node))
        return urls

    def _detect_media_type(self, url: str) -> str:
        path = urlparse(url).path.lower()
        for ext in IMAGE_EXTENSIONS:
            if path.endswith(ext):
                return "image"
        for ext in AUDIO_EXTENSIONS:
            if path.endswith(ext):
                return "audio"
        return "unknown"

    def _download(self, url: str) -> tuple[bytes, str]:
        response = requests.get(url, timeout=self.timeout_seconds, stream=True)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type", "application/octet-stream"))
        chunks: list[bytes] = []
        current = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            current += len(chunk)
            if current > self.max_bytes:
                raise ValueError("Media exceeds size limit.")
            chunks.append(chunk)
        return b"".join(chunks), content_type

    def _describe_image(self, url: str) -> str:
        if not self.llm or not self.llm.enabled:
            raise LLMClientError("OpenAI key not configured for image analysis.")
        prompt = (
            "Describe brevemente el contenido de la imagen para contexto comercial de atención "
            "al cliente en una escuela de conducción. Máximo 3 líneas."
        )
        text = self.llm.describe_image_url(
            system_prompt="Eres un asistente que resume contenido visual en español.",
            user_prompt=prompt,
            image_url=url,
            max_output_tokens=220,
        )
        return f"[imagen] {text}"

    def _transcribe_audio(self, url: str, data: bytes, mime_type: str) -> str:
        if not self.llm or not self.llm.enabled:
            raise LLMClientError("OpenAI key not configured for audio transcription.")

        files = {
            "file": ("audio_input", data, mime_type),
        }
        payload = {
            "model": "gpt-4o-mini-transcribe",
            "language": "es",
        }
        response = requests.post(
            f"{self.llm.base_url}/audio/transcriptions",
            data=payload,
            files=files,
            headers={"authorization": f"Bearer {self.llm.api_key}"},
            timeout=max(self.timeout_seconds, 25),
        )
        response.raise_for_status()
        body = response.json()
        text = str(body.get("text", "")).strip()
        if not text:
            raise ValueError("Audio transcription returned empty text.")
        return f"[audio] {text}"
