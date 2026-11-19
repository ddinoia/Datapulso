"""Arma una organización de demostración completa pasando por la tubería real (CSV o conexión a base de datos).
Todo queda marcado is_demo = true."""
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path
from ..db import tenant_conn
from ..tenants import crear_tenant
from ..ingest.fuentes import crear_fuente, cargar_csv, sincronizar
from .generador import generar, exportar_csv, mapeo_para, snapshot_a, delta_desde, Dataset
from .fuente_db import servidor_defecto, crear_base_cliente, insertar, parametros_fuente, LECTOR_PASS
from .industrias import INDUSTRIAS

ORDEN = ("entidades", "eventos", "pagos")  # las entidades primero: eventos y pagos las referencian


def cargar_demo_csv(industria: str, *, nombre: str | None = None, semilla: int = 7, n_clientes: int | None = None,
                    estilo: str = "iso", carpeta: str | Path | None = None, ds: Dataset | None = None) -> dict:
    ds = ds or generar(industria, semilla, n_clientes)
    carpeta = Path(carpeta) if carpeta else Path(tempfile.mkdtemp(prefix=f"dp_demo_{industria}_"))
    rutas = exportar_csv(ds, carpeta, estilo)
    tid = crear_tenant(nombre or f"Demo {industria} {uuid.uuid4().hex[:4]}", industria, is_demo=True)
    informes = {}
    with tenant_conn(tid) as c:
        for tabla in ORDEN:
            crear_fuente(c, nombre=f"csv_{tabla}", tipo="csv", mapeo=mapeo_para(ds.ind, tabla, estilo), is_demo=True)
            informes[tabla] = cargar_csv(c, f"csv_{tabla}", rutas[tabla])
    return {"tenant_id": tid, "dataset": ds, "carpeta": str(carpeta), "informes": informes}


def cargar_demo_bd(industria: str, *, motor: str = "postgresql", nombre: str | None = None, semilla: int = 7,
                   n_clientes: int | None = None, hasta: date | None = None, ds: Dataset | None = None) -> dict:
    """Simula la base de un cliente, y la sincroniza con el conector. Si se da `hasta`, la base arranca en esa fecha
    y después se puede `avanzar_demo_bd` hasta el corte."""
    ds = ds or generar(industria, semilla, n_clientes)
    srv = servidor_defecto(motor)
    base = f"cliente_{industria}_{motor[:2]}_{uuid.uuid4().hex[:5]}"
    crear_base_cliente(srv, base, snapshot_a(ds, hasta) if hasta else ds)
    tid = crear_tenant(nombre or f"Demo {industria} (BD {motor}) {uuid.uuid4().hex[:4]}", industria, is_demo=True)
    informes = {}
    with tenant_conn(tid) as c:
        for tabla in ORDEN:
            crear_fuente(c, nombre=f"bd_{tabla}", tipo="base_de_datos", is_demo=True,
                         mapeo=mapeo_para(ds.ind, tabla, desde_bd=True),
                         parametros=parametros_fuente(srv, base, ds.ind, tabla), password=LECTOR_PASS)
            informes[tabla] = sincronizar(c, f"bd_{tabla}")
    return {"tenant_id": tid, "dataset": ds, "base": base, "servidor": srv, "informes": informes}


def avanzar_demo_bd(demo: dict, desde: date) -> dict:
    """El cliente 'sigue operando': aplica en su base lo ocurrido después de `desde` y re-sincroniza."""
    ds = demo["dataset"]
    insertar(demo["servidor"], demo["base"], delta_desde(ds, desde))
    informes = {}
    with tenant_conn(demo["tenant_id"]) as c:
        for tabla in ORDEN:
            informes[tabla] = sincronizar(c, f"bd_{tabla}")
    return informes
