"""
Sincroniza las plantillas de Kommo al RAG de Supabase (agentes.vector_cursos).

Toma un catalogo hardcoded de chunks derivados de las plantillas oficiales
de Kommo, genera embeddings con OpenAI y los inserta en la tabla del RAG.
Tambien desactiva los chunks viejos con precios desactualizados para que
el agente deje de usarlos.

Ejecucion:
    python scripts/sync_kommo_templates.py

Env vars requeridas:
    OPENAI_API_KEY
    SUPABASE_DB_USER
    SUPABASE_DB_PASSWORD
    SUPABASE_DB_NAME    (opcional, default: postgres)
    SUPABASE_DB_HOST    (opcional, default: aws-1-us-east-1.pooler.supabase.com)
    SUPABASE_DB_PORT    (opcional, default: 5432)
    SUPABASE_PROJECT_REF (opcional, default: tgvfvsruvfzrmfohbgwx)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any

import psycopg2
import requests

logger = logging.getLogger("sync_kommo_templates")


EMBEDDING_MODEL = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Catalogo de chunks derivados de las plantillas oficiales de Kommo.
# Cada chunk tiene:
#   - slug: identificador unico para upsert idempotente
#   - titulo, categoria: metadata para filtros y UI
#   - text: contenido que el agente vera en el contexto RAG
#   - kommo_template_id: id de la plantilla origen en Kommo
# ---------------------------------------------------------------------------
CHUNKS: list[dict[str, Any]] = [
    {
        "slug": "licencia_a2_individual",
        "titulo": "Licencia A2 - Moto",
        "categoria": "licencia_a2",
        "kommo_template_id": 79350,
        "text": (
            "LICENCIA A2 - MOTOCICLETAS DE MAS DE 125 CC\n"
            "Intensidad horaria: 28 horas teoricas y 15 horas practicas.\n"
            "Desglose de costos:\n"
            "- Valor del curso: $930.000\n"
            "- Examen medico: $237.000\n"
            "- Valor de la licencia: $66.000\n"
            "Total: $1.233.000\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "licencia_b1_individual",
        "titulo": "Licencia B1 - Vehiculo particular",
        "categoria": "licencia_b1",
        "kommo_template_id": 79322,
        "text": (
            "LICENCIA B1 - VEHICULO PARTICULAR (CARRO)\n"
            "Intensidad horaria: 30 horas teoricas y 20 horas practicas.\n"
            "Desglose de costos:\n"
            "- Valor del curso: $1.215.000\n"
            "- Examen medico: $237.000\n"
            "- Valor de la licencia: $66.000\n"
            "Total: $1.518.000\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "licencia_c1_individual",
        "titulo": "Licencia C1 - Particular y servicio publico",
        "categoria": "licencia_c1",
        "kommo_template_id": 79348,
        "text": (
            "LICENCIA C1 - VEHICULO PARTICULAR Y SERVICIO PUBLICO\n"
            "Intensidad horaria: 36 horas teoricas y 30 horas practicas.\n"
            "Desglose de costos:\n"
            "- Valor del curso: $1.510.000\n"
            "- Examen medico: $237.000\n"
            "- Valor de la licencia: $66.000\n"
            "Total: $1.813.000\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "licencia_c2_individual",
        "titulo": "Licencia C2 - Vehiculos rigidos",
        "categoria": "licencia_c2",
        "kommo_template_id": 79318,
        "text": (
            "LICENCIA C2 - VEHICULOS RIGIDOS (CAMIONES)\n"
            "Intensidad horaria: 30 horas teoricas y 15 horas practicas.\n"
            "Desglose de costos:\n"
            "- Valor del curso: $1.425.000\n"
            "- Examen medico: $237.000\n"
            "- Valor de la licencia: $139.155\n"
            "Total: $1.801.155\n"
            "Requisito indispensable: Contar con licencia C1 para acceder a esta categoria."
        ),
    },
    {
        "slug": "licencia_c3_individual",
        "titulo": "Licencia C3 - Vehiculos articulados",
        "categoria": "licencia_c3",
        "kommo_template_id": 79240,
        "text": (
            "LICENCIA C3 - VEHICULOS ARTICULADOS (TRACTOMULAS)\n"
            "Intensidad horaria: 46 horas teoricas y 20 horas practicas.\n"
            "Desglose de costos:\n"
            "- Valor del curso: $3.150.000\n"
            "- Examen medico: $237.000\n"
            "- Valor de la licencia: $139.155\n"
            "Total: $3.526.155\n"
            "Requisito indispensable: Contar con licencia C2 para acceder a esta categoria."
        ),
    },
    {
        "slug": "combo_a2_b1",
        "titulo": "Combo A2 + B1 (Moto y Carro)",
        "categoria": "combo_a2b1",
        "kommo_template_id": 79408,
        "text": (
            "COMBO A2 + B1 (MOTOCICLETAS DE MAS DE 125 CC + VEHICULO PARTICULAR)\n"
            "Intensidad horaria: 30 horas teoricas, 15 horas practicas de moto y 20 horas practicas de carro.\n"
            "Desglose de costos:\n"
            "- Valor de los cursos: $2.145.000\n"
            "- Examen medico combo: $324.000\n"
            "- Valor de las dos licencias: $132.000\n"
            "Total: $2.601.000\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "combo_a2_c1",
        "titulo": "Combo A2 + C1 (Moto y Carro publico)",
        "categoria": "combo_a2c1",
        "kommo_template_id": 79340,
        "text": (
            "COMBO A2 + C1 (MOTO DE MAS DE 125 CC + VEHICULO PARTICULAR Y SERVICIO PUBLICO)\n"
            "Intensidad horaria: 36 horas teoricas, 15 horas practicas de moto y 30 horas practicas de carro.\n"
            "Desglose de costos:\n"
            "- Valor de los cursos: $2.440.000\n"
            "- Examen medico combo: $324.000\n"
            "- Valor de las dos licencias: $132.000\n"
            "Total: $2.896.000\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "combo_a2_c2",
        "titulo": "Combo A2 + C2 (Moto y Camion)",
        "categoria": "combo_a2c2",
        "kommo_template_id": 79410,
        "text": (
            "COMBO A2 + C2 (MOTO DE MAS DE 125 CC + VEHICULO RIGIDO / CAMION)\n"
            "Intensidad horaria: 30 horas teoricas, 15 horas practicas de moto y 15 horas practicas de camion.\n"
            "Desglose de costos:\n"
            "- Valor de los cursos: $2.355.000\n"
            "- Examen medico combo: $324.000\n"
            "- Valor de las dos licencias: $205.155\n"
            "Total: $2.884.155\n"
            "Requisito: Estar inscrito en el RUNT para iniciar el proceso."
        ),
    },
    {
        "slug": "combo_a2_c3",
        "titulo": "Combo A2 + C3 (Moto y Tractomula)",
        "categoria": "combo_a2c3",
        "kommo_template_id": 79238,
        "text": (
            "COMBO A2 + C3 (MOTO DE MAS DE 125 CC + VEHICULO ARTICULADO / TRACTOMULA)\n"
            "Intensidad horaria: 46 horas teoricas, 15 horas practicas de moto y 20 horas practicas de tractomula.\n"
            "Desglose de costos:\n"
            "- Valor de los cursos: $4.080.000\n"
            "- Examen medico combo: $324.000\n"
            "- Valor de las dos licencias: $205.155\n"
            "Total: $4.609.155\n"
            "Requisito indispensable: Contar con licencia C2 para recategorizar a C3."
        ),
    },
    {
        "slug": "instructor_a2",
        "titulo": "Curso Instructor A2",
        "categoria": "instructor_a2",
        "kommo_template_id": 79336,
        "text": (
            "CURSO INSTRUCTOR A2 - MOTOCICLETAS\n"
            "Intensidad horaria: 90 horas teoricas y 60 horas practicas.\n"
            "Valor del curso: $2.800.000\n"
            "Importante: La licencia de instructor se paga aparte directamente en la territorial de Bogota.\n"
            "Ideal para quienes ya tienen licencia A2 y quieren dar el siguiente paso como instructores.\n"
            "Telefono de contacto para cupos de instructor: 3134246298."
        ),
    },
    {
        "slug": "instructor_b1",
        "titulo": "Curso Instructor B1",
        "categoria": "instructor_b1",
        "kommo_template_id": 79324,
        "text": (
            "CURSO INSTRUCTOR B1 - VEHICULO PARTICULAR\n"
            "Intensidad horaria: 90 horas teoricas y 60 horas practicas.\n"
            "Valor del curso: $3.000.000\n"
            "Importante: La licencia de instructor se paga aparte directamente en la territorial de Bogota.\n"
            "Requisito: Contar con dos anos de antigüedad con la licencia B1 de conduccion.\n"
            "Telefono de contacto para cupos de instructor: 3134246298."
        ),
    },
    {
        "slug": "instructor_c1",
        "titulo": "Curso Instructor C1",
        "categoria": "instructor_c1",
        "kommo_template_id": 79312,
        "text": (
            "CURSO INSTRUCTOR C1\n"
            "Intensidad horaria: 110 horas teoricas y 60 horas practicas.\n"
            "Valor del curso: $3.500.000\n"
            "Importante: La licencia de instructor se paga aparte directamente en la territorial de Bogota.\n"
            "Requisito: Contar con dos anos de antigüedad con la licencia C1 de conduccion.\n"
            "Telefono de contacto para cupos de instructor: 3134246298."
        ),
    },
    {
        "slug": "instructor_c2",
        "titulo": "Curso Instructor C2",
        "categoria": "instructor_c2",
        "kommo_template_id": 79338,
        "text": (
            "CURSO INSTRUCTOR C2 - CAMIONES\n"
            "Intensidad horaria: 110 horas teoricas y 70 horas practicas.\n"
            "Valor del curso: $4.500.000\n"
            "Importante: La licencia de instructor se paga aparte directamente en la territorial de Bogota.\n"
            "Requisito: Contar con dos anos de antigüedad con la licencia C2 de conduccion.\n"
            "Telefono de contacto para cupos de instructor: 3134246298."
        ),
    },
    {
        "slug": "instructor_c3",
        "titulo": "Curso Instructor C3",
        "categoria": "instructor_c3",
        "kommo_template_id": 79346,
        "text": (
            "CURSO INSTRUCTOR C3 - TRACTOMULAS\n"
            "Intensidad horaria: 120 horas teoricas y 80 horas practicas.\n"
            "Valor del curso: $10.500.000\n"
            "Importante: La licencia de instructor se paga aparte directamente en la territorial de Bogota.\n"
            "Requisito: Contar con dos anos de antigüedad con la licencia C3 de conduccion.\n"
            "Telefono de contacto para cupos de instructor: 3134246298."
        ),
    },
    {
        "slug": "refrendacion_una_categoria",
        "titulo": "Refrendacion 1 categoria",
        "categoria": "refrendacion",
        "kommo_template_id": 79344,
        "text": (
            "REFRENDACION DE LICENCIA - 1 CATEGORIA (TRAMITE SIN CURSO)\n"
            "Incluye examen medico y derechos de licencia en Mosquera.\n"
            "Desglose:\n"
            "- Examen medico: $237.000\n"
            "- Derechos de licencia en Mosquera: $140.000\n"
            "Total: $377.000\n"
            "Cobertura: A2, B1, C1, C2 o C3.\n"
            "Incluye todo el proceso, sin tramites adicionales."
        ),
    },
    {
        "slug": "refrendacion_dos_categorias",
        "titulo": "Refrendacion 2 categorias",
        "categoria": "refrendacion",
        "kommo_template_id": 79334,
        "text": (
            "REFRENDACION DE LICENCIA - 2 CATEGORIAS (TRAMITE SIN CURSO)\n"
            "Incluye examenes medicos combinados y derechos de licencias individuales.\n"
            "Desglose:\n"
            "- Examenes medicos combo x2: $324.000\n"
            "- Derechos de licencia individual en Mosquera (A2, B1, C1, C2 o C3): $140.000\n"
            "Total: $604.000\n"
            "Incluye todo el proceso, sin tramites adicionales."
        ),
    },
    {
        "slug": "horas_refuerzo",
        "titulo": "Horas de refuerzo - Clases practicas adicionales",
        "categoria": "horas_refuerzo",
        "kommo_template_id": 79320,
        "text": (
            "HORAS DE REFUERZO (CLASES PRACTICAS ADICIONALES)\n"
            "Precios por hora segun la categoria:\n"
            "- A2 (motocicleta): $45.000 por hora\n"
            "- B1 y C1 (vehiculo carro): $60.000 por hora\n"
            "- C2 (vehiculo rigido, camion): $140.000 por hora\n"
            "- C3 (vehiculo articulado, tractomula): $180.000 por hora\n"
            "Tambien hay un paquete de 10 horas de refuerzo disponible."
        ),
    },
    {
        "slug": "horarios_clientes",
        "titulo": "Horarios de clases teoricas y practicas",
        "categoria": "horarios",
        "kommo_template_id": 79406,
        "text": (
            "HORARIOS DE CLASES EN MI COCHE\n"
            "Clases teoricas A2, B1, C1, C2 y C3:\n"
            "- Lunes a sabado: 8:00 am a 9:00 pm\n"
            "- Domingo: 8:00 am a 2:00 pm\n"
            "- Minimo 2 horas al dia y maximo 8 horas al dia.\n"
            "Clases practicas A2, B1 y C1:\n"
            "- Lunes a viernes: 6:00 am a 10:00 pm\n"
            "- Sabado: 7:00 am a 5:00 pm\n"
            "- Domingo: 8:00 am a 2:00 pm\n"
            "Clases practicas C2 y C3:\n"
            "- Lunes a sabado: 8:00 am a 5:00 pm\n"
            "- Domingo no hay clases practicas.\n"
            "En practica se pueden tomar minimo 2 horas al dia y maximo 4 horas al dia."
        ),
    },
    {
        "slug": "horarios_instructores",
        "titulo": "Horarios de clases para instructores",
        "categoria": "horarios",
        "kommo_template_id": 79326,
        "text": (
            "HORARIOS DE CLASES PARA CURSOS DE INSTRUCTOR (A2, B1, C1, C2, C3)\n"
            "Lunes a sabado: 8:00 am a 5:00 pm.\n"
            "Minimo 1 tema (2 horas) y maximo 4 temas (8 horas) por dia."
        ),
    },
    {
        "slug": "medios_de_pago",
        "titulo": "Medios de pago aceptados",
        "categoria": "pagos",
        "kommo_template_id": 79330,
        "text": (
            "MEDIOS DE PAGO EN MI COCHE\n"
            "Se aceptan los siguientes medios:\n"
            "- Efectivo\n"
            "- Tarjeta (credito y debito)\n"
            "- Codigo QR\n"
            "- Transferencia bancaria (Bancolombia)\n"
            "- Financiacion al 100% con Meddipay"
        ),
    },
    {
        "slug": "meddipay_financiacion",
        "titulo": "Financiacion con Meddipay",
        "categoria": "pagos",
        "kommo_template_id": 79352,
        "text": (
            "FINANCIACION CON MEDDIPAY\n"
            "Meddipay es una opcion de credito que financia hasta el 100 por ciento del curso de conduccion.\n"
            "Link oficial para aplicar al credito: https://app.meddipay.com.co/\n"
            "El cliente diligencia la solicitud en linea directamente con Meddipay."
        ),
    },
    {
        "slug": "ubicacion_sede",
        "titulo": "Ubicacion de la sede",
        "categoria": "ubicacion",
        "kommo_template_id": 117802,
        "text": (
            "UBICACION DE MI COCHE\n"
            "Ubicacion en Google Maps: https://maps.app.goo.gl/k3P7HvdsQLUtMmAX9\n"
            "Este link es el que se comparte con los clientes cuando preguntan por donde quedamos o como llegar."
        ),
    },
    {
        "slug": "flota_tractomulas",
        "titulo": "Tractomulas disponibles para practicas C3",
        "categoria": "flota",
        "kommo_template_id": 79316,
        "text": (
            "FLOTA DE TRACTOMULAS PARA PRACTICAS DE LICENCIA C3\n"
            "Para las horas practicas de la categoria C3 se cuenta con tres tractomulas:\n"
            "- International Eagle\n"
            "- Kodiak\n"
            "- Ford Cargo"
        ),
    },
    {
        "slug": "categorias_overview",
        "titulo": "Categorias de licencia que ofrece Mi Coche",
        "categoria": "categorias_overview",
        "kommo_template_id": 79402,
        "text": (
            "CATEGORIAS DE LICENCIA DISPONIBLES EN MI COCHE\n"
            "- A2: Motocicletas de mas de 125 cc de cilindrada\n"
            "- B1: Vehiculo particular (carro)\n"
            "- C1: Vehiculo particular y servicio publico (taxi, aplicaciones)\n"
            "- C2: Vehiculos rigidos (camiones)\n"
            "- C3: Vehiculos articulados (tractomulas)"
        ),
    },
]


CATEGORIES_WITH_OUTDATED_PRICES = (
    "combos",
    "licencia_a2",
    "licencia_b1",
    "licencia_c1",
    "licencia_c2",
    "licencia_c3",
    "pagos",
    "tramites",  # chunk viejo de horarios y refrendacion roto
)


def get_embedding(text: str, api_key: str) -> list[float]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def build_dsn() -> str:
    user = os.environ["SUPABASE_DB_USER"]
    password = os.environ["SUPABASE_DB_PASSWORD"]
    name = os.environ.get("SUPABASE_DB_NAME", "postgres")
    host = os.environ.get("SUPABASE_DB_HOST", "aws-1-us-east-1.pooler.supabase.com")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    project_ref = os.environ.get("SUPABASE_PROJECT_REF", "tgvfvsruvfzrmfohbgwx")
    # Session Pooler uses "postgres.<project_ref>" as the login user
    login_user = f"{user}.{project_ref}"
    return f"postgresql://{login_user}:{password}@{host}:{port}/{name}?sslmode=require"


def deactivate_old(cur) -> int:
    """Desactiva los chunks viejos con precios desactualizados.

    Solo toca los que NO fueron creados por este sync (updated_by distinto
    de 'kommo_sync'), asi es idempotente y seguro para correr varias veces.
    """
    cur.execute(
        """
        UPDATE agentes.vector_cursos
           SET activo = false,
               updated_at = NOW()
         WHERE categoria = ANY(%s)
           AND (updated_by IS NULL OR updated_by <> 'kommo_sync');
        """,
        (list(CATEGORIES_WITH_OUTDATED_PRICES),),
    )
    return cur.rowcount


def delete_previous_sync(cur) -> int:
    """Limpia los chunks sembrados por sync anteriores (upsert idempotente)."""
    cur.execute(
        """
        DELETE FROM agentes.vector_cursos
         WHERE updated_by = 'kommo_sync';
        """
    )
    return cur.rowcount


def insert_chunk(cur, chunk: dict[str, Any], embedding: list[float]) -> None:
    metadata = {
        "sync_slug": chunk["slug"],
        "kommo_template_id": chunk.get("kommo_template_id"),
        "source": "kommo_template",
    }
    cur.execute(
        """
        INSERT INTO agentes.vector_cursos
            (id, text, metadata, embedding, titulo, categoria, activo, updated_at, updated_by)
        VALUES (%s, %s, %s::jsonb, %s::vector, %s, %s, true, NOW(), 'kommo_sync');
        """,
        (
            str(uuid.uuid4()),
            chunk["text"],
            json.dumps(metadata),
            json.dumps(embedding),
            chunk["titulo"],
            chunk["categoria"],
        ),
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.error("Missing OPENAI_API_KEY.")
        return 1

    dsn = build_dsn()
    logger.info("Conectando a Supabase Postgres...")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            deactivated = deactivate_old(cur)
            logger.info("Chunks viejos desactivados: %d", deactivated)

            deleted = delete_previous_sync(cur)
            logger.info("Chunks previos de sync eliminados: %d", deleted)

            logger.info("Generando embeddings e insertando %d chunks...", len(CHUNKS))
            for i, chunk in enumerate(CHUNKS, 1):
                logger.info("  [%d/%d] %s", i, len(CHUNKS), chunk["titulo"])
                embedding = get_embedding(chunk["text"], api_key)
                insert_chunk(cur, chunk, embedding)
        conn.commit()
        logger.info("Commit exitoso.")
    except Exception:
        conn.rollback()
        logger.exception("Sync fallo — rollback ejecutado.")
        return 2
    finally:
        conn.close()

    logger.info("Listo. Total chunks sincronizados: %d", len(CHUNKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
