"""Alta de tenants, versiones de configuración y auditoría."""
import json
from .db import admin_conn, tenant_conn
from .config import ConfigTenant, plantilla


def auditar(conn, actor: str, accion: str, detalle: dict | None = None) -> None:
    conn.execute("INSERT INTO auditoria (tenant_id, actor, accion, detalle) VALUES (app_tenant(), %s, %s, %s)",
                 (actor, accion, json.dumps(detalle or {})))


def crear_tenant(nombre: str, industria: str, *, is_demo: bool = False, idioma: str = "es",
                 moneda: str = "ARS", actor: str = "sistema") -> str:
    """Crea el tenant (operación de administración) y su configuración v1 desde la plantilla de industria."""
    cfg = plantilla(industria)
    with admin_conn() as c:
        row = c.execute("INSERT INTO tenants (nombre, industria, idioma, moneda, is_demo) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                        (nombre, industria, idioma, moneda, is_demo)).fetchone()
        c.commit()
        tid = str(row["id"])
    with tenant_conn(tid) as c:
        crear_version_config(c, cfg, actor, nota=f"plantilla inicial de {industria}")
        auditar(c, actor, "tenant_creado", {"nombre": nombre, "industria": industria, "is_demo": is_demo})
        c.commit()
    return tid


def crear_version_config(conn, cfg: ConfigTenant, creado_por: str, nota: str | None = None) -> int:
    """Nunca se edita una versión: se crea una nueva y pasa a ser la activa."""
    ult = conn.execute("SELECT COALESCE(MAX(version),0) AS v FROM config_versions WHERE tenant_id = app_tenant()").fetchone()["v"]
    nueva = ult + 1
    conn.execute("INSERT INTO config_versions (tenant_id, version, contenido, creado_por, nota) VALUES (app_tenant(), %s, %s, %s, %s)",
                 (nueva, cfg.model_dump_json(), creado_por, nota))
    conn.execute("UPDATE tenants SET config_version_activa = %s WHERE id = app_tenant()", (nueva,))
    auditar(conn, creado_por, "config_version_creada", {"version": nueva, "nota": nota})
    return nueva


def config_activa(conn) -> tuple[int, ConfigTenant]:
    row = conn.execute("""SELECT cv.version, cv.contenido FROM config_versions cv
                          JOIN tenants t ON t.id = cv.tenant_id AND t.config_version_activa = cv.version""").fetchone()
    if not row:
        raise RuntimeError("El tenant no tiene configuración activa")
    return row["version"], ConfigTenant.model_validate(row["contenido"])


def tenant_info(conn) -> dict:
    return conn.execute("SELECT * FROM tenants").fetchone()
