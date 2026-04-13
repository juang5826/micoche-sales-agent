"""
Procesador de multimedia para el agente de Mi Coche.
- Audio: transcribe con OpenAI Whisper
- Imagen: analiza con GPT Vision
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

# MIME types que sabemos procesar
AUDIO_MIMES = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/amr"}
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@dataclass
class MediaResult:
    """Resultado de procesar un archivo multimedia."""
    media_type: str  # "audio", "image", "unsupported"
    text: str  # transcripción o descripción
    success: bool


class MediaProcessor:
    def __init__(
        self,
        openai_api_key: str,
        vision_model: str = "gpt-5.4-mini",
        whisper_model: str = "whisper-1",
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = (openai_api_key or "").strip()
        self.vision_model = vision_model
        self.whisper_model = whisper_model
        self.timeout = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers_json(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _headers_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def download_media(self, url: str, auth_headers: dict[str, str] | None = None) -> tuple[bytes, str]:
        """Descarga un archivo multimedia desde una URL.
        Returns (bytes, content_type)."""
        headers = auth_headers or {}
        resp = requests.get(url, headers=headers, timeout=self.timeout, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        data = resp.content
        logger.info("Downloaded media: %s (%d bytes, %s)", url[:80], len(data), content_type)
        return data, content_type

    def transcribe_audio(self, audio_bytes: bytes, filename: str = "audio.ogg") -> MediaResult:
        """Transcribe audio usando OpenAI Whisper."""
        if not self.enabled:
            return MediaResult(media_type="audio", text="", success=False)

        try:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=self._headers_auth(),
                files={"file": (filename, io.BytesIO(audio_bytes), "audio/ogg")},
                data={"model": self.whisper_model, "language": "es"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()
            logger.info("Audio transcribed: '%s'", text[:100])
            return MediaResult(media_type="audio", text=text, success=bool(text))
        except Exception:
            logger.exception("Audio transcription failed")
            return MediaResult(media_type="audio", text="", success=False)

    def analyze_image(self, image_bytes: bytes, content_type: str = "image/jpeg") -> MediaResult:
        """Analiza una imagen usando GPT Vision para entender qué envió el cliente."""
        if not self.enabled:
            return MediaResult(media_type="image", text="", success=False)

        import base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{content_type};base64,{b64}"

        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=self._headers_json(),
                json={
                    "model": self.vision_model,
                    "max_tokens": 200,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Eres asistente de Mi Coche, academia de conduccion. "
                                "El cliente envio esta imagen por WhatsApp. "
                                "Describe brevemente que ves en la imagen y que podria necesitar el cliente. "
                                "Si es un comprobante de pago, di que es un comprobante. "
                                "Si es un documento (cedula, licencia), di que tipo de documento parece. "
                                "Si es algo irrelevante, solo describe brevemente. "
                                "Maximo 2 oraciones en español."
                            ),
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": data_uri}},
                            ],
                        },
                    ],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info("Image analyzed: '%s'", text[:100])
            return MediaResult(media_type="image", text=text, success=bool(text))
        except Exception:
            logger.exception("Image analysis failed")
            return MediaResult(media_type="image", text="", success=False)

    def process_media_url(
        self,
        media_url: str,
        content_type: str | None = None,
        auth_headers: dict[str, str] | None = None,
    ) -> MediaResult:
        """Procesa multimedia: descarga + transcribe/analiza según tipo."""
        if not self.enabled or not media_url:
            return MediaResult(media_type="unsupported", text="", success=False)

        try:
            data, detected_type = self.download_media(media_url, auth_headers)
            mime = (content_type or detected_type).lower()

            if mime in AUDIO_MIMES:
                ext = mime.split("/")[-1].replace("mpeg", "mp3")
                return self.transcribe_audio(data, filename=f"audio.{ext}")
            elif mime in IMAGE_MIMES:
                return self.analyze_image(data, content_type=mime)
            else:
                logger.info("Unsupported media type: %s", mime)
                return MediaResult(media_type="unsupported", text="", success=False)
        except Exception:
            logger.exception("Media processing failed for %s", media_url[:80])
            return MediaResult(media_type="unsupported", text="", success=False)

    def detect_media_type(self, content_type: str) -> str:
        """Clasifica un MIME type como audio, image o unsupported."""
        mime = (content_type or "").lower().strip()
        if mime in AUDIO_MIMES:
            return "audio"
        if mime in IMAGE_MIMES:
            return "image"
        return "unsupported"
