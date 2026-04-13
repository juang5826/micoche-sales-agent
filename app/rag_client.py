"""
Cliente RAG para buscar informacion en agentes.vector_cursos via Supabase.
Genera embeddings con OpenAI y busca chunks similares con pgvector.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class RAGChunk:
    text: str
    similarity: float
    metadata: dict[str, Any]


class RAGClient:
    def __init__(
        self,
        openai_api_key: str,
        supabase_url: str,
        supabase_service_key: str,
        embedding_model: str = "text-embedding-3-small",
        match_threshold: float = 0.75,
        match_count: int = 5,
        timeout_seconds: int = 15,
    ) -> None:
        self.openai_api_key = (openai_api_key or "").strip()
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_service_key = (supabase_service_key or "").strip()
        self.embedding_model = embedding_model
        self.match_threshold = match_threshold
        self.match_count = match_count
        self.timeout = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.openai_api_key and self.supabase_service_key)

    def _get_embedding(self, text: str) -> list[float]:
        resp = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.embedding_model, "input": text},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def _search_chunks(self, embedding: list[float]) -> list[RAGChunk]:
        resp = requests.post(
            f"{self.supabase_url}/rest/v1/rpc/match_cursos",
            headers={
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json",
            },
            json={
                "query_embedding": embedding,
                "match_threshold": self.match_threshold,
                "match_count": self.match_count,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return [
            RAGChunk(
                text=row["text"],
                similarity=row["similarity"],
                metadata=row.get("metadata") or {},
            )
            for row in rows
        ]

    def search(self, query: str) -> list[RAGChunk]:
        if not self.enabled:
            return []
        try:
            embedding = self._get_embedding(query)
            chunks = self._search_chunks(embedding)
            logger.info(
                "RAG search for '%s' returned %d chunks (best=%.3f)",
                query[:50],
                len(chunks),
                chunks[0].similarity if chunks else 0,
            )
            return chunks
        except Exception:
            logger.exception("RAG search failed for query: %s", query[:50])
            return []

    def search_as_context(self, query: str) -> str:
        chunks = self.search(query)
        if not chunks:
            return ""
        lines = []
        for chunk in chunks:
            lines.append(chunk.text.strip())
        return "\n---\n".join(lines)
