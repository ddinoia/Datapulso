"""Fixtures. Requieren PostgreSQL (y MariaDB/MySQL para los tests del conector) levantados:
    scripts/dev_env.sh start
Cada corrida usa una base de datos propia y la borra al final."""
import os
import uuid
import pytest
from cryptography.fernet import Fernet


@pytest.fixture(scope="session", autouse=True)
def entorno():
    os.environ.setdefault("DATAPULSO_SECRET_KEY", Fernet.generate_key().decode())
    nombre = "dp_test_" + uuid.uuid4().hex[:8]
    os.environ["DP_DB_NAME"] = nombre
    from datapulso import migrate as m
    from datapulso.db import admin_conn
    try:
        m.migrate()
    except Exception as e:  # pragma: no cover
        pytest.exit(f"No hay PostgreSQL disponible ({e}). Ejecutar scripts/dev_env.sh start", returncode=2)
    yield nombre
    with admin_conn(dbname="postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')


def _mysql_disponible() -> bool:
    try:
        from datapulso.datagen.fuente_db import servidor_defecto, _conectar
        _conectar(servidor_defecto("mysql"), None).close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def motores():
    ms = ["postgresql"]
    if _mysql_disponible():
        ms.append("mysql")
    return ms


@pytest.fixture()
def tenant(request):
    from datapulso.tenants import crear_tenant
    industria = getattr(request, "param", "servicios")
    return crear_tenant(f"T-{uuid.uuid4().hex[:8]}", industria, is_demo=True)
