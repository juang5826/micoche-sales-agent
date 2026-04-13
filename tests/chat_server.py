"""
Servidor ligero de pruebas para el chat de Mi Coche.
NO necesita Kommo — solo OpenAI + Supabase (para RAG).
Usa Microsoft Agent Framework con memoria y RAG.

Uso:
    set OPENAI_API_KEY=sk-...
    set SUPABASE_SERVICE_KEY=eyJ...
    python tests/chat_server.py

Luego abre tests/chat_playground.html y conecta a http://localhost:8900
"""
from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from uuid import uuid4

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.orchestrator import MiCocheMAFOrchestrator
from app.rag_client import RAGClient
from app.utils import filter_agent_output

api_key = os.environ.get("OPENAI_API_KEY", "")
model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
supabase_url = os.environ.get("SUPABASE_URL", "https://tgvfvsruvfzrmfohbgwx.supabase.co")

# Fallback: use the project's anon key if no key provided via env
if not supabase_key:
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRndmZ2c3J1dmZ6cm1mb2hiZ3d4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTYzMDUzMzUsImV4cCI6MjA3MTg4MTMzNX0.I3Rml57Y0bs2hmSFUzR1whIFDrPXTkZ_3Mtus2kReG8"

if not api_key:
    print("ERROR: Define OPENAI_API_KEY antes de ejecutar.")
    sys.exit(1)

rag_client = None
if supabase_key:
    rag_client = RAGClient(
        openai_api_key=api_key,
        supabase_url=supabase_url,
        supabase_service_key=supabase_key,
        match_threshold=0.45,
    )
    print(f"RAG habilitado — buscara en agentes.vector_cursos")
else:
    print("SUPABASE_SERVICE_KEY no definida — RAG deshabilitado (sin precios)")

orch = MiCocheMAFOrchestrator(api_key=api_key, model=model, temperature=0.5, rag_client=rag_client)
print(f"MAF Orchestrator listo con modelo: {model}")


class ChatHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "service": "micoche-chat-test-maf",
                "environment": "local-test",
                "tenant": "micoche",
                "integrations": {
                    "openai": "configured",
                    "framework": "microsoft-agent-framework",
                    "rag": "enabled" if rag_client and rag_client.enabled else "disabled",
                },
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/chat":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        message = body.get("message", "")
        thread_id = body.get("thread_id") or str(uuid4())

        try:
            result = orch.answer(message=message, thread_id=thread_id)
            filtered = filter_agent_output(result.answer)

            response = {
                "thread_id": thread_id,
                "route": result.route,
                "answer": filtered.text,
                "metadata": {
                    **result.metadata,
                    "escalated": filtered.should_escalate,
                },
            }
            self.send_response(200)
        except Exception as exc:
            response = {"detail": str(exc)}
            self.send_response(500)

        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        msg = args[0] if args else ""
        if "OPTIONS" not in str(msg):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


PORT = int(os.environ.get("TEST_PORT", "8900"))
print(f"\nServidor de pruebas (MAF+RAG) escuchando en http://localhost:{PORT}")
print(f"Abre tests/chat_playground.html y conecta a http://localhost:{PORT}")
print("Ctrl+C para detener\n")
HTTPServer(("", PORT), ChatHandler).serve_forever()
