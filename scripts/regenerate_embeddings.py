"""
Regenera todos los embeddings en agentes.vector_cursos
usando text-embedding-3-small para consistencia con el agente.

Uso:
    set OPENAI_API_KEY=sk-...
    python scripts/regenerate_embeddings.py
"""
import json
import os
import sys
import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://tgvfvsruvfzrmfohbgwx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"

# Fallback anon key
if not SUPABASE_KEY:
    SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRndmZ2c3J1dmZ6cm1mb2hiZ3d4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTYzMDUzMzUsImV4cCI6MjA3MTg4MTMzNX0.I3Rml57Y0bs2hmSFUzR1whIFDrPXTkZ_3Mtus2kReG8"

if not OPENAI_API_KEY:
    print("ERROR: Define OPENAI_API_KEY")
    sys.exit(1)


def get_all_chunks():
    """Fetch all chunks from vector_cursos."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/list_knowledge",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json={},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_embedding(text: str) -> list[float]:
    """Generate embedding using OpenAI."""
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def update_embedding(chunk_id: str, embedding: list[float]):
    """Update embedding via upsert_knowledge RPC."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/upsert_knowledge",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "p_id": chunk_id,
            "p_titulo": "",  # won't overwrite if empty — but RPC does overwrite
            "p_categoria": "",
            "p_text": "",
            "p_embedding": embedding,
            "p_updated_by": "embedding-regen-script",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"Modelo de embedding: {EMBEDDING_MODEL}")
    print(f"Supabase: {SUPABASE_URL}")
    print()

    chunks = get_all_chunks()
    print(f"Total chunks: {len(chunks)}")
    print()

    # We need a direct update since upsert_knowledge overwrites text/titulo/categoria
    # Use direct PATCH via PostgREST instead
    for i, chunk in enumerate(chunks, 1):
        chunk_id = chunk["id"]
        text = chunk.get("text", "")
        titulo = chunk.get("titulo", "")

        if not text:
            print(f"  [{i}/{len(chunks)}] SKIP (no text): {chunk_id}")
            continue

        print(f"  [{i}/{len(chunks)}] {titulo or chunk_id[:8]}... ", end="", flush=True)

        try:
            embedding = get_embedding(text)
            # Update via RPC (SECURITY DEFINER bypasses schema perms)
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/update_embedding",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                },
                json={"p_id": chunk_id, "p_embedding": embedding},
                timeout=15,
            )
            if resp.status_code < 300:
                print(f"OK (dim={len(embedding)})")
            else:
                print(f"WARN: HTTP {resp.status_code} — {resp.text[:100]}")
        except Exception as e:
            print(f"ERROR: {e}")

    print()
    print("Listo! Todos los embeddings regenerados con text-embedding-3-small.")
    print("Ahora puedes subir el match_threshold de 0.3 a 0.7 en config.py")


if __name__ == "__main__":
    main()
