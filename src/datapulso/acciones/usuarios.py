"""Usuarios por tenant con rol. Sin login hasta el hito 5: cada comando recibe --usuario y queda en auditoría."""
from ..tenants import auditar
from ..textos import t
from . import ErrorAcciones

ROLES = ("administrador", "analista", "aprobador", "solo_lectura")

# Qué puede hacer cada rol (propuesta del agente; decisión pendiente para Diego y Marcelo).
PERMISOS = {
    "administrar": {"administrador"},                                  # usuarios, catálogo, valor económico, salidas
    "crear_corrida": {"administrador", "analista"},                    # crear y pedir aprobación
    "aprobar": {"administrador", "aprobador"},
    "cancelar": {"administrador", "aprobador", "analista"},            # analista: solo las que creó, antes de aprobar
    "lanzar": {"administrador", "analista", "aprobador"},
    "exportar": {"administrador", "analista", "aprobador"},
    "emitir": {"administrador", "aprobador"},
}
_VERBO = {"administrar": "administrar la organización", "crear_corrida": "crear corridas", "aprobar": "aprobar corridas",
          "cancelar": "cancelar corridas", "lanzar": "marcar corridas como lanzadas", "exportar": "exportar listas",
          "emitir": "emitir webhooks"}


def alta(conn, nombre: str, rol: str, actor: str) -> dict:
    if rol not in ROLES:
        raise ErrorAcciones(f"Rol desconocido: {rol}. Válidos: {', '.join(ROLES)}")
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    u = conn.execute("""INSERT INTO usuarios (tenant_id, nombre, rol, is_demo) VALUES (app_tenant(), %s, %s, %s)
                        RETURNING *""", (nombre, rol, demo)).fetchone()
    auditar(conn, actor, "usuario_alta", {"usuario": nombre, "rol": rol})
    return u


def listar(conn) -> list[dict]:
    return conn.execute("SELECT * FROM usuarios ORDER BY nombre").fetchall()


def obtener(conn, nombre: str) -> dict:
    u = conn.execute("SELECT * FROM usuarios WHERE nombre = %s AND activo", (nombre,)).fetchone()
    if not u:
        raise ErrorAcciones(t("acciones.error.usuario", usuario=nombre))
    return u


def exigir(conn, nombre: str, permiso: str) -> dict:
    u = obtener(conn, nombre)
    if u["rol"] not in PERMISOS[permiso]:
        raise ErrorAcciones(t("acciones.error.permiso", usuario=nombre, rol=t("acciones.rol." + u["rol"]),
                              permiso=_VERBO[permiso]))
    return u
