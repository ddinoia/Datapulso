"""Conexiones. La app usa SIEMPRE el rol sin privilegios con RLS; el rol admin solo migra y crea tenants."""
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from .settings import get_settings


@contextmanager
def admin_conn(dbname: str | None = None, autocommit: bool = False):
    s = get_settings()
    with psycopg.connect(**s.admin_kwargs(dbname), autocommit=autocommit, row_factory=dict_row) as c:
        yield c


@contextmanager
def tenant_conn(tenant_id):
    """Conexión de aplicación fijada a UN tenant. El tenant se fija a nivel de sesión:
    no reutilizar esta conexión para otro tenant (si se agrega un pool, resetear al devolverla)."""
    s = get_settings()
    with psycopg.connect(**s.app_kwargs(), row_factory=dict_row) as c:
        c.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        c.commit()
        yield c
