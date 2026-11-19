"""Conector de base de datos: carga inicial, sincronización incremental, solo lectura. PostgreSQL y MySQL."""
import uuid
from datetime import date
import pytest
from datapulso.db import tenant_conn
from datapulso.tenants import crear_tenant
from datapulso.datagen.generador import generar, snapshot_a, delta_desde, mapeo_para
from datapulso.datagen.fuente_db import (servidor_defecto, crear_base_cliente, insertar, parametros_fuente,
                                         LECTOR_USER, LECTOR_PASS)
from datapulso.ingest.fuentes import crear_fuente, sincronizar, probar_conexion
from datapulso.ingest.origenes import OrigenBaseDatos, OrigenError

T1 = date(2026, 9, 30)


def _crear_fuentes(c, srv, base, ind):
    for tabla in ("entidades", "eventos", "pagos"):
        crear_fuente(c, nombre=tabla, tipo="base_de_datos", is_demo=True,
                     mapeo=mapeo_para(ind, tabla, desde_bd=True),
                     parametros=parametros_fuente(srv, base, ind, tabla), password=LECTOR_PASS)


def _sync_todo(c):
    return {t: sincronizar(c, t) for t in ("entidades", "eventos", "pagos")}


@pytest.fixture(params=["postgresql", "mysql"])
def motor(request, motores):
    if request.param not in motores:
        pytest.skip(f"{request.param} no está disponible (scripts/dev_env.sh start)")
    return request.param


def test_sincronizacion_incremental(motor):
    ds = generar("servicios", 11, 250)
    srv = servidor_defecto(motor)
    base = f"cli_{motor[:2]}_{uuid.uuid4().hex[:6]}"
    crear_base_cliente(srv, base, snapshot_a(ds, T1))
    t = crear_tenant(f"BD-{motor}-{base}", "servicios", is_demo=True)
    snap = snapshot_a(ds, T1)
    with tenant_conn(t) as c:
        _crear_fuentes(c, srv, base, ds.ind)
        assert probar_conexion(c, "entidades", 2)["columnas"]  # prueba de conexión + muestra

        # 1) carga inicial completa: todo lo del snapshot, sin rechazos
        r1 = _sync_todo(c)
        assert r1["entidades"]["filas"]["insertadas"] == len(snap.entidades)
        assert r1["eventos"]["filas"]["insertadas"] == len(snap.eventos)
        assert r1["pagos"]["filas"]["insertadas"] == len(snap.pagos)
        assert all(x["filas"]["rechazadas"] == 0 for x in r1.values()), {k: v["rechazos_por_motivo"] for k, v in r1.items()}
        assert r1["eventos"]["marca_de_agua"]["antes"] is None and r1["eventos"]["marca_de_agua"]["despues"]

        # 2) segunda sincronización sin cambios en el cliente: no trae nada
        r2 = _sync_todo(c)
        assert all(x["filas"]["leidas"] == 0 for x in r2.values())

        # 3) el cliente sigue operando: llegan altas, eventos nuevos, pagos que se actualizan, bajas
        d = delta_desde(ds, T1)
        insertar(srv, base, d)
        r3 = _sync_todo(c)
        assert r3["eventos"]["filas"]["leidas"] == len(d.eventos) == r3["eventos"]["filas"]["insertadas"]
        assert r3["entidades"]["filas"]["leidas"] == len(d.entidades)
        assert r3["pagos"]["filas"]["leidas"] == len(d.pagos)
        assert r3["pagos"]["filas"]["actualizadas"] > 0     # pagos que estaban impagos y se cobraron
        assert r3["pagos"]["filas"]["insertadas"] + r3["pagos"]["filas"]["actualizadas"] == len(d.pagos)

        # 4) el resultado final equivale a haber cargado todo de una vez
        n = lambda tb: c.execute(f"SELECT count(*) AS n FROM {tb}").fetchone()["n"]
        assert (n("entidades"), n("eventos"), n("pagos")) == (len(ds.entidades), len(ds.eventos), len(ds.pagos))
        impagos = c.execute("SELECT count(*) AS n FROM pagos WHERE fecha_pago IS NULL").fetchone()["n"]
        assert impagos == int(ds.pagos["fecha_pago"].isna().sum())
        bajas = c.execute("SELECT count(*) AS n FROM entidades WHERE fecha_baja IS NOT NULL").fetchone()["n"]
        assert bajas == int(ds.entidades["fecha_baja"].notna().sum())
        # jerarquía empresa-usuarios resuelta
        assert c.execute("SELECT count(*) AS n FROM entidades WHERE parent_id IS NOT NULL").fetchone()["n"] == int(ds.entidades["empresa"].notna().sum())

        # 5) reiniciar la marca permite recarga completa, que no duplica
        from datapulso.ingest.fuentes import reiniciar_marca
        reiniciar_marca(c, "eventos")
        r5 = sincronizar(c, "eventos")
        assert r5["filas"]["insertadas"] == 0 and r5["filas"]["sin_cambios"] == len(ds.eventos)


def test_usuario_de_solo_lectura_no_puede_escribir(motor):
    ds = generar("salud", 3, 60)
    srv = servidor_defecto(motor)
    base = f"ro_{motor[:2]}_{uuid.uuid4().hex[:6]}"
    crear_base_cliente(srv, base, snapshot_a(ds, T1))
    # 1) el usuario de BD no tiene permisos de escritura
    if motor == "postgresql":
        import psycopg
        with psycopg.connect(host=srv.host, port=srv.puerto, user=LECTOR_USER, password=LECTOR_PASS, dbname=base) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM {ds.ind.tablas['entidades']}")
    else:
        import pymysql
        c = pymysql.connect(host=srv.host, port=srv.puerto, user=LECTOR_USER, password=LECTOR_PASS, database=base)
        with pytest.raises(pymysql.err.OperationalError):
            c.cursor().execute(f"DELETE FROM {ds.ind.tablas['entidades']}")
        c.close()
    # 2) aun con un usuario con permisos de escritura, la sesión del conector es de solo lectura
    o = OrigenBaseDatos(motor=motor, host=srv.host, puerto=srv.puerto, base=base, usuario=srv.admin_user,
                        password=srv.admin_password, consulta=f"SELECT * FROM {ds.ind.tablas['entidades']}")
    conn = o._abrir()
    try:
        cur = conn.cursor()
        with pytest.raises(Exception):
            cur.execute(f"DELETE FROM {ds.ind.tablas['entidades']}")
    finally:
        conn.close()


def test_motor_no_soportado_y_marca_invalida():
    with pytest.raises(OrigenError):
        OrigenBaseDatos(motor="oracle", host="h", puerto=1, base="b", usuario="u", password="p", consulta="SELECT 1")
    with pytest.raises(OrigenError):
        OrigenBaseDatos(motor="mysql", host="h", puerto=1, base="b", usuario="u", password="p", consulta="SELECT 1",
                        marca_columna="x; DROP TABLE y")
