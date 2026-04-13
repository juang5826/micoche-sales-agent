"""
Banco de pruebas del agente Mi Coche.

Uso:
    python -m tests.run_evaluation                     # usa orchestrator local (sin LLM)
    python -m tests.run_evaluation --live              # usa orchestrator con LLM real
    python -m tests.run_evaluation --url http://...    # llama al endpoint /chat remoto

Requiere: pip install pyyaml
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_cases(path: str | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else Path(__file__).parent / "evaluation_cases.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _normalize(text: str) -> str:
    """Remove accents/tildes for fuzzy matching."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def check_contains(answer: str, must: list[str], must_not: list[str]) -> list[str]:
    answer_norm = _normalize(answer)
    failures: list[str] = []
    for word in must:
        if _normalize(word) not in answer_norm:
            failures.append(f"MISSING '{word}'")
    for word in must_not:
        word_norm = _normalize(word)
        # For must_not, check as whole word to avoid false positives like "Maria" matching "IA"
        import re
        if re.search(r"\b" + re.escape(word_norm) + r"\b", answer_norm):
            failures.append(f"FORBIDDEN '{word}' found")
    return failures


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_local(case: dict[str, Any], *, use_llm: bool = False) -> tuple[str, bool]:
    from app.orchestrator import MiCocheMAFOrchestrator
    from app.llm_client import OpenAIHTTPClient
    from app.utils import filter_agent_output

    llm = None
    if use_llm:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            llm = OpenAIHTTPClient(api_key=api_key, model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))

    orch = MiCocheMAFOrchestrator(llm=llm)
    result = orch.answer(message=case["message"], thread_id=str(case["id"]))
    filtered = filter_agent_output(result.answer)
    return filtered.text, filtered.should_escalate


def run_remote(case: dict[str, Any], base_url: str) -> tuple[str, bool]:
    import requests
    from app.utils import filter_agent_output

    resp = requests.post(
        f"{base_url.rstrip('/')}/chat",
        json={"message": case["message"], "thread_id": str(case["id"])},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    filtered = filter_agent_output(data["answer"])
    return filtered.text, filtered.should_escalate


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluacion del agente Mi Coche")
    parser.add_argument("--live", action="store_true", help="Usar LLM real (requiere OPENAI_API_KEY)")
    parser.add_argument("--url", type=str, default="", help="URL base del agente (ej: http://localhost:8080)")
    parser.add_argument("--cases", type=str, default="", help="Ruta al YAML de casos")
    parser.add_argument("--output", type=str, default="", help="Ruta CSV de resultados")
    args = parser.parse_args()

    cases = load_cases(args.cases or None)
    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for case in cases:
        cid = case["id"]
        msg = case.get("message", "")
        must = case.get("must_contain", [])
        must_not = case.get("must_not_contain", [])
        expect_esc = case.get("expect_escalation", False)

        try:
            if args.url:
                answer, escalated = run_remote(case, args.url)
            else:
                answer, escalated = run_local(case, use_llm=args.live)
        except Exception as exc:
            answer = f"ERROR: {exc}"
            escalated = False

        failures = check_contains(answer, must, must_not)
        if expect_esc and not escalated:
            failures.append("EXPECTED escalation but not triggered")
        if not expect_esc and escalated:
            failures.append("UNEXPECTED escalation triggered")

        ok = len(failures) == 0
        if ok:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        results.append({
            "id": cid,
            "category": case.get("category", ""),
            "message": msg[:80],
            "status": status,
            "failures": "; ".join(failures),
            "answer_preview": answer[:120],
            "escalated": escalated,
        })
        symbol = "OK" if ok else "XX"
        print(f"  {symbol} #{cid:02d} [{case.get('category','')}] {status}  {'; '.join(failures) if failures else ''}")

    print(f"\n{'='*60}")
    print(f"  RESULTADO: {passed} pasaron, {failed} fallaron de {len(cases)} total")
    print(f"{'='*60}")

    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"  Resultados guardados en {out_path}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
