"""Migrador mínimo: aplica db/migrations/*.sql en orden, una sola vez cada una."""
from pathlib import Path
import psycopg
from psycopg import sql
from .settings import get_settings
from .db import admin_conn

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def ensure_database_and_role() -> None:
    s = get_settings()
    with admin_conn(dbname="postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (s.db_name,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(s.db_name)))
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (s.app_user,)).fetchone():
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                sql.Identifier(s.app_user), sql.Literal(s.app_password)))
        else:
            c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(s.app_user), sql.Literal(s.app_password)))


def migrate() -> list[str]:
    ensure_database_and_role()
    aplicadas = []
    with admin_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migraciones (nombre text PRIMARY KEY, aplicada_en timestamptz DEFAULT now())")
        hechas = {r["nombre"] for r in c.execute("SELECT nombre FROM schema_migraciones")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in hechas:
                continue
            c.execute(f.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_migraciones (nombre) VALUES (%s)", (f.name,))
            aplicadas.append(f.name)
        c.commit()
    return aplicadas
