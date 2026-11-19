"""Alta y ejecución de fuentes de datos (CSV, base de datos)."""
import json
from pathlib import Path
from .mapeo import Mapeo
from .origenes import OrigenCsv, OrigenBaseDatos, OrigenError
from .tuberia import ejecutar_carga
from ..secrets import cifrar, descifrar
from ..tenants import auditar


def crear_fuente(conn, *, nombre: str, tipo: str, mapeo: dict, parametros: dict | None = None,
                 password: str | None = None, frecuencia: str = "manual", is_demo: bool = False,
                 actor: str = "sistema") -> str:
    m = Mapeo.model_validate(mapeo)  # falla si el mapeo es inválido
    parametros = dict(parametros or {})
    if "password" in parametros:
        raise ValueError("No guardar contraseñas en `parametros`: usar el argumento `password` (se cifra)")
    if tipo == "base_de_datos":
        _origen_bd(parametros, password or "")  # valida consulta/motor sin conectar
    fid = conn.execute(
        """INSERT INTO fuentes (tenant_id, nombre, tipo, destino, mapeo, parametros, credencial_cifrada, frecuencia, is_demo)
           VALUES (app_tenant(), %s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) RETURNING id""",
        (nombre, tipo, m.destino, m.model_dump_json(), json.dumps(parametros),
         cifrar(password) if password else None, frecuencia, is_demo)).fetchone()["id"]
    auditar(conn, actor, "fuente_creada", {"nombre": nombre, "tipo": tipo, "destino": m.destino})
    conn.commit()
    return str(fid)


def _fuente(conn, nombre: str) -> dict:
    f = conn.execute("SELECT * FROM fuentes WHERE nombre=%s", (nombre,)).fetchone()
    if not f:
        raise ValueError(f"No existe la fuente '{nombre}'")
    return f


def _origen_bd(p: dict, password: str) -> OrigenBaseDatos:
    return OrigenBaseDatos(
        motor=p["motor"], host=p["host"], puerto=int(p["puerto"]), base=p["base"], usuario=p["usuario"],
        password=password, consulta=p["consulta"], marca_columna=p.get("marca_columna"),
        marca_tipo=p.get("marca_tipo", "fecha"), lookback_segundos=int(p.get("lookback_segundos", 0)))


def cargar_csv(conn, nombre_fuente: str, ruta: str | Path) -> dict:
    f = _fuente(conn, nombre_fuente)
    if f["tipo"] != "csv":
        raise OrigenError(f"La fuente '{nombre_fuente}' no es de tipo csv")
    return ejecutar_carga(conn, f, OrigenCsv(ruta))


def sincronizar(conn, nombre_fuente: str) -> dict:
    """Sincronización incremental de una fuente de base de datos (trae lo posterior a la marca de agua)."""
    f = _fuente(conn, nombre_fuente)
    if f["tipo"] != "base_de_datos":
        raise OrigenError(f"La fuente '{nombre_fuente}' no es de tipo base_de_datos")
    pw = descifrar(f["credencial_cifrada"]) if f["credencial_cifrada"] else ""
    return ejecutar_carga(conn, f, _origen_bd(f["parametros"], pw))


def probar_conexion(conn, nombre_fuente: str, n: int = 5) -> dict:
    f = _fuente(conn, nombre_fuente)
    pw = descifrar(f["credencial_cifrada"]) if f["credencial_cifrada"] else ""
    return _origen_bd(f["parametros"], pw).probar(n)


def reiniciar_marca(conn, nombre_fuente: str, actor: str = "sistema") -> None:
    """Fuerza una recarga completa en la próxima sincronización (queda auditado)."""
    f = _fuente(conn, nombre_fuente)
    conn.execute("UPDATE fuentes SET marca_de_agua=NULL WHERE id=%s", (f["id"],))
    auditar(conn, actor, "marca_de_agua_reiniciada", {"fuente": nombre_fuente})
    conn.commit()
