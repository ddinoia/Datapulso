"""Simula la base de datos de un cliente (PostgreSQL o MySQL) con los datos generados, para probar el conector.

Crea tablas con los nombres de columna "del cliente", un usuario de SOLO LECTURA (dp_lector) y permite
`avanzar` el tiempo: aplicar los cambios posteriores (altas, eventos, pagos actualizados) para probar la
sincronización incremental.
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np
from .generador import Dataset
from .industrias import Industria

LECTOR_USER = "dp_lector"
LECTOR_PASS = "lector-dev"

_FECHAS = {"fecha_alta", "fecha_baja", "fecha_emision", "fecha_vencimiento", "fecha_pago"}
_TS = {"ts", "modificado_en"}
_NUM = {"monto", "monto_pagado"}


@dataclass(frozen=True)
class Servidor:
    motor: str
    host: str
    puerto: int
    admin_user: str
    admin_password: str = ""


def servidor_defecto(motor: str) -> Servidor:
    import os
    if motor == "postgresql":
        return Servidor("postgresql", os.environ.get("DP_PG_HOST", "127.0.0.1"), int(os.environ.get("DP_PG_PORT", "55432")),
                        os.environ.get("DP_ADMIN_USER", "postgres"), os.environ.get("DP_ADMIN_PASSWORD", ""))
    return Servidor("mysql", "127.0.0.1", int(os.environ.get("DP_MYPORT", "53306")), "root", "")


def _tipo_sql(motor: str, interna: str, es_pk: bool) -> str:
    pg = motor == "postgresql"
    if interna in _FECHAS:
        return "date"
    if interna in _TS:
        return "timestamp" if pg else "datetime"
    if interna in _NUM:
        return "numeric(18,2)"
    return "varchar(64)" if (es_pk or not pg) else "text"


def _clave(tabla: str) -> str:
    return {"entidades": "id_externo", "eventos": "id_externo", "pagos": "comprobante"}[tabla]


def _ddl(motor: str, ind: Industria, tabla: str, columnas: list[str]) -> str:
    nombre = ind.tablas[tabla]
    pk = _clave(tabla)
    cols = []
    for c in columnas:
        externo = ind.renombres[tabla].get(c, c)
        cols.append(f"{externo} {_tipo_sql(motor, c, c == pk)}" + (" PRIMARY KEY" if c == pk else ""))
    idx = ""
    return f"CREATE TABLE {nombre} ({', '.join(cols)}){idx}"


def _filas(df: pd.DataFrame):
    d = df.astype(object).where(df.notna(), None)
    for fila in d.itertuples(index=False, name=None):
        yield tuple(v.to_pydatetime() if isinstance(v, pd.Timestamp) else
                    (float(v) if isinstance(v, (np.floating,)) else v) for v in fila)


def _cols(ds: Dataset, tabla: str) -> list[str]:
    return list({"entidades": ds.entidades, "eventos": ds.eventos, "pagos": ds.pagos}[tabla].columns)


def _conectar(srv: Servidor, base: str | None):
    if srv.motor == "postgresql":
        import psycopg
        kw = dict(host=srv.host, port=srv.puerto, user=srv.admin_user, dbname=base or "postgres", autocommit=True)
        if srv.admin_password:
            kw["password"] = srv.admin_password
        return psycopg.connect(**kw)
    import pymysql
    return pymysql.connect(host=srv.host, port=srv.puerto, user=srv.admin_user, password=srv.admin_password,
                           database=base, autocommit=True, charset="utf8mb4")


def crear_base_cliente(srv: Servidor, base: str, ds: Dataset) -> None:
    """(Re)crea la base `base` con las 3 tablas cargadas con `ds` y un usuario de solo lectura."""
    ind = ds.ind
    with _conectar(srv, None) as c:
        cur = c.cursor()
        if srv.motor == "postgresql":
            cur.execute(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{base}" ENCODING \'UTF8\' TEMPLATE template0')
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (LECTOR_USER,))
            if not cur.fetchone():
                cur.execute(f"CREATE ROLE {LECTOR_USER} LOGIN PASSWORD '{LECTOR_PASS}' NOSUPERUSER")
        else:
            cur.execute(f"DROP DATABASE IF EXISTS `{base}`")
            cur.execute(f"CREATE DATABASE `{base}` CHARACTER SET utf8mb4")
            for h in ("%", "localhost"):  # 'localhost' evita que un usuario anónimo del servidor tape al comodín
                cur.execute(f"CREATE USER IF NOT EXISTS '{LECTOR_USER}'@'{h}' IDENTIFIED BY '{LECTOR_PASS}'")
    with _conectar(srv, base) as c:
        cur = c.cursor()
        for tabla in ("entidades", "eventos", "pagos"):
            cur.execute(_ddl(srv.motor, ind, tabla, _cols(ds, tabla)))
            mod = ind.tablas[tabla]
            cur.execute(f"CREATE INDEX {mod}_mod_idx ON {mod} (modificado_en)")
        if srv.motor == "postgresql":
            cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {LECTOR_USER}")
        else:
            for h in ("%", "localhost"):
                cur.execute(f"GRANT SELECT ON `{base}`.* TO '{LECTOR_USER}'@'{h}'")
    insertar(srv, base, ds, upsert=False)


def insertar(srv: Servidor, base: str, ds: Dataset, upsert: bool = True) -> dict:
    """Inserta (o actualiza si ya existe) las filas de `ds` en la base del cliente simulada."""
    ind = ds.ind
    n = {}
    with _conectar(srv, base) as c:
        cur = c.cursor()
        for tabla in ("entidades", "eventos", "pagos"):
            df = {"entidades": ds.entidades, "eventos": ds.eventos, "pagos": ds.pagos}[tabla]
            if df.empty:
                n[tabla] = 0
                continue
            cols = list(df.columns)
            ext = [ind.renombres[tabla].get(x, x) for x in cols]
            pk = ind.renombres[tabla].get(_clave(tabla), _clave(tabla))
            ph = ", ".join(["%s"] * len(cols))
            base_sql = f"INSERT INTO {ind.tablas[tabla]} ({', '.join(ext)}) VALUES ({ph})"
            if upsert:
                if srv.motor == "postgresql":
                    base_sql += f" ON CONFLICT ({pk}) DO UPDATE SET " + ", ".join(f"{e}=EXCLUDED.{e}" for e in ext if e != pk)
                else:
                    base_sql += " ON DUPLICATE KEY UPDATE " + ", ".join(f"{e}=VALUES({e})" for e in ext if e != pk)
            filas = list(_filas(df))
            for i in range(0, len(filas), 5000):
                cur.executemany(base_sql, filas[i:i + 5000])
            n[tabla] = len(filas)
    return n


def parametros_fuente(srv: Servidor, base: str, ind: Industria, tabla: str) -> dict:
    """`parametros` de la fuente de base de datos (sin contraseña)."""
    return {
        "motor": srv.motor, "host": srv.host, "puerto": srv.puerto, "base": base, "usuario": LECTOR_USER,
        "consulta": f"SELECT * FROM {ind.tablas[tabla]}", "marca_columna": "modificado_en", "marca_tipo": "fecha",
    }
